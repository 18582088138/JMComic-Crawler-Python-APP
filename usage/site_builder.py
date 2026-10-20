"""
站点数据构建器（与具体爬虫解耦）

输入任一 crawler 产出的 (meta, raw_dir)，输出站点数据：
    <site_dir>/comics/<id>/images/<章>/NNN.<原后缀>
    <site_dir>/comics/<id>/cover.jpg        (首页缩到 480 宽)
    <site_dir>/comics/<id>/manifest.json
    <site_dir>/comics/<id>/<id>.zip         (ZIP_STORED 快速打包)
    <site_dir>/index.json                    (该站书架索引 upsert，最新在前)

site_dir 通常是对应 data/<site> 分支的 worktree；本地 mock 时也可以是任意目录。
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


def make_zip(raw_dir, meta, zip_path):
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
        for seq, ch in enumerate(meta['chapters'], 1):
            title = re.sub(r'[\\/:*?"<>|]', '_', ch['title'])[:60].strip()
            folder = f'{seq:02d}_{title}' if title else f'{seq:02d}'
            for img_path in list_chapter_images(raw_dir, ch['dir_name']):
                zf.write(img_path, f'{meta["id"]}/{folder}/{os.path.basename(img_path)}')


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    return total


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


def build_site(meta, raw_dir, site_dir, mode):
    """把原始下载产物整理成站点数据，返回书架索引条目"""
    aid = meta['id']
    site = meta.get('site') or 'jmcomic'
    has_read = mode in ('both', 'read')
    has_zip = mode in ('both', 'zip')

    comic_dir = os.path.join(site_dir, 'comics', aid)
    if os.path.isdir(comic_dir):
        shutil.rmtree(comic_dir)
    os.makedirs(comic_dir)

    manifest = {
        'id': aid,
        'site': site,
        'title': meta['title'],
        'author': meta['authors'][0] if meta['authors'] else '未知',
        'authors': meta['authors'],
        'tags': meta['tags'],
        'description': meta['description'],
        'updatedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'hasReader': has_read,
        'hasZip': False,
        'zip': None,
        'pageCount': 0,
        'chapterCount': len(meta['chapters']),
        'chapters': [],
    }

    first_image = None
    for seq, ch in enumerate(meta['chapters'], 1):
        images = list_chapter_images(raw_dir, ch['dir_name'])
        if not images:
            log(f'警告: 章节 {ch["dir_name"]} 没有图片，跳过')
            continue
        if first_image is None:
            first_image = images[0]

        page_entries = []
        if has_read:
            dest_dir = os.path.join(comic_dir, 'images', str(seq))
            os.makedirs(dest_dir)
            for i, src in enumerate(images, 1):
                suffix = os.path.splitext(src)[1].lower() or '.jpg'
                dest_name = f'{i:03d}{suffix}'
                shutil.copy2(src, os.path.join(dest_dir, dest_name))
                page_entries.append(f'images/{seq}/{dest_name}')

        manifest['chapters'].append({
            'index': seq,
            'photoId': ch['photo_id'],
            'title': ch['title'],
            'pageCount': len(images),
            'pages': page_entries,
        })
        manifest['pageCount'] += len(images)

    if first_image is None:
        raise RuntimeError('没有下载到任何图片，无法生成站点数据')

    build_cover(first_image, os.path.join(comic_dir, 'cover.jpg'))

    if has_zip:
        zip_name = f'JM{aid}.zip' if aid.isdigit() else f'{aid}.zip'
        make_zip(raw_dir, meta, os.path.join(comic_dir, zip_name))
        manifest['hasZip'] = True
        manifest['zip'] = zip_name

    manifest['sizeBytes'] = dir_size(comic_dir)

    with open(os.path.join(comic_dir, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    entry = {
        'id': aid,
        'site': site,
        'title': manifest['title'],
        'author': manifest['author'],
        'pageCount': manifest['pageCount'],
        'chapterCount': manifest['chapterCount'],
        'cover': f'comics/{aid}/cover.jpg',
        'hasReader': has_read,
        'hasZip': manifest['hasZip'],
        'zipName': manifest['zip'],
        'updatedAt': manifest['updatedAt'],
        'sizeBytes': manifest['sizeBytes'],
    }
    upsert_index(site_dir, site, entry)
    log(f'站点数据生成完毕: 《{meta["title"]}》 共 {manifest["pageCount"]} 页 / '
        f'{manifest["chapterCount"]} 章 / {manifest["sizeBytes"] / 1024 / 1024:.1f}MB')
    return entry


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
