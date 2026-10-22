"""
wnacg（紳士漫畫）

画廊列表页提供缩略图，缩略图 URL 的 /data/t/ 段去掉 /t 即是原图，
免逐页解析。列表分页 /photos-index-page-{n}-aid-{aid}.html。

⚠️ 本地无法访问该站，实现依据公开爬虫的通行做法编写，需在 Actions 云端实测校准。
"""
import re
import time
from urllib.parse import urljoin

from .base import CrawlerBase, CrawlError, log

MIRRORS = ['https://www.wnacg.com', 'https://www.wnacg.org']


class WnacgCrawler(CrawlerBase):
    site = 'wnacg'
    name = 'wnacg'
    id_prefix = 'wn-'
    home = 'https://www.wnacg.com'
    mirrors = ['https://www.wnacg.org']
    hint = '画廊链接（wnacg.com/photos-index-aid-<id>.html）或数字ID，整本单章'
    color = '#a855f7'
    url_patterns = [r'wnacg\.\w+/photos-']
    single_chapter = True
    download_delay = 0.3

    def parse_target(self, text):
        text = text.strip()
        m = re.search(r'aid-(\d+)\.html', text)
        if m:
            return m.group(1)
        m = re.search(r'wnacg\.\w+/(?:photos|download)-\w+-(\d+)', text)
        if m:
            return m.group(1)
        if text.isdigit():
            return text
        m = re.search(r"(https?://(?:www\.)?wnacg\.\w+[^\s<>\"']*)", text)
        if m:
            return f'home:{m.group(1)}'  # 非画廊链接：自动发现第一本
        raise ValueError(f'无法识别 wnacg 目标: {text}')

    def _get(self, path):
        last = None
        for base in MIRRORS:
            try:
                resp = self.session.get(urljoin(base, path), timeout=30)
                if resp.status_code == 200:
                    return base, resp.text
                last = f'{base} → HTTP {resp.status_code}'
                log(last + '，尝试下一个镜像')
            except Exception as e:
                last = f'{base} → {e}'
                log(last + '，尝试下一个镜像')
        raise CrawlError(f'wnacg 全部镜像不可用（最后错误: {last}）')

    def _get_gallery(self, aid):
        """画廊首页：新版多页画廊裸 -aid- URL 会 404，先试 page-1 形式"""
        try:
            return self._get(f'/photos-index-page-1-aid-{aid}.html')
        except CrawlError:
            log('wnacg: page-1 形式 404，回退裸 -aid- 形式')
            return self._get(f'/photos-index-aid-{aid}.html')

    def fetch_meta(self, aid, batch):
        if aid.startswith('home:'):
            resp = self.session.get(aid[5:], timeout=30)
            m = re.search(r'aid-(\d+)\.html', resp.text)
            if not m:
                raise CrawlError('wnacg 自动发现失败：页面没有画廊链接')
            log(f'wnacg 自动发现 → aid-{m.group(1)}')
            aid = m.group(1)
        base, html = self._get_gallery(aid)
        self._aid = aid
        tm = re.search(r'<h2[^>]*>([^<]+)</h2>', html) or re.search(r'<title>([^<|]+)', html)
        title = tm.group(1).strip() if tm else f'wnacg {aid}'
        am = re.search(r'作者[：:]\s*<a[^>]*>([^<]+)</a>', html)
        authors = [am.group(1).strip()] if am else []

        # 收集全部分页的缩略图与视图页链接（第一页已含，后续页递增直到为空）
        thumbs = re.findall(r'<img[^>]+src="(//[^"]+/data/t/[^"]+)"', html)
        self._view_links = []
        for v in re.findall(r'["\']((?:https?://www\.wnacg\.\w+)?/photos-view-id-\d+\.html)["\']', html):
            full = v if v.startswith('http') else base + v
            if full not in self._view_links:
                self._view_links.append(full)
        pages = set(int(p) for p in re.findall(r'photos-index-page-(\d+)-aid-', html))
        for p in sorted(pages):
            if p <= 1:
                continue
            time.sleep(0.3)
            try:
                _b, h = self._get(f'/photos-index-page-{p}-aid-{aid}.html')
            except CrawlError:
                break
            thumbs.extend(re.findall(r'<img[^>]+src="(//[^"]+/data/t/[^"]+)"', h))
            for v in re.findall(r'["\']((?:https?://www\.wnacg\.\w+)?/photos-view-id-\d+\.html)["\']', h):
                full = v if v.startswith('http') else _b + v
                if full not in self._view_links:
                    self._view_links.append(full)
        # 视图页链接按页码排序（链接里含 -id-<pid>，pid 顺序即阅读顺序）
        self._view_links.sort(key=lambda u: int(re.search(r'-id-(\d+)', u).group(1)))
        log(f'wnacg: 视图页链接 {len(self._view_links)} 个')

        # 去重保序
        seen, ordered = set(), []
        for t in thumbs:
            if t not in seen:
                seen.add(t)
                ordered.append(t)
        if not ordered:
            raise CrawlError('wnacg 画廊页没有解析到图片（页面结构可能已变化）')
        log(f'wnacg《{title}》: 共 {len(ordered)} 页')
        return self.make_meta(aid, title, authors, [], f'wnacg aid-{aid}',
                              [{'photo_id': aid, 'title': '全一话', 'order': 1.0, 'ref': ordered}],
                              total=1, source_url=f'https://www.wnacg.com/photos-index-aid-{aid}.html')

    def get_chapter_image_urls(self, chapter_ref):
        # 策略0（最优）：幻灯页 /photos-slide-aid-<aid>.html 一页含全部原图
        slide = self._try_slide(chapter_ref)
        if slide:
            return slide
        # 策略1：视图页 /photos-view-id-<pid>.html 解析原图
        # （缩略图 /data/t/→/data/ 规则 2026-10 实测已失效：图床分离，全尺寸 404）
        if self._view_links:
            urls = []
            for i, vurl in enumerate(self._view_links, 1):
                time.sleep(0.3)
                try:
                    resp = self.session.get(vurl, timeout=30)
                except Exception as e:
                    log(f'wnacg 视图页请求失败 {vurl}: {e}')
                    continue
                if resp.status_code != 200:
                    log(f'wnacg 视图页 {vurl} → HTTP {resp.status_code}')
                    continue
                m = (re.search(r'<img[^>]+id="picarea"[^>]+src="([^"]+)"', resp.text)
                     or re.search(r'src="((?:https?:)?//[^"]*(?:%s)[^"]*\.(?:jpe?g|png|webp)[^"]*)"' % 'img', resp.text))
                if m:
                    u = m.group(1)
                    urls.append('https:' + u if u.startswith('//') else u)
                elif i == 1:
                    log(f'wnacg 视图页未解出原图，页面调试: {resp.text[:600]!r}')
            if len(urls) >= max(3, len(self._view_links) // 2):
                return urls
            log(f'wnacg 视图页只解出 {len(urls)}/{len(self._view_links)} 张，回退缩略图变换')
        # 策略2：缩略图 /data/t/ → /data/ 变换（旧规则，可能 404）
        urls = []
        for t in chapter_ref:
            full = t.replace('/data/t/', '/data/')
            urls.append('https:' + full if full.startswith('//') else full)
        return urls

    def _try_slide(self, chapter_ref):
        """幻灯页一次性给出全部图片（结构好则完胜逐视图页）"""
        from .generic import GenericCrawler
        aid = chapter_ref[0] if chapter_ref and re.search(r'aid-(\d+)', str(chapter_ref)) else None
        if not aid:
            # 从视图链接推不出 aid 时，用 meta 阶段的缓存
            aid = getattr(self, '_aid', None)
        if not aid:
            return []
        for base in MIRRORS:
            try:
                resp = self.session.get(f'{base}/photos-slide-aid-{aid}.html', timeout=30)
                if resp.status_code != 200:
                    log(f'wnacg 幻灯页 {base} → HTTP {resp.status_code}')
                    continue
                html = resp.text
                urls = []
                # script 数组 / img 标签里的全尺寸图
                for m in re.finditer(r'((?:https?:)?//[^\s"\'<>]+?\.(?:jpe?g|png|webp)(?:\?[^\s"\'<>]*)?)', html):
                    u = m.group(1)
                    u = 'https:' + u if u.startswith('//') else u
                    if re.search(r'/(?:data|img|image|photo|gallery)/', u) and '/t/' not in u \
                            and not re.search(r'(logo|icon|banner|avatar|cover)', u, re.IGNORECASE):
                        if u not in urls:
                            urls.append(u)
                if len(urls) >= 3:
                    log(f'wnacg 幻灯页命中 {len(urls)} 页（{base}）')
                    return urls
                if urls or len(html) > 2000:
                    log(f'wnacg 幻灯页只抽到 {len(urls)} 张，页面片段: {html[:500]!r}')
            except Exception as e:
                log(f'wnacg 幻灯页 {base} → {e}')
        return []
