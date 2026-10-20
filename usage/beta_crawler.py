"""
内测版多平台漫画爬取器

与 JMComic 主流程解耦：各平台实现统一的 (meta, raw_dir) 契约，
之后复用 workflow_pages_build.build_site 生成站点数据。

契约：
    meta = {
        'id': 'md-xxx',          # 书架唯一ID（带平台前缀，避免与JM车号冲突）
        'title': str,
        'authors': [str],
        'tags': [str],
        'description': str,
        'chapters': [            # 按阅读顺序排列
            {'photo_id': str, 'dir_name': '1', 'title': str},
        ],
    }
    raw_dir/<dir_name>/ 下为该章图片文件（文件名需可自然排序）

支持平台：
    mangadex   MangaDex（官方API，最稳定）
    copymanga  拷贝漫画（第三方API）
    nhentai    nhentai（网页解析，Cloudflare 用 curl_cffi 模拟浏览器）
"""
import json
import os
import re
import time

SOURCES = {
    'mangadex': {
        'name': 'MangaDex',
        'hint': '漫画链接（https://mangadex.org/title/<id>）或章节链接，也支持直接填 id',
    },
    'copymanga': {
        'name': '拷贝漫画',
        'hint': '漫画链接（https://www.copymanga.tv/comic/<path_word>）或 path_word',
    },
    'nhentai': {
        'name': 'nhentai',
        'hint': '画廊号或链接（https://nhentai.net/g/<id>）',
    },
}

DEFAULT_LIMIT = 3   # 默认爬最近几章
MAX_LIMIT = 10      # 内测期单次章节上限，防止把书库撑爆


def log(msg):
    print(f'[beta-crawler] {msg}', flush=True)


def mkdir(p):
    os.makedirs(p, exist_ok=True)
    return p


def natural_key(name):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', name)]


def save_image(session, url, path, headers=None, referer=None):
    hdrs = dict(headers or {})
    if referer:
        hdrs['Referer'] = referer
    resp = session.get(url, headers=hdrs, timeout=30)
    resp.raise_for_status()
    with open(path, 'wb') as f:
        f.write(resp.content)
    return os.path.getsize(path)


def download_chapter_images(session, image_urls, dest_dir, headers=None, referer=None, delay=0.2):
    """把一章的图片按 00001.<ext> 命名下载到 dest_dir"""
    mkdir(dest_dir)
    total = 0
    for i, url in enumerate(image_urls, 1):
        ext = os.path.splitext(url.split('?')[0])[1].lower() or '.jpg'
        if len(ext) > 5:
            ext = '.jpg'
        path = os.path.join(dest_dir, f'{i:05d}{ext}')
        if not os.path.exists(path):
            total += save_image(session, url, path, headers, referer)
            time.sleep(delay)
    return total


def make_meta(source, sid, title, authors, tags, description, chapters):
    return {
        'id': f'{source}-{sid}',
        'title': title,
        'authors': authors or ['未知'],
        'tags': tags or [],
        'description': description or '',
        'chapters': [
            {'photo_id': str(c.get('photo_id', i + 1)), 'dir_name': str(i + 1), 'title': c['title']}
            for i, c in enumerate(chapters)
        ],
    }


# ============================================================
# MangaDex（官方 API：https://api.mangadex.org/docs/）
# ============================================================

MD_API = 'https://api.mangadex.org'
MD_UA = {'User-Agent': 'JMComicCloudShelf/1.0 (personal; github-actions)'}


def md_parse_target(text):
    text = text.strip()
    m = re.search(r'mangadex\.org/(?:title|chapter)/([0-9a-f-]{36})', text)
    kind = 'chapter' if '/chapter/' in text else 'title'
    if m:
        return kind, m.group(1)
    if re.fullmatch(r'[0-9a-f-]{36}', text):
        return 'title', text
    raise ValueError(f'无法识别 MangaDex 目标: {text}')


def md_get(session, path, params=None):
    time.sleep(0.3)  # 遵守 5 req/s 限速
    resp = session.get(MD_API + path, params=params, headers=MD_UA, timeout=30)
    resp.raise_for_status()
    return resp.json()


