"""
GitHub Pages 移动端站点构建脚本

配合 .github/workflows/pages_crawler.yml 使用，整体流程：
    移动端网页（GitHub Pages）提交本子链接/ID
    → 触发 GitHub Actions
    → 本脚本下载本子并生成静态站点数据（图片/封面/manifest/zip/书架索引）
    → Actions 把 docs/ 推送回仓库
    → 移动端网页即可在线阅览或打包下载

环境变量输入：
    JM_PAGES_INPUT      本子链接或ID（必填），支持：
                        1483616 / JM1483616 / https://18comic.vip/album/1483616/...
    JM_PAGES_MODE       产物模式：both（默认，在线阅览+打包下载）/ read（仅在线阅览）/ zip（仅打包下载）
    JM_PAGES_KEEP       书库最多保留几本，超出自动清理最旧的（默认 10）
    JM_PAGES_RAW_DIR    原始下载目录（默认 ./build/pages_raw）
    JM_PAGES_DOCS_DIR   站点目录（默认 ./docs）
    JM_PAGES_ISSUE_BODY issue 触发时传入 issue 正文，可在正文中用 `mode: zip` / `keep: 5` 覆盖配置
    CLIENT_IMPL         客户端类型 api/html，下载失败时可切换
    JM_USERNAME         禁漫账号（可选，secrets）
    JM_PASSWORD         禁漫密码（可选，secrets）
    JM_PAGES_MOCK       置 1 时不联网，生成模拟图片用于本地调试站点构建流程
"""
import json
import os
import re
import shutil
import sys
import time
import zipfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OPTION_FILE = os.path.abspath(os.path.join(SCRIPT_DIR, '../assets/option/option_pages_build.yml'))


def env(name, default=''):
    value = os.getenv(name)
    if value is None or str(value).strip() == '':
        return default
    return str(value).strip()


def log(msg):
    print(f'[pages-build] {msg}', flush=True)


def parse_album_id(text: str) -> str:
    """从用户输入中解析本子ID，支持纯数字、JM前缀、album/photo链接、?id= 参数"""
    text = (text or '').strip()
    if not text:
        raise ValueError('输入为空，无法解析本子ID')

    if text.isdigit():
        return text

    m = re.fullmatch(r'(?i)jm\s*(\d+)', text)
    if m:
        return m.group(1)

    for pattern in (r'/album/(\d+)', r'/photo/(\d+)', r'[?&]id=(\d+)',
                    r'(?i)\[jm\]\s*(\d+)', r'(?i)\bjm\s*[:：]?\s*(\d+)\b',
                    r'\b(\d{3,})\b'):
        m = re.search(pattern, text)
        if m:
            return m.group(1)

    # 兜底交给 jmcomic 的解析器（处理更多链接形态）
    from jmcomic import JmcomicText
    return JmcomicText.parse_to_jm_id(text)


def parse_issue_body_overrides(body: str):
    """从 issue 正文中解析可选覆盖项：mode: read|zip|both / keep: 数字"""
    overrides = {}
    if not body:
        return overrides
    m = re.search(r'(?im)^\s*mode\s*[:：]\s*(read|zip|both)\s*$', body)
    if m:
        overrides['mode'] = m.group(1).lower()
    m = re.search(r'(?im)^\s*keep\s*[:：]\s*(\d+)\s*$', body)
    if m:
        overrides['keep'] = int(m.group(1))
    return overrides


def strip_html(text: str) -> str:
    return re.sub(r'<[^>]+>', '', text or '').strip()


def natural_key(name: str):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', name)]


# ============================================================
# 1. 下载本子（或 mock）
# ============================================================

def crawl_album(aid: str, impl: str):
    """联网下载本子，返回 (album_meta, raw_dir)"""
    from jmcomic import create_option

    os.environ.setdefault('JM_PAGES_RAW_DIR', default_raw_dir())

    option = create_option(OPTION_FILE)

    if impl in ('api', 'html'):
        option.client.impl = impl

    username, password = env('JM_USERNAME'), env('JM_PASSWORD')
    if username and password:
        option.plugins.setdefault('after_init', []).append({
            'plugin': 'login',
            'kwargs': {'username': username, 'password': password},
        })
        log('已启用登录插件')

    log(f'开始下载本子: JM{aid}')
    from jmcomic import download_album
    album, _dler = download_album(aid, option)

    raw_dir = option.dir_rule.decide_album_root_dir(album)
    log(f'下载完成: 《{album.name}》，原始目录: {raw_dir}')
    return album_to_meta(album), raw_dir


