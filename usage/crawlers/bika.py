"""
哔咔漫画（PicACG 官方 App API）

API 走 HMAC-SHA256 签名（公开实现通行做法）：
    signature = HMAC(secret, (path+query + time + nonce + "GET" + api-key).lower())
漫画信息/章节/分页接口无需登录；图片 {fileServer}/static/{path}。

⚠️ 本地无法访问该站，实现依据公开爬虫的通行做法编写，需在 Actions 云端实测校准。
"""
import hashlib
import hmac
import re
import time
import uuid

from .base import CrawlerBase, CrawlError, log

API_HOST = 'https://pica-api.picacomic.com'
API_KEY = 'C69BAF41DA5ABD1FFEDC6D2FEA56B'
SECRET_KEY = '~d}$Q7$eIni=V)9\\RK/P.RM4;9[7|@/CA}b~OW!3?EV`:<>M7pddUBL5n|0/*Cn'


class BikaCrawler(CrawlerBase):
    site = 'bika'
    name = '哔咔漫画'
    id_prefix = 'bk-'
    home = 'https://www.picacomic.com'
    hint = '无网页版：漫画ID 需从哔咔 App 分享链接获取（24位十六进制），默认爬最新几章'
    color = '#f472b6'
    url_patterns = [r'picacomic\.com/']

    def parse_target(self, text):
        text = text.strip()
        m = re.search(r'\b([0-9a-f]{24})\b', text)
        if m:
            return m.group(1)
        raise ValueError(f'无法识别哔咔目标: {text}（需要 24 位十六进制漫画ID）')

    def _signed_get(self, path_query):
        """path_query 形如 'comics/<id>/eps?page=1'（不含 host 与前导斜杠）"""
        ts = str(int(time.time()))
        nonce = str(uuid.uuid4()).replace('-', '')
        raw = (path_query + ts + nonce + 'GET' + API_KEY).lower()
        sig = hmac.new(SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
        headers = {
            'api-key': API_KEY,
            'accept': 'application/vnd.picacomic.com.v1+json',
            'app-channel': '2',
            'time': ts,
            'nonce': nonce,
            'signature': sig,
            'app-version': '2.2.1.3.2',
            'app-uuid': str(uuid.uuid4()),
            'image-quality': 'original',
            'app-platform': 'android',
            'app-build-version': '45',
            'User-Agent': 'okhttp/3.8.1',
        }
        time.sleep(0.3)
        resp = self.session.get(f'{API_HOST}/{path_query}', headers=headers, timeout=30)
        if resp.status_code != 200:
            raise CrawlError(f'哔咔 API {path_query} 返回 HTTP {resp.status_code}: {resp.text[:120]!r}')
        return resp.json()

    def fetch_meta(self, cid, limit):
        info = self._signed_get(f'comics/{cid}')
        comic = info.get('comic') or info
        title = comic.get('title') or f'哔咔 {cid}'
        authors = [comic['author']] if comic.get('author') else []
        tags = comic.get('tags') or []

        # 章节（eps）分页拉取
        eps, page = [], 1
        while True:
            data = self._signed_get(f'comics/{cid}/eps?page={page}')
            box = data.get('eps') or {}
            eps.extend(box.get('docs') or [])
            if page >= int(box.get('pages') or 1):
                break
            page += 1
        if not eps:
            raise CrawlError('哔咔没有解析到章节（该漫画可能需登录/App 内可见）')
        eps.sort(key=lambda e: e.get('order', 0))
        wanted = eps[-limit:]

        chapters = [{'photo_id': e.get('_id', str(e.get('order'))),
                     'title': e.get('title') or f"第{e.get('order', i + 1)}话",
                     'ref': {'cid': cid, 'order': e.get('order', i + 1)}}
                    for i, e in enumerate(wanted)]
        return self.make_meta(cid, title, authors, tags[:10], f'哔咔 {cid}', chapters)

    def get_chapter_image_urls(self, chapter_ref):
        urls, page = [], 1
        while True:
            data = self._signed_get(f"comics/{chapter_ref['cid']}/order/{chapter_ref['order']}/pages?page={page}")
            box = data.get('pages') or {}
            for doc in box.get('docs') or []:
                media = doc.get('media') or {}
                fs, path = media.get('fileServer', ''), media.get('path', '')
                if fs and path:
                    urls.append(f'{fs}/static/{path}')
            if page >= int(box.get('pages') or 1):
                break
            page += 1
        return urls
