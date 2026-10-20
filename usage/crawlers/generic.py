"""
通用兜底爬虫（GenericCrawler）

对不在支持列表的站点做最大努力尝试：
    1. 抓取页面 HTML
    2. 在 <script> 中找图片 URL 的 JSON 数组（≥5 张、同目录最佳）
    3. 退化为收集 <img src/data-src>，按目录分组取最大一组
    4. 全部失败 → 抛 GenericCrawlFailed，由编排层把站点记入「愿望清单」

能跑通大量「单页多图」的简单画廊站；复杂站点（加密/懒加载分页）走愿望清单。
"""
import json
import re
from urllib.parse import urljoin, urlparse

from .base import CrawlerBase, CrawlError, log, strip_html

IMG_EXT = r'(?:jpe?g|png|webp|gif|avif)'
MIN_IMAGES = 5


class GenericCrawlFailed(CrawlError):
    """兜底爬取失败——编排层捕获后把域名记入愿望清单"""

    def __init__(self, url, reason):
        self.url = url
        self.domain = urlparse(url).netloc
        super().__init__(f'暂不支持该站点（{self.domain}）：{reason}')


class GenericCrawler(CrawlerBase):
    site = 'generic'
    name = '通用爬虫'
    id_prefix = 'gx-'
    home = ''
    hint = '任意漫画页面链接（尽力尝试，失败会记入愿望清单）'
    color = '#6b7280'
    single_chapter = True

    def parse_target(self, text):
        text = (text or '').strip()
        m = re.search(r'https?://[^\s<>"\']+', text)
        if m:
            return m.group(0)
        raise ValueError('通用兜底需要一个 http(s) 链接')

    def fetch_meta(self, url, limit):
        try:
            resp = self.session.get(url, timeout=30)
        except Exception as e:
            raise GenericCrawlFailed(url, f'页面请求失败: {e}')
        if resp.status_code != 200:
            raise GenericCrawlFailed(url, f'页面返回 HTTP {resp.status_code}')
        html = resp.text

        tm = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
        if not tm:
            tm = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
        title = strip_html(tm.group(1))[:80] if tm else urlparse(url).netloc

        urls = self._extract_image_urls(html, url)
        if not urls:
            raise GenericCrawlFailed(url, '页面中没有找到可识别的图片序列（可能需要专用爬虫）')
        if len(urls) < MIN_IMAGES:
            raise GenericCrawlFailed(url, f'只找到 {len(urls)} 张图，低于阈值 {MIN_IMAGES}，疑似非漫画页')

        domain = urlparse(url).netloc.replace('.', '_')
        log(f'通用爬虫命中《{title}》: {len(urls)} 张候选图')
        return self.make_meta(domain, title, [], [], f'通用爬虫 {url}',
                              [{'photo_id': '1', 'title': '全部图片', 'ref': urls}])

    def get_chapter_image_urls(self, chapter_ref):
        return chapter_ref  # fetch_meta 已给出完整 URL 列表

    @staticmethod
    def _extract_image_urls(html, base_url):
        # 策略1：script 中的 JSON 图片数组
        best = []
        for block in re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL):
            for arr in re.findall(r'\[\s*("https?://[^"]+?' + IMG_EXT + r'[^"]*"(?:\s*,\s*"[^"]+?"\s*)*)\]', block):
                try:
                    items = json.loads('[' + arr + ']')
                except ValueError:
                    continue
                imgs = [u for u in items if isinstance(u, str) and re.search(IMG_EXT, u, re.IGNORECASE)]
                if len(imgs) > len(best):
                    best = imgs
        if len(best) >= MIN_IMAGES:
            return best

        # 策略2：<img> 按目录分组取最大组
        groups = {}
        for m in re.finditer(r'<img[^>]+(?:src|data-src|data-original)="([^"]+)"', html, re.IGNORECASE):
            src = m.group(1)
            if src.startswith('data:'):
                continue
            full = urljoin(base_url, src)
            if not re.search(r'\.' + IMG_EXT + r'(?:[?#]|$)', full, re.IGNORECASE):
                continue
            folder = full.rsplit('/', 1)[0]
            groups.setdefault(folder, []).append(full)
        for folder in sorted(groups, key=lambda f: len(groups[f]), reverse=True):
            urls = []
            for u in groups[folder]:
                if u not in urls:
                    urls.append(u)
            if len(urls) >= MIN_IMAGES:
                return urls
        return []
