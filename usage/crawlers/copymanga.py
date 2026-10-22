"""
拷贝漫画（copymanga 第三方 API）

⚠️ 状态：保留代码、未注册启用。此前实测其 API 频繁变动导致不可用（见记忆库），
待后续研究稳定后再加入 registry。
"""
import re
import time

from .base import CrawlerBase, CrawlError, log

CM_APIS = [
    'https://api.mangacopy.com/api/v3',
    'https://www.mangacopy.com/api/v3',
    'https://api.copymanga.com/api/v3',
    'https://mangacopy.com/api/v3',
    'https://api.copymanga.tv/api/v3',
    'https://api.copymanga.org/api/v3',
    'https://api.copymanga.net/api/v3',
]
CM_UA = {'User-Agent': 'COPY/2.3.0', 'Accept': 'application/json'}


class CopymangaCrawler(CrawlerBase):
    site = 'copymanga'
    name = '拷贝漫画'
    id_prefix = 'cm-'
    home = 'https://www.mangacopy.com'
    hint = '漫画链接（mangacopy.com/comic/<path_word>）、path_word 或直接输漫画名搜索'
    color = '#22c55e'
    url_patterns = [r'copymanga\.\w+/comic/', r'mangacopy\.\w+/comic/']
    image_referer = 'https://www.mangacopy.com/'
    image_headers = CM_UA

    def parse_target(self, text):
        text = text.strip()
        m = re.search(r'/comic/([A-Za-z0-9_-]+)(?:/chapter/([0-9a-f-]{36}))?', text)
        if m:
            return f'{m.group(1)}:{m.group(2) or ""}'
        if re.fullmatch(r'[A-Za-z0-9_-]+', text):
            return f'{text}:'
        if text:
            return f'search:{text}'  # 非链接非 path_word：按漫画名搜索
        raise ValueError(f'无法识别拷贝漫画目标: {text}')

    def resolve_search(self, keyword):
        """漫画名 → path_word（取搜索首条结果）"""
        try:
            results = self.cm_get('/comic/search', {'q': keyword, 'limit': 10, 'offset': 0})
        except CrawlError:
            raise CrawlError(f'拷贝漫画搜索 "{keyword}" 失败（API 不可用）')
        items = (results.get('list') if isinstance(results, dict) else None) or []
        if not items:
            raise CrawlError(f'拷贝漫画搜索 "{keyword}" 没有结果')
        pw = items[0].get('path_word')
        log(f'拷贝漫画搜索 "{keyword}" → {items[0].get("name")} ({pw})')
        return pw

    def cm_get(self, path, params=None):
        errs = []
        for base in CM_APIS:
            try:
                time.sleep(0.4)
                q = dict(params or {})
                q.setdefault('platform', '1')
                resp = self.session.get(base + path, params=q, headers=CM_UA, timeout=30)
                if resp.status_code != 200:
                    errs.append(f'{base} → HTTP {resp.status_code}: {resp.text[:100]!r}')
                    log(errs[-1] + '，尝试下一个域名')
                    continue
                try:
                    body = resp.json()
                except ValueError:
                    errs.append(f'{base} → 非JSON响应: {resp.text[:100]!r}')
                    log(errs[-1] + '，尝试下一个域名')
                    continue
                if body.get('code') and body['code'] != 200:
                    errs.append(f"{base} → code={body.get('code')}: {body.get('message')}")
                    log(errs[-1] + '，尝试下一个域名')
                    continue
                return body.get('results', body)
            except Exception as e:
                errs.append(f'{base} → {e}')
                log(errs[-1] + '，尝试下一个域名')
        raise CrawlError('拷贝漫画API全部不可用:\n' + '\n'.join(errs))

    def fetch_meta(self, target, batch):
        if target.startswith('search:'):
            path_word, chapter_uuid = self.resolve_search(target[len('search:'):]), ''
        else:
            path_word, chapter_uuid = target.split(':', 1)
        comic = self.cm_get(f'/comic/{path_word}')['comic']
        title = comic['name']
        authors = [a['name'] for a in comic.get('author', [])]

        if chapter_uuid:
            wanted = [(chapter_uuid, '单章', 1)]
            total = 1
        else:
            feed = self.cm_get(f'/comic/{path_word}/group/default/chapters',
                               {'limit': 500, 'offset': 0})
            items = feed.get('list', [])
            items.sort(key=lambda x: x.get('ordered', 0))
            all_chapters = [(c['uuid'], c['name'], i + 1) for i, c in enumerate(items)]
            total = len(all_chapters)
            wanted = self.slice_batch(all_chapters, batch)

        if not wanted:
            raise CrawlError(f'第 {batch} 批没有章节（该作品共 {total} 章）')

        chapters = [{'photo_id': cuuid, 'title': cname or f'第{o}章', 'order': float(o),
                     'ref': {'path_word': path_word, 'uuid': cuuid}}
                    for cuuid, cname, o in wanted]
        return self.make_meta(path_word, title, authors, [], f'拷贝漫画 {path_word}', chapters,
                              total=total, source_url=f'https://www.mangacopy.com/comic/{path_word}')

    def get_chapter_image_urls(self, chapter_ref):
        detail = self.cm_get(f"/comic/{chapter_ref['path_word']}/chapter/{chapter_ref['uuid']}")
        ch = detail['chapter']
        contents = ch.get('contents', [])
        words = ch.get('words', list(range(len(contents))))
        ordered = [c for c, _w in sorted(zip(contents, words), key=lambda x: x[1])]
        return [c['url'] for c in ordered]
