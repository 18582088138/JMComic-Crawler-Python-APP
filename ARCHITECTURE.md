# 云书架 · 架构文档

> 本文档梳理「GitHub Actions + GitHub Pages 多平台云书架」子系统的代码结构与核心设计，
> 不涉及 jmcomic 库本体（`src/jmcomic/`，那是上游的下载引擎）。

## 1. 系统总览

```
┌──────────────────────┐        ┌───────────────────────────┐
│  手机/Pad/PC 浏览器   │        │  GitHub 云端               │
│                      │        │                            │
│  gh-pages 静态站点    │  触发   │  Actions Runner (Ubuntu)   │
│  ├─ index.html 四分页 │dispatch│  ┌──────────────────────┐  │
│  │  爬取/找本子/书架  │───────▶│  │ workflow_pages_build │  │
│  │  /设置            │ 或     │  │  └ crawlers 框架      │  │
│  ├─ reader.html 阅读器│ Issue  │  │    每站一个爬虫类     │  │
│  ├─ sw.js 离线缓存   │        │  └─────────┬────────────┘  │
│  └─ comics/ 图片/zip │◀───────│            │                │
└──────────▲───────────┘ 静态托管│  data/<site> 分支（每站一个）│
           │                     │  gh-pages 分支（装配产物）   │
           └─────────────────────│  均单 commit force-push    │
                                  └───────────────────────────┘
```

**职责划分**：浏览器只做"遥控器 + 阅读器"；爬取全在 Actions 云端（境外机房，天然绕过源站地区限制）；
数据按站点存孤立分支，gh-pages 只做装配与托管。

## 2. 分支模型（P0：git 历史不膨胀）

| 分支 | 内容 | 写入方式 |
| --- | --- | --- |
| `master` | 代码 + 前端源文件（`docs/` 不含数据） | 正常开发提交 |
| `data/<site>` | 每站一个孤立分支：`comics/<id>/` + `index.json` | 每次爬取后单 commit **force-push** |
| `gh-pages` | Pages 源：前端 + 全站 `comics/` 合并 + `data/*.json` 聚合 | 每次爬取/前端部署后单 commit **force-push** |

- 单 commit force-push ⇒ 历史永不积累图片 blob；旧 blob 失去引用后被 GC。
- 相同内容的 blob 在 git 对象库只存一份，data 分支与 gh-pages 不重复占空间。
- 已注册站点：`jmcomic`（JM 纯数字车号）、`mangadex`、`nhentai`、`manhuagui`、`dmzj`、`wnacg`、`ehentai`、`bika`；兜底 `generic` 不落分支。
- 书架 ID 带站点前缀（`md-`/`nh-`/`mg-`/`dz-`/`wn-`/`eh-`/`bk-`），JM 沿用纯数字，互不冲突。
- **装配是增量的**：每次爬取只向 gh-pages 复制本次构建的那本（下架目录照常删除），
  不整站复制；全量校准用 `usage/reassemble_pages.py`
  （pages_deploy.yml 的 `REASSEMBLE=true` 触发，用于修复装配中途失败等异常）。
- **保留策略（30/宽限 10）**：正常爬取只在该站超过 `KEEP+10` 时硬删；
  超出 `KEEP` 未满 `KEEP+10` 的本子由书架页横幅提示「下载或清理」，
  用户确认后 dispatch `TARGET=__cleanup__`（Token 或 issue `[jm] __cleanup__` 均可）
  对所有站点按 KEEP 精确清理。

**已知限制**：GITHUB_TOKEN 推送 gh-pages **不会自动触发 Pages 构建**（GitHub 限制），
两个工作流都在推送后显式 `POST /repos/{repo}/pages/builds` 请求部署（含失败路径：
`last_run.json`/`wishlist.json` 是 Contents API 直写 gh-pages 的，同样需要部署才可见）。
gh-pages 装配需拉取全量工作区（随总量增长会变慢）；数据涨到 GB 级后可改
`--filter=blob:none` + sparse-checkout 优化。

## 3. 文件结构与职责

### 后端（master）