def album_to_meta(album) -> dict:
    """把 JmAlbumDetail 转成构建用的元数据 dict"""
    chapters = []
    for pid, pindex, pname in album.episode_list:
        chapters.append({
            'photo_id': str(pid),
            'dir_name': str(pindex),  # dir_rule 为 Bd_Pindex，章节目录名即 Pindex
            'title': str(pname).strip(),
        })
    return {
        'id': str(album.album_id),
        'title': album.name,
        'authors': list(album.authors),
        'tags': [str(t) for t in album.tags],
        'description': strip_html(getattr(album, 'description', '')),
        'chapters': chapters,
    }


def default_raw_dir():
    return os.path.abspath(env('JM_PAGES_RAW_DIR', './build/pages_raw'))


def mock_download(aid: str):
    """本地调试用：不联网，生成模拟章节图片"""
    from PIL import Image, ImageDraw

    raw_dir = default_raw_dir()
    meta = {
        'id': aid,
        'title': f'模拟本子 JM{aid}',
        'authors': ['mock-author'],
        'tags': ['mock', '测试'],
        'description': '本地 mock 数据，仅用于调试站点构建流程',
        'chapters': [
            {'photo_id': aid, 'dir_name': '1', 'title': '第1章'},
            {'photo_id': aid, 'dir_name': '2', 'title': '第2章'},
        ],
    }
    colors = [(94, 84, 255), (0, 180, 216), (255, 121, 63), (46, 196, 182)]
    for ch in meta['chapters']:
        ch_dir = os.path.join(raw_dir, ch['dir_name'])
        os.makedirs(ch_dir, exist_ok=True)
        for i in range(1, 7):
            w, h = 800, 1400 if i % 3 else 2400
            color = colors[(i + int(ch['dir_name'])) % len(colors)]
            img = Image.new('RGB', (w, h), color)
            d = ImageDraw.Draw(img)
            d.rectangle([20, 20, w - 20, h - 20], outline=(255, 255, 255), width=6)
            d.text((60, 60), f"JM{aid} ch{ch['dir_name']} p{i}", fill=(255, 255, 255))
            img.save(os.path.join(ch_dir, f'{i:05d}.jpg'), quality=88)
    log(f'mock 数据生成完毕: {raw_dir}')
    return meta, raw_dir


# ============================================================
# 2. 生成站点数据
# ============================================================

def list_chapter_images(raw_dir: str, dir_name: str):
    ch_dir = os.path.join(raw_dir, dir_name)
    if not os.path.isdir(ch_dir):
        return []
    files = [f for f in os.listdir(ch_dir) if os.path.isfile(os.path.join(ch_dir, f))]
    files.sort(key=natural_key)
    return [os.path.join(ch_dir, f) for f in files]


def build_cover(src_image: str, dest: str, max_width=480, quality=85):
    from PIL import Image
    with Image.open(src_image) as img:
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        if img.width > max_width:
            img = img.resize((max_width, int(img.height * max_width / img.width)), Image.LANCZOS)
        img.save(dest, 'JPEG', quality=quality, optimize=True)


def make_zip(raw_dir: str, meta: dict, zip_path: str):
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
        for seq, ch in enumerate(meta['chapters'], 1):
            title = re.sub(r'[\\/:*?"<>|]', '_', ch['title'])[:60].strip()
            folder = f'{seq:02d}_{title}' if title else f'{seq:02d}'
            for img_path in list_chapter_images(raw_dir, ch['dir_name']):
                zf.write(img_path, f'JM{meta["id"]}/{folder}/{os.path.basename(img_path)}')


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    return total


