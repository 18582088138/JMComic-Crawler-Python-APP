"""
爬虫基类与统一数据契约

契约（与站点构建管线 site_builder 对接）：
    meta = {
        'id': 'md-xxx',          # 书架唯一ID（带站点前缀，JM 为纯数字车号）
        'site': 'mangadex',      # 站点 key（registry 中的注册名）
        'title': str,
        'authors': [str],
        'tags': [str],
        'description': str,
        'chapters': [            # 按阅读顺序排列
            {'photo_id': str, 'dir_name': '1', 'title': str},
        ],
    }
    raw_dir/<dir_name>/ 下为该章图片文件（文件名需可自然排序）

子类最小实现：
    parse_target(text)  解析用户输入（链接/ID）→ 规范化目标字符串
    fetch_meta(target, limit)  拉取元数据，返回 meta（不含图片）
    get_chapter_image_urls(chapter_ref)  返回某章图片 URL 列表
基类模板方法 crawl() 负责逐章下载与目录组织；
下载逻辑特殊的站点（如 JMComic 复用 jmcomic 库）可直接覆写 crawl()。
"""
import os
import re
import time


def log(msg):
    print(f'[crawler] {msg}', flush=True)


def mkdir(p):
    os.makedirs(p, exist_ok=True)
    return p


def natural_key(name):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', name)]


def strip_html(text):
    return re.sub(r'<[^>]+>', '', text or '').strip()


def make_session():
    """统一使用 curl_cffi（jmcomic 的依赖，自带浏览器指纹可过部分 Cloudflare）"""
    from curl_cffi import requests as cffi_requests
    return cffi_requests.Session(impersonate='chrome')


class CrawlError(RuntimeError):
    """爬取失败（向用户展示原因）"""


class CrawlerBase:
    # ---- 站点描述（子类必填）----
    site = ''            # 注册 key，如 'mangadex'
    name = ''            # 显示名，如 'MangaDex'
    id_prefix = ''       # 书架 ID 前缀，如 'md-'（JMComic 例外：纯数字无前缀）
    home = ''            # 站点主页（找本子跳转）
    mirrors = []         # 备用域名（找本子页展示，主域名被墙时用）
    hint = ''            # 输入提示（前端占位符/帮助）
    color = '#888888'    # 站点主题色（前端站点徽章）
    url_patterns = []    # re 正则列表，命中任一即认为该输入属于本站（auto 识别用）
    default_limit = 3    # 默认爬取章节数
    max_limit = 10       # 单次章节上限，防止把书库撑爆
    single_chapter = False   # True 表示整本单章（如 nhentai），limit 不生效

    def __init__(self, session=None):
        self.session = session or make_session()

    # ---- 子类实现 ----
    def parse_target(self, text):
        """解析用户输入 → 规范化目标字符串；无法识别抛 ValueError"""
        raise NotImplementedError

    def fetch_meta(self, target, limit):
        """返回 meta（见模块 docstring 契约），chapters 不含图片"""
        raise NotImplementedError

    def get_chapter_image_urls(self, chapter_ref):
        """返回某章图片 URL 列表。chapter_ref 为 fetch_meta 塞入 chapter['ref'] 的内容"""
        raise NotImplementedError

    # ---- 下载细节（可按站点覆写）----
    image_headers = None     # 额外请求头
    image_referer = None     # Referer
    download_delay = 0.2     # 每张图间隔

    def save_image(self, url, path):
        headers = dict(self.image_headers or {})
        if self.image_referer:
            headers['Referer'] = self.image_referer
        resp = self.session.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        with open(path, 'wb') as f:
            f.write(resp.content)
        return os.path.getsize(path)

    def download_chapter_images(self, image_urls, dest_dir):
        mkdir(dest_dir)
        total = 0
        for i, url in enumerate(image_urls, 1):
            ext = os.path.splitext(url.split('?')[0])[1].lower() or '.jpg'
            if len(ext) > 5:
                ext = '.jpg'
            path = os.path.join(dest_dir, f'{i:05d}{ext}')
            if not os.path.exists(path):
                total += self.save_image(url, path)
                time.sleep(self.download_delay)
        return total

    # ---- 模板方法 ----
    def crawl(self, target_text, work_dir, limit=None):
        """统一入口：解析 → 拉元数据 → 逐章下载 → (meta, work_dir)"""
        limit = self.clamp_limit(limit)
        target = self.parse_target(target_text)
        log(f'{self.name}: 目标 {target}，章节数 {limit}')
        meta = self.fetch_meta(target, limit)
        meta.setdefault('site', self.site)
        mkdir(work_dir)
        for i, ch in enumerate(meta['chapters'], 1):
            urls = self.get_chapter_image_urls(ch.get('ref'))
            if not urls:
                raise CrawlError(f'{self.name}《{meta["title"]}》第 {i} 章没有取到图片')
            log(f'{self.name}《{meta["title"]}》{ch["title"]}: {len(urls)} 页')
            self.download_chapter_images(urls, os.path.join(work_dir, ch['dir_name']))
        return meta, work_dir

    def clamp_limit(self, limit):
        if self.single_chapter:
            return 1
        try:
            limit = int(limit or self.default_limit)
        except (TypeError, ValueError):
            limit = self.default_limit
        return max(1, min(limit, self.max_limit))

    @classmethod
    def match(cls, text):
        """输入是否属于本站（auto 识别）"""
        text = (text or '').strip()
        return any(re.search(p, text, re.IGNORECASE) for p in cls.url_patterns)

    # ---- 元数据辅助 ----
    def make_meta(self, sid, title, authors, tags, description, chapters):
        """chapters: [{'photo_id','title','ref'?}]，dir_name 自动按顺序编号"""
        return {
            'id': f'{self.id_prefix}{sid}',
            'site': self.site,
            'title': title or '未命名',
            'authors': authors or ['未知'],
            'tags': tags or [],
            'description': description or '',
            'chapters': [
                {'photo_id': str(c.get('photo_id', i + 1)), 'dir_name': str(i + 1),
                 'title': c.get('title') or f'第{i + 1}章',
                 **({'ref': c['ref']} if c.get('ref') is not None else {})}
                for i, c in enumerate(chapters)
            ],
        }
