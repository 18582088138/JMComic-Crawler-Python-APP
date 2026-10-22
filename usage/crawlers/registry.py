"""
站点注册表与分发

- detect_crawler(text)：按 url_patterns 自动识别输入属于哪个站点；
  纯数字默认归 JMComic（历史约定；nhentai 等数字ID站点请贴链接或显式指定平台）。
- crawl(source, ...)：source='auto' 时自动识别，识别不到走通用兜底。
- 动漫之家已下架（站点失效）；拷贝漫画已恢复注册（mangacopy API 域名更新）。
"""
from .base import CrawlerBase, CrawlError, log
from .bika import BikaCrawler
from .copymanga import CopymangaCrawler
from .ehentai import EhentaiCrawler
from .generic import GenericCrawler, GenericCrawlFailed
from .jmcomic_crawler import JmcomicCrawler
from .kuaikan import KuaikanCrawler
from .mangadex import MangadexCrawler
from .manhuagui import ManhuaguiCrawler
from .nhentai import NhentaiCrawler
from .simple_cms import BaozimhCrawler, MangabzCrawler, MheeeCrawler, MkzhanCrawler
from .wnacg import WnacgCrawler

# 注册顺序即 auto 识别优先级（JM 的数字车号兜底放最后单独处理）
CRAWLER_CLASSES = [
    MangadexCrawler,
    NhentaiCrawler,
    ManhuaguiCrawler,
    MangabzCrawler,
    MheeeCrawler,
    BaozimhCrawler,
    MkzhanCrawler,
    KuaikanCrawler,
    CopymangaCrawler,
    WnacgCrawler,
    EhentaiCrawler,
    BikaCrawler,
    JmcomicCrawler,
]

CRAWLERS = {cls.site: cls for cls in CRAWLER_CLASSES}


def get_crawler(site, session=None):
    cls = CRAWLERS.get(site)
    if not cls:
        raise ValueError(f'未知平台: {site}（可选: {", ".join(CRAWLERS)} / auto）')
    return cls(session=session)


def get_generic(session=None):
    return GenericCrawler(session=session)


def detect_crawler(text, session=None):
    """自动识别输入属于哪个站点；识别不到返回通用兜底爬虫"""
    text = (text or '').strip()
    if not text:
        raise ValueError('输入为空')
    # 纯数字沿用历史约定归 JMComic
    if text.isdigit():
        return JmcomicCrawler(session=session)
    for cls in CRAWLER_CLASSES:
        if cls.match(text):
            return cls(session=session)
    log('未识别出目标站点，使用通用兜底爬虫')
    return GenericCrawler(session=session)


def crawl(source, target, work_dir, batch=1, session=None):
    """统一爬取入口。source 为注册 key 或 'auto'；batch 从 1 开始（20章/批）"""
    crawler = detect_crawler(target, session=session) if source == 'auto' else get_crawler(source, session=session)
    meta, raw_dir = crawler.crawl(target, work_dir, batch)
    meta.setdefault('site', crawler.site)
    return meta, raw_dir, crawler


def registered_sites():
    """导出站点描述（供 export_sites.py 生成前端站点配置）"""
    sites = []
    for cls in CRAWLER_CLASSES:
        sites.append({
            'key': cls.site,
            'name': cls.name,
            'home': cls.home,
            'mirrors': list(cls.mirrors),
            'hint': cls.hint,
            'color': cls.color,
            'prefix': cls.id_prefix,
            'singleChapter': cls.single_chapter,
            'batchSize': 1 if cls.single_chapter else cls.batch_size,
        })
    return sites
