# 项目长期记忆 — 万能视频下载网站（极易下）

## 项目概况
- 路径：`D:\code\video-downloader`；`backend/`（FastAPI + yt-dlp，端口 8000，无数据库）+ `frontend/`（Vue 3 + Vite）
- 前端构建产物输出到 `backend/static`（vite outDir），由 FastAPI 托管 → 单服务单端口部署
- UI 仿 https://ai.codefather.cn/painting：浅色 + 蓝色强调 (#3370ff) + Hero 渐变标题 + 卡片流
- 已发布线上（2026-10-08）：https://yijixia-video-downloader.app.workbuddy.host/ （应用名「极易下视频下载」）

## 关键约定/经验
- **支付**：`backend/app/payments.py` 有 PaymentProvider 抽象 + MockProvider（模拟收银台）；接真实渠道（虎皮椒/蓝兔等）只需实现接口替换 `provider`，前后端零改动
- **Cookie**：`backend/cookies/cookies.txt`（Netscape 格式）解决抖音风控；yt-dlp 运行时会自动回写新 Cookie 到该文件；抖音 Cookie 约 90 天失效，需用浏览器扩展重新导出
- **沙箱代理坑**：本机开发时必须用 `env -u http_proxy -u https_proxy ...` 启动后端，否则继承 WorkBuddy 沙箱代理 (127.0.0.1:4185) 导致 YouTube 502
- **发布沙箱限制**：云端部署后解析接口不可用（B站被云 IP 风控、YouTube 出口不通），但应用本身正常；完整功能只能本地跑或部署到自有服务器
- **无 ffmpeg 环境自动降级**：只提供渐进式格式（不合并），禁用音频提取
- 测试链接：B站 BV1GJ411x7h7、YouTube dQw4w9WgXcQ 可用；抖音 7692329355342679331（注意抖音视频 ID 会失效）
- 用户偏好：前端用 Vue（Vite 工程）；文档另存新文件；不确定先问
