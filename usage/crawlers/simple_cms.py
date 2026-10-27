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
        resp = self.session.get(target, timeout=30)
        if resp.status_code != 200:
            raise CrawlError(f'{self.name}详情页返回 HTTP {resp.status_code}')
        html = resp.text
        # 重定向后的最终 URL 作为基准（如 baozimh.com → cn.bzmgcn.com）
        url = str(getattr(resp, 'url', None) or target)

        tm = (re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
              or re.search(r'<h1[^>]*>([^<]+)</h1>', html)
              or re.search(r'<title>([^<_|]+)', html))
        title = tm.group(1).strip() if tm else f'{self.name} 作品'
        title = re.sub(r'[-_](漫画|在线漫画|下拉式).*$', '', title).strip() or title
        am = re.search(r'作者[：:]\s*(?:<[^>]+>)*([^<，,]+)', html)
        authors = [am.group(1).strip()] if am else []

        links = extract_chapter_links(html, url)
        if links:
            log(f'{self.name}: 章节链接候选 {len(links)} 个，示例: {[u for u, _ in links[:3]]}')
        else:
            log(f'{self.name}: 章节链接候选 0 个（页面 {url}）')
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

    def predict_id(self, target_text):
        try:
            url = self.parse_target(target_text)
            m = re.search(re.escape(self.host) + r'/(\d+)', url)
            return f'{self.id_prefix}{m.group(1)}' if m else None
        except Exception:
            return None

    def get_chapter_image_urls(self, chapter_ref):
        resp = self.session.get(chapter_ref['url'], timeout=30)
        if resp.status_code != 200:
            raise CrawlError(f'{self.name}章节页返回 HTTP {resp.status_code}')
        html = resp.text
        url = str(getattr(resp, 'url', None) or chapter_ref['url'])
        self._chapter_url = url  # save_image 覆写用作 Referer

        # 策略1：manhuagui 风格 packer 解包
        urls = self._try_packer(html, url)
        if urls:
            return urls
        # 策略2/3：script JSON 数组 / <img> 目录分组（复用 generic 的启发式）
        urls = GenericCrawler._extract_image_urls(html, url)
        if not urls:
            self._dump_debug(html, url)
        return urls

    def _dump_debug(self, html, url):
        """抽图失败时把页面线索打进日志（云端校准用）"""
        log(f'{self.name}: 抽图失败，页面调试信息 {url}（HTML {len(html)} 字符）')
        for src in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html):
            log(f'  script-src: {src[:160]}')
        for m in re.finditer(r'<script[^>]*>(.*?)</script>', html, re.DOTALL):
            block = m.group(1).strip()
            if not block:
                continue
            if re.search(r'(jpg|jpeg|png|webp|files|images|pic|chapter|comic_id|api|ajax)',
                         block, re.IGNORECASE):
                log(f'  script[{len(block)}]: {block[:900]!r}')
        imgs = re.findall(r'<img[^>]+>', html)
        for tag in imgs[:5]:
            log(f'  img: {tag[:200]}')
        hrefs = sorted(set(re.findall(r'href="([^"]+)"', html)))
        for h in hrefs[:25]:
            log(f'  href: {h[:160]}')

    def probe_endpoints(self, desc, urls):
        """尝试候选 API 端点并记录状态（云端校准用），返回第一个 200 且像 JSON 的响应"""
        for u in urls:
            try:
                r = self.session.get(u, timeout=20)
                log(f'{self.name}: 端点尝试[{desc}] {u} → {r.status_code} {r.text[:150]!r}')
                if r.status_code == 200 and r.text.strip().startswith(('{', '[')):
                    return r.text
            except Exception as e:
                log(f'{self.name}: 端点尝试[{desc}] {u} → {e}')
        return None

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

    def get_chapter_image_urls(self, chapter_ref):
        urls = super().get_chapter_image_urls(chapter_ref)
        if urls:
            return urls
        # 图片数据在外部 JS（chapter.js / newyb.js）里，抓出来看结构（校准用）
        for js in ('https://css.mangabz.com/v202609180933/mangabz/js/chapter.js',
                   'https://css.mangabz.com/v202609180933/mangabz/js/newyb.js'):
            try:
                r = self.session.get(js, timeout=20)
                log(f'{self.name}: 外部JS {js.split("/")[-1]} → {r.status_code}: {r.text[:700]!r}')
            except Exception as e:
                log(f'{self.name}: 外部JS 获取失败 {js} → {e}')
        return urls


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
    url_patterns = [r'baozimh\.com/', r'bzmgcn\.com/']
    image_host = 'https://static.baozimh.com'

    def parse_target(self, text):
        # 主域 403 时回退 cn.bzmgcn.com
        try:
            return super().parse_target(text)
        except ValueError:
            m = re.search(r'(https?://[^\s<>"\']*bzmgcn\.com[^\s<>"\']*)', text)
            if m:
                return m.group(1)
            raise


