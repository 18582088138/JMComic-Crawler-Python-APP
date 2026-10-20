# 移动端云书架 · 架构文档

> 本文档梳理「GitHub Actions + GitHub Pages 移动端云书架」子系统的代码结构与核心设计，
> 不涉及 jmcomic 库本体（`src/jmcomic/`，那是上游的下载引擎）。

## 1. 系统总览

```
┌──────────────────────┐        ┌───────────────────────────┐
│  手机/浏览器          │        │  GitHub 云端               │
│                      │        │                            │
│  GitHub Pages 静态站点│        │  Actions Runner (Ubuntu)   │
│  ├─ index.html 书架   │  触发   │  ┌──────────────────────┐  │
│  │   提交任务/进度跟踪 │───────▶│  │ workflow_pages_build │  │
│  │                    │dispatch│  │  ├─ JMComic 主流程    │  │
│  ├─ reader.html 阅读器│ 或     │  │  └─ beta_crawler 内测 │  │
│  │   卷轴/翻页/离线ZIP │ Issue  │  └─────────┬────────────┘  │
│  │                    │        │            │ 生成站点数据     │
│  ├─ comics/<id>/     │◀───────│  git push docs/ 回仓库      │
│  │   图片/manifest/zip│ 静态托管│            │               │
│  └─ data/comics.json │        │  Pages 自动部署 ◀────────────┘│
└──────────────────────┘        └───────────────────────────┘
```

**职责划分**：浏览器只做"遥控器 + 阅读器"；Pages 只做静态文件橱窗；
所有爬取都在 Actions 云端（境外机房，天然绕过源站地区限制）。

## 2. 文件结构与职责

| 文件 | 层 | 职责 |
| --- | --- | --- |
| `docs/index.html` | 前端 | 书架主页：提交爬取（JM + 内测平台）、任务进度跟踪、书架（喜欢置顶/进度角标）、设置（Token/仓库） |
| `docs/reader.html` | 前端 | 阅读器：卷轴/翻页双模式、点按/滑动/键盘翻页、章节跳转、进度记忆、离线 ZIP 解包阅读 |
| `docs/vendor/jszip.min.js` | 前端 | 离线 ZIP 解包依赖（内嵌保证离线可用） |
| `docs/data/comics.json` | 数据 | 书架索引（构建脚本每次运行时更新） |
| `docs/comics/<id>/` | 数据 | 每本漫画：`images/<章>/<页>`、`cover.jpg`、`manifest.json`、`<id>.zip` |
| `index.html`（根目录） | 兼容 | Pages 源被配成根目录时自动跳转到 `docs/` |
| `.github/workflows/pages_crawler.yml` | 编排 | 触发入口（workflow_dispatch / issues）、环境装配、执行构建、git push 回仓库、issue 结果回复 |
| `usage/workflow_pages_build.py` | 后端 | 构建主脚本：解析输入 → 下载 → 生成站点数据 → 更新索引 → 保留策略清理 |
| `usage/beta_crawler.py` | 后端 | 内测多平台爬取器（MangaDex / nhentai；拷贝漫画代码保留暂下架） |
| `assets/option/option_pages_build.yml` | 配置 | JMComic 下载配置（api 客户端免登录、章节维度目录规则） |

## 3. 核心链路

### 3.1 任务提交（两条通路）

1. **Token 通路（推荐）**：页面用用户存在 localStorage 的 Fine-grained PAT 调
   `POST /repos/{repo}/actions/workflows/pages_crawler.yml/dispatches`。
   全程网页内完成，可轮询工作流状态，**不产生任何 issue**。
2. **Issue 通路（免 Token 兜底）**：页面打开预填的 `[jm] <目标>` issue 创建页；
   工作流监听 `issues: opened` 事件自动执行，成功后自动评论链接并关闭 issue。
   issue 正文支持 `mode:/source:/limit:/keep:` 覆盖项。

### 3.2 构建管线（workflow_pages_build.py）

```
输入(链接/ID) → parse → 下载(原始目录) → build_site → 站点数据
                                ↓
   raw_dir/<章>/xxx.jpg  →  docs/comics/<id>/images/<章>/NNN.<原后缀>
                            docs/comics/<id>/cover.jpg   (首页缩到480宽)
                            docs/comics/<id>/manifest.json
                            docs/comics/<id>/<id>.zip    (ZIP_STORED 快速打包)
                            docs/data/comics.json        (索引 upsert，最新在前)
                            保留策略: 超出 KEEP_LATEST 的旧本子整目录删除
```

