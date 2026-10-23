"""
云书架构建主入口（GitHub Actions + GitHub Pages 多平台云书架）

整体流程：
    网页/手动/ Issue 提交目标（任意已支持站点的链接或ID）
    → 本脚本自动识别站点 → 对应爬虫下载 → 生成站点数据
    → 推送到该站孤立分支 data/<site>（单 commit force-push，历史不膨胀）
    → 重新装配 gh-pages（前端 + 全站数据聚合）→ Pages 自动部署

环境变量输入：
    JM_PAGES_INPUT      目标链接或ID（必填），任意已支持站点；纯数字默认 JMComic 车号。
                        特殊值 __cleanup__：不爬取，按 KEEP 对所有站点执行保留清理
    JM_PAGES_SOURCE     平台：auto（默认，自动识别）或注册 key（jmcomic/mangadex/...）
    JM_PAGES_MODE       产物模式：both（默认）/ read / zip
    JM_PAGES_KEEP       每站书库最多保留几本（默认 30）。
                        正常爬取时宽限到 KEEP+10 才硬删（给前端「下载或清理」提示留出时间窗），
                        __cleanup__ 任务按 KEEP 精确清理
    JM_PAGES_BATCH      批次号（多章节站点生效：20章/批，默认第 1 批；单章站点忽略）
    JM_PAGES_ISSUE_BODY issue 触发时传入正文，支持 mode:/keep:/source:/limit: 覆盖
    JM_PAGES_MOCK       置 1 时不联网，生成模拟图片并写入 JM_PAGES_DOCS_DIR（本地调试用）
    JM_PAGES_DOCS_DIR   mock 模式的站点目录（默认 ./docs）
    JM_PAGES_PUBLISH    置 0 时只构建不推分支（本地验证构建产物）
    JM_PAGES_RAW_DIR    原始下载目录（默认 ./build/pages_raw）
    CLIENT_IMPL         JMComic 客户端类型 api/html，下载失败时可切换
    JM_USERNAME/JM_PASSWORD  禁漫账号（可选，secrets）
    GITHUB_TOKEN        工作流注入，用于写 data/last_run.json 与 wishlist
"""
import json
import os
import re
import shutil
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
sys.path.insert(0, SCRIPT_DIR)

import branch_store  # noqa: E402
import site_builder  # noqa: E402
from crawlers import registry  # noqa: E402
from crawlers.base import log  # noqa: E402
from crawlers.generic import GenericCrawlFailed  # noqa: E402

LEGACY_SOURCE = {'jm': 'jmcomic'}
RETENTION_GRACE = 10  # 宽限期：正常爬取只在超过 KEEP+10 时硬删，KEEP..KEEP+10 之间由用户决定


def env(name, default=''):
    value = os.getenv(name)
    if value is None or str(value).strip() == '':
        return default
    return str(value).strip()


def parse_issue_body_overrides(body):
    overrides = {}
    if not body:
        return overrides
    m = re.search(r'(?im)^\s*mode\s*[:：]\s*(read|zip|both)\s*$', body)
    if m:
        overrides['mode'] = m.group(1).lower()
    m = re.search(r'(?im)^\s*keep\s*[:：]\s*(\d+)\s*$', body)
    if m:
        overrides['keep'] = int(m.group(1))
    m = re.search(r'(?im)^\s*source\s*[:：]\s*(\w+)\s*$', body)
    if m:
        overrides['source'] = normalize_source(m.group(1))
    m = re.search(r'(?im)^\s*(?:batch|limit)\s*[:：]\s*(\d+)\s*$', body)
    if m:
        overrides['batch'] = int(m.group(1))
    return overrides


def normalize_source(source):
    source = (source or 'auto').strip().lower()
    return LEGACY_SOURCE.get(source, source)


