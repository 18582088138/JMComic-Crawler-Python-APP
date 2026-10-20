"""
MangaDex（官方 API：https://api.mangadex.org/docs/，最稳定）

实测要点（来自 ARCHITECTURE.md 排障速查）：
    语言过滤参数只接受 zh/zh-hk/en/ja（zh-hans 会 400）
    遵守 5 req/s 限速
    用 dataSaver 压缩图，体积约小一半
"""
import re
import time

from .base import CrawlerBase, CrawlError, log

MD_API = 'https://api.mangadex.org'
MD_UA = {'User-Agent': 'JMComicCloudShelf/1.0 (personal; github-actions)'}


class MangadexCrawler(CrawlerBase):
    site = 'mangadex'
    name = 'MangaDex'
    id_prefix = 'md-'
    home = 'https://mangadex.org'
    hint = '漫画链接（mangadex.org/title/<id>）或章节链接，也支持直接填 id'
    color = '#ff6740'
    url_patterns = [r'mangadex\.org/(?:title|chapter)/']
    image_referer = 'https://mangadex.org/'

    def parse_target(self, text):
        text = text.strip()
        m = re.search(r'mangadex\.org/(?:title|chapter)/([0-9a-f-]{36})', text)
        kind = 'chapter' if '/chapter/' in text else 'title'
        if m:
            return f'{kind}:{m.group(1)}'
        if re.fullmatch(r'[0-9a-f-]{36}', text):
            return f'title:{text}'
        raise ValueError(f'无法识别 MangaDex 目标: {text}')

    def md_get(self, path, params=None):
        time.sleep(0.3)  # 遵守 5 req/s 限速
        resp = self.session.get(MD_API + path, params=params, headers=MD_UA, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def fetch_meta(self, target, limit):
        kind, tid = target.split(':', 1)
        chapters_wanted = []  # (chapter_id, 章节号, 标题)

        if kind == 'chapter':
            ch = self.md_get(f'/chapter/{tid}')['data']
            manga_id = next(r['id'] for r in ch['relationships'] if r['type'] == 'manga')
            a = ch['attributes']
            chapters_wanted.append((tid, a.get('chapter') or '1', a.get('title') or ''))
        else:
            manga_id = tid
            feed = self.md_get(f'/manga/{manga_id}/feed', {
                'limit': 500,
                'order[chapter]': 'desc',
                'contentRating[]': ['safe', 'suggestive', 'erotica', 'pornographic'],
                'translatedLanguage[]': ['zh', 'zh-hk', 'en', 'ja'],
            })
            seen = set()
            for item in feed['data']:
                a = item['attributes']
                if a.get('externalUrl') or not a.get('pages'):
                    continue  # 外部托管章节抓不到图
                chno = a.get('chapter') or '0'
                if chno in seen:
                    continue
                seen.add(chno)
                chapters_wanted.append((item['id'], chno, a.get('title') or ''))
                if len(chapters_wanted) >= limit:
                    break
            chapters_wanted.reverse()  # 按章节号升序（阅读顺序）

        if not chapters_wanted:
            raise CrawlError('没有找到可下载的章节（可能都是外部托管或无中/英/日语章节）')

        info = self.md_get(f'/manga/{manga_id}', {'includes[]': ['author']})
        attr = info['data']['attributes']
        title = (attr['title'].get('en') or next(iter(attr['title'].values()), '未命名'))
        authors = [r['attributes']['name'] for r in info['data']['relationships']
                   if r['type'] == 'author' and 'attributes' in r]
        tags = [t['attributes']['name'].get('en', '') for t in attr.get('tags', [])][:10]

        chapters = [{'photo_id': cid, 'title': f'第{chno}章' + (f' {ct}' if ct else ''), 'ref': cid}
                    for cid, chno, ct in chapters_wanted]
        return self.make_meta(manga_id, title, authors, tags, f'MangaDex {manga_id}', chapters)

    def get_chapter_image_urls(self, chapter_ref):
        home = self.md_get(f'/at-home/server/{chapter_ref}')
        base, h = home['baseUrl'], home['chapter']['hash']
        files = home['chapter'].get('dataSaver') or home['chapter']['data']
        quality = 'data-saver' if home['chapter'].get('dataSaver') else 'data'
        return [f'{base}/{quality}/{h}/{fn}' for fn in files]
