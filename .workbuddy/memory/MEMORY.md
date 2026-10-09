# 项目长期记忆 — 万能视频下载网站（极易下）

> 更新：2026-10-09（功能全部完成、文档已沉淀、已发布上线）
> 详细过程看 `2026-10-08.md` / `2026-10-09.md` 日志；正式文档在项目 `docs/` 目录

## 项目概况
- 路径：`D:\code\video-downloader`
  - `backend/`：FastAPI + yt-dlp（端口 8000，无数据库，任务存内存，文件 30 分钟 TTL）
  - `frontend/`：Vue 3 + Vite（构建产物输出到 `backend/static`，由 FastAPI 托管）
  - `docs/`：需求分析（01）+ 方案设计（02，含抖音通道设计与踩坑记录）
- UI 仿 codefather painting：浅色 + 蓝色强调 #3370ff + Hero 渐变标题 + 卡片流 + 定价区
- 状态：**P0/P1 功能全部完成并上线**；P2 待办（批量下载、真实支付、会员权益门禁）

## 线上与部署
- 线上链接：https://yijixia-video-downloader.app.workbuddy.host/ （应用名「极易下视频下载」）
- **用户硬性要求：每次改动都要同步到线上**。deploy 参数（workbuddy_sites_deploy）：
  `directory=D:\code\video-downloader\backend, language=python, installCmd="pip install -r requirements.txt", startCmd="python run.py", domainPrefix=yijixia-video-downloader, userAskedToPublish=true`
- 线上平台可用性（实测）：抖音✅（自研通道云端可用）/ B站❌（云 IP 风控）/ YouTube❌（出口不通）/ 其它 yt-dlp 平台多数受云 IP 影响问

## 关键模块地图
- `backend/app/downloader.py`：下载调度——URL 判定平台分流（douyin → 自研，其余 → yt-dlp）、任务内存表、线程池(max 3)、清理
- `backend/app/douyin.py`：抖音免 Cookie 通道——自铸访客 ttwid（字节官方接口，内存缓存 5 天）→ 移动 UA 请求 iesdouyin 分享页 `_ROUTER_DATA` → `playwm→play` 无水印直链；**强制 ProxyHandler({}) 绕过系统代理**；ID 提取覆盖 /video/ /note/ 任意路径 `?modal_id=` / v.douyin.com 短链 / 口令文本
- `backend/app/payments.py`：PaymentProvider 抽象 + MockProvider；接真实渠道只需实现接口替换 `provider`
- 其它平台 Cookie（可选）：`backend/cookies/cookies.txt`（Netscape 格式，yt-dlp 会自动回写）

## 踩坑经验（勿重蹈）
- **Python urllib 读 Windows 注册表系统代理**（本机残留 127.0.0.1:4185）：`env -u` 清环境变量不够；curl 测通≠Python 测通，网络验证必须同技术栈（已修：douyin.py 强制直连 + 重试 + 三层异常兜底）
- WorkBuddy 发布沙箱：仅一个 HTTP 端口、无数据库；run.py 必须读 `PORT` 环境变量
- 无 ffmpeg 环境自动降级：只给渐进式格式、禁音频提取
- 抖音 URL 形态远比想象多：ID 提取按**参数**匹配而非路径匹配；抖音视频 ID 会失效，测试要用实时链接
- 抖音图集/图文暂不支持（明确报错）；超过 100MB 的原画文件下载较慢

## 测试资源
- B站：BV1GJ411x7h7；YouTube：dQw4w9WgXcQ（直连时好时坏，需代理）；抖音：7692329355342679331、精选页 modal_id=7685717085573434664 / 7691552296500595978
- 本地启动：`cd backend && python run.py`（若走沙箱 shell 需 `env -u http_proxy -u https_proxy ...`）

## 协作约定（本项目确立）
- 改动完成后：本地实测 → 同步线上 → 线上实测，全部通过才报完成
- 用户偏好：前端用 Vue；文档另存新文件；不确定先问