def mock_crawl(source, target, work_dir, batch):
    """本地调试用：不联网，生成模拟章节图片"""
    from PIL import Image, ImageDraw

    crawler = registry.get_crawler(source) if source != 'auto' else registry.get_crawler('jmcomic')
    aid = re.sub(r'\W+', '', target) or 'mock'
    if crawler.site == 'jmcomic':
        aid = re.sub(r'\D', '', aid) or '9999999'

    meta = {
        'id': f'{crawler.id_prefix}{aid}',
        'site': crawler.site,
        'title': f'模拟本子 {crawler.id_prefix}{aid}',
        'authors': ['mock-author'],
        'tags': ['mock', '测试'],
        'description': '本地 mock 数据，仅用于调试站点构建流程',
        'sourceUrl': '',
        'totalChapters': 2,
        'chapters': [
            {'photo_id': f'{aid}-1', 'dir_name': '1', 'title': '第1章', 'order': 1.0},
            {'photo_id': f'{aid}-2', 'dir_name': '2', 'title': '第2章', 'order': 2.0},
        ],
    }
    colors = [(94, 84, 255), (0, 180, 216), (255, 121, 63), (46, 196, 182)]
    for ch in meta['chapters']:
        ch_dir = os.path.join(work_dir, ch['dir_name'])
        os.makedirs(ch_dir, exist_ok=True)
        for i in range(1, 7):
            w, h = 800, 1400 if i % 3 else 2400
            color = colors[(i + int(ch['dir_name'])) % len(colors)]
            img = Image.new('RGB', (w, h), color)
            d = ImageDraw.Draw(img)
            d.rectangle([20, 20, w - 20, h - 20], outline=(255, 255, 255), width=6)
            d.text((60, 60), f"{meta['id']} ch{ch['dir_name']} p{i}", fill=(255, 255, 255))
            img.save(os.path.join(ch_dir, f'{i:05d}.jpg'), quality=88)
    log(f'mock 数据生成完毕: {work_dir}')
    return meta, work_dir, crawler


