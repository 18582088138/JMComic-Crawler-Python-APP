"""
存量迁移：V2.0 zip-only 架构（散图退役，仓库体积约减半）

对每个 data/<site> 分支逐本处理：
1. 已有 zip 的书：zip 原字节保留不动，只把 manifest 章节的 pages 重写为
   zip 内条目路径（v2 pages 指向线上散图，v3 起为 zip 内路径）；
   重写前做结构校验（章节数 ↔ 文件夹数）+ 全量 CRC（testzip），
   校验不过且有散图时回退为「从散图重建」，再不过则跳过不删图
2. 无 zip 的书（迁移前体积超限未打包的 9 本）：用仍在分支上的散图
   生成分卷/整包，写出后逐卷 CRC 校验
3. 校验通过后删除 comics/<id>/images/**——此后漫画内容只存在于 zip
4. 更新 index 条目（zipName/zipParts/hasZip/sizeBytes）

幂等：已迁移的书重跑无害（zip 已就位 → 快路径重写 manifest 为 no-op）。
gh-pages 装配交给 15 分钟巡检（reassemble_pages 按 data 分支 sha 自动补装配，
装配层已瘦身：只同步 manifest+封面）。

用法：
    python usage/migrate_zip_only.py [--site=jmcomic] [--dry-run] [--no-push]
Actions：dispatch TARGET=__migrate__（SOURCE=auto 迁移全部站点，否则只迁指定站）

注意：迁移会先推新 zip/manifest 再删散图，旧散图 blob 等 GitHub GC 回收，
期间仓库报告体积可能先涨后落（数天~两周）。
"""
import json
import os
import re
import sys
import time
import zipfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
sys.path.insert(0, SCRIPT_DIR)

import branch_store  # noqa: E402
import site_builder as sb  # noqa: E402

RUN_BUDGET_MIN = 50  # 单 run 软预算：超时则收尾，剩余站点下次再跑（幂等）


def log(msg):
    print(f'[migrate] {msg}', flush=True)


def _zip_layout(zf):
    """返回 (根目录, [章节文件夹自然序], {文件夹: [完整条目名自然序]})"""
    root = ''
    folders = []
    by_folder = {}
    for name in zf.namelist():
        parts = name.split('/')
        if len(parts) < 3 or parts[-1] == '':
            continue
        root = root or parts[0]
        folder = parts[1]
        if folder not in by_folder:
            by_folder[folder] = []
            folders.append(folder)
        by_folder[folder].append(name)
    folders.sort(key=sb.natural_key)
    for f in by_folder:
        by_folder[f].sort(key=sb.natural_key)
    return root, folders, by_folder