### 3.3 多平台契约（beta_crawler.py）

各平台爬取器只需实现统一契约，即可复用整条站点生成管线：

```python
meta = {'id': 'md-xxx', 'title': str, 'authors': [...], 'tags': [...],
        'description': str,
        'chapters': [{'photo_id': str, 'dir_name': '1', 'title': str}, ...]}
raw_dir/<dir_name>/  # 该章图片，文件名可自然排序
```

书架 ID 带平台前缀（`md-`/`nh-`），与 JM 纯数字车号隔离，互不冲突。

### 3.4 阅读器（reader.html）

- 在线模式：`reader.html?id=<书架ID>[&ch=N&p=N]` → 拉取 `comics/<id>/manifest.json` 渲染。
- 离线模式：`reader.html?local=1` → 选择本地 ZIP → JSZip 浏览器内解包 →
  按压缩包目录结构分章，blob URL 直读，无需任何网络请求。
- 进度存 `localStorage: jm_prog_<id>`，书架卡片显示"读到 X 章 X 页"。

## 4. 关键设计决策与权衡

| 决策 | 选择 | 理由 / 代价 |
| --- | --- | --- |
| 站点形态 | 单文件 HTML + 内联 CSS/JS，无构建步骤 | Pages 直接可部署、零工具链；代价是单文件偏大（当前规模可接受） |
| 任务触发 | dispatch 为主、issue 为兜底 | GitHub 没有免鉴权的触发 API，见下文 §5 |
| 图片存储 | 提交到仓库 `docs/comics/` | 零外部依赖、Pages 直出；代价是 git 历史会累积图片 blob（见 roadmap P0） |
| 内测平台 | 统一契约 + 平台前缀 ID | 新增平台只需实现一个 crawl 函数；拷贝漫画因其 API 变动暂下架 |
| 下载引擎 | JM 主流程复用 jmcomic 库；内测平台用 curl_cffi | curl_cffi 是 jmcomic 既有依赖，自带浏览器指纹可过部分 Cloudflare |

## 5. 任务提交的三种鉴权方式

GitHub 触发工作流的 API（`workflow_dispatch`/`repository_dispatch`）**强制要求鉴权**，
纯静态网页没有"免登录触发"的官方手段，因此提供三条通路（推荐程度递减）：

1. **一键创建经典 PAT（推荐）**：设置页按钮直达
   `github.com/settings/tokens/new?scopes=public_repo&description=...`
   （经典 Token 创建页支持 URL 预填 scope），用户 Generate 后粘贴回来，约 30 秒。
2. **Fine-grained PAT**：权限可窄到单仓库 Actions 读写，但创建页不支持 URL 预填，需手动勾选。
3. **Issue 兜底**：利用「开 issue 能触发 `issues` 事件」提交任务，成功后 bot 自动回评并关闭。
   前两者都未配置时的降级路径，issue 即开即焚不会堆积。

> 已否决方案：OAuth Device Flow 纯前端授权。`github.com/login/device/code` 与
> `/login/oauth/access_token` 均不返回 CORS 头（实测任意 Origin 都被浏览器拦截），
> 静态网页无法直接调用；若引入 token 交换代理则得不偿失。GitHub App 同理需要
> client secret 换 token，不适合无后端静态站。

## 6. Roadmap（按优先级）

- **P0 仓库体积**：图片进 git 历史会无限累积（保留策略只删工作区不删历史）。
  中期方案：站点数据改推送到孤立分支（如 `gh-pages`，每次 force-push 单 commit），
  Pages 源切到该分支，master 历史保持干净。
- **P1 失败可见性**：工作流失败时也回评 issue / 写 `data/last_run.json` 状态文件，
  网页直接展示失败原因，不用翻 Actions 日志。
- ~~P2 免 Token 一键授权~~（Device Flow 因 OAuth 端点无 CORS 被否决，改以预填 scope 的
  经典 PAT 直达链接实现 30 秒配置，见 §5）
- **P3 体验**：阅读器 Service Worker 缓存已读漫画实现真·离线；前端拆模块提升缓存效率。
