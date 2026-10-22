"""
爬虫基类与统一数据契约（v2：批次化 + 章节合并）

契约（与站点构建管线 site_builder 对接）：
    meta = {
        'id': 'md-xxx',          # 书架唯一ID（带站点前缀，JM 为纯数字车号）
        'site': 'mangadex',      # 站点 key（registry 中的注册名）
        'title': str,
        'authors': [str],
        'tags': [str],
        'description': str,
        'sourceUrl': str,        # 作品在源站的详情页（前端「抓更多/源站查看」用）
        'totalChapters': int,    # 作品总章节数（书架显示 已上架 x/N）
        'chapters': [            # 本批次章节，按阅读顺序
            {
                'photo_id': str,   # 章节稳定 key（站点内唯一、跨爬取不变）
                'dir_name': str,   # raw_dir 下的目录名（与 photo_id 相同）
                'title': str,
                'order': float,    # 章节在整部作品中的位置（合并排序键）
            },
        ],
    }
    raw_dir/<dir_name>/ 下为该章图片文件（文件名需可自然排序）

多话作品按「一本」管理：同一作品的多次批次爬取在 site_builder 里按
photo_id 合并章节（图片存 comics/<id>/images/<photo_id>/），书架只出现一个条目。

子类最小实现：
    parse_target(text)              解析用户输入 → 规范化目标字符串
    fetch_meta(target, batch)       拉取元数据 + 本批次章节（batch 从 1 开始，每批 batch_size 章）
    get_chapter_image_urls(ref)     返回某章图片 URL 列表
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
    batch_size = 20      # 每批章节数
    single_chapter = False   # True 表示整本单章（如 nhentai），批次不生效

    def __init__(self, session=None):
        self.session = session or make_session()

    # ---- 子类实现 ----
    def parse_target(self, text):
        """解析用户输入 → 规范化目标字符串；无法识别抛 ValueError"""
        raise NotImplementedError

    def fetch_meta(self, target, batch):
        """返回 meta（见模块 docstring 契约），chapters 为本批次、按阅读顺序"""
        raise NotImplementedError

    def get_chapter_image_urls(self, chapter_ref):
        """返回某章图片 URL 列表。chapter_ref 为 fetch_meta 塞入 chapter['ref'] 的内容"""
        raise NotImplementedError

    # ---- 批次 ----
    def parse_batch(self, batch):
        try:
            b = int(batch or 1)
        except (TypeError, ValueError):
            b = 1
        return max(1, b)

    def slice_batch(self, chapters, batch):
        """chapters 为全量候选（阅读顺序），切出本批次"""
        if self.single_chapter:
            return chapters[:1]
        start = (batch - 1) * self.batch_size
        return chapters[start:start + self.batch_size]

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
    def crawl(self, target_text, work_dir, batch=1):
        """统一入口：解析 → 拉元数据 → 逐章下载 → (meta, work_dir)"""
        batch = self.parse_batch(batch)
        target = self.parse_target(target_text)
        log(f'{self.name}: 目标 {target}，批次 第{batch}批（{self.batch_size}章/批）')
        meta = self.fetch_meta(target, batch)
        meta.setdefault('site', self.site)
        mkdir(work_dir)
        for i, ch in enumerate(meta['chapters'], 1):
            urls = self.get_chapter_image_urls(ch.get('ref'))
            if not urls:
                raise CrawlError(f'{self.name}《{meta["title"]}》第 {i} 章没有取到图片')
            log(f'{self.name}《{meta["title"]}》{ch["title"]}: {len(urls)} 页')
            self.download_chapter_images(urls, os.path.join(work_dir, ch['dir_name']))
        return meta, work_dir

    @classmethod
    def match(cls, text):
        """输入是否属于本站（auto 识别）"""
        text = (text or '').strip()
        return any(re.search(p, text, re.IGNORECASE) for p in cls.url_patterns)

    # ---- 元数据辅助 ----
    def make_meta(self, sid, title, authors, tags, description, chapters,
                  total=None, source_url=''):
        """chapters: [{'photo_id','title','order'?,'ref'?}]；dir_name 取 photo_id（稳定 key）"""
        out_chapters = []
        for i, c in enumerate(chapters):
            photo_id = str(c.get('photo_id', i + 1))
            out_chapters.append({
                'photo_id': photo_id,
                'dir_name': photo_id,
                'title': c.get('title') or f'第{i + 1}章',
                'order': float(c.get('order', i + 1)),
                **({'ref': c['ref']} if c.get('ref') is not None else {}),
            })
        return {
            'id': f'{self.id_prefix}{sid}',
            'site': self.site,
            'title': title or '未命名',
            'authors': authors or ['未知'],
            'tags': tags or [],
            'description': description or '',
            'sourceUrl': source_url,
            'totalChapters': int(total if total is not None else len(out_chapters)),
            'chapters': out_chapters,
        }
