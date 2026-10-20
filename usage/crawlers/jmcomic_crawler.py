"""
JMComic（禁漫天堂）爬虫

与其余站点不同：下载引擎复用 jmcomic 库本体（整本下载、章节目录由 dir_rule 决定），
因此覆写 crawl() 而不是逐章下载。
"""
import os
import re

from .base import CrawlerBase, CrawlError, log, strip_html

OPTION_FILE = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '../../assets/option/option_pages_build.yml'))


class JmcomicCrawler(CrawlerBase):
    site = 'jmcomic'
    name = 'JMComic'
    id_prefix = ''           # 历史约定：JM 车号纯数字无前缀
    home = 'https://18comic.vip'
    hint = '本子链接（18comic.vip/album/<车号>）或直接输入车号'
    color = '#ff5c8a'
    url_patterns = [
        r'18comic\.\w+/(?:album|photo)/',
        r'\bjm\s*[:：]?\s*\d+\b',
    ]
    max_limit = 999          # JM 按整本下载，limit 不适用

    def parse_target(self, text):
        text = (text or '').strip()
        if not text:
            raise ValueError('输入为空，无法解析本子ID')
        if text.isdigit():
            return text
        m = re.fullmatch(r'(?i)jm\s*(\d+)', text)
        if m:
            return m.group(1)
        for pattern in (r'/album/(\d+)', r'/photo/(\d+)', r'[?&]id=(\d+)',
                        r'(?i)\[jm\]\s*(\d+)', r'(?i)\bjm\s*[:：]?\s*(\d+)\b',
                        r'\b(\d{3,})\b'):
            m = re.search(pattern, text)
            if m:
                return m.group(1)
        from jmcomic import JmcomicText
        return JmcomicText.parse_to_jm_id(text)

    def fetch_meta(self, target, limit):
        raise NotImplementedError('JMComic 走整本下载，见 crawl()')

    def get_chapter_image_urls(self, chapter_ref):
        raise NotImplementedError('JMComic 走整本下载，见 crawl()')

    def crawl(self, target_text, work_dir, limit=None):
        aid = self.parse_target(target_text)
        impl = os.getenv('CLIENT_IMPL', '').strip()

        from jmcomic import create_option
        option = create_option(OPTION_FILE)
        if impl in ('api', 'html'):
            option.client.impl = impl

        username = os.getenv('JM_USERNAME', '').strip()
        password = os.getenv('JM_PASSWORD', '').strip()
        if username and password:
            option.plugins.setdefault('after_init', []).append({
                'plugin': 'login',
                'kwargs': {'username': username, 'password': password},
            })
            log('已启用登录插件')

        log(f'开始下载本子: JM{aid}')
        from jmcomic import download_album
        album, _dler = download_album(aid, option)

        raw_dir = option.dir_rule.decide_album_root_dir(album)
        log(f'下载完成: 《{album.name}》，原始目录: {raw_dir}')
        return self.album_to_meta(album), raw_dir

    def album_to_meta(self, album):
        chapters = []
        for pid, pindex, pname in album.episode_list:
            chapters.append({
                'photo_id': str(pid),
                'dir_name': str(pindex),  # dir_rule 为 Bd_Pindex，章节目录名即 Pindex
                'title': str(pname).strip(),
            })
        return {
            'id': str(album.album_id),
            'site': self.site,
            'title': album.name,
            'authors': list(album.authors),
            'tags': [str(t) for t in album.tags],
            'description': strip_html(getattr(album, 'description', '')),
            'chapters': chapters,
        }
