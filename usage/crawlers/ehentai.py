"""
E-Hentai（e-hentai.org 公开画廊）

画廊页收集 /s/ 图片页链接（自动翻页），逐页解析 <img id="img"> 原图。
匿名 IP 有浏览配额，页间限速 1s；单次安全上限 300 页防失控。

⚠️ 本地无法访问该站，实现依据公开爬虫的通行做法编写，需在 Actions 云端实测校准。
"""
import re
import time

from .base import CrawlerBase, CrawlError, log

BASE = 'https://e-hentai.org'
MAX_PAGES = 300


class EhentaiCrawler(CrawlerBase):
    site = 'ehentai'
    name = 'E-Hentai'
    id_prefix = 'eh-'
    home = 'https://e-hentai.org'
    hint = '画廊链接（e-hentai.org/g/<gid>/<token>），整本单章'
    color = '#ede0d4'
    url_patterns = [r'e-hentai\.org/g/\d+/[0-9a-f]+']
    single_chapter = True
    download_delay = 1.0

    def parse_target(self, text):
        text = text.strip()
        m = re.search(r'e-hentai\.org/g/(\d+)/([0-9a-f]{8,12})', text)
        if m:
            return f'{m.group(1)}/{m.group(2)}'
        m = re.search(r"(https?://e-hentai\.org[^\s<>\"']*)", text)
        if m:
            return f'home:{m.group(1)}'  # 非画廊链接：自动发现第一本
        raise ValueError(f'无法识别 E-Hentai 目标: {text}（需要完整画廊链接，含 token 段）')

    def predict_id(self, target_text):
        try:
            gt = self.parse_target(target_text)
            if gt.startswith('home:'):
                return None
            return f'{self.id_prefix}{gt.split("/", 1)[0]}'
        except Exception:
            return None

    def fetch_meta(self, target, batch):
        if target.startswith('home:'):
            resp = self.session.get(target[5:], timeout=30)
            m = re.search(r'e-hentai\.org/g/(\d+)/([0-9a-f]{8,12})/?', resp.text)
            if not m:
                raise CrawlError('E-Hentai 自动发现失败：页面没有画廊链接')
            log(f'E-Hentai 自动发现 → g/{m.group(1)}/{m.group(2)}')
            target = f'{m.group(1)}/{m.group(2)}'
        gid, token = target.split('/', 1)
        gallery_url = f'{BASE}/g/{gid}/{token}/'
        resp = self.session.get(gallery_url, timeout=30)
        if resp.status_code != 200:
            raise CrawlError(f'E-Hentai 画廊页返回 HTTP {resp.status_code}（可能触发风控或需要账号）')
        html = resp.text
        if 'This gallery has been removed' in html or 'not available' in html.lower():
            raise CrawlError('该画廊已被移除或不可访问')

        tm = re.search(r'<h1 id="gn">([^<]+)</h1>', html) or re.search(r'<title>([^<]+)</title>', html)
        title = tm.group(1).strip() if tm else f'e-hentai {gid}'
        artists = re.findall(r'tag/artist:([^"/?]+)', html)[:3]
        artists = [a.replace('+', ' ') for a in artists]

        page_links = []
        page_urls = [gallery_url]
        p = 0
        while True:
            for href in re.findall(r'<a[^>]+href="(https://e-hentai\.org/s/[0-9a-f]+/\d+-\d+)"', html):
                if href not in page_links:
                    page_links.append(href)
            p += 1
            if len(page_links) >= MAX_PAGES:
                break
            next_url = f'{gallery_url}?p={p}'
            if f'?p={p}' not in html:
                break
            time.sleep(0.5)
            resp = self.session.get(next_url, timeout=30)
            if resp.status_code != 200:
                break
            html = resp.text
            page_urls.append(next_url)

        if not page_links:
            raise CrawlError('E-Hentai 画廊页没有解析到图片页链接（页面结构可能已变化）')
        page_links = page_links[:MAX_PAGES]
        log(f'E-Hentai《{title}》: 共 {len(page_links)} 页（上限 {MAX_PAGES}）')
        return self.make_meta(gid, title, artists, [], f'e-hentai g/{gid}',
                              [{'photo_id': gid, 'title': '全一话', 'order': 1.0, 'ref': page_links}],
                              total=1, source_url=gallery_url)

    def get_chapter_image_urls(self, chapter_ref):
        """逐页解析原图（返回与原图等长的 URL 列表，下载仍由基类统一执行）"""
        urls = []
        for i, page_url in enumerate(chapter_ref, 1):
            time.sleep(0.8)  # 页解析限速，保护匿名配额
            resp = self.session.get(page_url, timeout=30)
            if resp.status_code != 200:
                raise CrawlError(f'E-Hentai 图片页 {page_url} 返回 HTTP {resp.status_code}')
            m = re.search(r'<img[^>]+id="img"[^>]+src="([^"]+)"', resp.text)
            if not m:
                raise CrawlError(f'E-Hentai 图片页 {page_url} 未解析到原图（可能超出 IP 配额）')
            urls.append(m.group(1))
            if i % 20 == 0:
                log(f'E-Hentai 已解析 {i}/{len(chapter_ref)} 页')
        return urls
