# 极易下 - 万能视频下载网站

基于开源项目 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 封装的视频下载网站。
前端 Vue 3 + Vite，后端 FastAPI，无数据库（任务状态存内存），轻量单服务部署。

## 功能

- 粘贴链接即可解析下载，支持 1000+ 平台（B站 / 抖音 / YouTube / 小红书 等）
- 多清晰度选择（360p ~ 4K），仅音频提取
- 下载进度实时显示，文件保留 30 分钟自动清理
- 会员定价展示 + 支付流程（当前为**模拟支付**，预留真实渠道接入接口）

## 目录结构

```
video-downloader/
├── backend/
│   ├── app/
│   │   ├── main.py        # FastAPI 路由 + 前端静态托管
│   │   ├── downloader.py  # yt-dlp 封装（解析/下载/进度/清理）
│   │   └── payments.py    # 支付抽象层 + 模拟支付实现
│   ├── cookies/
│   │   └── cookies.txt    # Netscape 格式 Cookie（抖音等平台需要，见下文）
│   ├── downloads/         # 下载临时目录（自动清理）
│   ├── run.py             # 启动入口 (0.0.0.0:8000)
│   └── requirements.txt
└── frontend/              # Vue 3 + Vite 工程，构建产物 dist/ 由后端托管
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

# 3. 前端构建（发布模式，由 8000 端口直接托管）
cd frontend
npm run build
```

启动后访问 http://127.0.0.1:8000

## Cookie 维护（重要）

抖音等平台有 Cookie 风控。若抖音解析报错"需要 Cookie"：

1. 本机浏览器打开 douyin.com（保持登录或游客均可）
2. 使用浏览器扩展（如 "Get cookies.txt LOCALLY"）导出 douyin.com 的 Cookie（Netscape 格式）
3. 将内容替换 `backend/cookies/cookies.txt` 中 `.douyin.com` 的行，无需重启服务

当前 cookies.txt 内已包含一组有效的抖音 Cookie（有效期约 90 天），失效后按上述步骤更新即可。
其它平台同理：导出对应域名的 Cookie 追加进同一文件。

## 接入真实支付

支付逻辑已抽象为 `backend/app/payments.py` 中的 `PaymentProvider` 接口：

- `create_payment(order)` → 返回收银台信息（跳转链接/二维码）
- `query_payment(order_id)` → 查询支付状态
- `verify_notify(payload)` → 校验渠道异步回调签名

接入虎皮椒/蓝兔/微信商户号时，实现该接口并替换模块级 `provider` 实例即可，
路由与前端代码零改动。当前 `MockProvider` 用于演示（前端弹模拟收银台）。

## 免责声明

本项目仅作技术演示。所有视频内容版权归原平台及作者所有，下载内容仅供个人学习与研究，
请遵守各平台用户协议及相关法律法规。
