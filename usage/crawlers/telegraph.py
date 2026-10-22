"""
Telegra（telegra.ph 即时阅读文章）

Telegraph 文章是静态 HTML：正文 <article> 内 <figure><img src="/file/..."> 即原图，
无加密无懒加载，对爬虫很友好。整篇单章处理。
"""
import re
from urllib.parse import urljoin

from .base import CrawlerBase, CrawlError, log


class TelegraphCrawler(CrawlerBase):
    site = 'telegraph'
    name = 'Telegra'
    id_prefix = 'tg-'
    home = 'https://telegra.ph'
    hint = '文章链接（telegra.ph/<标题>-<日期>），整篇单章'
    color = '#37aee2'
    url_patterns = [r'telegra\.ph/\S']
    single_chapter = True
    image_referer = 'https://telegra.ph/'

    def parse_target(self, text):
        text = text.strip()
        m = re.search(r'(https?://telegra\.ph/[^\s<>"\']+)', text)
        if m:
            return m.group(1)
        m = re.search(r'(telegra\.ph/[^\s<>"\']+)', text)
        if m:
            return 'https://' + m.group(1)
        raise ValueError(f'无法识别 Telegra 目标: {text}（需要 telegra.ph 文章链接）')

    def fetch_meta(self, url, batch):
        resp = self.session.get(url, timeout=30)
        if resp.status_code != 200:
            raise CrawlError(f'Telegra 文章页返回 HTTP {resp.status_code}')
        html = resp.text

        tm = (re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
              or re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL))
        title = (tm.group(1).strip() if tm else '') or 'Telegra 文章'
        title = re.sub(r'[–—-]\s*Telegraph\s*$', '', title).strip() or title
        am = (re.search(r'<meta[^>]+name="author"[^>]+content="([^"]+)"', html)
              or re.search(r'rel="author"[^>]*>([^<]+)<', html))
        authors = [am.group(1).strip()] if am else []

        # 正文 <article> 内 <figure> 的 <img>（精确，避开表情/头像等杂图）
        body = re.search(r'<article[^>]*>(.*?)</article>', html, re.DOTALL)
        scope = body.group(1) if body else html
        urls = []
        for tag in re.findall(r'<figure[^>]*>.*?</figure>', scope, re.DOTALL):
            m = re.search(r'<img[^>]+src="([^"]+)"', tag)
            if m:
                full = urljoin('https://telegra.ph/', m.group(1))
                if full not in urls:
                    urls.append(full)
        if not urls:
            # 兜底：正文内所有 <img>
            for m in re.finditer(r'<img[^>]+src="([^"]+)"', scope):
                full = urljoin('https://telegra.ph/', m.group(1))
                if '/file/' in full and full not in urls:
                    urls.append(full)
        if not urls:
            raise CrawlError('Telegra 文章页没有找到图片（页面结构可能已变化）')

        slug = url.rstrip('/').rsplit('/', 1)[-1][:60] or 'article'
        log(f'Telegra《{title}》: {len(urls)} 页')
        return self.make_meta(slug, title, authors, [], f'Telegra {url}',
                              [{'photo_id': '1', 'title': '全文', 'order': 1.0, 'ref': urls}],
                              total=1, source_url=url)

    def get_chapter_image_urls(self, chapter_ref):
        return chapter_ref  # fetch_meta 已给出完整 URL 列表
