"""
分支存储层：站点数据按「每站一个孤立分支」管理，gh-pages 为装配产物

分支模型：
    data/<site>   孤立分支，单 commit（force-push），保存该站 comics/ + index.json
    gh-pages      孤立分支，单 commit（force-push），Pages 源。内容 =
                  前端(master docs/) + 各站 comics/ 合并 + data/*.json 聚合

单 commit force-push 让 git 历史永不积累图片 blob，解决仓库体积膨胀；
相同内容的 blob 在 git 对象库中只存一份，data 分支与 gh-pages 不重复占空间。

小文件（data/last_run.json、data/wishlist.json）走 GitHub Contents API 直接改，
不必为状态更新做一次完整装配。
"""
import base64
import json
import os
import shutil
import subprocess
import urllib.request
import urllib.error

PAGES_BRANCH = 'gh-pages'


def log(msg):
    print(f'[branch-store] {msg}', flush=True)


def git(*args, cwd=None, input_text=None):
    r = subprocess.run(['git', *args], cwd=cwd, input=input_text,
                       capture_output=True, text=True, encoding='utf-8')
    if r.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)} 失败: {r.stderr.strip()}')
    return r.stdout.strip()


def git_ok(*args, cwd=None):
    return subprocess.run(['git', *args], cwd=cwd,
                          capture_output=True).returncode == 0


def remote_branch_exists(repo, branch):
    return git_ok('ls-remote', '--exit-code', '--heads', 'origin', branch, cwd=repo)


def list_data_branches(repo):
    """列出远端所有 data/<site> 分支名"""
    out = git('ls-remote', '--heads', 'origin', 'data/*', cwd=repo)
    branches = []
    for line in out.splitlines():
        m = line.split('refs/heads/', 1)
        if len(m) == 2 and m[1].startswith('data/'):
            branches.append(m[1])
    return sorted(set(branches))


def open_branch_worktree(repo, branch, dest):
    """把远端分支（可能不存在）以 worktree 形式打开到 dest，返回是否已有远端内容"""
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    if remote_branch_exists(repo, branch):
        git('fetch', '--depth', '1', 'origin',
            f'+refs/heads/{branch}:refs/remotes/origin/{branch}', cwd=repo)
        git('worktree', 'add', '--detach', dest, f'origin/{branch}', cwd=repo)
        return True
    # 分支不存在：从空 tree 造一个根 commit 起步
    empty_tree = git('mktree', cwd=repo, input_text='')
    commit = git('commit-tree', empty_tree, '-m', f'init {branch}', cwd=repo)
    git('worktree', 'add', '--detach', dest, commit, cwd=repo)
    return False


def close_worktree(repo, dest):
    git_ok('worktree', 'remove', '--force', dest, cwd=repo)


def commit_and_push(worktree, branch, message, push=True):
    """把 worktree 的全部变更压成单 commit 并 force-push（历史永不增长）"""
    git('add', '-A', cwd=worktree)
    if git_ok('diff', '--cached', '--quiet', cwd=worktree):
        log(f'{branch}: 无变更，跳过提交')
        return False
    git('commit', '--amend', '-m', message, '--no-verify', cwd=worktree)
    if push:
        git('push', '--force', 'origin', f'HEAD:refs/heads/{branch}', cwd=worktree)
        log(f'{branch}: 已 force-push 单 commit')
    else:
        log(f'{branch}: 已提交（本地，未推送）')
    return True


# ============================================================
# gh-pages 装配
# ============================================================

FRONTEND_ENTRIES = [  # docs/ 下属于前端的条目（其余如 comics/、data/ 属于数据）
    'index.html', 'reader.html', 'manifest.webmanifest', 'icon.svg',
    'sw.js', 'css', 'js', 'vendor',
]


def sync_frontend(repo, pages_dir, docs_dir):
    """master docs/ 的前端文件同步进 gh-pages worktree（不碰 comics/ 与 data/）"""
    for entry in FRONTEND_ENTRIES:
        dest = os.path.join(pages_dir, entry)
        if os.path.isfile(dest):
            os.remove(dest)
        elif os.path.isdir(dest):
            shutil.rmtree(dest)
        src = os.path.join(docs_dir, entry)
        if os.path.isfile(src):
            shutil.copy2(src, dest)
        elif os.path.isdir(src):
            shutil.copytree(src, dest)
    bump_sw_version(pages_dir)