def build_site(meta: dict, raw_dir: str, docs_dir: str, mode: str) -> dict:
    """把原始下载产物整理成站点数据，返回书架索引条目"""
    aid = meta['id']
    has_read = mode in ('both', 'read')
    has_zip = mode in ('both', 'zip')

    comic_dir = os.path.join(docs_dir, 'comics', aid)
    if os.path.isdir(comic_dir):
        shutil.rmtree(comic_dir)
    os.makedirs(comic_dir)

    manifest = {
        'id': aid,
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
        zip_name = f'JM{aid}.zip'
        make_zip(raw_dir, meta, os.path.join(comic_dir, zip_name))
        manifest['hasZip'] = True
        manifest['zip'] = zip_name

    manifest['sizeBytes'] = dir_size(comic_dir)

    with open(os.path.join(comic_dir, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    entry = {
        'id': aid,
        'title': manifest['title'],
        'author': manifest['author'],
        'pageCount': manifest['pageCount'],
        'chapterCount': manifest['chapterCount'],
        'cover': f'comics/{aid}/cover.jpg',
        'hasReader': has_read,
        'hasZip': manifest['hasZip'],
        'updatedAt': manifest['updatedAt'],
        'sizeBytes': manifest['sizeBytes'],
    }
    update_index(docs_dir, entry)
    log(f'站点数据生成完毕: 《{meta["title"]}》 共 {manifest["pageCount"]} 页 / '
        f'{manifest["chapterCount"]} 章 / {manifest["sizeBytes"] / 1024 / 1024:.1f}MB')
    return entry


def index_path(docs_dir: str) -> str:
    return os.path.join(docs_dir, 'data', 'comics.json')


def update_index(docs_dir: str, entry: dict):
    os.makedirs(os.path.join(docs_dir, 'data'), exist_ok=True)
    data = {'updatedAt': '', 'comics': []}
    path = index_path(docs_dir)
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    comics = [c for c in data.get('comics', []) if c.get('id') != entry['id']]
    comics.insert(0, entry)
    data['comics'] = comics
    data['updatedAt'] = entry['updatedAt']

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def apply_retention(docs_dir: str, keep: int, protect_id: str):
    """书库最多保留 keep 本，删除最旧的本子目录（绝不删除本次构建的本子）"""
    path = index_path(docs_dir)
    if not os.path.isfile(path) or keep <= 0:
        return
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    comics = data.get('comics', [])
    if len(comics) <= keep:
        return

    kept, removed = comics[:keep], comics[keep:]
    removed = [c for c in removed if c.get('id') != protect_id]
    if len(kept) + len(removed) != len(comics):
        # protect_id 落在保留区之外，把它保回来
        for c in comics:
            if c.get('id') == protect_id and c not in kept:
                kept.append(c)
                break
    if not removed:
        return

    for c in removed:
        cid = c.get('id')
        if not cid or not re.fullmatch(r'\w+', str(cid)):
            continue
        target = os.path.join(docs_dir, 'comics', str(cid))
        if os.path.isdir(target):
            shutil.rmtree(target)
            log(f'清理旧本子: JM{cid}《{c.get("title", "")}》')

    data['comics'] = kept
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_summary(entry: dict):
    summary_file = env('GITHUB_STEP_SUMMARY')
    if not summary_file:
        return
    lines = [
        '## ✅ 本子爬取完成',
        '',
        f'- 车号：**JM{entry["id"]}**',
        f'- 标题：《{entry["title"]}》',
        f'- 作者：{entry["author"]}',
        f'- 规模：{entry["pageCount"]} 页 / {entry["chapterCount"]} 章 / {entry["sizeBytes"] / 1024 / 1024:.1f}MB',
        f'- 在线阅览：{"✅" if entry["hasReader"] else "❌"}　打包下载：{"✅" if entry["hasZip"] else "❌"}',
        '',
        '> 站点数据已提交到 docs/，GitHub Pages 部署完成后即可在移动端访问。',
    ]
    with open(summary_file, 'a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def main():
    raw_input = env('JM_PAGES_INPUT')
    mode = env('JM_PAGES_MODE', 'both').lower()
    if mode not in ('both', 'read', 'zip'):
        mode = 'both'
    keep = int(env('JM_PAGES_KEEP', '10') or '10')

    overrides = parse_issue_body_overrides(env('JM_PAGES_ISSUE_BODY'))
    mode = overrides.get('mode', mode)
    keep = overrides.get('keep', keep)

    docs_dir = os.path.abspath(env('JM_PAGES_DOCS_DIR', './docs'))
    impl = env('CLIENT_IMPL')

    aid = parse_album_id(raw_input)
    log(f'解析车号: JM{aid}，模式: {mode}，保留: {keep}')

    if env('JM_PAGES_MOCK') == '1':
        meta, raw_dir = mock_download(aid)
    else:
        meta, raw_dir = crawl_album(aid, impl)

    entry = build_site(meta, raw_dir, docs_dir, mode)
    apply_retention(docs_dir, keep, protect_id=aid)
    write_summary(entry)

    # 清理原始下载目录，避免被误提交
    if os.path.isdir(raw_dir) and os.path.abspath(raw_dir).startswith(os.path.abspath('./build')):
        shutil.rmtree(raw_dir, ignore_errors=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log(f'构建失败: {e}')
        raise
