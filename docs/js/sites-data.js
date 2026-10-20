// 本文件由 usage/export_sites.py 自动生成，请勿手改
// 数据源：usage/crawlers/registry.py 注册的站点
window.SITES = [
  {
    "key": "mangadex",
    "name": "MangaDex",
    "home": "https://mangadex.org",
    "hint": "漫画链接（mangadex.org/title/<id>）或章节链接，也支持直接填 id",
    "color": "#ff6740",
    "prefix": "md-",
    "singleChapter": false,
    "defaultLimit": 3,
    "maxLimit": 10
  },
  {
    "key": "nhentai",
    "name": "nhentai",
    "home": "https://nhentai.net",
    "hint": "画廊号或链接（nhentai.net/g/<id>），整本单章",
    "color": "#ec2753",
    "prefix": "nh-",
    "singleChapter": true,
    "defaultLimit": 3,
    "maxLimit": 10
  },
  {
    "key": "manhuagui",
    "name": "漫画柜",
    "home": "https://www.manhuagui.com",
    "hint": "漫画链接（manhuagui.com/comic/<id>）或章节链接，默认爬最新几章",
    "color": "#3b82f6",
    "prefix": "mg-",
    "singleChapter": false,
    "defaultLimit": 3,
    "maxLimit": 10
  },
  {
    "key": "dmzj",
    "name": "动漫之家",
    "home": "https://manhua.dmzj.com",
    "hint": "漫画链接（manhua.dmzj.com/<拼音名>）或数字漫画ID，默认爬最新几章",
    "color": "#0ea5e9",
    "prefix": "dz-",
    "singleChapter": false,
    "defaultLimit": 3,
    "maxLimit": 10
  },
  {
    "key": "wnacg",
    "name": "wnacg",
    "home": "https://www.wnacg.com",
    "hint": "画廊链接（wnacg.com/photos-index-aid-<id>.html）或数字ID，整本单章",
    "color": "#a855f7",
    "prefix": "wn-",
    "singleChapter": true,
    "defaultLimit": 3,
    "maxLimit": 10
  },
  {
    "key": "ehentai",
    "name": "E-Hentai",
    "home": "https://e-hentai.org",
    "hint": "画廊链接（e-hentai.org/g/<gid>/<token>），整本单章",
    "color": "#ede0d4",
    "prefix": "eh-",
    "singleChapter": true,
    "defaultLimit": 3,
    "maxLimit": 10
  },
  {
    "key": "bika",
    "name": "哔咔漫画",
    "home": "https://www.picacomic.com",
    "hint": "漫画ID（24位十六进制，App 分享链接里可找到），默认爬最新几章",
    "color": "#f472b6",
    "prefix": "bk-",
    "singleChapter": false,
    "defaultLimit": 3,
    "maxLimit": 10
  },
  {
    "key": "jmcomic",
    "name": "JMComic",
    "home": "https://18comic.vip",
    "hint": "本子链接（18comic.vip/album/<车号>）或直接输入车号",
    "color": "#ff5c8a",
    "prefix": "",
    "singleChapter": false,
    "defaultLimit": 3,
    "maxLimit": 10
  }
];
