"""
站点数据构建器 v3：zip 单一内容载体（V2.0 zip-only 架构）

与 v2 的关键差异（2026-11 V2.0 减重改造，仓库 6.2GB → ~3.5GB）：
- 分支不再保存散图（comics/<id>/images/**）：漫画内容只存在于 zip，
  gh-pages 装配只同步 manifest.json + cover.jpg（见 branch_store.sync_site_comics），
  仓库体积约为 v2 的一半（此前散图与 ZIP_STORED 打包互相重复）
- manifest 章节的 pages 从「线上散图路径」改为「zip 内条目路径」——
  阅读器以 manifest 为唯一钥匙，从本地 zip（自动下载到 OPFS/自定义文件夹）
  按条目取图，JSZip 直读（STORED 未压缩，零解压成本）
- 超过 90MB 的本子自动分卷：<id>.part01.zip / .part02.zip…，
  manifest.parts 记录每卷全局页区间（firstPage/lastPage），阅读器懒加载当前卷
- 续抓合并：旧章节按文件夹从旧 zip 流式拷贝，不再依赖散图目录；
  迁移前的旧散图目录仍可作为来源兜底（迁移完成后消失）
- zip 内部布局沿用 v2（PC 手动解压文件夹名可读）：
    JM<aid>/<序号>_<章名>/NNN.<原后缀>（非数字 ID 根目录为 <aid>）
- 产物模式（mode）退役：read/zip/both 一律按「zip + 元数据」构建（保留入参兼容旧调用）

输出（data/<site> 分支）：
    comics/<id>/<id>.zip 或 <id>.partNN.zip
    comics/<id>/manifest.json
    comics/<id>/cover.jpg        (首页缩到 480 宽)
    index.json                    (该站书架索引 upsert，最新在前)
"""
import json
import os
import re
import shutil
import time
import zipfile

ZIP_PART_DEFAULT = 90 * 1024 * 1024  # 单卷上限：GitHub 100MB 单文件硬限制留余量


def log(msg):
    print(f'[site-builder] {msg}', flush=True)


def zip_part_max():
    """分卷上限（环境变量可覆盖，便于本地小体积测试；下限防误配）"""
    try:
        v = int(os.getenv('JM_ZIP_PART_MAX', '') or 0)
    except ValueError:
        v = 0
    return v if v >= 64 * 1024 else ZIP_PART_DEFAULT


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


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
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


# ============================================================
# zip 命名与布局（与 v2 产物保持一致，旧包无需重写）
# ============================================================

def zip_name_for(aid):
    return f'JM{aid}.zip' if str(aid).isdigit() else f'{aid}.zip'


def zip_root_for(aid):
    return f'JM{aid}' if str(aid).isdigit() else str(aid)


def folder_name(seq, title):
    t = re.sub(r'[\\/:*?"<>|]', '_', title or '')[:60].strip()
    return f'{seq:02d}_{t}' if t else f'{seq:02d}'


def zip_base_for(aid):
    """zip/分卷文件名的公共前缀（去 .zip）：JM123 / tg-xxx"""
    return zip_name_for(aid)[:-len('.zip')]


def enumerate_zip_folders(zf):
    """枚举 zip 内的章节文件夹（根目录下一级，自然序）"""
    folders = []
    for name in zf.namelist():
        parts = name.split('/')
        if len(parts) >= 3 and parts[1] and parts[1] not in folders:
            folders.append(parts[1])
    folders.sort(key=natural_key)
    return folders


def old_zip_folder_of(ch, idx, old_folders):
    """定位旧章节在旧 zip 里的文件夹名。

    优先用旧 manifest pages 的路径推断（v3 格式）；v1/v2 旧格式的
    pages 指向散图（images/...），回退为「按章节顺序 ↔ 文件夹自然序」，
    与 v2 打包时的顺序一致（zip 每次构建都按合并顺序重排）。
    """
    pages = ch.get('pages') or []
    if pages:
        p = str(pages[0])
        if not p.startswith('images/'):
            folder = p.rsplit('/', 1)[0]
            folder = folder.split('/', 1)[1] if '/' in folder else folder
            if folder:
                return folder
    if idx < len(old_folders):
        return old_folders[idx]
    raise RuntimeError(f'章节 {chapter_key(ch)} 在旧 zip 中找不到对应文件夹，请强制重爬整本')


