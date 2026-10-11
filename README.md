# 极易下 - 万能视频下载网站

基于开源项目 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 封装的视频下载网站。
前端 Vue 3 + Vite，后端 FastAPI；任务状态存内存，用户/订单/会话存 SQLite（零依赖标准库），轻量单服务部署。

| 文档 | 说明 |
|------|------|
| [docs/01-需求分析.md](docs/01-需求分析.md) | 背景、用户场景、功能/非功能需求、风险对策 |
| [docs/02-方案设计.md](docs/02-方案设计.md) | 架构、选型理由、核心流程、抖音通道与支付模块设计 |
| [docs/03-AI视频总结功能方案.md](docs/03-AI视频总结功能方案.md) | AI 总结：竞品调研、字幕/降级路线、LLM 接入、限流设计 |
| [docs/04-功能扩展与前端改版.md](docs/04-功能扩展与前端改版.md) | 导图全屏/字幕下载、总结质量调优、UI 改版与国际化、线上部署实录 |
| [docs/05-SEO优化与运营清单.md](docs/05-SEO优化与运营清单.md) | 后端运行时 SEO 注入、robots/sitemap、GEO（llms.txt）、站长运营清单 |
| [docs/06-支付与会员系统.md](docs/06-支付与会员系统.md) | 登录系统、Stripe Checkout、幂等三层、权益门禁、Stripe 配置手册 |
| [docs/07-落地页改版与会员体验.md](docs/07-落地页改版与会员体验.md) | 落地页信息架构、视觉决策、内容真实性原则 |
| 本文档（README） | 快速上手与运维手册 |

## 功能

- 粘贴链接即可解析下载，支持 1000+ 平台（B站 / 抖音 / YouTube / 小红书 等）
- 多清晰度选择（360p ~ 4K），仅音频提取
- 抖音免 Cookie、无水印，支持长链 / 短链 / App 分享口令文本
- **AI 视频总结**：解析后一键生成概述、核心要点、章节大纲（时间戳取自字幕真实时间，可跳转）与思维导图；字幕优先、无字幕自动降级并如实标注依据
- **思维导图**：页内全屏查看（适应窗口/原始大小），一键导出 PNG（2x）/ SVG
- **字幕下载**：字幕时间线支持导出 SRT（带时间轴，可导入播放器）与 TXT
- **多语言**：简体中文 / English / 日本語 / 한국어 / Español / Français，导航栏切换，自动记忆
- 紧凑双栏工作台布局（左侧视频信息+下载操作，右侧 AI 总结面板），Teal 低饱和配色
- 下载进度实时显示，文件与总结结果保留 30 分钟自动清理
- **账号与会员**：邮箱密码注册/登录（scrypt 加盐哈希，HttpOnly Cookie 会话），订单归属用户；会员享 AI 总结不限次 + 解除 1080p 清晰度上限（服务端强制）
- **Stripe 支付**：Checkout 托管支付页（一次性购买，CNY），月卡 +30 天 / 年卡 +365 天自动叠加；幂等三层防重复开通，webhook 验签 + 回跳轮询双通道不丢单；未配置密钥自动回退模拟收银台
- 下载进度实时显示，文件与总结结果保留 30 分钟自动清理
- 落地页：深色渐变 Hero + 真实数据统计带、AI 总结效果展示、使用场景、底部 CTA（详见 docs/07）

## 平台支持现状（实测）

| 平台 | 本地部署 | 说明 |
|------|:---:|------|
| 抖音 | ✅ | 自研免 Cookie 通道，无水印，支持口令/短链/精选页链接 |
| B站 | ✅ 字幕/总结全可用 | 本地/VPS 部署不受影响 |
| YouTube | ✅ 普通视频字幕/总结可用 | 解析已异步化；字幕自动选最优语言 |
| 其它 yt-dlp 平台 | ✅ | 云端 IP 信誉不如家宽/自有服务器 |

> YouTube 依赖新版 yt-dlp 的 JS 运行时（自动检测部署机上的 node **并按版本取最高**，多 node
> 共存时低版本会导致签名求解静默失败）与可达的网络出口。代理解析顺序：`backend/proxy.txt`
> 显式指定 > Windows 注册表系统代理（自动读取，启动时清除环境变量注入的干扰）。国内直连

> 将本项目部署到国内 VPS 即可（代码零改动）。

## 目录结构

```
video-downloader/
├── docs/                   # 需求分析 + 方案设计文档
├── backend/
│   ├── app/
│   │   ├── main.py         # FastAPI 路由 + 前端静态托管 + 异常兜底
│   │   ├── downloader.py   # 下载调度（平台分流 / 任务表 / 清理 / 清晰度门禁）
│   │   ├── douyin.py       # 抖音免 Cookie 通道（自研：访客 ttwid + iesdouyin 分享页）
│   │   ├── summarizer.py   # AI 视频总结（字幕提取 / LLM 抽象 / 限流 / 总结任务表）
│   │   ├── payments.py     # 支付抽象层：StripeProvider（Checkout）+ MockProvider（回退）
│   │   ├── auth.py         # 注册/登录（scrypt）/ Cookie 会话 / 会员判定
│   │   ├── db.py           # SQLite 封装（users/sessions/orders/webhook 去重）
│   │   ├── envutil.py      # 统一 .env 加载
│   │   └── seo.py          # SEO 运行时注入 + robots/sitemap/llms.txt
│   ├── data/               # SQLite 数据库（app.db，自动创建，勿提交）
│   ├── cookies/
│   │   └── cookies.txt     # 其它平台可选 Cookie（抖音已无需；B站 AI 字幕需有效登录态）
│   ├── downloads/          # 下载临时目录（自动清理，勿存重要文件）
│   ├── static/             # 前端构建产物（vite outDir，构建自动生成）
│   ├── .env.example        # 环境变量模板（LLM_API_KEY / STRIPE_API_KEY 等）
│   ├── run.py              # 启动入口 (0.0.0.0:8000，支持 PORT 环境变量)
│   └── requirements.txt
└── frontend/               # Vue 3 + Vite 工程
```

