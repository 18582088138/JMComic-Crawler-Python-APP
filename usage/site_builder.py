"""
站点数据构建器 v2：多话作品按「一本」合并管理

与 v1 的关键差异：
- 不再整本重建：同一作品的多次批次爬取，章节按 photo_id 合并
  （图片存 comics/<id>/images/<photo_id>/，跨爬取路径稳定）
- manifest 记录 totalChapters（作品总章数）与 sourceUrl（源站详情页）
- 旧版 manifest（images/<序号>/ 布局）的章节原样保留，新旧共存

输出：
    <site_dir>/comics/<id>/images/<photo_id>/NNN.<原后缀>
    <site_dir>/comics/<id>/cover.jpg        (首页缩到 480 宽)
    <site_dir>/comics/<id>/manifest.json
    <site_dir>/comics/<id>/<id>.zip         (ZIP_STORED，含全部已合并章节)
    <site_dir>/index.json                    (该站书架索引 upsert，最新在前)
"""
import json
import os
import re
import shutil
import time
import zipfile


def log(msg):
    print(f'[site-builder] {msg}', flush=True)


def natural_key(name):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', name)]


def list_chapter_images(raw_dir, dir_name):
    ch_dir = os.path.join(raw_dir, dir_name)
    if not os.path.isdir(ch_dir):
        return []
    files = [f for f in os.listdir(ch_dir) if os.path.isfile(os.path.join(ch_dir, f))]
    files.sort(key=natural_key)
    return [os.path.join(ch_dir, f) for f in files]


def build_cover(src_image, dest, max_width=480, quality=85):
    from PIL import Image
    with Image.open(src_image) as img:
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        if img.width > max_width:
            img = img.resize((max_width, int(img.height * max_width / img.width)), Image.LANCZOS)
        img.save(dest, 'JPEG', quality=quality, optimize=True)


ZIP_MAX_BYTES = 95 * 1024 * 1024  # GitHub 单文件 100MB 硬限制，留 5MB 余量


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    return total


def read_manifest(comic_dir):
    path = os.path.join(comic_dir, 'manifest.json')
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return None


def chapter_key(ch):
    return str(ch.get('photoId') or ch.get('photo_id') or ch.get('index'))


def merge_chapters(old_chapters, new_chapters):
    """按 photoId 合并章节，order 排序，重排 display index"""
    merged = {}
    for c in old_chapters or []:
        merged[chapter_key(c)] = c
    for c in new_chapters:
        merged[chapter_key(c)] = c
    chapters = list(merged.values())
    # 旧版 manifest 章节没有 order 字段，回退用其旧 index（本身就是正确顺序）
    chapters.sort(key=lambda c: (float(c.get('order') or c.get('index') or 0), chapter_key(c)))
    for i, c in enumerate(chapters, 1):
        c['index'] = i
    return chapters


def chapter_image_dir(comic_dir, key):
    return os.path.join(comic_dir, 'images', str(key))


def make_zip_merged(comic_dir, raw_dir, meta_batch, chapters, zip_path, zip_root):
    """合包：已合并章节的图片优先取站点目录，本批次章节可回退到 raw_dir"""
    batch_map = {str(c['photo_id']): c['dir_name'] for c in meta_batch['chapters']}
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
        for seq, ch in enumerate(chapters, 1):
            title = re.sub(r'[\\/:*?"<>|]', '_', ch.get('title', ''))[:60].strip()
            folder = f'{seq:02d}_{title}' if title else f'{seq:02d}'
            key = chapter_key(ch)
            src_dir = chapter_image_dir(comic_dir, key)
            if not os.path.isdir(src_dir) and key in batch_map:
                src_dir = os.path.join(raw_dir, batch_map[key])
            if not os.path.isdir(src_dir):
                continue
            files = [f for f in os.listdir(src_dir) if os.path.isfile(os.path.join(src_dir, f))]
            files.sort(key=natural_key)
            for f in files:
                zf.write(os.path.join(src_dir, f), f'{zip_root}/{folder}/{f}')


