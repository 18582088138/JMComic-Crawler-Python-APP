# 云书架 · 架构文档

> 本文档梳理「GitHub Actions + GitHub Pages 多平台云书架」子系统的代码结构与核心设计，
> 不涉及 jmcomic 库本体（`src/jmcomic/`，那是上游的下载引擎）。
> **V2.0（2026-11）：zip-only 架构**——漫画内容只以 zip/分卷存于 data 分支，
> gh-pages 只装元数据；阅读改为「首读下载到本机 → JSZip 直读」。

## 1. 系统总览

```
┌──────────────────────┐        ┌───────────────────────────┐
│  手机/Pad/PC 浏览器   │        │  GitHub 云端               │
│                      │        │                            │
│  gh-pages 静态站点    │  触发   │  Actions Runner (Ubuntu)   │
│  ├─ index.html 四分页 │dispatch│  ┌──────────────────────┐  │
│  │  爬取/找本子/书架  │───────▶│  │ workflow_pages_build │  │
│  │  /设置            │ 或     │  │ workflow_migrate     │  │
│  ├─ reader.html 阅读器│ Issue  │  │ （zip-only 打包/迁移）│  │
│  ├─ dlstore.js 下载器 │        │  └─────────┬────────────┘  │
│  ├─ sw.js 外壳缓存    │        │            │                │
│  └─ OPFS/FSA 本地存储 │◀───────│  data/<site> 分支（每站一个）│
│      本地 zip 直读    │ raw下载 │  = zip/分卷+manifest+封面   │
└──────────▲───────────┘        │  gh-pages 分支（装配产物）   │
           │                     │  = 前端+manifest/封面+索引   │
           └─────────────────────│  均单 commit force-push    │
                                  └───────────────────────────┘
```

**职责划分**：浏览器只做"遥控器 + 下载器 + 阅读器"；爬取全在 Actions 云端（境外机房，天然绕过源站地区限制）；
漫画内容（zip/分卷）按站点存孤立分支，gh-pages 只做**元数据装配与托管**（MB 级、构建稳定）。

## 2. 分支模型（P0：git 历史不膨胀）

| 分支 | 内容 | 写入方式 |
| --- | --- | --- |
| `master` | 代码 + 前端源文件（`docs/` 不含数据） | 正常开发提交 |
| `data/<site>` | 每站一个孤立分支：`comics/<id>/`（**zip/分卷 + manifest.json + cover.jpg**，V2.0 起不含散图）+ `index.json` | 每次爬取后单 commit **force-push** |
| `gh-pages` | Pages 源：前端 + 各站 **manifest/封面** + `data/*.json` 聚合（V2.0 起不含漫画内容） | 每次爬取/前端部署后单 commit **force-push** |

- 单 commit force-push ⇒ 历史永不积累图片 blob；旧 blob 失去引用后被 GC。
- 相同内容的 blob 在 git 对象库只存一份，data 分支与 gh-pages 不重复占空间。
- **V2.0 zip-only 减重（2026-11）**：v2 每本书散图与 ZIP_STORED 打包互相重复（仓库 6.17GB）；
  v3 只留 zip ⇒ 仓库约减半（~3.5GB）。**zip 放哪个分支不影响仓库体积**（blob 按内容去重），
  gh-pages 瘦身省的是 **Pages 构建健康度**（6.3GB 大站构建不稳 → MB 级秒构建）。
- 已注册站点：`jmcomic`（JM 纯数字车号）、`mangadex`、`nhentai`、`manhuagui`、`dmzj`、`wnacg`、`ehentai`、`bika`；兜底 `generic` 不落分支。
- 书架 ID 带站点前缀（`md-`/`nh-`/`tg-`/`mkz-`/`wn-`/`eh-`），JM 沿用纯数字，互不冲突。
- **爬取/装配解耦**：爬取 run 只推 data 分支，装配为 best-effort（失败仅记日志）；
  pages_deploy 每 15 分钟定时巡检（按 `data/.assembled-<site>` 标记比对 data 分支 HEAD sha，
  无变更零 git 操作），书架页「⚙ 装配」可手动触发。装配失败不再连累爬取队列。
