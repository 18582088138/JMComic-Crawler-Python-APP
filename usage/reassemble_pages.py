"""
gh-pages 全量重装配（维护动作）

用途：
    - 某次构建在 data 分支推送成功但 gh-pages 装配失败后的修复
    - gh-pages 内容出现异常时的整体校准

从所有 data/<site> 分支读取最新数据，全量同步进 gh-pages 并 force-push。
由 pages_deploy.yml 的 REASSEMBLE=true 触发；也可本地运行：
    python usage/reassemble_pages.py            # 实际执行（push）
    python usage/reassemble_pages.py --dry-run  # 只本地构建
"""
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
sys.path.insert(0, SCRIPT_DIR)

import branch_store  # noqa: E402


def main():
    push = '--dry-run' not in sys.argv
    branches = branch_store.list_data_branches(REPO_DIR)
    print('发现 data 分支:', branches or '(无)')
    if not branches:
        print('没有任何 data 分支，退出')
        return

    pages_dir = os.path.join(REPO_DIR, 'build', 'gh_pages_worktree')
    branch_store.open_branch_worktree(REPO_DIR, branch_store.PAGES_BRANCH, pages_dir)
    site_dirs = []
    try:
        branch_store.sync_frontend(REPO_DIR, pages_dir, os.path.join(REPO_DIR, 'docs'))
        for branch in branches:
            site = branch.split('/', 1)[1]
            site_dir = os.path.join(REPO_DIR, 'build', f'reassemble_{site}')
            branch_store.open_branch_worktree(REPO_DIR, branch, site_dir)
            site_dirs.append(site_dir)
            branch_store.sync_site_comics(site_dir, pages_dir, site)  # 全量（only_ids=None）
        changed = branch_store.commit_and_push(
            pages_dir, branch_store.PAGES_BRANCH, 'pages: 全量重装配 [skip ci]', push=push)
        print('gh-pages 全量重装配完成' if changed else 'gh-pages 无差异')
    finally:
        for d in site_dirs:
            branch_store.close_worktree(REPO_DIR, d)
        branch_store.close_worktree(REPO_DIR, pages_dir)


if __name__ == '__main__':
    main()
