"""
前端独立部署：master docs/ 的前端文件同步到 gh-pages（不动任何站点数据）

由 pages_deploy.yml 在 docs/** 变更时触发；纯标准库，无需安装依赖。
"""
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
sys.path.insert(0, SCRIPT_DIR)

import branch_store  # noqa: E402


def main():
    pages_dir = os.path.join(REPO_DIR, 'build', 'gh_pages_worktree')
    docs_dir = os.path.join(REPO_DIR, 'docs')
    branch_store.open_branch_worktree(REPO_DIR, branch_store.PAGES_BRANCH, pages_dir)
    try:
        branch_store.sync_frontend(REPO_DIR, pages_dir, docs_dir)
        branch_store.rebuild_merged_index(pages_dir)
        changed = branch_store.commit_and_push(
            pages_dir, branch_store.PAGES_BRANCH, 'pages: 前端部署 [skip ci]', push=True)
        print('前端已部署' if changed else '前端无变更')
    finally:
        branch_store.close_worktree(REPO_DIR, pages_dir)


if __name__ == '__main__':
    main()
