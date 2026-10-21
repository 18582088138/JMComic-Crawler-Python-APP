"""
nhentai（网页解析，Cloudflare 用 curl_cffi 模拟浏览器）

实测要点（来自 ARCHITECTURE.md 排障速查）：
    主站封数据中心 IP（API 403、页面假 404），靠镜像 nhentai.to/.xxx 回退
    整本单章站点，limit 不生效
"""
import re

from .base import CrawlerBase, CrawlError, log

NH_EXT = {'j': 'jpg', 'p': 'png', 'g': 'gif', 'w': 'webp'}
NH_MIRRORS = ['https://nhentai.net', 'https://nhentai.to', 'https://nhentai.xxx']


class NhentaiCrawler(CrawlerBase):
    site = 'nhentai'
    name = 'nhentai'
    id_prefix = 'nh-'
    home = 'https://nhentai.net'
    mirrors = ['https://nhentai.to', 'https://nhentai.xxx']
    hint = '画廊号或链接（nhentai.net/g/<id>），整本单章'
    color = '#ec2753'
    url_patterns = [r'nhentai\.(?:net|to|xxx)/g/']
    single_chapter = True
    image_referer = 'https://nhentai.net/'
    download_delay = 0.3

    def parse_target(self, text):
        text = text.strip()
        m = re.search(r'nhentai\.(?:net|to|xxx)/g/(\d+)', text)
        if m:
            return m.group(1)
        if text.isdigit():
            return text
        raise ValueError(f'无法识别 nhentai 目标: {text}')

    def fetch_meta(self, gid, limit):
        media_id, title, artists, types = self._fetch_gallery(gid)
        return self.make_meta(gid, title, artists, [], f'nhentai g/{gid}',
                              [{'photo_id': gid, 'title': '全一话',
                                'ref': {'media_id': media_id, 'types': types}}])

    def get_chapter_image_urls(self, chapter_ref):
        media_id, types = chapter_ref['media_id'], chapter_ref['types']
        return [f'https://i.nhentai.net/galleries/{media_id}/{i}.{NH_EXT.get(t, "jpg")}'
                for i, t in enumerate(types, 1)]

    def _fetch_gallery(self, gid):
        """依次尝试主站 API / 主站页面 / 镜像站页面，返回 (media_id, title, artists, page_types)"""
        resp = self.session.get(f'https://nhentai.net/api/gallery/{gid}', timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            title = ((data.get('title') or {}).get('english')
                     or data['title'].get('japanese') or f'nhentai {gid}')
            artists = [t['name'] for t in data.get('tags', []) if t.get('type') == 'artist']
            types = [p.get('t') for p in data['images']['pages']]
            log('nhentai 主站 API 命中')
            return data['media_id'], title, artists, types
        log(f'nhentai 主站 API 返回 {resp.status_code}，回退页面解析')

        last_status = resp.status_code
        for base in NH_MIRRORS:
            try:
                r = self.session.get(f'{base}/g/{gid}/', timeout=30)
                last_status = r.status_code
                if r.status_code != 200:
                    log(f'{base} 返回 {r.status_code}，尝试下一个镜像')
                    continue
                html = r.text
                m = re.search(r'"media_id":\s*"?(\d+)"?', html)
                pages_block = re.search(r'"pages":\s*\[(.*?)\]', html, re.DOTALL)
                if not m or not pages_block:
                    log(f'{base} 页面结构异常（可能被拦截），尝试下一个镜像')
                    continue
                tm = re.search(r'<meta property="og:title" content=["“]([^"”]+)', html)
                title = tm.group(1).strip() if tm else f'nhentai {gid}'
                artists = re.findall(r'"artists":\s*\[.*?"name":\s*"([^"]+)"', html)
                types = re.findall(r'\{"t"\s*:\s*"(\w)"', pages_block.group(1))
                log(f'{base} 页面解析命中')
                return m.group(1), title, artists, types
            except Exception as e:
                log(f'{base} 请求失败: {e}，尝试下一个镜像')
        raise CrawlError(f'nhentai 全部入口不可用（主站最后状态 {last_status}，'
                         f'可能被 Cloudflare 封锁数据中心 IP）')