def build_site(meta, raw_dir, site_dir, mode):
    """把本批次下载产物合并进站点数据，返回书架索引条目"""
    aid = meta['id']
    site = meta.get('site') or 'jmcomic'
    has_read = mode in ('both', 'read')
    has_zip = mode in ('both', 'zip')

    comic_dir = os.path.join(site_dir, 'comics', aid)
    os.makedirs(comic_dir, exist_ok=True)  # v2: 不再整本删除，按章节合并
    old = read_manifest(comic_dir)

    # 1. 本批次章节落盘
    new_chapters = []
    first_new_image = None
    for ch in meta['chapters']:
        images = list_chapter_images(raw_dir, ch['dir_name'])
        if not images:
            log(f'警告: 章节 {ch["dir_name"]} 没有图片，跳过')
            continue
        if first_new_image is None:
            first_new_image = images[0]
        key = str(ch['photo_id'])
        page_entries = []
        if has_read:
            dest_dir = chapter_image_dir(comic_dir, key)
            if os.path.isdir(dest_dir):
                shutil.rmtree(dest_dir)
            os.makedirs(dest_dir)
            for i, src in enumerate(images, 1):
                suffix = os.path.splitext(src)[1].lower() or '.jpg'
                dest_name = f'{i:03d}{suffix}'
                shutil.copy2(src, os.path.join(dest_dir, dest_name))
                page_entries.append(f'images/{key}/{dest_name}')
        new_chapters.append({
            'photoId': key,
            'title': ch['title'],
            'order': ch.get('order', 0),
            'pageCount': len(images),
            'pages': page_entries,
        })

    if not new_chapters and not old:
        raise RuntimeError('没有下载到任何图片，无法生成站点数据')

    # 2. 合并（旧章节原样保留，包括 v1 images/<序号>/ 布局）
    old_chapters = (old or {}).get('chapters', [])
    chapters = merge_chapters(old_chapters, new_chapters)
    page_count = sum(c.get('pageCount', 0) for c in chapters)
    total_chapters = max(int(meta.get('totalChapters') or 0),
                         int((old or {}).get('totalChapters') or 0),
                         len(chapters))

    # 3. 封面：保留旧的；没有则用本批次首页
    cover_path = os.path.join(comic_dir, 'cover.jpg')
    if not os.path.isfile(cover_path):
        src = first_new_image
        if src is None:
            # 从旧章节里找第一张图
            for c in chapters:
                if c.get('pages'):
                    cand = os.path.join(comic_dir, c['pages'][0])
                    if os.path.isfile(cand):
                        src = cand
                        break
        if src:
            build_cover(src, cover_path)

    # 4. ZIP 合包（含全部已合并章节）
    # GitHub 单文件 100MB 硬限制：超限的 zip 会让整个 git push 被拒、连累管线，
    # 护栏：超限则放弃打包（在线阅读不受影响），manifest 标记 zipTooLarge
    zip_name = f'JM{aid}.zip' if aid.isdigit() else f'{aid}.zip'
    zip_built = False
    zip_note = ''
    if has_zip:
        zip_path = os.path.join(comic_dir, zip_name)
        make_zip_merged(comic_dir, raw_dir, meta, chapters,
                        zip_path, f'JM{aid}' if aid.isdigit() else aid)
        zsize = os.path.getsize(zip_path)
        if zsize > ZIP_MAX_BYTES:
            os.remove(zip_path)
            zip_note = f'zip 体积 {zsize / 1048576:.0f}MB 超出 GitHub 100MB 单文件限制，未打包'
            log(f'⚠️ {zip_note}')
        else:
            zip_built = True

    manifest = {
        'id': aid,
        'site': site,
        'title': meta['title'] or (old or {}).get('title'),
        'author': (meta['authors'][0] if meta['authors'] else None) or (old or {}).get('author') or '未知',
        'authors': meta['authors'] or (old or {}).get('authors') or [],
        'tags': meta['tags'] or (old or {}).get('tags') or [],
        'description': meta['description'] or (old or {}).get('description') or '',
        'sourceUrl': meta.get('sourceUrl') or (old or {}).get('sourceUrl') or '',
        'totalChapters': total_chapters,
        'updatedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'hasReader': has_read or bool(old and old.get('hasReader')),
        'hasZip': zip_built or bool(old and old.get('hasZip') and os.path.isfile(os.path.join(comic_dir, zip_name))),
        'zip': zip_name if (zip_built or (old and old.get('zip'))) else None,
        'zipNote': zip_note or (old or {}).get('zipNote', ''),
        'pageCount': page_count,
        'chapterCount': len(chapters),
        'chapters': chapters,
    }
    # zip-only 模式下的新章节 pages 为空（图片未落站），hasReader 以实际有 pages 的章节为准
    if not any(c.get('pages') for c in chapters):
        manifest['hasReader'] = False

    with open(os.path.join(comic_dir, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    size = dir_size(comic_dir)
    entry = {
        'id': aid,
        'site': site,
        'title': manifest['title'],
        'author': manifest['author'],
        'pageCount': page_count,
        'chapterCount': len(chapters),
        'totalChapters': total_chapters,
        'sourceUrl': manifest['sourceUrl'],
        'cover': f'comics/{aid}/cover.jpg',
        'hasReader': manifest['hasReader'],
        'hasZip': manifest['hasZip'],
        'zipName': manifest['zip'] if manifest['hasZip'] else None,
        'updatedAt': manifest['updatedAt'],
        'sizeBytes': size,
    }
    upsert_index(site_dir, site, entry)
    log(f'站点数据生成完毕: 《{manifest["title"]}》 已上架 {len(chapters)}/{total_chapters} 章 / '
        f'{page_count} 页 / {size / 1024 / 1024:.1f}MB')
    return entry


def index_path(site_dir):
    return os.path.join(site_dir, 'index.json')


def read_index(site_dir):
    path = index_path(site_dir)
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {'site': None, 'updatedAt': '', 'comics': []}


def write_index(site_dir, data):
    os.makedirs(site_dir, exist_ok=True)
    with open(index_path(site_dir), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def upsert_index(site_dir, site, entry):
    data = read_index(site_dir)
    data['site'] = site
    comics = [c for c in data.get('comics', []) if c.get('id') != entry['id']]
    comics.insert(0, entry)
    data['comics'] = comics
    data['updatedAt'] = entry['updatedAt']
    write_index(site_dir, data)


def apply_retention(site_dir, keep, protect_id):
    """每站最多保留 keep 本，删除最旧的本子目录（绝不删除本次构建的本子）"""
    data = read_index(site_dir)
    comics = data.get('comics', [])
    if keep <= 0 or len(comics) <= keep:
        return

    kept, removed = comics[:keep], comics[keep:]
    removed = [c for c in removed if c.get('id') != protect_id]
    if len(kept) + len(removed) != len(comics):
        for c in comics:
            if c.get('id') == protect_id and c not in kept:
                kept.append(c)
                break
    if not removed:
        return

    for c in removed:
        cid = c.get('id')
        if not cid or not re.fullmatch(r'\w[\w-]*', str(cid)):
            continue
        target = os.path.join(site_dir, 'comics', str(cid))
        if os.path.isdir(target):
            shutil.rmtree(target)
            log(f'清理旧本子: {cid}《{c.get("title", "")}》')

    data['comics'] = kept
    write_index(site_dir, data)
