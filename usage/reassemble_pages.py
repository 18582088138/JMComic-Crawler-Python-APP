"""
gh-pages 巡检装配（增量版）

机制：装配成功的站点会在 gh-pages 写入 data/.assembled-<site>（内容为该 data 分支 HEAD sha）。
巡检时先用 ls-remote（轻量）比对各 data 分支 sha 与标记，全部一致则零 git 操作直接退出；
有变更才打开 gh-pages 与对应站点 worktree 做全量同步（该站数据可能增删，无法预知章节级别差异）。

触发：pages_deploy.yml 定时（*/15min）或手动 REASSEMBLE=true；
本地：python usage/reassemble_pages.py [--dry-run]
"""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
sys.path.insert(0, SCRIPT_DIR)

import branch_store  # noqa: E402


def remote_branch_sha(repo, branch):
    out = branch_store.git('ls-remote', '--heads', 'origin', branch, cwd=repo)
    if not out.strip():
        return None
    return out.split()[0]


def assembled_sha(repo, site, token=None):
    cur = branch_store.contents_read(f'data/.assembled-{site}', token=token)
    if not cur:
        return None
    return cur[1].strip()


def main():
    push = '--dry-run' not in sys.argv
    branches = branch_store.list_data_branches(REPO_DIR)
    if not branches:
        print('没有任何 data 分支，退出')
        return

    changed = []
    for branch in branches:
        site = branch.split('/', 1)[1]
        remote_sha = remote_branch_sha(REPO_DIR, branch)
        done_sha = assembled_sha(REPO_DIR, site)
        if remote_sha and remote_sha != done_sha:
            changed.append((site, remote_sha))
            print(f'{site}: 有更新待装配 {str(done_sha)[:8]} → {remote_sha[:8]}')
        else:
            print(f'{site}: 无变更')

    if not changed:
        print('全部站点已装配到最新，无需操作')
        return

    pages_dir = os.path.join(REPO_DIR, 'build', 'gh_pages_worktree')
    branch_store.open_branch_worktree(REPO_DIR, branch_store.PAGES_BRANCH, pages_dir)
    site_dirs = []
    try:
        branch_store.sync_frontend(REPO_DIR, pages_dir, os.path.join(REPO_DIR, 'docs'))
        for site, remote_sha in changed:
            site_dir = os.path.join(REPO_DIR, 'build', f'reassemble_{site}')
            branch_store.open_branch_worktree(REPO_DIR, f'data/{site}', site_dir)
            site_dirs.append(site_dir)
            branch_store.sync_site_comics(site_dir, pages_dir, site)  # 全量（含删除）
            marker = os.path.join(pages_dir, 'data', f'.assembled-{site}')
            with open(marker, 'w') as f:
                f.write(remote_sha)
        branch_store.rebuild_merged_index(pages_dir)
        ok = branch_store.commit_and_push(
            pages_dir, branch_store.PAGES_BRANCH,
            f'pages: 巡检装配 {len(changed)} 站 [skip ci]', push=push)
        print('巡检装配完成' if ok else '装配后无差异（内容其实一致）')
    finally:
        for d in site_dirs:
            branch_store.close_worktree(REPO_DIR, d)
        branch_store.close_worktree(REPO_DIR, pages_dir)


if __name__ == '__main__':
    main()
