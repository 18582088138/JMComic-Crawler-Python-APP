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

    def fetch_meta(self, aid, batch):
        base, html = self._get(f'/photos-index-aid-{aid}.html')
        tm = re.search(r'<h2[^>]*>([^<]+)</h2>', html) or re.search(r'<title>([^<|]+)', html)
        title = tm.group(1).strip() if tm else f'wnacg {aid}'
        am = re.search(r'作者[：:]\s*<a[^>]*>([^<]+)</a>', html)
        authors = [am.group(1).strip()] if am else []

        # 收集全部分页的缩略图（第一页已含，后续页递增直到为空）
        thumbs = re.findall(r'<img[^>]+src="(//[^"]+/data/t/[^"]+)"', html)
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
        # chapter_ref 为缩略图 URL 列表；/data/t/ → /data/ 得原图
        urls = []
        for t in chapter_ref:
            full = t.replace('/data/t/', '/data/')
            urls.append('https:' + full if full.startswith('//') else full)
        return urls