def _rewrite_pages_from_zip(comic_dir, cid, m, zip_path):
    """快路径：zip 不动，manifest pages 重写为 zip 内条目路径。成功返回 files 描述"""
    chapters = m.get('chapters') or []
    with zipfile.ZipFile(zip_path) as zf:
        root, folders, by_folder = _zip_layout(zf)
        if len(folders) != len(chapters):
            raise RuntimeError(f'zip 文件夹数 {len(folders)} 与章节数 {len(chapters)} 不一致')
        bad = zf.testzip()
        if bad is not None:
            raise RuntimeError(f'zip CRC 校验失败: {bad}')
        pages_by_ch = []
        for i, ch in enumerate(chapters):
            if not by_folder[folders[i]]:
                raise RuntimeError(f'章节 {i + 1} 在 zip 中没有文件')
            pages_by_ch.append(by_folder[folders[i]])
    # 写回 manifest
    for i, ch in enumerate(chapters):
        ch['pages'] = pages_by_ch[i]
        ch['pageCount'] = len(pages_by_ch[i])
        ch['index'] = i + 1
    m['zip'] = os.path.basename(zip_path)
    m['parts'] = None
    m['zipNote'] = ''
    m['hasZip'] = True
    m['hasReader'] = True
    m['pageCount'] = sum(len(p) for p in pages_by_ch)
    m['chapterCount'] = len(chapters)
    with open(os.path.join(comic_dir, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    return [{'file': m['zip']}]


def _rebuild_from_images(comic_dir, cid, m):
    """重建路径：从散图生成 zip/分卷（9 本无 zip 书专用）"""
    chapters = m.get('chapters') or []
    plan = []
    for i, ch in enumerate(chapters):
        entries = sb.loose_files(comic_dir, sb.chapter_key(ch))
        if not entries:
            raise RuntimeError(f"章节 {sb.chapter_key(ch)}《{ch.get('title', '')}》散图缺失")
        plan.append({'folder': sb.folder_name(i + 1, ch.get('title', '')), 'entries': entries})
    files, pages_by_ch = sb.write_book_zip(comic_dir, cid, plan)
    # 逐卷 CRC 校验（之后要删散图，必须确认包完好）
    for p in files:
        with zipfile.ZipFile(os.path.join(comic_dir, p['file'])) as z:
            if z.testzip() is not None:
                raise RuntimeError(f"新分卷 CRC 校验失败: {p['file']}")
    for i, ch in enumerate(chapters):
        ch['pages'] = pages_by_ch[i]
        ch['pageCount'] = len(pages_by_ch[i])
        ch['index'] = i + 1
    m['zip'] = files[0]['file'] if len(files) == 1 else None
    m['parts'] = files if len(files) > 1 else None
    m['zipNote'] = ''
    m['hasZip'] = True
    m['hasReader'] = True
    m['pageCount'] = sum(len(p) for p in pages_by_ch)
    m['chapterCount'] = len(chapters)
    with open(os.path.join(comic_dir, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    return files


def _is_v3(m, zip_ok):
    """v3 判定：包在 + （分卷 或 所有有 pages 的章节都指向 zip 条目）"""
    if not zip_ok:
        return False
    if m.get('parts'):
        return True
    chapters = m.get('chapters') or []
    with_pages = [c for c in chapters if c.get('pages')]
    return bool(with_pages) and all(
        not str(c['pages'][0]).startswith('images/') for c in with_pages)


def migrate_book(comic_dir, cid, dry=False):
    """迁移一本。返回 (action, detail)；action ∈ rewrite/rebuild/skip"""
    m = sb.read_manifest(comic_dir)
    if not m:
        return 'skip', 'manifest 缺失'
    chapters = m.get('chapters') or []
    if not chapters:
        return 'skip', 'manifest 无章节'
    images_dir = os.path.join(comic_dir, 'images')
    has_images = os.path.isdir(images_dir)
    zip_name = m.get('zip')
    zip_path = os.path.join(comic_dir, zip_name) if zip_name else None
    if (not zip_path or not os.path.isfile(zip_path)) and os.path.isfile(os.path.join(comic_dir, sb.zip_name_for(cid))):
        zip_name = sb.zip_name_for(cid)
        zip_path = os.path.join(comic_dir, zip_name)

    # 已是 v3 格式 → 幂等 no-op（顺带清残留散图，比如上次跑到一半中断）
    if _is_v3(m, bool(zip_path and os.path.isfile(zip_path))):
        if has_images:
            if dry:
                return 'skip', '已是 v3 格式（残留散图待清理）'
            import shutil
            shutil.rmtree(images_dir, ignore_errors=True)
            return 'skip', '已是 v3 格式（已清理残留散图）'
        return 'skip', '已是 v3 格式'

    action = None
    if zip_path and os.path.isfile(zip_path):
        action = 'rewrite'
    elif has_images:
        action = 'rebuild'
    else:
        return 'skip', '既无 zip 也无散图'

    if dry:
        # dry-run = 真实校验不落盘：rewrite 验结构+CRC；rebuild 验散图齐全
        try:
            if action == 'rewrite':
                with zipfile.ZipFile(zip_path) as zf:
                    _root, folders, by_folder = _zip_layout(zf)
                    if len(folders) != len(chapters):
                        raise RuntimeError(f'文件夹数 {len(folders)} != 章节数 {len(chapters)}')
                    if zf.testzip() is not None:
                        raise RuntimeError('CRC 校验失败')
                return 'rewrite', f'校验通过（{len(chapters)} 章，zip 保留仅重写 manifest）'
            missing = [sb.chapter_key(c) for c in chapters
                       if not sb.loose_files(comic_dir, sb.chapter_key(c))]
            if missing:
                return 'skip', f'散图缺失章节 {missing}（真实迁移会跳过且不删图）'
            if action == 'rewrite' and has_images:
                return 'rebuild', '快路径不过，将改从散图重建'
            return 'rebuild', '可重建（各章散图齐全）'
        except Exception as e:
            if action == 'rewrite' and has_images:
                missing = [sb.chapter_key(c) for c in chapters
                           if not sb.loose_files(comic_dir, sb.chapter_key(c))]
                if not missing:
                    return 'rebuild', f'快路径失败（{e}），将改从散图重建'
                return 'skip', f'校验失败（{e}）且散图不全'
            return 'skip', f'校验失败: {e}'

    try:
        if action == 'rewrite':
            try:
                files = _rewrite_pages_from_zip(comic_dir, cid, m, zip_path)
            except RuntimeError as e:
                if has_images:
                    log(f'  {cid}: 快路径失败（{e}），改从散图重建')
                    action = 'rebuild'
                    files = _rebuild_from_images(comic_dir, cid, m)
                else:
                    raise
        else:
            files = _rebuild_from_images(comic_dir, cid, m)
    except Exception as e:
        return 'skip', f'{action} 失败: {e}（散图保留不动）'

    if has_images:
        import shutil
        shutil.rmtree(images_dir, ignore_errors=True)
    return action, f"OK（{len(files)} 卷: {os.path.basename(files[0]['file'])}）"


def migrate_site(site_dir, site, dry=False):
    idx = sb.read_index(site_dir)
    comics = idx.get('comics', [])
    stats = {'rewrite': 0, 'rebuild': 0, 'skip': 0}
    log(f'== [{site}] {len(comics)} 本 ==')
    changed = False
    for c in comics:
        cid = str(c.get('id', ''))
        comic_dir = os.path.join(site_dir, 'comics', cid)
        if not os.path.isdir(comic_dir):
            stats['skip'] += 1
            continue
        action, detail = migrate_book(comic_dir, cid, dry=dry)
        stats[action] = stats.get(action, 0) + 1
        mark = {'rewrite': 'pages→zip', 'rebuild': '重建', 'skip': '跳过'}[action]
        log(f'  {cid}: [{mark}] {detail}')
        if action in ('rewrite', 'rebuild'):
            changed = True
            if not dry:
                m = sb.read_manifest(comic_dir) or {}
                c['hasZip'] = True
                c['hasReader'] = True
                c['zipName'] = m.get('zip')
                c['zipParts'] = len(m.get('parts') or []) or None
                c['sizeBytes'] = sb.dir_size(comic_dir)
    log(f'== [{site}] rewrite={stats["rewrite"]} rebuild={stats["rebuild"]} skip={stats["skip"]} ==')
    if changed and not dry:
        sb.write_index(site_dir, idx)
    return changed, stats


def migrate_all(repo, push=True, only_site=None, dry=False, budget_min=RUN_BUDGET_MIN):
    branches = branch_store.list_data_branches(repo)
    if only_site and only_site != 'auto':
        branches = [b for b in branches if b.split('/', 1)[1] == only_site]
    if not branches:
        log('没有匹配的 data 分支')
        return
    start = time.time()
    for branch in branches:
        if (time.time() - start) / 60 > budget_min:
            log(f'超出本次运行时间预算（{budget_min} 分钟），剩余站点下次运行继续（迁移幂等）')
            break
        site = branch.split('/', 1)[1]
        site_dir = os.path.join(repo, 'build', f'migrate_{site}')
        branch_store.open_branch_worktree(repo, branch, site_dir)
        try:
            changed, _stats = migrate_site(site_dir, site, dry=dry)
            if changed and not dry and push:
                branch_store.commit_and_push(site_dir, branch, f'data: [{site}] V2.0 zip-only 迁移（散图退役）')
        finally:
            branch_store.close_worktree(repo, site_dir)
    log('迁移完成（gh-pages 装配由 15 分钟巡检自动补上）')


def main():
    args = sys.argv[1:]
    dry = '--dry-run' in args
    push = '--no-push' not in args
    only = None
    for a in args:
        if a.startswith('--site='):
            only = a.split('=', 1)[1].strip()
    migrate_all(REPO_DIR, push=push, only_site=only, dry=dry)


if __name__ == '__main__':
    main()
