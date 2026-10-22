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

    def fetch_meta(self, target, batch):
        # 先探官方风格的 JSON 端点（SPA 站点 SSR 里没有章节列表）
        idm = re.search(r'/web/comic/(\d+)|/webs/comic-next/(\d+)|\b(\d{5,})\b', target)
        comic_id = next((g for g in (idm.groups() if idm else []) if g), None)
        if comic_id:
            text = self.probe_endpoints('kk-chapters', [
                f'https://www.kuaikanmanhua.com/mini/dramaturgy/comic/{comic_id}',
                f'https://www.kuaikanmanhua.com/mini/comic/{comic_id}/chapter/list',
                f'https://www.kuaikanmanhua.com/v2/webs/comic-next/{comic_id}/chapters',
                f'https://www.kuaikanmanhua.com/api/comic/{comic_id}/chapters',
            ])
            if text:
                meta = self._meta_from_api(text, comic_id, target, batch)
                if meta:
                    return meta
        return super().fetch_meta(target, batch)

    def _meta_from_api(self, text, comic_id, target, batch):
        try:
            data = json.loads(text)
        except ValueError:
            return None
        # 兼容几种可能的结构：找章节列表（含 id/order/title 的对象数组）
        chapters_raw = None
        def find_list(obj):
            if isinstance(obj, list) and obj and isinstance(obj[0], dict):
                keys = set(obj[0].keys())
                if keys & {'id', 'chapter_id'} and keys & {'title', 'name', 'chapter_title'}:
                    return obj
            if isinstance(obj, dict):
                for v in obj.values():
                    r = find_list(v)
                    if r:
                        return r
            return None
        chapters_raw = find_list(data)
        if not chapters_raw:
            log('快看: API 返回中未找到章节列表结构')
            return None
        chapters_raw.sort(key=lambda c: c.get('order', c.get('chapter_order', 0)))
        total = len(chapters_raw)
        wanted = self.slice_batch(list(range(total)), batch)
        tm = re.search(r'"title"\s*:\s*"([^"]+)"', text)
        title = tm.group(1) if tm else f'快看 {comic_id}'
        chapters = [{'photo_id': str(c.get('id') or c.get('chapter_id')),
                     'title': c.get('title') or c.get('name') or f'第{i + 1}章',
                     'order': float(c.get('order', c.get('chapter_order', i + 1))),
                     'ref': {'url': f'https://www.kuaikanmanhua.com/webs/comic-next/{comic_id}/{c.get("id") or c.get("chapter_id")}',
                             'comic_id': comic_id,
                             'chapter_id': str(c.get('id') or c.get('chapter_id'))}}
                    for i, c in enumerate(chapters_raw) if i in wanted]
        return self.make_meta(comic_id, title, [], [], f'快看 {comic_id}', chapters,
                              total=total,
                              source_url=f'https://www.kuaikanmanhua.com/web/comic/{comic_id}')

    def get_chapter_image_urls(self, chapter_ref):
        # 有 API 候选时先试 API
        if chapter_ref.get('chapter_id'):
            cid, chid = chapter_ref['comic_id'], chapter_ref['chapter_id']
            text = self.probe_endpoints('kk-images', [
                f'https://www.kuaikanmanhua.com/mini/dramaturgy/comic/{cid}/chapter/{chid}',
                f'https://www.kuaikanmanhua.com/mini/comic/{cid}/chapter/{chid}',
                f'https://www.kuaikanmanhua.com/v2/webs/comic-next/{cid}/{chid}',
            ])
            if text:
                urls = [u for u in IMG_RE.findall(text) if not re.search(r'(avatar|logo|icon)', u, re.IGNORECASE)]
                urls = [u for i, u in enumerate(urls) if u not in urls[:i]]
                if len(urls) >= 3:
                    log(f'快看: API 命中 {len(urls)} 页')
                    return urls
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