def loose_files(comic_dir, key):
    """迁移前兜底：散图目录仍在本子上时直接取文件（v2 布局 images/<photoId>/）"""
    d = os.path.join(comic_dir, 'images', str(key))
    if not os.path.isdir(d):
        return None
    files = sorted((f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))), key=natural_key)
    if not files:
        return None
    return [(f, ('file', os.path.join(d, f))) for f in files]


# ============================================================
# zip / 分卷写出
# ============================================================

def _write_entry(zf, arc, src):
    if src[0] == 'file':
        zf.write(src[1], arc)
    else:  # ('zip', old_zf, entry_name) —— 从旧包流式拷贝，常量内存
        old_zf, entry_name = src[1], src[2]
        info = old_zf.getinfo(entry_name)
        zi = zipfile.ZipInfo(arc, date_time=info.date_time)
        zi.compress_type = zipfile.ZIP_STORED
        zi.external_attr = info.external_attr
        with zf.open(zi, 'w') as w, old_zf.open(entry_name) as r:
            shutil.copyfileobj(r, w, 1024 * 1024)


def _entry_size(src):
    if src[0] == 'file':
        return os.path.getsize(src[1])
    return src[1].getinfo(src[2]).file_size


def cleanup_book_zips(comic_dir, aid, keep=()):
    """移除本子的旧 zip/分卷/构建残留 tmp（不碰 manifest/cover/images）"""
    base = re.escape(zip_base_for(aid))
    pat = re.compile(rf'^{base}(?:\.part\d+)?\.zip(?:\.tmp)?$')
    for f in os.listdir(comic_dir):
        if pat.match(f) and f not in keep:
            try:
                os.remove(os.path.join(comic_dir, f))
            except OSError:
                pass


def write_book_zip(comic_dir, aid, plan, zip_root=None):
    """按 plan 写出整本 zip（超限自动分卷）。

    plan: [{'folder': '01_第1章', 'entries': [(文件名, src), ...]}, ...]
    src = ('file', 绝对路径) 或 ('zip', 旧ZipFile, 旧条目名)
    返回 (files, pages_by_chapter)：
      files          [{'file','bytes','firstPage','lastPage'}]  firstPage/lastPage 为全局页序（1-based）
      pages_by_chapter  [ [zip条目路径, ...], ... ]  与 plan 逐项对齐
    """
    part_max = zip_part_max()
    zip_root = zip_root or zip_root_for(aid)
    base = zip_base_for(aid)
    single_name = zip_name_for(aid)

    # 清掉上次构建中断的 tmp 残留
    for f in os.listdir(comic_dir):
        if f.endswith('.zip.tmp'):
            try:
                os.remove(os.path.join(comic_dir, f))
            except OSError:
                pass

    files = []
    pages_by_chapter = []
    part_no = 1
    written = 0
    gp = 0            # 已写出的全局页数
    first_page = 1
    zf = None
    tmp_path = None

    def open_part():
        nonlocal zf, tmp_path, written, first_page
        tmp_path = os.path.join(comic_dir, f'{base}.part{part_no:02d}.zip.tmp')
        zf = zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_STORED)
        written = 0
        first_page = gp + 1

    def close_part():
        nonlocal zf, tmp_path, written
        if zf is not None:
            zf.close()
        if tmp_path and written > 0:
            files.append({'file': f'{base}.part{part_no:02d}.zip',
                          'bytes': written, 'firstPage': first_page, 'lastPage': gp,
                          'tmp': tmp_path})
        zf = None
        tmp_path = None

    open_part()
    try:
        for item in plan:
            pages = []
            for name, src in item['entries']:
                arc = f'{zip_root}/{item["folder"]}/{name}'
                size = _entry_size(src)
                if written > 0 and written + size > part_max:
                    close_part()
                    part_no += 1
                    open_part()
                _write_entry(zf, arc, src)
                written += size
                gp += 1
                pages.append(arc)
            pages_by_chapter.append(pages)
        close_part()
    except BaseException:
        close_part()
        for f in os.listdir(comic_dir):
            if f.endswith('.zip.tmp'):
                try:
                    os.remove(os.path.join(comic_dir, f))
                except OSError:
                    pass
        raise

    if not files:
        raise RuntimeError('没有任何图片写入 zip')

    # 关闭来源 zip 句柄再落名（Windows 不允许替换正被占用的文件）
    src_zfs = []
    for item in plan:
        for _name, src in item['entries']:
            if src[0] == 'zip' and src[1] not in src_zfs:
                src_zfs.append(src[1])
    for z in src_zfs:
        try:
            z.close()
        except Exception:
            pass

    # 落名：单卷 → <id>.zip；多卷保留 part 命名；随后清掉旧包
    if len(files) == 1:
        os.replace(files[0]['tmp'], os.path.join(comic_dir, single_name))
        files[0]['file'] = single_name
        files[0]['tmp'] = os.path.join(comic_dir, single_name)
    else:
        for f in files:
            os.replace(f['tmp'], os.path.join(comic_dir, f['file']))
            f['tmp'] = os.path.join(comic_dir, f['file'])
    cleanup_book_zips(comic_dir, aid, keep={f['file'] for f in files})

    # 校验：重开各卷数条目，必须与页区间一致（防写出损坏包）
    total_expect = 0
    for f in files:
        expect = f['lastPage'] - f['firstPage'] + 1
        total_expect += expect
        with zipfile.ZipFile(os.path.join(comic_dir, f['file'])) as z:
            n = len([x for x in z.namelist() if not x.endswith('/')])
        if n != expect:
            raise RuntimeError(f'分卷 {f["file"]} 校验失败：写出 {n} 页，应为 {expect}')
    if total_expect != gp:
        raise RuntimeError(f'整包校验失败：{total_expect} != {gp}')
    return files, pages_by_chapter


