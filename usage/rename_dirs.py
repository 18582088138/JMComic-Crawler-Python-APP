"""
存量目录重命名：comics/<id>/ → comics/<id>_<部分标题>/（V2.1 统一目录风格）

对 data/<site> 分支里仍是纯 id 目录的存量书：
1. os.rename 目录为 id_部分标题（标题清洗截断 20，见 site_builder.dir_slug）
2. 更新 manifest.json 的 dir 字段与 index 条目的 dir/cover 字段
   （zip 文件名与 manifest 内部 pages 路径均不含父目录，无需改动）
3. 幂等：已是 slug 目录的书跳过；标题为空保持 id 不动；目标目录已存在则跳过不覆盖

gh-pages 侧由巡检装配自动同步（sync_site_comics 的改名处理会清掉旧目录）。

用法：
    python usage/rename_dirs.py [--site=jmcomic] [--dry-run] [--no-push]
Actions：dispatch TARGET=__rename_dirs__（SOURCE=auto 全部，MIGRATE_DRY 复用作 dry-run）
"""
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
sys.path.insert(0, SCRIPT_DIR)

import branch_store  # noqa: E402
import site_builder as sb  # noqa: E402

RUN_BUDGET_MIN = 50


def log(msg):
    print(f'[rename] {msg}', flush=True)


def rename_book(comic_parent, entry, dry=False):
    """重命名一本书的目录并同步字段。返回是否发生了改名（dry 时为计划值）"""
    cid = str(entry.get('id', ''))
    cur = str(entry.get('dir') or cid)
    slug = sb.dir_slug(entry.get('title', ''))
    target = f'{cid}_{slug}' if slug else cid
    if cur == target:
        return False
    src = os.path.join(comic_parent, cur)
    dst = os.path.join(comic_parent, target)
    if not os.path.isdir(src):
        return False
    if os.path.exists(dst):
        log(f'  {cid}: 目标目录已存在 {target}，跳过不覆盖')
        return False
    if dry:
        log(f'  {cid}: {cur} → {target}（计划）')
        return True
    os.rename(src, dst)
    m = sb.read_manifest(dst)
    if m is not None:
        m['dir'] = target
        with open(os.path.join(dst, 'manifest.json'), 'w', encoding='utf-8') as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
    entry['dir'] = target
    entry['cover'] = f'comics/{target}/cover.jpg'
    log(f'  {cid}: {cur} → {target}')
    return True


def rename_site(site_dir, site, dry=False):
    idx = sb.read_index(site_dir)
    comics = idx.get('comics', [])
    n = 0
    for c in comics:
        if rename_book(os.path.join(site_dir, 'comics'), c, dry=dry):
            n += 1
    if n and not dry:
        sb.write_index(site_dir, idx)
    return n


def rename_all(repo, push=True, only_site=None, dry=False, budget_min=RUN_BUDGET_MIN):
    branches = branch_store.list_data_branches(repo)
    if only_site and only_site != 'auto':
        branches = [b for b in branches if b.split('/', 1)[1] == only_site]
    if not branches:
        log('没有匹配的 data 分支')
        return
    start = time.time()
    for branch in branches:
        if (time.time() - start) / 60 > budget_min:
            log(f'超出时间预算（{budget_min} 分钟），剩余站点下次继续（幂等）')
            break
        site = branch.split('/', 1)[1]
        site_dir = os.path.join(repo, 'build', f'rename_{site}')
        branch_store.open_branch_worktree(repo, branch, site_dir)
        try:
            n = rename_site(site_dir, site, dry=dry)
            log(f'{site}: {n} 本重命名')
            if n and not dry and push:
                branch_store.commit_and_push(site_dir, branch, f'data: [{site}] 存量目录重命名为 id_标题')
        finally:
            branch_store.close_worktree(repo, site_dir)
    log('重命名完成（gh-pages 由巡检装配自动同步，旧目录随装配清除）')


def main():
    args = sys.argv[1:]
    dry = '--dry-run' in args
    push = '--no-push' not in args
    only = None
    for a in args:
        if a.startswith('--site='):
            only = a.split('=', 1)[1].strip()
    rename_all(REPO_DIR, push=push, only_site=only, dry=dry)


if __name__ == '__main__':
    main()
