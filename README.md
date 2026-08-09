# douyin-matrices

> 基于 [Youhai020616/douyin](https://github.com/Youhai020616/douyin)（MIT）的 fork，新增受治理的自动化编排层。原始版权归 `douyin` 上游项目。

[![CI](https://github.com/alanl1234/douyin-matrices-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/alanl1234/douyin-matrices-cli/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/badge/pypi-douyin--matrices-blue.svg)](https://pypi.org/project/douyin-matrices/)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](https://pypi.org/project/douyin-matrices/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)

A local-first, multi-account matrix CLI for Douyin (抖音) — search, download, publish, trending, live, interact, and orchestrate across many accounts under unified rate-limiting, deduplication, and accountability 📱

[English](#features) | [中文](#功能特性)

## Features

- 🔍 **Search** — keyword search with sort/time/type filters, user search
- 📥 **Download** — no-watermark video/image with progress bar, batch user download
- 📝 **Publish** — video & image posts with tags, cover, scheduling, visibility
- 🔥 **Trending** — real-time hot search Top 50 with watch mode
- 📺 **Live** — room listing, stream info, URL extraction, ffmpeg recording
- 💬 **Interact** — like, favorite, comment, follow (Playwright)
- 📊 **Analytics** — creator dashboard via XHR interception
- 👤 **Profile** — user info, posts listing
- 🔢 **Short-index navigation** — open recent list results with `dy read 1` or `dy comments 1`
- 📦 **Export** — `dy search "AI" -o results.csv` (JSON/CSV/YAML)
- 🔐 **Auth** — QR scan + browser cookie auto-extraction
- 👥 **Multi-Account** — isolated cookie storage
- 🛡️ **Anti-Detection** — Gaussian jitter, exponential backoff, captcha cooldown
- 🧩 **Account matrix** — manage many accounts with per-account personas; orchestrate publishing and interaction across the matrix under unified rate-limiting, deduplication, and accountability

## Installation

### Quick install (PyPI + dashboard)

```bash
pip install --user douyin-matrices
dy-dashboard                       # launch local dashboard → http://127.0.0.1:8765
```

### Install from source (development)

Clone this repo and sync dependencies with [uv](https://github.com/astral-sh/uv):

```bash
git clone https://github.com/alanl1234/douyin-matrices-cli douyin-matrices-cli
cd douyin-matrices-cli
uv sync --extra dev
```

### Interface

This tool ships **two interfaces**: the **command line `dy`** and the **local dashboard `dy-dashboard`**.
After launching the dashboard, visit http://127.0.0.1:8765 to use account management, persona library, search, publishing, orchestration, engagement governance, and material collection.

## Usage

```bash
# ─── Auth ─────────────────────────────────────────
dy login                             # QR scan login (one time)
dy login --browser                   # Extract cookies from an already-logged-in browser
dy status                            # Check login status
dy account list                      # List matrix accounts

# ─── Search ───────────────────────────────────────
dy search "美食"                      # Search videos
dy search "咖啡" --sort 最多点赞       # Sort: 综合 / 最多点赞 / 最新
dy search "风景" --type atlas         # Filter: video, atlas (image), user
dy search "日食记" --type user        # Search users
dy search "AI" -o results.csv        # Export to CSV
dy read 1                            # Read the 1st result from the last list command
dy detail AWEME_ID                   # Detail by ID
dy comments 1                        # View comments for the 1st result

# ─── Download ─────────────────────────────────────
dy dl 1                              # Download by short index
dy download https://v.douyin.com/xxx # Download by URL
dy download 1234567890 --music       # Also download BGM
dy dl SEC_USER_ID --user --limit 20  # Batch download a user's posts

# ─── Trending & Live ──────────────────────────────
dy trending                          # Hot search Top 50
dy trending --count 10 -o hot.json   # Export top 10
dy trending --watch                  # Auto-refresh every 5 min
dy live list                         # Recommended live rooms
dy live list --count 10              # Show 10 rooms
dy live info ROOM_ID                 # Live stream info
dy live record ROOM_ID               # Record with ffmpeg

# ─── Publish ──────────────────────────────────────
dy publish -t "标题" -c "描述" -v video.mp4                       # Video
dy publish -t "标题" -c "描述" -i img1.jpg -i img2.jpg            # Image post
dy publish -t "标题" -v v.mp4 --tags AI --visibility 仅自己可见    # Private + tags
dy publish -t "标题" -v v.mp4 --schedule "2026-03-20T08:00:00+08:00"  # Scheduled
dy pub -t "标题" -v v.mp4 --dry-run                              # Preview only

# ─── Interact ─────────────────────────────────────
dy like 1                            # Like the 1st result from the latest listing
dy like 1 --unlike                   # Unlike
dy fav 1                             # Favorite
dy comment 1 -c "好看!"              # Comment
dy follow SEC_USER_ID                # Follow user

# ─── Profile & Analytics ──────────────────────────
dy me                                # My login info
dy profile SEC_USER_ID --posts       # User profile + posts
dy analytics                         # Creator dashboard
dy notifications                     # Messages

# ─── Account matrix ───────────────────────────────
dy account add primary               # QR login and register into the matrix
dy account add alt --group beauty    # Register with a group
dy account persona create foodie --topics food,explore --tone lively
dy account persona bind foodie --account primary
dy --account primary publish -t "Title" -v video.mp4   # Publish AS a specific account
dy account verify primary            # Verify cookie usability
dy account toggle primary            # Enable / disable this account
```

> **Global `--account` option.** Every authenticated command accepts
> `--account <id|alias>` to bridge that matrix account's cookies
> **without touching the global cookie store**. This is the recommended way to
> target a specific account (e.g. `dy --account <ALIAS> publish -t …`).
> Run `dy account list` to list available matrix accounts.
> Note: `--account` is a *global* flag, so it must come **before** the subcommand
> (`dy --account X status`, not `dy status --account X`).

## Authentication

douyin-matrices supports multiple authentication methods:

1. **Saved cookies** — stored per account under `~/.dy/cookies/<alias>.json` (Playwright `storage_state`)
2. **Browser cookies** — auto-detects installed browsers and extracts cookies (supports Chrome, Edge, Firefox, Safari, Brave, Chromium, Opera, Vivaldi, and more)
3. **QR code login** — browser-assisted login, scan the QR code in the terminal (`dy login`)

`dy login` automatically tries all installed browsers and uses the first one with valid cookies.
Use `--browser` to specify extraction explicitly, or `dy login` for the QR flow.
Other authenticated commands automatically retry once with fresh browser cookies when the saved session has expired.

### Web QR binding (dashboard)

You can bind / re-bind an account **from the web dashboard** without opening a local
browser: the dashboard backend launches a *headless* Playwright browser, captures the
Douyin creator login QR, and serves it to the page. Scan it with the Douyin App and
confirm on your phone — the backend detects login, persists `storage_state` to the
account's cookie file, and registers the account in the matrix DB.

- Open **账号矩阵 → ＋ 添加账号（扫码）** to create a new account, or the **扫码** button
  next to an existing account to re-bind it.
- Endpoint flow: `POST /api/accounts/qr-start` → `GET /api/accounts/qr-status?session=…`
  (poll every few seconds; QR expires after ~2 minutes).
- If Douyin serves an anti-bot challenge instead of a QR, fall back to the CLI:
  `dy account add <alias>`.


### Multi-account: `--account`

If you run the local dashboard (`dy-dashboard`, at http://127.0.0.1:8765),
each account's cookies live in an isolated Playwright storage-state file. The CLI can bridge
any of them on demand:

```
dy --account <ALIAS> status          # show that account's state
dy --account <alias> read <url>      # read as that account
dy --account <alias> publish -t …    # publish AS that account
```

This never mutates another account's cookie file, so switching target accounts can't cross-post.
Use `dy account list` (no `--account`) to list all registered matrix accounts.

### Browser Profile lock

Browser-backed operations (login, publish, comment scraping) take a per-account
**Profile lock**: only one process may occupy an account's browser session at a time
(PID-based mutual exclusion + reentrancy + expiry reclaim). This prevents multiple
processes from clobbering the same cookie file and triggering risk control.

## Environment Variables

### CLI

| Variable | Default | Description |
|----------|---------|-------------|
| `OUTPUT` | `auto` | Output format: `json`, `yaml`, `rich`, or `auto` |

### Dashboard (`dy-dashboard`)

| Variable | Default | Description |
|----------|---------|-------------|
| `DY_MATRICES_DATA` | `~/.douyin-matrices` | Matrix data directory (SQLite, uploads, cookies) |
| `DY_MATRICES_WORKERS` | `2` | Background worker threads |
| `DY_MATRICES_REQUEST_INTERVAL` | `1.0` | Min seconds between API requests |
| `DY_MATRICES_DAILY_REQUEST_LIMIT` | `2500` | Max requests per account per day |
| `DY_MATRICES_QUEUE_LEASE` | `180` | Task lease seconds (resilience to crashes) |
| `DY_MATRICES_QUEUE_POLL` | `0.5` | Queue poll interval seconds |

### Orchestrator (auto pipeline — all opt-in, off by default)

| Variable | Default | Description |
|----------|---------|-------------|
| `DY_ORCHESTRATOR` | _(off)_ | Set `1` to start the orchestrator loop |
| `DY_ORCHESTRATOR_TICK` | `60` | Orchestrator poll interval (seconds, min 10) |
| `DY_AUTO_PUBLISH` | _(off)_ | `1` = enqueue gate-complete drafts for review; legacy `approve` behaves the same and never bypasses human approval |
| `DY_DAILY_PUBLISH_LIMIT` | `5` | Max auto-publishes per account per day |
| `DY_ENGAGEMENT_MODE` | `shadow` | `shadow` / `inbound` / `reviewed` — grayscale for auto engagement |

## Rate Limiting & Anti-Detection

douyin-matrices includes comprehensive anti-risk-control measures designed to minimize detection:

### Request Timing
- **Gaussian jitter**: Delays between requests use a truncated Gaussian distribution (not fixed intervals) to mimic natural browsing patterns
- **Random long pauses**: ~5% of requests include an additional 2-5 second delay simulating reading behavior
- **Auto-retry**: Exponential backoff on HTTP 429/5xx and network errors (up to 3 retries)

### Browser Fingerprint Consistency
- **UA/Platform alignment**: User-Agent and fingerprint fields are consistent across requests
- **Session-stable identity**: browser identity is generated once per session and reused (real browsers don't change mid-session)
- **Host-OS independent**: the same anti-detection identity is used on Windows / macOS / Linux

### Captcha Cooldown
- **Progressive backoff**: On captcha trigger, automatically sleeps with increasing delays
- **Adaptive rate limiting**: Request delay is increased after a captcha event to reduce future risk

### Account Matrix Governance

The matrix layer adds a deterministic governance engine on top of the raw client:

- **PII detection** — phone / WeChat / address / email / ID-card / QQ patterns are blocked before any post or outbound message
- **Opt-out & sensitive-word detection** — complaint / unsubscribe signals and high-risk phrasing (bot-farming, off-platform redirection) are intercepted
- **Content similarity dedup** — `difflib`-based near-duplicate detection prevents the same creative from spamming across accounts
- **Engagement grayscale** — `shadow` (draft-only, no real execution) / `inbound` (replies + passive interaction only) / `reviewed` (everything); includes warm-lead gating, blocklist + cross-account cooldown, and per-account daily / hourly budgets
- **Durable task queue** — SQLite-backed queue with leases + heartbeat + crash-safe recovery + idempotent enqueue + same-account serialization + backoff retry; both publish and collection tasks survive process crashes
- **Self-accounting** — every automated action is recorded (publish tasks + orchestration ledger) for traceability

## Account Matrix

This project builds on the original dy-cli and aligns with [`xiaohongshu-matrices-cli`](https://github.com/alanl1234/xiaohongshu-matrices-cli) on "account matrix" capabilities. The matrix layer does **not** modify the original client; it layers SQLite metadata (accounts / personas / publish tasks / orchestration ledger / material library) on top of the native cookie store `~/.dy/cookies/<alias>.json`, so it is completely non-intrusive to all existing manual flows.

### Core Concepts

- **Account matrix (accounts)** — Each Douyin account is registered as a matrix record (alias, uid, nickname, group, enabled flag, bound persona, `last_verified_at` health timestamp). Cookies remain in the native dy mechanism; the matrix only records their path. Supports **session cache** (5-minute TTL) and **browser Profile lock** (PID-based mutual exclusion) to stop multiple processes from hijacking the same account's browser.
- **Persona library (personas)** — Define tone, favored topics, and banned-word templates per account, so publishing / interaction stays on-message per persona.
- **Cross-account orchestrator** — *opt-in*. Batch-deliver the same content or interaction to multiple accounts; everything passes through the governance engine + unified rate limiting + similarity dedup + self-accounting.
- **Engagement governance** — Grayscale rollout (`shadow` / `inbound` / `reviewed`). Includes **warm-lead gating** (`dm_outbound` only allowed for warm leads), **opt-out / sensitive-word detection**, **blocklist + cross-account cooldown**, and **per-account daily budget / hourly comment budget / DM minimum interval**.
- **Durable task queue** — SQLite `task_queue` with leases + heartbeat + crash-safe recovery + idempotent enqueue + same-account serialization + backoff retry. Both publish and collection jobs go through this queue.
- **Material collector** — Douyin-specific: search videos by keyword / topic, scrape comments into a local material library, with **resumable** collection (state machine + `aweme_id` dedup upsert). Network sources are injected via `search_fn` / `comment_fn` (the real Douyin search endpoint is wired in after verification).
- **Unified rate limiting** — Per-account persistent request interval + daily cap, surviving across processes in `rate_limit.sqlite3`.

### Web Dashboard

```bash
dy-dashboard                        # Launch local dashboard → http://127.0.0.1:8765
```

Provides pages: overview, account matrix (group / persona / enable / verify / rebind / disable / delete), persona library, cross-account publishing, orchestrator status, engagement governance (grayscale mode / rules / blocklist), and material collection (job create / run / results).

All write operations use POST redirects (303); read state uses `/api/*` JSON endpoints (`/api/accounts`, `/api/health`, `/api/searches`, `/api/orchestrator/status`, `/api/engagement/status`).

### Browser & Terminal Commands

Douyin login, publishing, commenting, and comment-reading rely on a real browser (Playwright Chromium). Common "browser terminal commands":

```bash
dy login                              # Launch browser for QR login (one-time), register into matrix
dy login --browser                   # Extract cookie from an already-logged-in desktop browser
dy account add primary               # Equivalent: QR login and register into matrix (use --group to bucket)
dy account verify primary            # Verify cookie file usability, refresh last_verified_at
dy account toggle primary            # Enable / disable this account (whether it is included in orchestration)
dy publish -t "Title" -v video.mp4   # Browser-automated publish (use --account to specify)
dy comments 1                        # Browser scrape a video's comments
dy-dashboard                         # Launch web dashboard → http://127.0.0.1:8765
```

### Data Directory

`~/.douyin-matrices/`: `dashboard.sqlite3` (accounts / personas / tasks), `rate_limit.sqlite3` (rate-limit state), `library/`, `cookies/`, `uploads/`, `screenshots/`.

### Mapping to xiaohongshu-matrices-cli

| xhs | douyin-matrices |
|-----|-----------------|
| `~/.xiaohongshu-cli/dashboard` | `~/.douyin-matrices` |
| `XHS_*` env vars | `DY_*` env vars |
| Xiaohongshu account matrix | Douyin account matrix (reuses native `~/.dy/cookies`) |
| `account_bridge` Camoufox cookie decryption | `account_bridge` Playwright `storage_state` + session cache + Profile lock (single Chromium engine) |
| `P0Store` + `DurableTaskQueue` | Same durable queue (`rate_limit.sqlite3`) |
| `engagement.py` engagement grayscale | `engagement.py` (shadow/inbound/reviewed + warm-lead + blocklist + budget) |
| `collector` / `reading` material collection | `collector.py` (search + comments + resumable) |
| Dashboard pages: accounts/personas/publish/orchestrator | Dashboard pages: accounts/personas/publish/orchestrator/engagement/searches |

## Project Structure

```text
dy_cli/
├── __init__.py
├── main.py              # Click entry point & command registration
├── account_bridge.py    # Cookie storage_state, session cache, Profile lock, verify
├── engines/
│   ├── api_client.py    # Douyin API client (signing, retry, rate-limit, anti-detection)
│   └── ...
├── commands/
│   ├── search.py        # search / read / detail / comments / trending / live
│   ├── download.py      # dl / download
│   ├── publish.py       # publish / pub
│   ├── interact.py      # like / fav / comment / follow
│   ├── account.py       # account add / list / verify / toggle / persona
│   └── ...
├── utils/
│   ├── config.py        # Config + OUTPUT envelope
│   ├── signature.py     # Request signature generation
│   ├── export.py        # JSON / CSV / YAML export
│   ├── index_cache.py   # Short-index navigation cache
│   └── envelope.py      # Structured ok/schema_version/data/error envelope
└── dashboard/
    ├── __main__.py      # dy-dashboard entry point (FastAPI)
    ├── app.py           # Routes + Jinja2 templates
    ├── db.py            # SQLite schema (accounts / personas / tasks / collector)
    ├── config.py        # DashboardConfig
    ├── persistence.py   # P0Store (rate-limit + durable task queue)
    ├── queue.py         # DurableTaskQueue supervisor
    ├── rate_limit.py    # Per-account rate limiter
    ├── qr_login.py      # Headless QR-code login sessions (网页内扫码绑定)
    ├── governance.py    # PII / opt-out / similarity dedup
    ├── engagement.py    # Engagement grayscale governance
    ├── collector.py     # Douyin material collector (resumable)
    ├── orchestrator.py  # Cross-account orchestration loop
    ├── publisher.py     # Browser-automated publisher
    └── templates/       # base / accounts / personas / publish / orchestrator / engagement / searches
```

## Development

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest tests/ -v

# Unit tests only (no network / no browser)
uv run pytest tests/ -v -m "not smoke"

# Lint (optional, not enforced in CI)
uv run ruff check .
```

## Troubleshooting

**Q: `NoCookieError` / cookies not found**

1. Open any browser and visit https://www.douyin.com/
2. Log in with your account
3. Run `dy login --browser` (extract from browser) or `dy login` (QR)

**Q: Captcha required**

Douyin has triggered a captcha check. Open https://www.douyin.com/ in your browser, complete the captcha, then retry.

**Q: IP blocked**

Try a different network (e.g., mobile hotspot or VPN). Douyin blocks IPs that make too many requests.

**Q: Session expired**

Your cookies have expired. Run `dy login` to refresh.

**Q: Requests are slow**

The built-in Gaussian jitter delay (~1-1.5s between requests) is intentional to mimic natural browsing and avoid triggering risk control. Aggressive request patterns may lead to captcha triggers or IP blocks.

## 功能特性

- 🔍 **搜索** — 按关键词搜索，支持排序 / 时间 / 类型过滤，用户搜索
- 📥 **下载** — 去水印视频 / 图片，带进度条，支持批量下载用户作品
- 📝 **发布** — 视频与图文笔记，支持话题、封面、定时、可见范围
- 🔥 **热榜** — 实时热搜 Top 50，支持监听模式
- 📺 **直播** — 直播间列表、流信息、地址提取、ffmpeg 录制
- 💬 **互动** — 点赞、收藏、评论、关注（Playwright）
- 📊 **数据分析** — 创作者后台数据（XHR 拦截）
- 👤 **资料** — 用户信息、作品列表
- 🔢 **短索引导航** — `dy search → dy read 1 → dy comments 1`
- 📦 **导出** — `dy search "AI" -o results.csv`（JSON/CSV/YAML）
- 🔐 **登录** — 扫码 + 浏览器 Cookie 自动提取
- 👥 **多账号** — 隔离的 Cookie 存储
- 🛡️ **反风控** — 高斯抖动、指数退避、验证码冷却
- 🧩 **账号矩阵** — 多账号统一管理，按账号设定人设；在统一的限流 / 去重 / 问责治理下跨账号编排发布与互动

## 安装

本项目以源码方式分发。克隆本仓库并用 [uv](https://github.com/astral-sh/uv) 同步依赖：

```bash
git clone https://github.com/alanl1234/douyin-matrices-cli douyin-matrices-cli
cd douyin-matrices-cli
uv sync
```

上游基础项目 `douyin` 仍发布在 GitHub，如需上游包可自行安装。更新时拉取最新代码后重新执行 `uv sync` 即可。

## 使用示例

```bash
# 认证
dy login                             # 扫码登录（一次性）
dy login --browser                   # 从已登录的浏览器提取 Cookie
dy status                            # 检查登录状态
dy account list                      # 列出矩阵账号

# 搜索
dy search "美食"                      # 搜索视频
dy search "咖啡" --sort 最多点赞       # 排序：综合 / 最多点赞 / 最新
dy search "风景" --type atlas         # 类型：video / atlas / user
dy search "日食记" --type user        # 搜索用户
dy search "AI" -o results.csv        # 导出 CSV
dy read 1                            # 阅读最近一次列表里的第 1 条
dy detail AWEME_ID                   # 按 ID 看详情
dy comments 1                        # 查看最近一次列表里的第 1 条评论

# 下载
dy dl 1                              # 按短索引下载
dy download https://v.douyin.com/xxx # 按链接下载
dy download 1234567890 --music       # 同时下载 BGM
dy dl SEC_USER_ID --user --limit 20  # 批量下载用户作品

# 热榜与直播
dy trending                          # 热搜 Top 50
dy trending --count 10 -o hot.json   # 导出前 10
dy trending --watch                  # 每 5 分钟自动刷新
dy live list                         # 推荐直播间
dy live list --count 10              # 显示 10 个
dy live info ROOM_ID                 # 直播信息
dy live record ROOM_ID               # ffmpeg 录制

# 发布
dy publish -t "标题" -c "描述" -v video.mp4                       # 视频
dy publish -t "标题" -c "描述" -i img1.jpg -i img2.jpg            # 图文
dy publish -t "标题" -v v.mp4 --tags AI --visibility 仅自己可见    # 私密 + 话题
dy publish -t "标题" -v v.mp4 --schedule "2026-03-20T08:00:00+08:00"  # 定时
dy pub -t "标题" -v v.mp4 --dry-run                              # 仅预览

# 互动
dy like 1                            # 给最近一次列表里的第 1 条点赞
dy like 1 --unlike                   # 取消点赞
dy fav 1                             # 收藏
dy comment 1 -c "好看!"              # 评论
dy follow SEC_USER_ID                # 关注

# 账号矩阵
dy account add primary               # 扫码登录并登记进矩阵
dy account add alt --group beauty    # 带分组登记
dy account persona create foodie --topics food,explore --tone lively
dy account persona bind foodie --account primary
dy --account primary publish -t "Title" -v video.mp4   # 以指定账号发布
dy account verify primary            # 校验 Cookie 可用性
dy account toggle primary            # 启用 / 停用该账号
```

## 认证策略

douyin-matrices 支持多种认证方式：

1. **已保存 Cookie** — 按账号存于 `~/.dy/cookies/<alias>.json`（Playwright `storage_state`）
2. **浏览器 Cookie** — 自动检测已安装浏览器并提取（支持 Chrome、Edge、Firefox、Safari、Brave、Chromium、Opera、Vivaldi 等）
3. **扫码登录** — 浏览器辅助登录，终端显示二维码，用抖音 App 扫码（`dy login`）

`dy login` 会自动尝试所有已安装浏览器，使用第一个有有效 Cookie 的浏览器。也可用 `--browser` 指定提取，或 `dy login` 走扫码流程。其他需认证命令在 session 过期时会自动重试一次。

> 矩阵层对浏览器操作施加 **Profile 锁**：同一时刻仅允许一个进程占用某账号的浏览器会话（基于 PID 的互斥 + 可重入 + 过期回收），避免多进程互相踩 Cookie 文件、触发风控。

## 常见问题

- `NoCookieError` / 找不到 Cookie — 请先在任意浏览器打开 https://www.douyin.com/ 并登录，然后执行 `dy login --browser` 或 `dy login`
- 触发验证码 — 请到浏览器中完成验证后重试
- IP 被限制 — 尝试切换网络（手机热点或 VPN）
- Cookie 过期 — 执行 `dy login` 刷新
- 请求较慢是正常的 — 内置高斯随机延迟（~1-1.5s）是为了模拟人类浏览行为，避免触发风控

## 关于本仓库（Fork 说明）

本仓库基于 [Youhai020616/douyin](https://github.com/Youhai020616/douyin)（MIT）fork，并新增了一层**受治理的全自动编排**：

- 本地多账号运营后台 `dy-dashboard`（`dy_cli/dashboard/`）
- 编排调度模块 `dy_cli/dashboard/orchestrator.py`：在统一的治理引擎（限流 / opt-out / 敏感词 / 相似度）与持久任务队列下，跨账号批量发布与互动，所有自动化行为均走环境变量 opt-in。
- 互动灰度治理 `dy_cli/dashboard/engagement.py`：shadow / inbound / reviewed 三模式 + 暖线索 + 停止名单 + 预算。
- 抖音素材采集 `dy_cli/dashboard/collector.py`：关键词 / 话题搜索、评论抓取、去重素材库、断点恢复。

本仓库已做跨平台对齐，可在 Windows / macOS / Linux 上运行。

运行数据（cookie / token / 素材库）默认落在用户主目录（`~/.douyin-matrices` 与 `~/.dy`），不会进入仓库。

## 免责声明

本项目为技术研究与学习工具。自动化操作可能违反抖音用户协议，使用者应自行承担账号风控、限流、封禁等一切后果。作者不对因使用本项目导致的任何账号损失或其他损害承担责任。

## License

MIT
