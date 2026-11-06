// 本文件由 usage/export_sites.py 自动生成，请勿手改
// 数据源：usage/crawlers/registry.py 注册的站点
window.SITES = [
  {
    "key": "mangadex",
    "name": "MangaDex",
    "home": "https://mangadex.org",
    "mirrors": [],
    "hint": "漫画链接（mangadex.org/title/<id>）或章节链接，也支持直接填 id",
    "color": "#ff6740",
    "prefix": "md-",
    "singleChapter": false,
    "batchSize": 20
  },
  {
    "key": "nhentai",
    "name": "nhentai",
    "home": "https://nhentai.net",
    "mirrors": [
      "https://nhentai.to",
      "https://nhentai.xxx"
    ],
    "hint": "画廊号或链接（nhentai.net/g/<id>），整本单章",
    "color": "#ec2753",
    "prefix": "nh-",
    "singleChapter": true,
    "batchSize": 1
  },
  {
    "key": "mkzhan",
    "name": "漫客栈",
    "home": "https://www.mkzhan.com",
    "mirrors": [],
    "hint": "漫画链接（mkzhan.com/<数字ID>/ 或章节页链接）；目录接口需登录，详情页默认抓第一章，更多章节请直接贴章节页链接",
    "color": "#8b5cf6",
    "prefix": "mkz-",
    "singleChapter": false,
    "batchSize": 20
  },
  {
    "key": "telegraph",
    "name": "Telegra",
    "home": "https://telegra.ph",
    "mirrors": [],
    "hint": "文章链接（telegra.ph/<标题>-<日期>），整篇单章",
    "color": "#37aee2",
    "prefix": "tg-",
    "singleChapter": true,
    "batchSize": 1
  },
  {
    "key": "wnacg",
    "name": "wnacg",
    "home": "https://www.wnacg.com",
    "mirrors": [
      "https://www.wnacg.org"
    ],
    "hint": "画廊链接（wnacg.com/photos-index-aid-<id>.html）或数字ID，整本单章",
    "color": "#a855f7",
    "prefix": "wn-",
    "singleChapter": true,
    "batchSize": 1
  },
  {
    "key": "ehentai",
    "name": "E-Hentai",
    "home": "https://e-hentai.org",
    "mirrors": [],
    "hint": "画廊链接（e-hentai.org/g/<gid>/<token>），整本单章",
    "color": "#ede0d4",
    "prefix": "eh-",
    "singleChapter": true,
    "batchSize": 1
  },
  {
    "key": "jmcomic",
    "name": "JMComic",
    "home": "https://18comic.vip",
    "mirrors": [],
    "hint": "本子链接（18comic.vip/album/<车号>）或直接输入车号",
    "color": "#ff5c8a",
    "prefix": "",
    "singleChapter": false,
    "batchSize": 20
  }
];
