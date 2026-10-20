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
    'https://api.copymanga.tv/api/v3',
    'https://api.copymanga.org/api/v3',
]
CM_UA = {'User-Agent': 'COPY/2.3.0', 'Accept': 'application/json'}


class CopymangaCrawler(CrawlerBase):
    site = 'copymanga'
    name = '拷贝漫画'
    id_prefix = 'cm-'
    home = 'https://www.copymanga.tv'
    hint = '漫画链接（copymanga.tv/comic/<path_word>）或 path_word'
    color = '#22c55e'
    url_patterns = [r'copymanga\.\w+/comic/', r'mangacopy\.\w+/comic/']
    image_referer = 'https://www.copymanga.tv/'
    image_headers = CM_UA

    def parse_target(self, text):
        text = text.strip()
        m = re.search(r'/comic/([A-Za-z0-9_-]+)(?:/chapter/([0-9a-f-]{36}))?', text)
        if m:
            return f'{m.group(1)}:{m.group(2) or ""}'
        if re.fullmatch(r'[A-Za-z0-9_-]+', text):
            return f'{text}:'
        raise ValueError(f'无法识别拷贝漫画目标: {text}')

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

    def fetch_meta(self, target, limit):
        path_word, chapter_uuid = target.split(':', 1)
        comic = self.cm_get(f'/comic/{path_word}')['comic']
        title = comic['name']
        authors = [a['name'] for a in comic.get('author', [])]

        wanted = []  # (uuid, 章节名)
        if chapter_uuid:
            wanted.append((chapter_uuid, '单章'))
        else:
            feed = self.cm_get(f'/comic/{path_word}/group/default/chapters',
                               {'limit': 500, 'offset': 0})
            items = feed.get('list', [])
            items.sort(key=lambda x: x.get('ordered', 0))
            wanted = [(c['uuid'], c['name']) for c in items[-limit:]]

        if not wanted:
            raise CrawlError('没有找到任何章节')

        self._path_word = path_word
        chapters = [{'photo_id': cuuid, 'title': cname or f'第{i + 1}章',
                     'ref': {'path_word': path_word, 'uuid': cuuid}}
                    for i, (cuuid, cname) in enumerate(wanted)]
        return self.make_meta(path_word, title, authors, [], f'拷贝漫画 {path_word}', chapters)

    def get_chapter_image_urls(self, chapter_ref):
        detail = self.cm_get(f"/comic/{chapter_ref['path_word']}/chapter/{chapter_ref['uuid']}")
        ch = detail['chapter']
        contents = ch.get('contents', [])
        words = ch.get('words', list(range(len(contents))))
        ordered = [c for c, _w in sorted(zip(contents, words), key=lambda x: x[1])]
        return [c['url'] for c in ordered]