def crawl_mangadex(session, target, work_dir, limit):
    kind, tid = md_parse_target(target)
    chapters_wanted = []  # (chapter_id, 章节号, 标题) 按阅读顺序

    if kind == 'chapter':
        ch = md_get(session, f'/chapter/{tid}')['data']
        manga_id = next(r['id'] for r in ch['relationships'] if r['type'] == 'manga')
        a = ch['attributes']
        chapters_wanted.append((tid, a.get('chapter') or '1', a.get('title') or ''))
    else:
        manga_id = tid
        feed = md_get(session, f'/manga/{manga_id}/feed', {
            'limit': 500,
            'order[chapter]': 'desc',
            'contentRating[]': ['safe', 'suggestive', 'erotica', 'pornographic'],
            'translatedLanguage[]': ['zh', 'zh-hk', 'en', 'ja'],
        })
        seen = set()
        for item in feed['data']:
            a = item['attributes']
            if a.get('externalUrl') or not a.get('pages'):
                continue  # 外部托管章节抓不到图
            chno = a.get('chapter') or '0'
            if chno in seen:
                continue
            seen.add(chno)
            chapters_wanted.append((item['id'], chno, a.get('title') or ''))
            if len(chapters_wanted) >= limit:
                break
        chapters_wanted.reverse()  # 按章节号升序（阅读顺序）

    if not chapters_wanted:
        raise RuntimeError('没有找到可下载的章节（可能都是外部托管或无中/英/日语章节）')

    info = md_get(session, f'/manga/{manga_id}', {'includes[]': ['author']})
    attr = info['data']['attributes']
    title = (attr['title'].get('en') or next(iter(attr['title'].values()), '未命名'))
    authors = [r['attributes']['name'] for r in info['data']['relationships']
               if r['type'] == 'author' and 'attributes' in r]
    tags = [t['attributes']['name'].get('en', '') for t in attr.get('tags', [])][:10]

    chapters = []
    for cid, chno, chtitle in chapters_wanted:
        home = md_get(session, f'/at-home/server/{cid}')
        base, h = home['baseUrl'], home['chapter']['hash']
        # dataSaver 压缩图足够阅读且体积小一半
        files = home['chapter'].get('dataSaver') or home['chapter']['data']
        quality = 'data-saver' if home['chapter'].get('dataSaver') else 'data'
        urls = [f'{base}/{quality}/{h}/{fn}' for fn in files]
        label = f'第{chno}章' + (f' {chtitle}' if chtitle else '')
        log(f'MangaDex《{title}》{label}: {len(urls)} 页')
        download_chapter_images(session, urls, os.path.join(work_dir, str(len(chapters) + 1)),
                                referer='https://mangadex.org/')
        chapters.append({'photo_id': cid, 'title': label})

    return make_meta('md', manga_id, title, authors, tags, f'MangaDex {manga_id}', chapters)


# ============================================================
# 拷贝漫画（copymanga 第三方 API）
# ============================================================

CM_APIS = [
    'https://api.copymanga.tv/api/v3',
    'https://api.mangacopy.com/api/v3',
    'https://api.copymanga.org/api/v3',
    'https://api.copymanga.site/api/v3',
]
CM_UA = {'User-Agent': 'COPY/2.3.0', 'platform': '1', 'Accept': 'application/json'}


def cm_parse_target(text):
    text = text.strip()
    m = re.search(r'/comic/([A-Za-z0-9_-]+)(?:/chapter/([0-9a-f-]{36}))?', text)
    if m:
        return m.group(1), m.group(2)
    if re.fullmatch(r'[A-Za-z0-9_-]+', text):
        return text, None
    raise ValueError(f'无法识别拷贝漫画目标: {text}')


def cm_get(session, path, params=None):
    errs = []
    for base in CM_APIS:
        try:
            time.sleep(0.4)
            resp = session.get(base + path, params=params, headers=CM_UA, timeout=30)
            if resp.status_code != 200:
                errs.append(f'{base} → HTTP {resp.status_code}: {resp.text[:100]!r}')
                log(errs[-1] + '，尝试下一个域名')
                continue
            try:
                body = resp.json()
            except ValueError:
                errs.append(f'{base} → 非JSON响应: {resp.text[:100]!r}')
                log(errs[-1] + '，尝试下一个域名')
                continue
            if body.get('code') and body['code'] != 200:
                errs.append(f"{base} → code={body.get('code')}: {body.get('message')}")
                log(errs[-1] + '，尝试下一个域名')
                continue
            return body.get('results', body)
        except Exception as e:
            errs.append(f'{base} → {e}')
            log(errs[-1] + '，尝试下一个域名')
    raise RuntimeError('拷贝漫画API全部不可用:\n' + '\n'.join(errs))