def write_summary(entry):
    summary_file = env('GITHUB_STEP_SUMMARY')
    if not summary_file:
        return
    lines = [
        '## ✅ 本子爬取完成',
        '',
        f'- 平台：**{entry.get("site", "?")}**',
        f'- 书架ID：**{entry["id"]}**',
        f'- 标题：《{entry["title"]}》',
        f'- 作者：{entry["author"]}',
        f'- 规模：{entry["pageCount"]} 页 / {entry["chapterCount"]} 章 / {entry["sizeBytes"] / 1024 / 1024:.1f}MB',
        f'- 在线阅览：{"✅" if entry["hasReader"] else "❌"}　打包下载：{"✅" if entry["hasZip"] else "❌"}',
        '',
        '> 站点数据已推送到孤立分支并装配 gh-pages，GitHub Pages 部署完成后即可访问。',
    ]
    with open(summary_file, 'a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def write_outputs(entry, crawler_site):
    output_file = env('GITHUB_OUTPUT')
    if output_file:
        with open(output_file, 'a', encoding='utf-8') as f:
            f.write(f'shelf_id={entry["id"]}\n')
            f.write(f'site={crawler_site}\n')


def report_status(status, target, source, **extra):
    obj = {'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
           'status': status, 'target': target[:200], 'source': source,
           'run_url': f"https://github.com/{env('GITHUB_REPOSITORY')}/actions/runs/{env('GITHUB_RUN_ID')}"
                      if env('GITHUB_REPOSITORY') and env('GITHUB_RUN_ID') else ''}
    obj.update(extra)
    branch_store.write_last_run(obj)


def parse_delete_ids(raw):
    """逗号/空格/换行分隔的书架 ID 列表；只接受安全字符"""
    ids = []
    for part in re.split(r'[\s,，、]+', raw or ''):
        part = part.strip()
        if part and re.fullmatch(r'[\w%.-]+', part):
            ids.append(part)
    return list(dict.fromkeys(ids))


def delete_books(ids):
    """__delete__ 维护任务：跨 data 分支按 ID 删除本子并重新装配"""
    if not ids:
        raise ValueError('__delete__ 但没有提供有效的书架 ID')
    remaining = list(ids)
    branches = branch_store.list_data_branches(REPO_DIR)
    for branch in branches:
        if not remaining:
            break
        site = branch.split('/', 1)[1]
        site_dir = os.path.join(REPO_DIR, 'build', 'site_worktree')
        branch_store.open_branch_worktree(REPO_DIR, branch, site_dir)
        try:
            idx = site_builder.read_index(site_dir)
            hits = [c for c in idx.get('comics', []) if c.get('id') in remaining]
            if not hits:
                continue
            for c in hits:
                cid = c['id']
                target = os.path.join(site_dir, 'comics', str(cid))
                if os.path.isdir(target):
                    shutil.rmtree(target)
                idx['comics'].remove(c)
                remaining.remove(cid)
                log(f'删除: [{site}] {cid}《{c.get("title", "")[:40]}》')
            site_builder.write_index(site_dir, idx)
            docs_dir = os.path.join(REPO_DIR, 'docs')
            branch_store.publish(REPO_DIR, site, site_dir, docs_dir, push=True,
                                 message=f'data: [{site}] 删除 {len(hits)} 本')
        finally:
            branch_store.close_worktree(REPO_DIR, site_dir)
    if remaining:
        log(f'以下 ID 未在任何 data 分支找到，跳过: {remaining}')
    log('删除任务完成')


def cleanup_all_sites(keep):
    """__cleanup__ 维护任务：对所有 data 分支按 keep 精确清理并重新装配"""
    branches = branch_store.list_data_branches(REPO_DIR)
    if not branches:
        log('没有任何 data 分支，无需清理')
        return
    for branch in branches:
        site = branch.split('/', 1)[1]
        site_dir = os.path.join(REPO_DIR, 'build', 'site_worktree')
        branch_store.open_branch_worktree(REPO_DIR, branch, site_dir)
        try:
            before = len(site_builder.read_index(site_dir).get('comics', []))
            if before <= keep:
                log(f'{site}: {before} 本，无需清理')
                continue
            site_builder.apply_retention(site_dir, keep, protect_id=None)
            after = len(site_builder.read_index(site_dir).get('comics', []))
            log(f'{site}: {before} → {after} 本')
            docs_dir = os.path.join(REPO_DIR, 'docs')
            branch_store.publish(REPO_DIR, site, site_dir, docs_dir, push=True,
                                 message=f'data: [{site}] 保留清理至 {keep} 本')
        finally:
            branch_store.close_worktree(REPO_DIR, site_dir)
    log('清理任务完成')


def cleanup_raw(raw_dir):
    if raw_dir and os.path.isdir(raw_dir) and os.path.abspath(raw_dir).startswith(os.path.abspath('./build')):
        shutil.rmtree(raw_dir, ignore_errors=True)


def main():
    raw_input = env('JM_PAGES_INPUT')
    if not raw_input:
        raise ValueError('JM_PAGES_INPUT 为空')
    # issue 触发时 INPUT 是标题（可能带 [jm] 前缀），先剥离
    raw_input = re.sub(r'^\s*\[jm\]\s*', '', raw_input, flags=re.IGNORECASE).strip()

    mode = env('JM_PAGES_MODE', 'both').lower()
    if mode not in ('both', 'read', 'zip'):
        mode = 'both'
    keep = int(env('JM_PAGES_KEEP', '30') or '30')
    source = normalize_source(env('JM_PAGES_SOURCE', 'auto'))
    batch = int(env('JM_PAGES_BATCH', '1') or '1')

    overrides = parse_issue_body_overrides(env('JM_PAGES_ISSUE_BODY'))
    mode = overrides.get('mode', mode)
    keep = overrides.get('keep', keep)
    source = overrides.get('source', source)
    batch = overrides.get('batch', overrides.get('limit', batch))

    raw_dir = os.path.abspath(env('JM_PAGES_RAW_DIR', './build/pages_raw'))
    mock = env('JM_PAGES_MOCK') == '1'
    publish = env('JM_PAGES_PUBLISH', '1') != '0'

    # ---------- 保留清理维护任务 ----------
    if raw_input.strip() == '__cleanup__':
        log(f'保留清理任务: 每站精确清理至 {keep} 本')
        cleanup_all_sites(keep)
        return

    # ---------- 指定删除维护任务 ----------
    if raw_input.strip() == '__delete__':
        delete_ids = env('JM_PAGES_DELETE_IDS')
        m = re.search(r'(?im)^\s*delete\s*[:：]\s*(.+)$', env('JM_PAGES_ISSUE_BODY'))
        if m:
            delete_ids = (delete_ids + ' ' + m.group(1)).strip()
        ids = parse_delete_ids(delete_ids)
        log(f'删除任务: {len(ids)} 本 {ids}')
        delete_books(ids)
        return

    # ---------- mock：本地构建调试，不碰分支 ----------
    if mock:
        mock_input = raw_input.split()[0] if raw_input.split() else raw_input  # mock 只跑第一个目标
        docs_dir = os.path.abspath(env('JM_PAGES_DOCS_DIR', './docs'))
        site_dir = docs_dir  # 兼容旧布局：docs/comics + docs/data/comics.json
        meta, raw_dir, crawler = mock_crawl(source, mock_input, raw_dir, batch)
        entry = site_builder.build_site(meta, raw_dir, site_dir, mode)
        site_builder.apply_retention(site_dir, keep, protect_id=meta['id'])
        # 旧布局兼容：index.json 落到 data/comics.json
        os.makedirs(os.path.join(docs_dir, 'data'), exist_ok=True)
        idx = site_builder.read_index(site_dir)
        with open(os.path.join(docs_dir, 'data', 'comics.json'), 'w', encoding='utf-8') as f:
            json.dump({'updatedAt': idx['updatedAt'], 'sites': {crawler.site: len(idx['comics'])},
                       'comics': idx['comics']}, f, ensure_ascii=False, indent=2)
        cleanup_raw(raw_dir)
        write_outputs(entry, crawler.site)
        return

    # ---------- 真实爬取（支持多目标：逐行/逐任务，单个 run 内串行完成） ----------
    targets = split_targets(raw_input, env('JM_PAGES_ISSUE_BODY'))
    log(f'任务数: {len(targets)}，模式: {mode}，保留: {keep}，批次: {batch}')

    worktrees = {}       # site -> site_dir（多目标任务期间保持打开，最后统一装配）
    touched = []         # (site_dir, site, entry_id)
    results = []         # ('ok'|'fail', target, entry_or_error)
    first_entry = None
    try:
        for ti, target in enumerate(targets, 1):
            log(f'—— 任务 {ti}/{len(targets)}: {target[:80]}')
            try:
                meta, raw_dir_actual, crawler = registry.crawl(source, target, raw_dir, batch)
                site = meta.get('site') or crawler.site
                if publish:
                    if site not in worktrees:
                        sd = os.path.join(REPO_DIR, 'build', f'site_{site}')
                        branch_store.open_branch_worktree(REPO_DIR, f'data/{site}', sd)
                        worktrees[site] = sd
                    site_dir = worktrees[site]
                    entry = site_builder.build_site(meta, raw_dir_actual, site_dir, mode)
                    site_builder.apply_retention(site_dir, keep + RETENTION_GRACE, protect_id=meta['id'])
                    branch_store.push_data_branch(site_dir, site, f'data: [{site}] +{entry["id"]}', push=True)
                    touched.append((site_dir, site, entry['id']))
                else:
                    site_dir = os.path.join(REPO_DIR, 'build', 'site_out')
                    entry = site_builder.build_site(meta, raw_dir_actual, site_dir, mode)
                    site_builder.apply_retention(site_dir, keep, protect_id=meta['id'])
                results.append(('ok', target, entry))
                if first_entry is None:
                    first_entry = entry
                write_summary(entry)
            except GenericCrawlFailed as e:
                log(f'任务 {ti} 通用兜底失败: {e}')
                branch_store.append_wishlist(e.url, e.domain, str(e)[:300])
                results.append(('fail', target, str(e)[:300]))
            except Exception as e:
                log(f'任务 {ti} 失败: {type(e).__name__}: {e}')
                results.append(('fail', target, f'{type(e).__name__}: {e}'[:300]))
            cleanup_raw(raw_dir)
    finally:
        if publish:
            if touched:
                docs_dir = os.path.join(REPO_DIR, 'docs')
                branch_store.assemble_gh_pages(REPO_DIR, docs_dir, touched, push=True)
            for site, sd in worktrees.items():
                branch_store.close_worktree(REPO_DIR, sd)

    ok_entries = [r[2] for r in results if r[0] == 'ok']
    fails = [(r[1], r[2]) for r in results if r[0] == 'fail']
    if not ok_entries:
        report_status('failed', raw_input, source,
                      error='；'.join(f'{t[:40]}: {e}' for t, e in fails)[:500])
        raise SystemExit(1)
    if fails:
        log(f'部分失败 {len(fails)}/{len(targets)}: {[t for t, _ in fails]}')
    report_status('success' if not fails else 'partial', raw_input,
                  ok_entries[0].get('site', source),
                  shelf_id=ok_entries[0]['id'], title=ok_entries[0]['title'],
                  done=[e['id'] for e in ok_entries][:20],
                  failed=[{'target': t[:80], 'error': e[:200]} for t, e in fails][:20])
    write_outputs(ok_entries[0], ok_entries[0].get('site', ''))


def split_targets(raw_input, issue_body=''):
    """多目标解析：按行/空白切分；issue 正文支持每行 `task: <链接>` 追加任务"""
    targets = [t for t in re.split(r'\s+', (raw_input or '').strip()) if t]
    if issue_body:
        for m in re.finditer(r'(?im)^\s*task\s*[:：]\s*(\S+)\s*$', issue_body):
            if m.group(1) not in targets:
                targets.append(m.group(1))
    return targets


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log(f'构建失败: {type(e).__name__}: {e}')
        raise