def bump_sw_version(pages_dir):
    """每次装配按前端内容哈希刷新 sw.js 的 VERSION。

    没有这一步，SW 脚本不变时浏览器不会检查更新，旧缓存（含旧版 JS）会一直
    残留——2026-10-22 用户就因旧 JS 的 dispatch payload 持续 422。
    VERSION 变化 ⇒ 浏览器安装新 SW ⇒ activate 清旧缓存 ⇒ 页面 controllerchange 自动刷新。
    """
    import hashlib
    import re as _re
    sw_path = os.path.join(pages_dir, 'sw.js')
    if not os.path.isfile(sw_path):
        return
    h = hashlib.md5()
    for entry in FRONTEND_ENTRIES:
        p = os.path.join(pages_dir, entry)
        if os.path.isfile(p):
            h.update(entry.encode())
            h.update(open(p, 'rb').read())
        elif os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in sorted(files):
                    fp = os.path.join(root, f)
                    h.update(os.path.relpath(fp, pages_dir).encode())
                    h.update(open(fp, 'rb').read())
    version = 'v' + h.hexdigest()[:10]
    with open(sw_path, 'r', encoding='utf-8') as f:
        text = f.read()
    text, n = _re.subn(r"const VERSION = '[^']*';", f"const VERSION = '{version}';", text, count=1)
    if n:
        with open(sw_path, 'w', encoding='utf-8') as f:
            f.write(text)
        log(f'sw.js VERSION → {version}')


def sync_site_comics(site_dir, pages_dir, site, only_ids=None):
    """把某站 worktree 的内容同步进 gh-pages worktree：

    - 覆盖/新增该站 comics/<id>（only_ids 给定时只同步这些，其余保持不动——
      增量同步避免每次构建复制全站数据；全量校准用 reassemble_pages.py）
    - 删除该站已下架（retention 清掉）的 comics/<id>
    - 更新 data/index-<site>.json 与聚合 data/comics.json
    """
    with open(os.path.join(site_dir, 'index.json'), 'r', encoding='utf-8') as f:
        site_index = json.load(f)
    new_ids = {c['id'] for c in site_index.get('comics', [])}

    data_dir = os.path.join(pages_dir, 'data')
    os.makedirs(data_dir, exist_ok=True)
    old_index_path = os.path.join(data_dir, f'index-{site}.json')
    old_ids = set()
    if os.path.isfile(old_index_path):
        try:
            with open(old_index_path, 'r', encoding='utf-8') as f:
                old_ids = {c['id'] for c in json.load(f).get('comics', [])}
        except (json.JSONDecodeError, OSError):
            pass

    for cid in old_ids - new_ids:
        target = os.path.join(pages_dir, 'comics', str(cid))
        if os.path.isdir(target):
            shutil.rmtree(target)
            log(f'gh-pages: 移除已下架 {cid}')

    copy_ids = new_ids if only_ids is None else (new_ids & set(only_ids))
    src_comics = os.path.join(site_dir, 'comics')
    for cid in copy_ids:
        src = os.path.join(src_comics, str(cid))
        dest = os.path.join(pages_dir, 'comics', str(cid))
        if not os.path.isdir(src):
            continue
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        shutil.copytree(src, dest)

    with open(old_index_path, 'w', encoding='utf-8') as f:
        json.dump(site_index, f, ensure_ascii=False, indent=2)

    rebuild_merged_index(pages_dir)