- **任务去重**：爬虫 `predict_id()` 轻量预判书架 ID（不发请求），提交后先查对应
  data 分支索引，已爬完（单章站/章节齐）直接跳过；`FORCE=1`（dispatch 勾选或 issue 正文
  `force: 1`）强制重爬；连载未爬完的重爬属于合并新章节，不拦截。
- **90MB 分卷（V2.0）**：v2 的 100MB 护栏是「超限不打包」（超大书只在线阅读）；
  v3 改为**超限自动分卷** `<id>.part01.zip / .part02.zip…`，manifest.parts 记录每卷
  全局页区间（firstPage/lastPage），阅读器懒加载当前卷——超大本子恢复可读。
- **装配是增量的**：每次爬取只向 gh-pages 复制本次构建那本的 manifest/封面
  （下架目录照常删除），不整站复制；全量校准用 `usage/reassemble_pages.py`
  （pages_deploy.yml 的 `REASSEMBLE=true` 触发，用于修复装配中途失败等异常）。
- **保留策略（30/宽限 10）**：正常爬取只在该站超过 `KEEP+10` 时硬删；
  超出 `KEEP` 未满 `KEEP+10` 的本子由书架页横幅提示「下载或清理」，
  用户确认后 dispatch `TARGET=__cleanup__`（Token 或 issue `[jm] __cleanup__` 均可）
  对所有站点按 KEEP 精确清理。
- **指定删除（__delete__）**：书架「管理」进入选择模式（checkbox 多选/全选/二次确认），
  dispatch `TARGET=__delete__` + `DELETE_IDS`（issue 则标题 `[jm] __delete__` + 正文 `delete: ids`），
  跨 data 分支按 ID 删目录+清索引，gh-pages 同步移除；
  **本地下载的 zip 不随下架删除**（用户资产，设置页本地清单单独管理）。

**已知限制**：GITHUB_TOKEN 推送 gh-pages **不会自动触发 Pages 构建**（GitHub 限制），
两个工作流都在推送后显式 `POST /repos/{repo}/pages/builds` 请求部署（含失败路径：
`last_run.json`/`wishlist.json` 是 Contents API 直写 gh-pages 的，同样需要部署才可见）。
gh-pages 装配需拉取全量工作区（V2.0 起仅 MB 级，不再是瓶颈）。

## 3. 文件结构与职责

### 后端（master）