# ============================================================
# 构建
# ============================================================

def build_site(meta, raw_dir, site_dir, mode='both'):
    """把本批次下载产物合并进站点数据（zip 单一载体），返回书架索引条目。

    mode 入参保留仅为兼容旧调用，v3 起一律构建 zip + 元数据。
    """
    aid = meta['id']
    site = meta.get('site') or 'jmcomic'
    if mode not in ('both', ''):
        log(f'产物模式 {mode} 已退役（V2.0 zip-only），按 zip + 元数据构建')

    comic_dir = os.path.join(site_dir, 'comics', aid)
    os.makedirs(comic_dir, exist_ok=True)
    old = read_manifest(comic_dir)

    # 旧 zip：续抓合并时的旧章节来源
    old_zip_name = (old or {}).get('zip')
    old_zip_path = os.path.join(comic_dir, old_zip_name) if old_zip_name else None
    if old_zip_path and not os.path.isfile(old_zip_path):
        old_zip_path = None
    old_zf = zipfile.ZipFile(old_zip_path) if old_zip_path else None
    old_folders = enumerate_zip_folders(old_zf) if old_zf else []
    try:
        # 1. 本批次章节（raw_dir）
        new_map = {}
        first_new_image = None
        for ch in meta['chapters']:
            images = list_chapter_images(raw_dir, ch['dir_name'])
            if not images:
                log(f'警告: 章节 {ch["dir_name"]} 没有图片，跳过')
                continue
            if first_new_image is None:
                first_new_image = images[0]
            key = str(ch['photo_id'])
            new_map[key] = {
                'title': ch['title'],
                'order': ch.get('order', 0),
                'files': [(f'{i:03d}{os.path.splitext(p)[1].lower() or ".jpg"}', p)
                          for i, p in enumerate(images, 1)],
            }

        if not new_map and not old:
            raise RuntimeError('没有下载到任何图片，无法生成站点数据')

        # 2. 合并章节（photoId 键，order 排序，重排 display index）
        chapters = list((old or {}).get('chapters', []) or [])
        for key, info in new_map.items():
            chapters = [c for c in chapters if chapter_key(c) != key]
            chapters.append({'photoId': key, 'title': info['title'], 'order': info['order']})
        chapters.sort(key=lambda c: (float(c.get('order') or c.get('index') or 0), chapter_key(c)))
        for i, c in enumerate(chapters, 1):
            c['index'] = i

        # 3. 组装写包计划：每章确定文件来源（raw > 旧 zip > 旧散图目录）
        plan = []
        for i, ch in enumerate(chapters):
            key = chapter_key(ch)
            entries = None
            if key in new_map:
                entries = [(name, ('file', path)) for name, path in new_map[key]['files']]
            elif old_zf is not None:
                folder_old = old_zip_folder_of(ch, i - 1, old_folders)
                arc_prefix = f'{zip_root_for(aid)}/{folder_old}/'
                names = [n for n in old_zf.namelist()
                         if n.startswith(arc_prefix) and not n.endswith('/')]
                names.sort(key=natural_key)
                entries = [(n.rsplit('/', 1)[1], ('zip', old_zf, n)) for n in names]
            else:
                entries = loose_files(comic_dir, key)
            if not entries:
                raise RuntimeError(f'章节 {key}《{ch.get("title", "")}》无可用来源'
                                   '（旧包缺失且无散图），请勾选强制重爬整本')
            plan.append({'folder': folder_name(i + 1, ch.get('title', '')), 'entries': entries})

        # 4. 封面来源（须在写包前确定——写包会关闭旧 zip 句柄）：
        #    保留旧的；没有则本批次首页 → 旧散图 → 旧 zip 首图（解到临时文件）
        cover_path = os.path.join(comic_dir, 'cover.jpg')
        cover_src = None
        cover_is_tmp = False
        if not os.path.isfile(cover_path):
            cover_src = first_new_image
            if cover_src is None:
                cover_src = _first_loose_image(comic_dir, chapters)
            if cover_src is None and old_zf is not None:
                cover_src = _extract_first_zip_image(old_zf)
                cover_is_tmp = cover_src is not None

        # 5. 写 zip / 分卷
        files, pages_by_chapter = write_book_zip(comic_dir, aid, plan)

        # 生成封面
        if cover_src and not os.path.isfile(cover_path):
            try:
                build_cover(cover_src, cover_path)
            finally:
                if cover_is_tmp:
                    try:
                        os.remove(cover_src)
                    except OSError:
                        pass

        # 6. manifest：pages = zip 条目路径（阅读器唯一钥匙）
        out_chapters = []
        for i, ch in enumerate(chapters):
            pages = pages_by_chapter[i]
            out_chapters.append({
                'photoId': chapter_key(ch),
                'title': ch.get('title', ''),
                'order': ch.get('order', 0),
                'index': i + 1,
                'pageCount': len(pages),
                'pages': pages,
            })
        page_count = sum(c['pageCount'] for c in out_chapters)
        total_chapters = max(int(meta.get('totalChapters') or 0),
                             int((old or {}).get('totalChapters') or 0),
                             len(out_chapters))
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
            'hasReader': True,
            'hasZip': True,
            'zip': files[0]['file'] if len(files) == 1 else None,
            'parts': files if len(files) > 1 else None,
            'zipNote': '',
            'pageCount': page_count,
            'chapterCount': len(out_chapters),
            'chapters': out_chapters,
        }
        with open(os.path.join(comic_dir, 'manifest.json'), 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        size = dir_size(comic_dir)
        entry = {
            'id': aid,
            'site': site,
            'title': manifest['title'],
            'author': manifest['author'],
            'pageCount': page_count,
            'chapterCount': len(out_chapters),
            'totalChapters': total_chapters,
            'sourceUrl': manifest['sourceUrl'],
            'cover': f'comics/{aid}/cover.jpg',
            'hasReader': True,
            'hasZip': True,
            'zipName': manifest['zip'],
            'zipParts': len(files) if len(files) > 1 else None,
            'updatedAt': manifest['updatedAt'],
            'sizeBytes': size,
        }
        upsert_index(site_dir, site, entry)
        log(f'站点数据生成完毕: 《{manifest["title"]}》 {len(files)} 卷 / '
            f'{page_count} 页 / {size / 1024 / 1024:.1f}MB')
        return entry
    finally:
        if old_zf is not None:
            old_zf.close()


def _first_loose_image(comic_dir, chapters):
    for ch in chapters:
        files = loose_files(comic_dir, chapter_key(ch))
        if files:
            return files[0][1][1]
    return None


def _extract_first_zip_image(zf):
    import tempfile
    names = [n for n in zf.namelist() if not n.endswith('/')]
    names.sort(key=natural_key)
    for n in names:
        if re.search(r'\.(jpe?g|png|webp|gif|avif|bmp)$', n, re.I):
            dest = os.path.join(tempfile.gettempdir(), os.path.basename(n) + '.cover_tmp')
            with zf.open(n) as r, open(dest, 'wb') as w:
                shutil.copyfileobj(r, w, 1024 * 1024)
            return dest
    return None


# ============================================================
# 站点索引（不变）
# ============================================================

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
