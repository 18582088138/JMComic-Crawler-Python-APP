"""
漫画柜（manhuagui.com / 看漫画）

章节页的图床信息藏在 eval 包裹的 Dean Edwards packer JS 里，
解包后得到 {path, files[], sl:{e,m}}，图片 URL：
    https://i.hamreus.com{path}{file}?e={sl.e}&m={sl.m}
图床强制校验 Referer: https://www.manhuagui.com/。

⚠️ 本地无法访问该站，实现依据公开爬虫的通行做法编写，需在 Actions 云端实测校准。
"""
import json
import re

from .base import CrawlerBase, CrawlError, log

IMAGE_HOST = 'https://i.hamreus.com'


def _base_n(num, radix):
    """0..radix-1 的基转换（>35 用 fromCharCode(c+29)，与 JS packer 的 e(c) 一致）"""
    digits = []
    while num:
        num, r = divmod(num, radix)
        digits.append(chr(r + 29) if r > 35 else (str(r) if r < 10 else chr(r + 87)))
    return ''.join(reversed(digits)) or '0'


def unpack_packer(packed, radix, count, keywords):
    """Dean Edwards p,a,c,k,e,d 解包器（Python 版）。

    以「最长键优先」的正则交替替换，避免短键误伤长键，兼容 62/94 两种字母表。
    """
    table = {}
    for i in range(count):
        if i < len(keywords) and keywords[i]:
            table[_base_n(i, radix)] = keywords[i]
    if not table:
        return packed
    pattern = re.compile('|'.join(re.escape(k) for k in sorted(table, key=len, reverse=True)))
    return pattern.sub(lambda m: table[m.group(0)], packed)


class ManhuaguiCrawler(CrawlerBase):
    site = 'manhuagui'
    name = '漫画柜'
    id_prefix = 'mg-'
    home = 'https://www.manhuagui.com'
    mirrors = ['https://www.mhgui.com', 'https://tw.manhuagui.com']
    hint = '漫画链接（manhuagui.com/comic/<id>）或章节链接，默认爬最新几章'
    color = '#3b82f6'
    url_patterns = [r'manhuagui\.com/comic/']
    image_referer = 'https://www.manhuagui.com/'
    image_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                                   'Chrome/124.0.0.0 Safari/537.36'}

    def parse_target(self, text):
        text = text.strip()
        m = re.search(r'manhuagui\.com/comic/(\d+)(?:/(\d+)\.html)?', text)
        if m:
            return f'{m.group(1)}:{m.group(2) or ""}'
        if text.isdigit():
            return f'{text}:'
        m = re.search(r"(https?://(?:www\.)?manhuagui\.com[^\s<>\"']*)", text)
        if m:
            return f'home:{m.group(1)}'  # 非详情页链接：自动发现第一本
        raise ValueError(f'无法识别漫画柜目标: {text}')

    def discover_first(self, page_url):
        resp = self.session.get(page_url, timeout=30)
        m = re.search(r'/comic/(\d+)/', resp.text)
        if not m:
            raise CrawlError('漫画柜自动发现失败：页面没有漫画链接')
        log(f'漫画柜自动发现 → comic/{m.group(1)}')
        return m.group(1)

    def fetch_meta(self, target, batch):
        if target.startswith('home:'):
            cid, ccid = self.discover_first(target[5:]), ''
        else:
            cid, ccid = target.split(':', 1)
        resp = self.session.get(f'https://www.manhuagui.com/comic/{cid}/', timeout=30)
        if resp.status_code != 200:
            raise CrawlError(f'漫画柜详情页返回 HTTP {resp.status_code}')
        html = resp.text

        tm = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        title = tm.group(1).strip() if tm else f'漫画柜 {cid}'
        am = re.search(r'作者[：:].{0,200}?title="([^"]+)"', html, re.DOTALL)
        authors = [a.strip() for a in (am.group(1).split(',') if am else []) if a.strip()]

        # 章节列表：<a href="/comic/{cid}/{ccid}.html" title="...">
        all_chapters = []  # (ccid, 标题, 全量序号) 页面内已是正序分组
        seen = set()
        for href, ctitle in re.findall(
                r'<a[^>]+href="/comic/\d+/(\d+)\.html"[^>]*title="([^"]*)"', html):
            if href in seen:
                continue
            seen.add(href)
            all_chapters.append((href, ctitle.strip(), len(all_chapters) + 1))
        if not all_chapters:
            raise CrawlError('漫画柜详情页没有解析到章节列表（页面结构可能已变化）')
        total = len(all_chapters)

        if ccid:
            title_map = {c: t for c, t, _ in all_chapters}
            order_map = {c: o for c, _, o in all_chapters}
            wanted = [(ccid, title_map.get(ccid, '单章'), order_map.get(ccid, 1))]
        else:
            wanted = self.slice_batch(all_chapters, batch)
        if not wanted:
            raise CrawlError(f'第 {batch} 批没有章节（该作品共 {total} 章）')

        meta_chapters = [{'photo_id': c, 'title': t or f'第{o}章', 'order': float(o),
                          'ref': {'cid': cid, 'ccid': c}}
                         for c, t, o in wanted]
        return self.make_meta(cid, title, authors, [], f'漫画柜 comic/{cid}', meta_chapters,
                              total=total, source_url=f'https://www.manhuagui.com/comic/{cid}/')

    def get_chapter_image_urls(self, chapter_ref):
        url = f"https://www.manhuagui.com/comic/{chapter_ref['cid']}/{chapter_ref['ccid']}.html"
        resp = self.session.get(url, timeout=30)
        if resp.status_code != 200:
            raise CrawlError(f'漫画柜章节页返回 HTTP {resp.status_code}')
        info = self._decode_chapter(resp.text)
        path, files, sl = info['path'], info['files'], info.get('sl', {})
        urls = []
        for fn in files:
            u = f'{IMAGE_HOST}{path}{fn}'
            if sl.get('e'):
                u += f"?e={sl['e']}&m={sl.get('m', '')}"
            urls.append(u)
        return urls

    @staticmethod
    def _decode_chapter(html):
        """从章节页解出图床信息。公开实现通行两种形态，逐一尝试。"""
        # 形态1：eval 包裹的 packer（function(h,u,n,t,e,r)）
        m = re.search(r"\}\('(.*?)',(\d+),(\d+),'([^']*)'\.split\('\|'\)", html, re.DOTALL)
        candidates = []
        if m:
            packed, radix, count, kw = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4).split('|')
            candidates.append(unpack_packer(packed, radix, count, kw))
        candidates.append(html)

        for text in candidates:
            jm = re.search(r'(\{[^{}]*"files"\s*:\s*\[.*?\][^{}]*\})', text, re.DOTALL)
            if not jm:
                continue
            try:
                info = json.loads(jm.group(1))
            except ValueError:
                continue
            if info.get('files') and info.get('path') is not None:
                return info
        # 解包失败：dump 页面结构线索（云端校准用）
        log(f'漫画柜解包失败，页面调试（HTML {len(html)} 字符）')
        for m in re.finditer(r'<script[^>]*>(.*?)</script>', html, re.DOTALL):
            block = m.group(1).strip()
            if block and re.search(r'(eval|pack|files|images|jpg|png|webp|hamreus|cid|chapter)', block, re.IGNORECASE):
                log(f'  script[{len(block)}]: {block[:800]!r}')
        for src in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html):
            log(f'  script-src: {src[:160]}')
        raise CrawlError('漫画柜章节页图床信息解包失败（JS 加密结构可能已变化）')
