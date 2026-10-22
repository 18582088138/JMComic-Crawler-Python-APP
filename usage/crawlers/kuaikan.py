"""
快看漫画（kuaikanmanhua.com）

网页章节页内嵌 __NUXT__/__INITIAL_STATE__ JSON，含图片 URL 列表；
抽不出时回退通用启发式（script 数组 / <img> 分组）。

⚠️ 本地无法访问该站，实现依据公开爬虫的通行做法编写，需在 Actions 云端实测校准。
"""
import json
import re
from urllib.parse import urljoin

from .base import CrawlError, log
from .generic import GenericCrawler
from .simple_cms import SimpleCmsCrawler, extract_chapter_links

IMG_RE = re.compile(r'https?://[^"\s]+?\.(?:jpe?g|png|webp)(?:\?[^"\s]*)?', re.IGNORECASE)


class KuaikanCrawler(SimpleCmsCrawler):
    site = 'kuaikan'
    name = '快看漫画'
    id_prefix = 'kk-'
    home = 'https://www.kuaikanmanhua.com'
    host = 'www.kuaikanmanhua.com'
    hint = '漫画链接（kuaikanmanhua.com/web/comic/<id>），默认第 1 批 20 章'
    color = '#ffd12e'
    url_patterns = [r'kuaikanmanhua\.com/']

    def get_chapter_image_urls(self, chapter_ref):
        url = chapter_ref['url']
        resp = self.session.get(url, timeout=30)
        if resp.status_code != 200:
            raise CrawlError(f'快看章节页返回 HTTP {resp.status_code}')
        html = resp.text
        self._chapter_url = url

        # 策略1：__NUXT__ / __INITIAL_STATE__ 内嵌 JSON 中的图片数组
        urls = self._extract_from_state(html)
        if urls:
            return urls
        # 策略2：通用启发式
        return GenericCrawler._extract_image_urls(html, url)

    @staticmethod
    def _extract_from_state(html):
        # 直接全文找图片 CDN URL，按出现顺序去重（快看图床一般在 kkmh.com/kuaikan 系域名）
        candidates = []
        for m in IMG_RE.finditer(html):
            u = m.group(0)
            if not re.search(r'(kkmh|kuaikan|yximgs|kwimgs)', u):
                continue
            if re.search(r'(avatar|logo|icon|banner|cover_s|thumbnail)', u, re.IGNORECASE):
                continue
            if u not in candidates:
                candidates.append(u)
        if len(candidates) >= 5:
            log(f'快看: 内嵌数据命中 {len(candidates)} 页')
            return candidates
        # 尝试解析 JSON 块精确取 photos
        for block in re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL):
            for arr in re.findall(r'\[(?:\s*"https?://[^"]+"\s*,?)+\]', block):
                try:
                    items = json.loads(arr)
                except ValueError:
                    continue
                imgs = [u for u in items if isinstance(u, str) and IMG_RE.search(u)]
                if len(imgs) >= 5:
                        log(f'快看: JSON 数组命中 {len(imgs)} 页')
                        return imgs
        return []
