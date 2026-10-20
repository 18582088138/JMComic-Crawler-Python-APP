"""
一次性迁移：master docs/comics 的存量站点数据 → 每站孤立分支 + gh-pages

用法：
    python usage/migrate_to_branches.py            # 实际执行（push）
    python usage/migrate_to_branches.py --dry-run  # 只本地构建不推送

迁移后 docs/comics 与 docs/data 即可从 master 删除。
"""
import json
import os
import shutil
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
sys.path.insert(0, SCRIPT_DIR)

import branch_store  # noqa: E402
from crawlers.registry import CRAWLERS  # noqa: E402

DOCS = os.path.join(REPO_DIR, 'docs')
COMICS = os.path.join(DOCS, 'comics')

PREFIX_TO_SITE = {cls.id_prefix: cls.site for cls in CRAWLERS.values() if cls.id_prefix}


def site_of(cid, manifest):
    if manifest.get('site'):
        return manifest['site']
    for prefix, site in PREFIX_TO_SITE.items():
        if str(cid).startswith(prefix):
            return site
    return 'jmcomic'


def entry_from_manifest(m):
    return {
        'id': m['id'],
        'site': m['site'],
        'title': m['title'],
        'author': m.get('author') or (m.get('authors') or ['未知'])[0],
        'pageCount': m.get('pageCount', 0),
        'chapterCount': m.get('chapterCount', 0),
        'cover': f"comics/{m['id']}/cover.jpg",
        'hasReader': m.get('hasReader', True),
        'hasZip': m.get('hasZip', False),
        'zipName': m.get('zip'),
        'updatedAt': m.get('updatedAt', ''),
        'sizeBytes': m.get('sizeBytes', 0),
    }


def main():
    push = '--dry-run' not in sys.argv
    if not push:
        print('=== DRY RUN（不推送）===')

    # 1. 按站点分组
    by_site = {}
    for cid in sorted(os.listdir(COMICS)):
        cdir = os.path.join(COMICS, cid)
        mpath = os.path.join(cdir, 'manifest.json')
        if not os.path.isfile(mpath):
            continue
        with open(mpath, 'r', encoding='utf-8') as f:
            m = json.load(f)
        m['site'] = site_of(cid, m)
        by_site.setdefault(m['site'], []).append((cid, cdir, m))

    print('存量分布:', {s: len(v) for s, v in by_site.items()})

    site_dirs = {}
    try:
        # 2. 每站建 data/<site> 分支
        for site, items in by_site.items():
            site_dir = os.path.join(REPO_DIR, 'build', f'migrate_{site}')
            branch_store.open_branch_worktree(REPO_DIR, f'data/{site}', site_dir)
            site_dirs[site] = site_dir
            entries = []
            for cid, cdir, m in items:
                dest = os.path.join(site_dir, 'comics', cid)
                if os.path.isdir(dest):
                    shutil.rmtree(dest)
                shutil.copytree(cdir, dest)
                entries.append(entry_from_manifest(m))
            entries.sort(key=lambda e: e['updatedAt'], reverse=True)
            index = {'site': site,
                     'updatedAt': entries[0]['updatedAt'] if entries else '',
                     'comics': entries}
            os.makedirs(site_dir, exist_ok=True)
            with open(os.path.join(site_dir, 'index.json'), 'w', encoding='utf-8') as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
            branch_store.commit_and_push(site_dir, f'data/{site}',
                                         f'data: [{site}] 存量迁移 {len(entries)} 本', push=push)
            print(f'data/{site}: {len(entries)} 本已提交')

        # 3. 装配 gh-pages
        pages_dir = os.path.join(REPO_DIR, 'build', 'gh_pages_worktree')
        branch_store.open_branch_worktree(REPO_DIR, branch_store.PAGES_BRANCH, pages_dir)
        branch_store.sync_frontend(REPO_DIR, pages_dir, DOCS)
        for site, site_dir in site_dirs.items():
            branch_store.sync_site_comics(site_dir, pages_dir, site)
        branch_store.commit_and_push(pages_dir, branch_store.PAGES_BRANCH,
                                     'pages: 初始装配（迁移自 master docs/）', push=push)
        branch_store.close_worktree(REPO_DIR, pages_dir)
        print('gh-pages: 装配完成')
    finally:
        for site_dir in site_dirs.values():
            branch_store.close_worktree(REPO_DIR, site_dir)

    print('迁移完成' + ('（dry-run，未推送）' if not push else '（已推送）'))


if __name__ == '__main__':
    main()
