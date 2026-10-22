"""
CMS 系漫画站通用爬虫（mangabz / mheee / baozimh / mkzhan）

这类站点共享相似的页面结构：详情页列章节链接，章节页内嵌图片列表。
实现策略（层层回退，尽量免逐站定制）：
    详情页：抓全部 <a>，按启发式筛出章节链接（本站 + 含数字 + .html/chapter 语义 + 排除导航路径）
    章节页：① manhuagui 风格 packer 解包 → ② script 中 JSON 图片数组 → ③ <img> 目录分组
    若详情页本身就找不到章节链接，则把它当单章页直接抽图（支持用户直接贴章节链接）

⚠️ 本地无法访问这些站点，需 Actions 云端逐站校准；子类只配 host/名称等元信息。
"""
import re
from urllib.parse import urljoin, urlparse

from .base import CrawlerBase, CrawlError, log
from .generic import GenericCrawler

# 常见导航/非章节路径
NAV_EXCLUDE = ('/list', '/author', '/search', '/rank', '/about', '/tags', '/tag/',
               '/category', '/login', '/register', '/help', '/app', '/topic',
               '/sort', '/classify', '/update', '/complete', '/comment')


def extract_chapter_links(html, base_url):
    """宽松启发式：从详情页抽 (章节URL, 标题)，去重后按数字自然排序"""
    host = urlparse(base_url).netloc
    seen, candidates = set(), []
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL):
        full = urljoin(base_url, href).split('#')[0]
        p = urlparse(full)
        if p.netloc and p.netloc != host:
            continue
        low = full.lower()
        if any(x in low for x in NAV_EXCLUDE):
            continue
        if not re.search(r'\d', p.path):
            continue
        # 章节链接语义：.html 结尾 / 含 chapter/read/comic/话/章 关键字 / 与详情页同前缀且更深
        if not (low.endswith('.html') or re.search(r'(chapter|read|comic|/m\d|/c\d)', low)):
            continue
        if full.rstrip('/') == base_url.rstrip('/'):
            continue
        if full in seen:
            continue
        seen.add(full)
        title = re.sub(r'<[^>]+>', '', text).strip()
        candidates.append((full, title))

    def sort_key(item):
        url, title = item
        nums = re.findall(r'\d+', url)
        tail = int(nums[-1]) if nums else 0
        tnums = re.findall(r'\d+', title)
        thead = int(tnums[0]) if tnums else tail
        return (thead, tail, url)

    candidates.sort(key=sort_key)
    return candidates