class MkzhanCrawler(SimpleCmsCrawler):
    site = 'mkzhan'
    name = '漫客栈'
    id_prefix = 'mkz-'
    home = 'https://www.mkzhan.com'
    host = 'www.mkzhan.com'
    hint = '漫画链接（mkzhan.com/<数字ID>/ 或章节页链接）；目录接口需登录，详情页默认抓第一章，更多章节请直接贴章节页链接'
    color = '#8b5cf6'
    url_patterns = [r'mkzhan\.com/']
    image_host = 'https://cdn.mkzhan.com'

    def fetch_meta(self, target, batch):
        meta = super().fetch_meta(target, batch)
        # SSR 只有「开始阅读」第一章链接时，探测目录 API 补全章节列表
        if meta['totalChapters'] <= 1:
            cidm = re.search(r'mkzhan\.com/(\d+)', target)
            cid = cidm.group(1) if cidm else ''
            if cid:
                text = self.probe_endpoints('mkzhan-catalog', [
                    f'https://comic.mkzhan.com/chapter/show/?comic_id={cid}',
                    f'https://comic.mkzhan.com/comic/chapters?comic_id={cid}',
                    f'https://comic.mkzhan.com/comic/catalog?comic_id={cid}',
                    f'https://www.mkzhan.com/api/comic/{cid}/chapters',
                ])
                if text:
                    chapters = self._parse_mkzhan_catalog(text, target)
                    if chapters:
                        total = len(chapters)
                        wanted = self.slice_batch(chapters, batch)
                        meta['chapters'] = wanted
                        meta['totalChapters'] = total
        return meta

    def _parse_mkzhan_catalog(self, text, base_url):
        import json
        try:
            data = json.loads(text)
        except ValueError:
            return []
        items = data.get('data') if isinstance(data, dict) else data
        # data 可能是 {list: [...]} 或直接数组
        if isinstance(items, dict):
            items = items.get('list') or items.get('chapters') or []
        if not isinstance(items, list):
            return []
        out = []
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            chid = it.get('chapter_id') or it.get('id')
            title = it.get('title') or it.get('chapter_title') or it.get('name') or f'第{i + 1}章'
            if not chid:
                continue
            cidm = re.search(r'mkzhan\.com/(\d+)', base_url)
            cid = cidm.group(1) if cidm else ''
            out.append({'photo_id': str(chid), 'title': title, 'order': float(i + 1),
                        'ref': {'url': f'https://www.mkzhan.com/{cid}/{chid}.html'}})
        return out

    def get_chapter_image_urls(self, chapter_ref):
        resp = self.session.get(chapter_ref['url'], timeout=30)
        if resp.status_code != 200:
            raise CrawlError(f'{self.name}章节页返回 HTTP {resp.status_code}')
        html = resp.text
        url = str(getattr(resp, 'url', None) or chapter_ref['url'])
        self._chapter_url = url

        # 页面内 comic_id / chapter_id
        comic_id = (re.search(r'comic_id["\'\s:=]+["\']?(\d+)', html)
                    or re.search(r'mkzhan\.com/(\d+)/', url))
        chapter_id = (re.search(r'chapter_id["\'\s:=]+["\']?(\d+)', html)
                      or re.search(r'mkzhan\.com/\d+/(\d+)\.html', url))
        cid = comic_id.group(1) if comic_id else ''
        chid = chapter_id.group(1) if chapter_id else ''
        if cid and chid:
            text = self.probe_endpoints('mkzhan-images', [
                f'https://comic.mkzhan.com/chapter/content/?comic_id={cid}&chapter_id={chid}',
                f'https://www.mkzhan.com/chapter/getChapterImages?comic_id={cid}&chapter_id={chid}',
                f'https://comic-api.mkzhan.com/chapter/info?comic_id={cid}&chapter_id={chid}',
                f'https://www.mkzhan.com/api/chapter/{cid}/{chid}',
            ])
            if text:
                urls = self._parse_mkzhan_json(text)
                if urls:
                    log(f'{self.name}: API 命中 {len(urls)} 页')
                    return urls
        # 回退通用策略
        return super().get_chapter_image_urls(chapter_ref)

    @staticmethod
    def _parse_mkzhan_json(text):
        """comic.mkzhan.com/chapter/content/ 返回 {code, data: [{image_url|...}]}，URL 可能为协议相对"""
        import json
        try:
            data = json.loads(text)
        except ValueError:
            return []
        items = data.get('data') if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        urls = []
        for it in items:
            if not isinstance(it, dict):
                continue
            u = it.get('image_url') or it.get('img_url') or it.get('url') or it.get('image') or ''
            if not u:
                continue
            if u.startswith('//'):
                u = 'https:' + u
            elif u.startswith('/'):
                u = 'https://static.mkzcdn.com' + u
            if re.search(r'\.(jpg|jpeg|png|webp)', u, re.IGNORECASE):
                urls.append(u)
        if len(urls) < 3:
            # 兜底：全文正则（含协议相对）
            raw = re.findall(r'((?:https?:)?//[^"\s]+?\.(?:jpg|jpeg|png|webp)(?:\?[^"\s]*)?)', text)
            for u in raw:
                u = 'https:' + u if u.startswith('//') else u
                if u not in urls:
                    urls.append(u)
        return urls if len(urls) >= 3 else []