| 文件 | 职责 |
| --- | --- |
| `usage/crawlers/base.py` | 爬虫基类：统一 meta 契约、模板方法 `crawl()`、图片下载 |
| `usage/crawlers/registry.py` | 站点注册表 + `detect_crawler()` 自动识别 + 统一入口 `crawl()` |
| `usage/crawlers/<site>.py` | 每站一个爬虫类（jmcomic/mangadex/nhentai/manhuagui/mkzhan/wnacg/ehentai/bika；simple_cms 为 CMS 系通用基类；copymanga/mangabz/mheee/baozimh/kuaikan/dmzj 代码保留已下架，见 §6） |
| `usage/crawlers/generic.py` | 通用兜底：script JSON 数组 / img 目录分组两种启发式；失败抛 `GenericCrawlFailed` |
| `usage/site_builder.py` | **v3 zip-only 构建**：zip/分卷打包（旧 zip 流式复用合并）、manifest 指向 zip 内路径、index upsert/retention |
| `usage/branch_store.py` | 分支存储层：worktree 打开/单 commit force-push/**瘦身装配（manifest+封面）**/Contents API 小文件 |
| `usage/workflow_pages_build.py` | 主入口编排：解析输入 → 识别站点 → 爬取 → 构建 → 发布 → 状态上报；`__migrate__` 维护任务接入 |
| `usage/migrate_zip_only.py` | **V2.0 存量迁移**：有 zip 的书重写 manifest pages（zip 原字节不动）、9 本无 zip 书散图重建分卷、删散图；幂等 |
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
| `js/view-shelf.js` | 书架页：站点筛选、喜欢置顶、分页（12/页）、满库清理横幅 |
| `js/view-book.js` | 作品详情页（`#/book/<id>`）：目录清单、续读、抓取更多章节（预填下一批）、ZIP 下载、源站链接 |
| `js/view-settings.js` | 设置页：仓库/Token/检查清单/愿望清单/**本地存储面板（位置切换、占用、逐本删除）** |
| `js/dlstore.js` | **V2.0 下载管理器**：raw 分支 zip 下载（进度/Range 断点续传/双 URL 回退）、OPFS+自定义文件夹存储、注册表与删除 |
| `js/task.js` / `js/github.js` / `js/util.js` | 任务状态机 / GitHub API / 工具 |
| `js/sites-data.js` | **由 export_sites.py 生成，勿手改** |
| `reader.html` + `css/reader.css` + `js/reader.js` | 阅读器：卷轴/翻页、进度记忆、章节跳转、**本地 zip 直读（分卷懒加载）**、未下载引导页、手动 ZIP 解包 |
| `sw.js` | Service Worker：外壳 SWR、`data/*` network-first（漫画图片 cache-first 已退役） |
| `vendor/jszip.min.js` | 离线 ZIP 解包依赖 |

## 4. 核心链路

### 4.1 爬取管线（v3：zip-only + 批次化 + 章节合并）

```
输入(任意站点链接/ID + 批次号) → detect_crawler 自动识别(或显式 source)
  → crawler.crawl(batch) → meta + raw_dir     （多章节站点 20 章/批，从第 1 章起算；
                                                JM 复用 jmcomic 库整本下载不分批）
  → site_builder.build_site() → data/<site> worktree:
        comics/<id>/<id>.zip 或 <id>.partNN.zip
            (ZIP_STORED，zip 内布局 JM<id>/<序号>_<章名>/NNN.<原后缀>；
             旧章节从旧 zip 流式拷贝合并，超 90MB 自动分卷)
        comics/<id>/cover.jpg   (首页缩到480宽)
        comics/<id>/manifest.json
            (含 totalChapters/sourceUrl/order；章节 pages = zip 内条目路径，
             parts 记录每卷全局页区间)
        index.json              (该站索引 upsert，最新在前)
        retention: 超过 KEEP+10 硬删（宽限内由书架提示「下载或清理」）
  → data/<site> 单 commit force-push
  → gh-pages 装配：同步前端 + 增量同步该本 manifest/封面 + 聚合 data/comics.json
  → gh-pages 单 commit force-push → POST /pages/builds → 部署
```

**多话作品按「一本」管理**：同一作品的多次批次爬取按 `photo_id` 合并章节——
第 1 批（1-20 章）与第 2 批（21-40 章）累积成书架上的一个条目；
作品详情页（`#/book/<id>`）展示「已上架 x/N 章」与目录清单，
「抓取更多章节」按钮自动预填下一批次。阅读进度按章节 key 记录（合并重排后不失效）。
**产物模式（read/zip/both）已退役**：一律 zip + 元数据，MODE 入参仅为兼容旧调用保留。

### 4.2 任务提交与多任务队列

- **多目标单 run**：TARGET 支持多行（换行/空白分隔），一个工作流 run 内串行爬取全部目标，
  data 分支逐站推送、gh-pages 只装配一次；单任务失败不阻断后续（部分成功），
  `last_run.json` 记录 done/failed 明细；issue 兜底用正文每行 `task: <链接>` 携带。
- **前端本地队列**：分批连续提交时排队（`jm_crawl_queue`），当前 run 完成后自动派发下一个——
  规避云端并发组「1 运行 + 1 待运行」快速连发丢任务的坑。
- **重试/取消**：失败任务进入队列卡片一键重试；排队中可移除；运行中的 run 有 Token 时
  可调 `POST /actions/runs/{id}/cancel` 取消。
- **防重复**：书架已有该作品（sourceUrl/ID 匹配 comics.json）时提交前提示
  「将合并进同一本」（重爬=抓新章节/更新，属正常操作，仅提醒不拦截）。

### 4.3 任务提交的三条通路（同旧版）

1. **一键创建经典 PAT（推荐）**：设置页按钮直达预填 `public_repo` 的创建页，30 秒完成。
2. **Fine-grained PAT**：权限可窄到单仓库 Actions 读写，需手动勾选。
3. **Issue 兜底**：`[jm] <目标>` 标题触发 `issues` 事件，成功自动回评关单，失败也回评。
   正文支持 `mode:/source:/batch:/keep:` 覆盖项。
> 已否决：OAuth Device Flow（`github.com/login/*` 端点无 CORS，纯前端不可行）。

### 4.3 状态与愿望清单（P1）

- 每次构建（无论成败）经 Contents API 写 `data/last_run.json`（状态/错误/运行链接），
  爬取页直接展示最近一次失败原因，不用翻 Actions 日志。
- 通用兜底失败时把域名追加进 `data/wishlist.json`（按域名去重，上限 100），
  设置页「愿望清单」展示，作为新平台适配候选。

### 4.4 本地阅读链路（V2.0，替代原在线阅读+SW 离线缓存）

```
首次点「阅读」→ reader.html?id=<id> 拉取 gh-pages manifest
  → manifest 含 zip/parts（v3 格式）→ dlstore.checkFiles() 查本地包
      未下载 → 引导页：一键下载（进度条，多卷逐个）
        → raw.githubusercontent.com/{repo}/data/<site>/comics/<id>/<zip>
          （实测 ACAO: *，Content-Length + Accept-Ranges: bytes；
            备用入口 github.com/{repo}/raw/... 同链路）
        → Range 断点续传（半包保留，重试从断点继续）→ 完整性校验
        → 存储：OPFS（全平台默认，navigator.storage.persist() 防回收）
               或自定义文件夹（桌面 Chromium showDirectoryPicker，句柄存 IndexedDB）
      已下载 → JSZip 直读：manifest 为目录钥匙，章节 pages 即 zip 条目名，
               分卷按全局页区间懒加载当前卷，页 objectURL 缓存换章回收
  → 旧格式（迁移前 manifest 无 zip/parts）自动回退在线阅读（过渡兼容）
```

- **删除双选项**：本地删除 = 删本地 zip + 清注册表（纯前端）；
  下架 = `__delete__` 云端删除（本地包保留）。
- **持久性**：OPFS 重启浏览器/机器不丢；iOS Safari 的 ITP 7 天规则
  （非主屏幕安装的站点连续 7 天不访问清脚本可写存储）→ 建议添加主屏幕，
  且始终可重新下载。
- **保存 ZIP 到系统**：书架/详情页走 raw 导航下载（Content-Type application/zip
  浏览器自动落盘），分卷逐卷列出。

### 4.5 存量迁移（V2.0 一次性，TARGET=__migrate__）

- `SOURCE` 选站点（auto=全部），`MIGRATE_DRY=1` 只打印计划不落盘；
  幂等可中断重跑，50 分钟软预算跨 run 续跑。
- 有 zip 的书：结构校验（章节数↔文件夹数）+ testzip 全量 CRC 通过后，
  只重写 manifest pages（zip 原字节保留）；无 zip 的 9 本：散图重建分卷+逐卷 CRC。
- 校验通过才删散图；任何失败保留散图只记日志。
- gh-pages 装配交给 15 分钟巡检自动补上（瘦身装配把 gh-pages 里的散图一并清掉）。
- 仓库体积预期：6.17GB → ~3.5GB（GC 收走散图 blob 需数天~两周，期间数字先涨后落）。

## 5. 运维状态与排障速查

**仓库设置（已配置好，勿重复）**：Pages 源 = `gh-pages /(root)`；
Actions Workflow permissions = write；Issues = 开启。

**本地测试**：
- 构建 mock：`JM_PAGES_MOCK=1 JM_PAGES_INPUT=<id> JM_PAGES_SOURCE=mangadex python usage/workflow_pages_build.py`
  （写入 ./docs（v3 zip-only 布局），不碰分支；docs/comics、docs/data 已 gitignore）
- 分卷小阈值测试：`JM_ZIP_PART_MAX=65536`（下限 64KB，防误配；生产不设）
- 只构建不推分支（真实爬取）：`JM_PAGES_PUBLISH=0 ...`
- 迁移 dry-run（本地，会拉取 data 分支）：`python usage/migrate_zip_only.py --site=mkzhan --dry-run --no-push`
- 页面调试：`cd docs && python -m http.server 8901`，iframe 嵌套可模拟手机视口
- 注册表变更后跑 `python usage/export_sites.py` 重新生成前端站点配置
- 本机无法访问禁漫域名与多数漫画站（真实爬取只能推到 Actions 验证）；MangaDex、pypi、unpkg 可通，jsdelivr 被墙
- **V2.0 raw 下载链路已实测**（2026-11）：raw.githubusercontent.com 对 data 分支 zip
  返回 200 + `Access-Control-Allow-Origin: *` + `Accept-Ranges: bytes` + Content-Length；
  github.com/.../raw/... 302 可作备用入口。**GitHub Releases 资产无 CORS 头**
  （release-assets.githubusercontent.com 全量/Range GET 均无 ACAO）→ Releases 二期若落地，
  网页只能「导航下载 + 本地导入」半自动，全自动阅读需换支持 CORS 的存储（如 R2）

**排障速查**：
- `usage_log` 插件依赖 `psutil`，工作流需单独 `pip install psutil`
- 本子原图多为 `.webp`，manifest 保留原后缀，手写测试 URL 勿想当然用 `.jpg`
- 统一用 `curl_cffi`（jmcomic 依赖、过 Cloudflare）；`requests` 不在依赖里
- OPFS/FSA 存储：OPFS 需安全上下文（https/localhost）；FSA 目录句柄存 IndexedDB，
  冷启动首次读写需用户手势内 `requestPermission`；iOS Safari ITP 7 天不访问清 OPFS
- 分卷命名约定：`<id>.part01.zip`（数字车号为 `JM<id>.partNN.zip`），
  manifest.parts.firstPage/lastPage 为全局页序（1-based）
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
- ~~多话按本管理 + 批次爬取 + 作品详情页~~：已解决（见 §4.1，2026-10-22 线上实测：
  MangaDex 两批次合并 36/96 章、顺序正确、章节 key 稳定）
- 站点云端校准最终结果（2026-10-23，逐站真实下载验证）：
  - ✅ 在线 7 站：jmcomic / mangadex / nhentai / mkzhan / wnacg / ehentai / telegraph
  - ✅ **wnacg**：缩略图 /data/t/→/data/ 变换已失效（图床分离全尺寸 404），
    改走视图页 /photos-view-id-<pid>.html 解析原图；注意 index 页 SSR 只出部分
    视图链接（懒加载余量），多页画廊可能抓不全（待改进）
  - ✅ **ehentai**：首页自动发现 → 匿名配额下载正常
  - ❌ manhuagui：图床 packer 参数含转义引号，需按完整脚本重写解析（六轮校准未破）
  - ❌ bika：API 域名现为 picaapi.picacomic.com，但旧签名密钥被服务端拒绝（401）
  - ❌ mangabz / mheee / baozimh / copymanga / kuaikan / dmzj：见 registry.py 注释
  - 下架站代码全部保留在 `usage/crawlers/`，破解后重新注册一行即可
  - **首页自动发现**：manhuagui/wnacg/ehentai/bika 实现过「贴首页链接抓第一本」
    （bika/manhuagui 下架后该能力随站停用，wnacg/ehentai 在线可用）
- mkzhan 目录 API 待破解（`chapter/show` 报"用户标识不能为空"，可能需要 App token）
- ~~100MB 超限打包~~：V2.0 已改为**自动分卷**（超大本子恢复可读），替代 v2 护栏方案
- **V2.0 zip-only 架构（2026-11 实施）**：
  - 仓库实测 6.17GB（散图+zip 双份重复）→ zip 单一载体约减半；
    gh-pages 只装元数据+封面（MB 级），根治大站 Pages 构建不稳（此前书架时隐时现的诱因之一）
  - 阅读改为「首读自动下载 zip/分卷到本机 → OPFS/FSA → JSZip 直读」，
    下载支持进度/断点续传（raw 链路实测 CORS+Range 齐全）；
    SW 漫画图片缓存、全本离线缓存按钮退役
  - 存量迁移走 `__migrate__`（有 zip 的书只重写 manifest pages、9 本超限书重建分卷），幂等
  - 待观察：仓库 size 回落需等 GitHub GC（数天~两周）；
    **二期备选**：zip 移 GitHub Releases（仓库 ~50MB）但实测资产**无 CORS 头**，
    网页全自动阅读不可行（仅导航下载+本地导入半自动），届时需评估 R2 等外部存储
- 后续可选项：抓取队列跨设备同步（localStorage → 云端 queue.json，含 keep 设置同步——
  keep 按「提交任务的设备」取值是书架被误硬删的隐患）、逐本即时上架（装配移入循环内）、
  data 分支目录名带部分标题（entry 加 dir 字段，向后兼容）
