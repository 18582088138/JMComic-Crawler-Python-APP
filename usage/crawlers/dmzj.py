"""
动漫之家（dmzj）

官方 v3 API 多年间陆续关停/变动，公开爬虫普遍走镜像 API。
按顺序尝试：v3api.dmzj.com → nnv3api.muwai.com（muwai 镜像）。

⚠️ 本地无法访问该站，实现依据公开爬虫的通行做法编写，需在 Actions 云端实测校准。
"""
import re
import time

from .base import CrawlerBase, CrawlError, log

APIS = [
    'https://v3api.dmzj.com',
    'https://nnv3api.muwai.com',
]
API_QUERY = {'channel': 'Android', 'version': '2.7.019'}
UA = {'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36'}


class DmzjCrawler(CrawlerBase):
    site = 'dmzj'
    name = '动漫之家'
    id_prefix = 'dz-'
    home = 'https://manhua.dmzj.com'
    hint = '漫画链接（manhua.dmzj.com/<拼音名>）或数字漫画ID，默认爬最新几章'
    color = '#0ea5e9'
    url_patterns = [r'(?:manhua\.)?dmzj\.com/', r'muwai\.com/comic/']
    image_referer = 'https://m.dmzj.com/'
    image_headers = UA

    def parse_target(self, text):
        text = text.strip()
        m = re.search(r'manhua\.dmzj\.com/([a-z0-9]+)/?(?:\s|$|(\d+)\.s?html)', text)
        if m:
            return m.group(1)
        m = re.search(r'dmzj\.com/(?:info|comic)/([a-z0-9]+)', text)
        if m:
            return m.group(1)
        if text.isdigit() or re.fullmatch(r'[a-z0-9]+', text):
            return text
        raise ValueError(f'无法识别动漫之家目标: {text}')

    def api_get(self, path):
        errs = []
        for base in APIS:
            try:
                time.sleep(0.4)
                resp = self.session.get(base + path, params=API_QUERY, headers=UA, timeout=30)
                if resp.status_code != 200:
                    errs.append(f'{base} → HTTP {resp.status_code}')
                    log(errs[-1] + '，尝试下一个 API')
                    continue
                try:
                    return resp.json()
                except ValueError:
                    errs.append(f'{base} → 非JSON响应')
                    log(errs[-1] + '，尝试下一个 API')
            except Exception as e:
                errs.append(f'{base} → {e}')
                log(errs[-1] + '，尝试下一个 API')
        raise CrawlError('动漫之家 API 全部不可用:\n' + '\n'.join(errs))

    def resolve_slug(self, slug):
        """拼音名/路径词 → 数字漫画ID（读详情页 JS 变量）"""
        if slug.isdigit():
            return slug
        try:
            resp = self.session.get(f'https://manhua.dmzj.com/{slug}/', headers=UA, timeout=30)
            for pat in (r'g_comic_id\s*=\s*"?(\d+)"?', r'comic_id["\s:=]+"?(\d+)"?',
                        r'/comic/comic_(\d+)\.json', r'addCC\((\d+)'):
                m = re.search(pat, resp.text)
                if m:
                    return m.group(1)
        except Exception as e:
            log(f'解析动漫之家漫画ID失败: {e}')
        raise CrawlError(f'无法从 {slug} 解析出数字漫画ID，请改用数字ID提交')

    def fetch_meta(self, target, limit):
        cid = self.resolve_slug(target)
        info = self.api_get(f'/comic/comic_{cid}.json')
        title = info.get('title') or f'动漫之家 {cid}'
        authors_raw = info.get('authors')
        if isinstance(authors_raw, str):
            authors = [authors_raw]
        elif isinstance(authors_raw, list):
            authors = [a.get('tag_name') or a.get('name', '') if isinstance(a, dict) else str(a)
                       for a in authors_raw]
        else:
            authors = []

        chapters_raw = info.get('chapters') or []
        # 有的版本按「卷/话」分组：chapters -> [{title: '连载', data: [...]}]
        flat = []
        for item in chapters_raw:
            if 'data' in item and isinstance(item['data'], list):
                flat.extend(item['data'])
            else:
                flat.append(item)
        if not flat:
            raise CrawlError('动漫之家没有解析到章节列表（API 结构可能已变化）')
        wanted = flat[-limit:]

        chapters = [{'photo_id': str(c.get('chapter_id') or c.get('id')),
                     'title': c.get('chapter_title') or c.get('title') or f'第{i + 1}章',
                     'ref': {'cid': cid, 'chapter_id': str(c.get('chapter_id') or c.get('id'))}}
                    for i, c in enumerate(wanted)]
        return self.make_meta(cid, title, authors, [], f'动漫之家 {cid}', chapters)

    def get_chapter_image_urls(self, chapter_ref):
        data = self.api_get(f"/comic/chapter/{chapter_ref['cid']}_{chapter_ref['chapter_id']}.json")
        urls = data.get('page_url') or data.get('page_url_hd') or []
        return [u if u.startswith('http') else 'https://images.dmzj.com/' + u.lstrip('/') for u in urls]
