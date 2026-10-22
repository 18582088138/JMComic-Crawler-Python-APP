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
    "key": "manhuagui",
    "name": "漫画柜",
    "home": "https://www.manhuagui.com",
    "mirrors": [
      "https://www.mhgui.com",
      "https://tw.manhuagui.com"
    ],
    "hint": "漫画链接（manhuagui.com/comic/<id>）或章节链接，默认爬最新几章",
    "color": "#3b82f6",
    "prefix": "mg-",
    "singleChapter": false,
    "batchSize": 20
  },
  {
    "key": "mangabz",
    "name": "漫画BZ",
    "home": "https://www.mangabz.com",
    "mirrors": [],
    "hint": "漫画链接（mangabz.com 详情页或章节页），默认第 1 批 20 章",
    "color": "#f97316",
    "prefix": "mbz-",
    "singleChapter": false,
    "batchSize": 20
  },
  {
    "key": "mheee",
    "name": "漫画人",
    "home": "https://mheee.com",
    "mirrors": [],
    "hint": "漫画链接（mheee.com 详情页或章节页），默认第 1 批 20 章",
    "color": "#14b8a6",
    "prefix": "mhe-",
    "singleChapter": false,
    "batchSize": 20
  },
  {
    "key": "baozimh",
    "name": "包子漫画",
    "home": "https://www.baozimh.com",
    "mirrors": [],
    "hint": "漫画链接（baozimh.com 详情页或章节页），默认第 1 批 20 章",
    "color": "#eab308",
    "prefix": "bz-",
    "singleChapter": false,
    "batchSize": 20
  },
  {
    "key": "mkzhan",
    "name": "漫客栈",
    "home": "https://www.mkzhan.com",
    "mirrors": [],
    "hint": "漫画链接（mkzhan.com 详情页或章节页），默认第 1 批 20 章",
    "color": "#8b5cf6",
    "prefix": "mkz-",
    "singleChapter": false,
    "batchSize": 20
  },
  {
    "key": "kuaikan",
    "name": "快看漫画",
    "home": "https://www.kuaikanmanhua.com",
    "mirrors": [],
    "hint": "漫画链接（kuaikanmanhua.com/web/comic/<id>），默认第 1 批 20 章",
    "color": "#ffd12e",
    "prefix": "kk-",
    "singleChapter": false,
    "batchSize": 20
  },
  {
    "key": "copymanga",
    "name": "拷贝漫画",
    "home": "https://www.mangacopy.com",
    "mirrors": [],
    "hint": "漫画链接（mangacopy.com/comic/<path_word>）、path_word 或直接输漫画名搜索",
    "color": "#22c55e",
    "prefix": "cm-",
    "singleChapter": false,
    "batchSize": 20
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
    "key": "bika",
    "name": "哔咔漫画",
    "home": "https://www.picacomic.com",
    "mirrors": [],
    "hint": "无网页版：漫画ID 需从哔咔 App 分享链接获取（24位十六进制），默认爬最新几章",
    "color": "#f472b6",
    "prefix": "bk-",
    "singleChapter": false,
    "batchSize": 20
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