| 文件 | 职责 |
| --- | --- |
| `usage/crawlers/base.py` | 爬虫基类：统一 meta 契约、模板方法 `crawl()`、图片下载 |
| `usage/crawlers/registry.py` | 站点注册表 + `detect_crawler()` 自动识别 + 统一入口 `crawl()` |
| `usage/crawlers/<site>.py` | 每站一个爬虫类（jmcomic/mangadex/nhentai/manhuagui/dmzj/wnacg/ehentai/bika；copymanga 保留未注册） |
| `usage/crawlers/generic.py` | 通用兜底：script JSON 数组 / img 目录分组两种启发式；失败抛 `GenericCrawlFailed` |
| `usage/site_builder.py` | 站点数据构建：图片/封面/manifest/zip/index.json upsert/retention |
| `usage/branch_store.py` | 分支存储层：worktree 打开/单 commit force-push/gh-pages 装配/Contents API 小文件 |
| `usage/workflow_pages_build.py` | 主入口编排：解析输入 → 识别站点 → 爬取 → 构建 → 发布 → 状态上报 |
| `usage/deploy_frontend.py` | 前端独立部署（docs/** 变更时由 pages_deploy.yml 调起） |
| `usage/migrate_to_branches.py` | 一次性迁移脚本（master docs/comics → 孤立分支，已执行，留档） |
| `usage/export_sites.py` | 从注册表生成 `docs/js/sites-data.js`（前后端站点列表单一事实源） |
| `assets/option/option_pages_build.yml` | JMComic 下载配置（api 客户端免登录） |

### 前端（master `docs/`，随装配进 gh-pages）

| 文件 | 职责 |
| --- | --- |
| `index.html` + `css/app.css` | 四分页外壳：爬取/找本子/书架/设置；<768px 底部标签栏，≥768px 左侧导航轨 |
| `js/app.js` | hash 路由（`#/crawl` 等）+ 启动 |
| `js/view-crawl.js` | 爬取页：平台自动识别 chip、手动指定、任务提交（Token dispatch / Issue 兜底）、进度跟踪、最近失败展示 |
| `js/view-find.js` | 找本子页：每站一张卡片跳转站点主页 |
| `js/view-shelf.js` | 书架页：站点筛选、喜欢置顶、分页（12/页）、阅读/ZIP 下载、离线角标 |
| `js/view-settings.js` | 设置页：仓库/Token/检查清单/愿望清单 |
| `js/task.js` / `js/github.js` / `js/util.js` | 任务状态机 / GitHub API / 工具 |
| `js/sites-data.js` | **由 export_sites.py 生成，勿手改** |
| `reader.html` + `css/reader.css` + `js/reader.js` | 阅读器：卷轴/翻页双模式、进度记忆、章节跳转、全本离线缓存、本地 ZIP 解包 |
| `sw.js` | Service Worker：外壳 SWR、`comics/*` cache-first、`data/*` network-first |
| `vendor/jszip.min.js` | 离线 ZIP 解包依赖 |

## 4. 核心链路

### 4.1 爬取管线

```
输入(任意站点链接/ID) → detect_crawler 自动识别(或显式 source)
  → crawler.crawl() → meta + raw_dir          （JM 复用 jmcomic 库整本下载）
  → site_builder.build_site() → data/<site> worktree:
        comics/<id>/images/<章>/NNN.<原后缀>
        comics/<id>/cover.jpg   (首页缩到480宽)
        comics/<id>/manifest.json
        comics/<id>/<id>.zip    (ZIP_STORED；JM 沿用 JM<id>.zip 命名)
        index.json              (该站索引 upsert，最新在前)
        retention: 每站超出 KEEP_LATEST 的旧本子整目录删除
  → data/<site> 单 commit force-push
  → gh-pages 装配：同步前端 + 同步该站 comics + 聚合 data/comics.json
  → gh-pages 单 commit force-push → POST /pages/builds → 部署
```

### 4.2 任务提交（三条通路，同旧版）

1. **一键创建经典 PAT（推荐）**：设置页按钮直达预填 `public_repo` 的创建页，30 秒完成。
2. **Fine-grained PAT**：权限可窄到单仓库 Actions 读写，需手动勾选。
3. **Issue 兜底**：`[jm] <目标>` 标题触发 `issues` 事件，成功自动回评关单，失败也回评。
   正文支持 `mode:/source:/limit:/keep:` 覆盖项。
> 已否决：OAuth Device Flow（`github.com/login/*` 端点无 CORS，纯前端不可行）。

### 4.3 状态与愿望清单（P1）

- 每次构建（无论成败）经 Contents API 写 `data/last_run.json`（状态/错误/运行链接），
  爬取页直接展示最近一次失败原因，不用翻 Actions 日志。
- 通用兜底失败时把域名追加进 `data/wishlist.json`（按域名去重，上限 100），
  设置页「愿望清单」展示，作为新平台适配候选。

### 4.4 离线（P3）

- SW 安装时缓存应用外壳；`comics/*` 图片 cache-first（看过即缓存）。
- 阅读器「缓存全本」按钮：并发 4 路预取全本图片 → SW 拦截入缓存 →
  完成后写 `jm_offline_<id>`，书架显示「已离线」角标，断网可读。
- 页面导航 network-first；sw.js 的 VERSION 由装配时按前端内容哈希自动注入
  （`branch_store.bump_sw_version`），任何前端变化都会产生新 SW；
  页面监听 `controllerchange` 自动刷新一次——旧缓存 JS 不会再残留服役
  （2026-10-22 旧 JS dispatch payload 持续 422 的根治措施）。

## 5. 运维状态与排障速查

**仓库设置（已配置好，勿重复）**：Pages 源 = `gh-pages /(root)`；
Actions Workflow permissions = write；Issues = 开启。

**本地测试**：
- 构建 mock：`JM_PAGES_MOCK=1 JM_PAGES_INPUT=<id> JM_PAGES_SOURCE=mangadex python usage/workflow_pages_build.py`
  （写入 ./docs 旧布局，不碰分支；docs/comics、docs/data 已 gitignore）
- 只构建不推分支（真实爬取）：`JM_PAGES_PUBLISH=0 ...`
- 页面调试：`cd docs && python -m http.server 8901`，iframe 嵌套可模拟手机视口
- 注册表变更后跑 `python usage/export_sites.py` 重新生成前端站点配置
- 本机无法访问禁漫域名与多数漫画站（真实爬取只能推到 Actions 验证）；MangaDex、pypi、unpkg 可通，jsdelivr 被墙

**排障速查**：
- `usage_log` 插件依赖 `psutil`，工作流需单独 `pip install psutil`
- 本子原图多为 `.webp`，manifest 保留原后缀，手写测试 URL 勿想当然用 `.jpg`
- 统一用 `curl_cffi`（jmcomic 依赖、过 Cloudflare）；`requests` 不在依赖里
- 「下载JM本子 (commit/dispatch)」两个上游工作流已通过 API 禁用
  （用户只用云书架；且历史重写 force-push 会把所有 path 过滤工作流误触发一次）
- MangaDex 语言过滤只接受 `zh/zh-hk/en/ja`；部分热门作品是外部托管章节抓不到图（报"没有找到可下载的章节"）
- nhentai 主站封数据中心 IP，靠镜像 `nhentai.to/.xxx` 回退
- **新站点（漫画柜/动漫之家/wnacg/E-Hentai/哔咔）按公开爬虫通行做法编写，本地被墙未逐站实测**；
  哪个站报错先看 Actions 日志，多半是页面结构/API 变动，改对应 crawler 即可
- GITHUB_TOKEN 推 gh-pages 不触发 Pages 构建，必须显式 POST /pages/builds
- **workflow_dispatch 的 inputs 值必须全部是字符串**：传数字 GitHub 返回 422
  `Invalid value for input 'KEEP_LATEST'`（2026-10-21 前端全站 422 的根因）
- 纯数字输入一律按 JMComic 车号处理（历史约定）；nhentai 等数字 ID 站点请贴链接或显式选平台

## 6. Roadmap

- ~~P0 仓库体积~~：已解决（孤立分支 + 单 commit force-push，见 §2）；
  **2026-10-21 master 历史已用 filter-repo 重写**（`--path docs/comics --path docs/data
  --invert-paths --refs master`），旧图片 blob 已脱离引用，GitHub 端 GC 后体积回落
- ~~P1 失败可见性~~：已解决（`data/last_run.json` + 页面展示 + issue 失败回评）
- ~~P2 免 Token 一键授权~~：已否决（Device Flow 无 CORS），以预填 PAT 链接替代
- ~~P3 离线~~：已解决（SW cache-first + 全本缓存按钮）
- 新站点按公开实现编写，待逐站云端实测校准（漫画柜的 packer 解包、dmzj 的 muwai API、
  哔咔的签名、wnacg 的 /data/t/ 变换、E-Hentai 的配额限速是重点观察项）
- 待研究：拷贝漫画 API 稳定后重新启用（代码保留在 `usage/crawlers/copymanga.py`）