def rebuild_merged_index(pages_dir):
    """data/index-*.json 聚合成 data/comics.json（书架唯一数据源）"""
    data_dir = os.path.join(pages_dir, 'data')
    merged, sites, updated = [], {}, ''
    for fn in sorted(os.listdir(data_dir)) if os.path.isdir(data_dir) else []:
        if not (fn.startswith('index-') and fn.endswith('.json')):
            continue
        try:
            with open(os.path.join(data_dir, fn), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        site = data.get('site') or fn[len('index-'):-len('.json')]
        comics = data.get('comics', [])
        sites[site] = len(comics)
        merged.extend(comics)
        updated = max(updated, data.get('updatedAt', ''))
    merged.sort(key=lambda c: c.get('updatedAt', ''), reverse=True)
    with open(os.path.join(data_dir, 'comics.json'), 'w', encoding='utf-8') as f:
        json.dump({'updatedAt': updated, 'sites': sites, 'comics': merged},
                  f, ensure_ascii=False, indent=2)


def publish(repo, site, site_dir, docs_dir, push=True, message=None, changed_id=None):
    """完整发布：data/<site> 分支提交 → gh-pages 重新装配 → 均 force-push

    changed_id：本次新建/重建的本子 id，gh-pages 侧只增量复制它（下架目录照常删除）。
    """
    message = message or f'data: 更新 {site} 书库'
    changed = push_data_branch(site_dir, site, message, push=push)
    assemble_gh_pages(repo, docs_dir, [(site_dir, site, changed_id)], push=push)
    return changed


def push_data_branch(site_dir, site, message, push=True):
    """只提交并推送 data/<site> 分支（多目标任务时每站一次，装配统一最后做）"""
    return commit_and_push(site_dir, f'data/{site}', message, push=push)


def assemble_gh_pages(repo, docs_dir, syncs, push=True):
    """一次性装配 gh-pages。syncs: [(site_dir, site, changed_id_or_None)]"""
    pages_dir = os.path.join(repo, 'build', 'gh_pages_worktree')
    existed = open_branch_worktree(repo, PAGES_BRANCH, pages_dir)
    try:
        sync_frontend(repo, pages_dir, docs_dir)
        for site_dir, site, changed_id in syncs:
            sync_site_comics(site_dir, pages_dir, site,
                             only_ids=None if not existed else ({changed_id} if changed_id else set()))
        if not existed:
            log('gh-pages 分支不存在，已初始化为完整站点')
        return commit_and_push(pages_dir, PAGES_BRANCH,
                               f'pages: 装配 {len(syncs)} 站更新 [skip ci]', push=push)
    finally:
        close_worktree(repo, pages_dir)


# ============================================================
# GitHub Contents API（小文件：last_run.json / wishlist.json）
# ============================================================

def _repo_slug():
    slug = os.getenv('GITHUB_REPOSITORY', '').strip()
    if slug:
        return slug
    url = git('config', '--get', 'remote.origin.url')
    # https://github.com/owner/repo.git 或 git@github.com:owner/repo.git
    tail = url.split('github.com', 1)[1].lstrip('/:').removesuffix('.git')
    return tail


def contents_read(path, branch=PAGES_BRANCH, token=None):
    token = token or os.getenv('GITHUB_TOKEN')
    if not token:
        return None
    url = f'https://api.github.com/repos/{_repo_slug()}/contents/{path}?ref={branch}'
    req = urllib.request.Request(url, headers={
        'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.loads(r.read())
        return body.get('sha'), base64.b64decode(body['content']).decode('utf-8')
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def contents_write(path, text, message, branch=PAGES_BRANCH, token=None):
    token = token or os.getenv('GITHUB_TOKEN')
    if not token:
        log(f'无 GITHUB_TOKEN，跳过 {path} 更新')
        return False
    current = contents_read(path, branch, token)
    sha = current[0] if current else None
    url = f'https://api.github.com/repos/{_repo_slug()}/contents/{path}'
    payload = {'message': message, 'branch': branch,
               'content': base64.b64encode(text.encode('utf-8')).decode()}
    if sha:
        payload['sha'] = sha
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method='PUT',
                                 headers={'Authorization': f'Bearer {token}',
                                          'Accept': 'application/vnd.github+json',
                                          'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()
    log(f'{path}: 已通过 Contents API 更新')
    return True


def write_last_run(status_obj, token=None):
    try:
        contents_write('data/last_run.json', json.dumps(status_obj, ensure_ascii=False, indent=2),
                       'pages: 更新任务状态', token=token)
    except Exception as e:
        log(f'写入 last_run.json 失败（不影响主流程）: {e}')


def append_wishlist(url, domain, reason, token=None):
    try:
        current = contents_read('data/wishlist.json', token=token)
        data = json.loads(current[1]) if current else {'items': []}
        items = [i for i in data.get('items', []) if i.get('domain') != domain]
        import time as _t
        items.insert(0, {'domain': domain, 'url': url, 'reason': reason,
                         'ts': _t.strftime('%Y-%m-%dT%H:%M:%SZ', _t.gmtime())})
        contents_write('data/wishlist.json',
                       json.dumps({'items': items[:100]}, ensure_ascii=False, indent=2),
                       f'pages: 愿望清单 +{domain}', token=token)
    except Exception as e:
        log(f'写入 wishlist.json 失败（不影响主流程）: {e}')
