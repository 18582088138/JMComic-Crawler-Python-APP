"""
多平台漫画爬虫框架

每个站点一个独立模块，继承 base.CrawlerBase；
registry 负责站点注册、URL 自动识别、兜底分发。

新增站点步骤：
    1. 在本目录新建 <site>.py，实现一个 CrawlerBase 子类
    2. 在 registry.CRAWLER_CLASSES 中登记
    3. 前端「找本子」站点按钮由 export_sites.py 自动生成，无需手改
"""
from .registry import (  # noqa: F401
    CrawlerBase,
    GenericCrawler,
    detect_crawler,
    get_crawler,
    get_generic,
    registered_sites,
    crawl,
)