def crawl_copymanga(session, target, work_dir, limit):
    path_word, chapter_uuid = cm_parse_target(target)
    comic = cm_get(session, f'/comic/{path_word}')['comic']
    title = comic['name']
    authors = [a['name'] for a in comic.get('author', [])]

    wanted = []  # (uuid, 章节名) 按阅读顺序
    if chapter_uuid:
        wanted.append((chapter_uuid, '单章'))
    else:
        feed = cm_get(session, f'/comic/{path_word}/group/default/chapters',
                      {'limit': 500, 'offset': 0})
        items = feed.get('list', [])
        items.sort(key=lambda x: x.get('ordered', 0))
        wanted = [(c['uuid'], c['name']) for c in items[-limit:]]

    if not wanted:
        raise RuntimeError('没有找到任何章节')

    chapters = []
    for cuuid, cname in wanted:
        detail = cm_get(session, f'/comic/{path_word}/chapter/{cuuid}')
        ch = detail['chapter']
        # contents 顺序需按 words 重排
        contents = ch.get('contents', [])
        words = ch.get('words', list(range(len(contents))))
        ordered = [c for c, _w in sorted(zip(contents, words), key=lambda x: x[1])]
        urls = [c['url'] for c in ordered]
        label = cname or ch.get('name') or f'第{len(chapters)+1}章'
        log(f'拷贝《{title}》{label}: {len(urls)} 页')
        download_chapter_images(session, urls, os.path.join(work_dir, str(len(chapters) + 1)),
                                headers=CM_UA, referer='https://www.copymanga.tv/')
        chapters.append({'photo_id': cuuid, 'title': label})

    return make_meta('cm', path_word, title, authors, [], f'拷贝漫画 {path_word}', chapters)


# ============================================================
# nhentai（网页解析，Cloudflare 用 curl_cffi 模拟浏览器）
# ============================================================

NH_EXT = {'j': 'jpg', 'p': 'png', 'g': 'gif', 'w': 'webp'}


def nh_parse_target(text):
    text = text.strip()
    m = re.search(r'nhentai\.net/g/(\d+)', text)
    if m:
        return m.group(1)
    if text.isdigit():
        return text
    raise ValueError(f'无法识别 nhentai 目标: {text}')


def crawl_nhentai(session, target, work_dir, _limit):
    gid = nh_parse_target(target)

    # 优先 JSON API（结构稳定，无需解析 HTML）
    resp = session.get(f'https://nhentai.net/api/gallery/{gid}', timeout=30)
    if resp.status_code == 200:
        data = resp.json()
        media_id = data['media_id']
        title = (data.get('title') or {}).get('english') or data['title'].get('japanese') or f'nhentai {gid}'
        artists = [t['name'] for t in data.get('tags', []) if t.get('type') == 'artist']
        urls = [f'https://i.nhentai.net/galleries/{media_id}/{i}.{NH_EXT.get(p.get("t"), "jpg")}'
                for i, p in enumerate(data['images']['pages'], 1)]
    else:
        log(f'nhentai API 返回 {resp.status_code}，回退 HTML 解析')
        resp = session.get(f'https://nhentai.net/g/{gid}/', timeout=30)
        resp.raise_for_status()
        html = resp.text
        m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        title = m.group(1).strip() if m else f'nhentai {gid}'
        m = re.search(r'"media_id":\s*"?(\d+)"?', html)
        if not m:
            raise RuntimeError(f'未能从页面解析 media_id（可能被 Cloudflare 拦截，HTTP {resp.status_code}）')
        media_id = m.group(1)
        pages_block = re.search(r'"pages":\s*\[(.*?)\]', html, re.DOTALL)
        if not pages_block:
            raise RuntimeError('未能从页面解析图片列表')
        types = re.findall(r'\{"t"\s*:\s*"(\w)"', pages_block.group(1))
        urls = [f'https://i.nhentai.net/galleries/{media_id}/{i}.{NH_EXT.get(t, "jpg")}'
                for i, t in enumerate(types, 1)]
        artists = re.findall(r'"artists":\s*\[.*?"name":\s*"([^"]+)"', html)

    log(f'nhentai《{title}》: {len(urls)} 页')
    download_chapter_images(session, urls, os.path.join(work_dir, '1'),
                            referer='https://nhentai.net/', delay=0.3)
    return make_meta('nh', gid, title, artists, [], f'nhentai g/{gid}',
                     [{'photo_id': gid, 'title': '全一话'}])


# ============================================================
# 入口
# ============================================================

def crawl(source, target, work_dir, limit=DEFAULT_LIMIT):
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    mkdir(work_dir)

    # 统一使用 curl_cffi（jmcomic 的依赖，且自带浏览器指纹可过 Cloudflare）
    from curl_cffi import requests as cffi_requests
    session = cffi_requests.Session(impersonate='chrome')

    if source == 'mangadex':
        return crawl_mangadex(session, target, work_dir, limit), work_dir
    if source == 'copymanga':
        return crawl_copymanga(session, target, work_dir, limit), work_dir
    if source == 'nhentai':
        return crawl_nhentai(session, target, work_dir, limit), work_dir

    raise ValueError(f'未知的内测平台: {source}（可选: {", ".join(SOURCES)}）')
