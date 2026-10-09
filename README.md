# 极易下 - 万能视频下载网站

基于开源项目 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 封装的视频下载网站。
前端 Vue 3 + Vite，后端 FastAPI，无数据库（任务状态存内存），轻量单服务部署。

| 文档 | 说明 |
|------|------|
| [docs/01-需求分析.md](docs/01-需求分析.md) | 背景、用户场景、功能/非功能需求、风险对策 |
| [docs/02-方案设计.md](docs/02-方案设计.md) | 架构、选型理由、核心流程、抖音通道与支付模块设计 |
| [docs/03-AI视频总结功能方案.md](docs/03-AI视频总结功能方案.md) | AI 总结：竞品调研、字幕/降级路线、LLM 接入、限流设计 |
| 本文档（README） | 快速上手与运维手册 |

## 功能

- 粘贴链接即可解析下载，支持 1000+ 平台（B站 / 抖音 / YouTube / 小红书 等）
- 多清晰度选择（360p ~ 4K），仅音频提取
- 抖音免 Cookie、无水印，支持长链 / 短链 / App 分享口令文本
- **AI 视频总结**：解析后一键生成概述、核心要点、章节大纲（时间戳可跳转）与思维导图；字幕优先、无字幕自动降级并如实标注依据
- 下载进度实时显示，文件与总结结果保留 30 分钟自动清理
- 会员定价展示 + 支付流程（当前为**模拟支付**，预留真实渠道接入接口）

## 平台支持现状（实测）

| 平台 | 本地部署 | 云沙箱部署（WorkBuddy 发布） | 说明 |
|------|:---:|:---:|------|
| 抖音 | ✅ | ✅ | 自研免 Cookie 通道，无水印，支持口令/短链/精选页链接 |
| B站 | ✅ | ⚠️ 云 IP 被风控 | 标准 yt-dlp 路径 |
| YouTube | ✅（需自动检测 node） | ❌ 沙箱缺 JS 运行时 + 出口受限 | 解析已异步化，不受网关超时影响 |
| 其它 yt-dlp 平台 | ✅ | ⚠️ 视平台风控而定 | 云端 IP 信誉不如家宽/自有服务器 |

> YouTube 依赖新版 yt-dlp 的 JS 运行时（自动检测部署机上的 node 并启用官方挑战求解脚本）
> 与可达的网络出口；国内直连 YouTube 不稳定，服务器部署时可创建 `backend/proxy.txt`
> 写入代理地址（如 `http://127.0.0.1:7890`）。年龄限制视频需登录验证，暂不支持。

> 云沙箱的局限是平台侧网络环境所致，代码与本地完全一致。需要完整多平台能力时，
> 将本项目部署到国内 VPS 即可（代码零改动）。

## 目录结构

```
video-downloader/
├── docs/                   # 需求分析 + 方案设计文档
├── backend/
│   ├── app/
│   │   ├── main.py         # FastAPI 路由 + 前端静态托管 + 异常兜底
│   │   ├── downloader.py   # 下载调度（平台分流 / 任务表 / 清理）
│   │   ├── douyin.py       # 抖音免 Cookie 通道（自研：访客 ttwid + iesdouyin 分享页）
│   │   ├── summarizer.py   # AI 视频总结（字幕提取 / LLM 抽象 / 限流 / 总结任务表）
│   │   └── payments.py     # 支付抽象层 + 模拟支付实现
│   ├── cookies/
│   │   └── cookies.txt     # 其它平台可选 Cookie（抖音已无需；B站 AI 字幕需有效登录态）
│   ├── downloads/          # 下载临时目录（自动清理，勿存重要文件）
│   ├── static/             # 前端构建产物（vite outDir，构建自动生成）
│   ├── .env.example        # AI 总结环境变量模板（复制为 .env 填 LLM_API_KEY）
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

- DeepSeek：`LLM_BASE_URL=https://api.deepseek.com`、`LLM_MODEL=deepseek-chat`
- 智谱 GLM：`LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4`、`LLM_MODEL=glm-4-flash`
- `AI_DAILY_LIMIT=3`：免费 IP 每日总结次数
- 未配置 key 时接口返回「AI 总结功能正在开通中」，不影响下载功能
- `LLM_PROVIDER=mock` 仅限本地前端联调（返回固定模拟数据），上线前必须去掉

> 字幕来源说明：B站 AI 字幕需要有效登录 Cookie（见下方 Cookie 维护）；
> 抖音等无字幕平台自动降级为「标题+简介」简略总结，前端会标注总结依据。

## 接入真实支付

支付逻辑已抽象为 `backend/app/payments.py` 中的 `PaymentProvider` 接口：

- `create_payment(order)` → 返回收银台信息（跳转链接/二维码）
- `query_payment(order_id)` → 查询支付状态
- `verify_notify(payload)` → 校验渠道异步回调签名

接入虎皮椒/蓝兔/微信商户号时，实现该接口并替换模块级 `provider` 实例即可，
路由与前端代码零改动。当前 `MockProvider` 用于演示（前端弹模拟收银台）。

## 已知限制

- 抖音图集/图文内容暂不支持（明确报错）
- AI 总结：B站 AI 字幕需有效登录 Cookie（未登录走降级总结）；抖音无字幕走降级总结；总结结果 30 分钟后自动清理
- 云沙箱部署时 B站/YouTube 等受平台网络环境限制（见上方平台支持现状表）
- 下载文件 30 分钟后自动清理，请及时保存

## 免责声明

本项目仅作技术演示。所有视频内容版权归原平台及作者所有，下载内容仅供个人学习与研究，
请遵守各平台用户协议及相关法律法规。