class SimpleCmsCrawler(CrawlerBase):
    host = ''
    image_host = ''          # packer 解包成功时使用的图床 host（可空，用回包内绝对地址或页面相对地址）
    image_referer = None     # 默认用章节页 URL
    download_delay = 0.25

    def parse_target(self, text):
        text = text.strip()
        m = re.search(r'(https?://[^\s<>"\']*' + re.escape(self.host) + r'[^\s<>"\']*)', text)
        if m:
            return m.group(1)
        if re.search(re.escape(self.host) + r'/', text):
            m = re.search(r'([\w./-]*' + re.escape(self.host) + r'/[^\s<>"\']*)', text)
            if m:
                return 'https://' + m.group(1)
        raise ValueError(f'无法识别{self.name}目标: {text}（需要本站漫画链接）')

    def fetch_meta(self, target, batch):
        url = target
        resp = self.session.get(url, timeout=30)
        if resp.status_code != 200:
            raise CrawlError(f'{self.name}详情页返回 HTTP {resp.status_code}')
        html = resp.text

        tm = (re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
              or re.search(r'<h1[^>]*>([^<]+)</h1>', html)
              or re.search(r'<title>([^<_|]+)', html))
        title = tm.group(1).strip() if tm else f'{self.name} 作品'
        title = re.sub(r'[-_](漫画|在线漫画|下拉式).*$', '', title).strip() or title
        am = re.search(r'作者[：:]\s*(?:<[^>]+>)*([^<，,]+)', html)
        authors = [am.group(1).strip()] if am else []

        links = extract_chapter_links(html, url)
        if not links:
            # 详情页找不到章节链接：当单章页处理（用户可能直接贴了章节链接）
            log(f'{self.name}: 未发现章节链接，按单章页抽图: {url}')
            return self.make_meta(
                self._sid_from_url(url), title, authors, [], f'{self.name} {url}',
                [{'photo_id': '1', 'title': title or '单章', 'order': 1.0,
                  'ref': {'url': url, 'single': True}}],
                total=1, source_url=url)

        total = len(links)
        wanted = self.slice_batch(list(range(total)), batch)
        if not wanted:
            raise CrawlError(f'第 {batch} 批没有章节（该作品共 {total} 章）')
        chapters = [{'photo_id': self._chapter_key(links[i][0], i),
                     'title': links[i][1] or f'第{i + 1}章',
                     'order': float(i + 1),
                     'ref': {'url': links[i][0]}}
                    for i in wanted]
        return self.make_meta(self._sid_from_url(url), title, authors, [], f'{self.name} {url}',
                              chapters, total=total, source_url=url)

    def get_chapter_image_urls(self, chapter_ref):
        url = chapter_ref['url']
        resp = self.session.get(url, timeout=30)
        if resp.status_code != 200:
            raise CrawlError(f'{self.name}章节页返回 HTTP {resp.status_code}')
        html = resp.text
        self._chapter_url = url  # save_image 覆写用作 Referer

        # 策略1：manhuagui 风格 packer 解包
        urls = self._try_packer(html, url)
        if urls:
            return urls
        # 策略2/3：script JSON 数组 / <img> 目录分组（复用 generic 的启发式）
        return GenericCrawler._extract_image_urls(html, url)

    def save_image(self, url, path):
        # 默认以章节页为 Referer（多数图床校验）
        headers = {}
        referer = getattr(self, '_chapter_url', None) or self.home
        if referer:
            headers['Referer'] = referer
        resp = self.session.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        with open(path, 'wb') as f:
            f.write(resp.content)
        return len(resp.content)

    def _try_packer(self, html, page_url):
        from .manhuagui import unpack_packer
        m = re.search(r"\}\('(.*?)',(\d+),(\d+),'([^']*)'\.split\('\|'\)", html, re.DOTALL)
        if not m:
            return []
        try:
            unpacked = unpack_packer(m.group(1), int(m.group(2)), int(m.group(3)),
                                     m.group(4).split('|'))
        except Exception:
            return []
        jm = re.search(r'(\{[^{}]*"files"\s*:\s*\[.*?\][^{}]*\})', unpacked, re.DOTALL)
        if not jm:
            return []
        import json
        try:
            info = json.loads(jm.group(1))
        except ValueError:
            return []
        files, path = info.get('files'), info.get('path', '')
        if not files:
            return []
        host = self.image_host or f'https://i.{urlparse(page_url).netloc.lstrip("www.")}'
        urls = []
        for fn in files:
            u = fn if fn.startswith('http') else f'{host}{path}{fn}'
            if info.get('sl', {}).get('e'):
                u += f"?e={info['sl']['e']}&m={info['sl'].get('m', '')}"
            urls.append(u)
        log(f'{self.name}: packer 解包成功，{len(urls)} 页')
        return urls

    def _sid_from_url(self, url):
        slug = urlparse(url).path.strip('/').replace('/', '_') or 'comic'
        return re.sub(r'\W+', '_', slug)[:60]

    @staticmethod
    def _chapter_key(url, idx):
        nums = re.findall(r'\d+', urlparse(url).path)
        return nums[-1] if nums else str(idx + 1)


class MangabzCrawler(SimpleCmsCrawler):
    site = 'mangabz'
    name = '漫画BZ'
    id_prefix = 'mbz-'
    home = 'https://www.mangabz.com'
    host = 'www.mangabz.com'
    hint = '漫画链接（mangabz.com 详情页或章节页），默认第 1 批 20 章'
    color = '#f97316'
    url_patterns = [r'mangabz\.com/']
    image_host = 'https://i.mangabz.com'


class MheeeCrawler(SimpleCmsCrawler):
    site = 'mheee'
    name = '漫画人'
    id_prefix = 'mhe-'
    home = 'https://mheee.com'
    host = 'mheee.com'
    hint = '漫画链接（mheee.com 详情页或章节页），默认第 1 批 20 章'
    color = '#14b8a6'
    url_patterns = [r'mheee\.com/']
    image_host = 'https://i.mheee.com'


class BaozimhCrawler(SimpleCmsCrawler):
    site = 'baozimh'
    name = '包子漫画'
    id_prefix = 'bz-'
    home = 'https://www.baozimh.com'
    host = 'www.baozimh.com'
    hint = '漫画链接（baozimh.com 详情页或章节页），默认第 1 批 20 章'
    color = '#eab308'
    url_patterns = [r'baozimh\.com/']
    image_host = 'https://static.baozimh.com'


class MkzhanCrawler(SimpleCmsCrawler):
    site = 'mkzhan'
    name = '漫客栈'
    id_prefix = 'mkz-'
    home = 'https://www.mkzhan.com'
    host = 'www.mkzhan.com'
    hint = '漫画链接（mkzhan.com 详情页或章节页），默认第 1 批 20 章'
    color = '#8b5cf6'
    url_patterns = [r'mkzhan\.com/']
    image_host = 'https://cdn.mkzhan.com'