## 本地运行

```bash
# 1. 后端（端口 8000）
cd backend
pip install -r requirements.txt
python run.py

# 2. 前端开发模式（端口 5173，已配置 /api 代理）
cd frontend
npm install
npm run dev

# 3. 前端构建（发布模式，产物输出到 backend/static，由 8000 端口直接托管）
cd frontend
npm run build
```

启动后访问 http://127.0.0.1:8000

> 注意：若部署机开了 Clash 等代理软件，国内平台（B站/抖音）建议直连；
> YouTube 是否可用取决于部署机的网络出口。本项目的抖音通道会强制绕过系统代理直连。

## Cookie 维护

**抖音已免 Cookie**：`backend/app/douyin.py` 走自研通道——服务端自动向字节官方接口铸造
**访客凭证 ttwid**（不含任何用户身份信息，仅存服务端内存，5 天自动轮换），再经 iesdouyin
移动分享页解析。用户全程零操作，无需登录、无需提供任何凭证。

其它平台如遇 Cookie 风控：用浏览器扩展（如 "Get cookies.txt LOCALLY"）导出对应站点 Cookie，
将 Netscape 格式内容放入 `backend/cookies/cookies.txt` 即可，无需重启服务。

## AI 视频总结配置

总结功能需要一个 OpenAI 兼容的大模型 API key（DeepSeek 或 智谱 GLM，国内直连无需代理）：

```bash
cd backend
cp .env.example .env    # 然后编辑 .env 填入 LLM_API_KEY
```

- DeepSeek：`LLM_BASE_URL=https://api.deepseek.com`、`LLM_MODEL=`
- 智谱 GLM：`LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4`、`LLM_MODEL=`
- `AI_DAILY_LIMIT=3`：免费 IP 每日总结次数
- 未配置 key 时接口返回「AI 总结功能正在开通中」，不影响下载功能
- `LLM_PROVIDER=mock` 仅限本地前端联调（返回固定模拟数据），上线前必须去掉

**字幕获取方式（全站用户零配置，无需登录）**：

- **B站**：走自研 `dm/view` 弹幕池通道——免登录获取原生中文/AI 中文字幕（含多语翻译轨），
  原生字幕优先于 AI 字幕。实测长视频可拿数百句带时间戳字幕
- **YouTube**：走 yt-dlp 自动字幕，语言智能排序（中文 > 视频原语言 > 英文 > 任意），
  日/韩等任何语言的视频都能总结；字幕内容下载遇 429 限流自动退避重试
- **抖音**：无字幕，自动降级为「标题+简介（+热门评论）」简略总结，前端徽标如实标注依据
- 结果中的「字幕时间线」选项卡保留全量带时间戳字幕，并提供 **SRT / TXT 下载**
- 喂给 LLM 的字幕素材每行带 `[分:秒]` 时间戳，章节大纲的时间为素材真实时间（非估算）

> 验证的账号 Cookie（见上方 Cookie 维护），配置后自动带 Cookie 定向重试。

## 支付与会员（Stripe 已接入）

完整架构、环境变量、Stripe Dashboard 配置步骤与测试卡见 **[docs/06-支付与会员系统.md](docs/06-支付与会员系统.md)**。快速版：

1. `backend/.env` 填入 `STRIPE_API_KEY=sk_test_...`（Dashboard → Test Mode → API keys）
2. 本地 webhook：安装 Stripe CLI → `stripe listen --forward-to localhost:8000/api/pay/stripe/webhook`，把输出的 `whsec_...` 填入 `STRIPE_WEBHOOK_SECRET`
3. 测试卡 `4242 4242 4242 4242`（任意未来有效期/CVC）；上线切换 Live 密钥 + Dashboard 配置公网 webhook endpoint

支付逻辑抽象于 `backend/app/payments.py` 的 `PaymentProvider` 接口，`StripeProvider` / `MockProvider` 由 `STRIPE_API_KEY` 是否存在自动切换；未配置密钥时本地仍可走完整模拟流程。

## 已知限制

- 抖音图集/图文内容暂不支持（明确报错）
- AI 总结：抖音等无字幕平台走降级总结（基于标题/简介/热门评论）；YouTube 字幕接口高频使用会临时 429（自动重试+降级）；总结结果与字幕时间线 30 分钟后自动清理
- 下载文件 30 分钟后自动清理，请及时保存

## 免责声明

本项目仅作技术演示。所有视频内容版权归原平台及作者所有，下载内容仅供个人学习与研究，
请遵守各平台用户协议及相关法律法规。
