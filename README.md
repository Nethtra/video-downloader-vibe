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
│   │   ├── downloader.py  # 下载调度（yt-dlp 封装 + 平台分流）
│   │   ├── douyin.py      # 抖音免 Cookie 通道（自研：访客 ttwid + iesdouyin 分享页）
│   │   └── payments.py    # 支付抽象层 + 模拟支付实现
│   ├── cookies/
│   │   └── cookies.txt    # 其它平台可选 Cookie（抖音已无需）
│   ├── downloads/         # 下载临时目录（自动清理）
│   ├── run.py             # 启动入口 (0.0.0.0:8000，支持 PORT 环境变量)
│   └── requirements.txt
└── frontend/              # Vue 3 + Vite 工程，构建产物输出到 backend/static
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

**抖音已免 Cookie**：`backend/app/douyin.py` 走自研通道——服务端自动向字节官方接口铸造**访客凭证 ttwid**
（不含任何用户身份信息，仅存服务端内存，5 天自动轮换），再经 iesdouyin 移动分享页解析，
支持长链 / 短链 / App 分享口令文本，下载无水印视频，可选 ffmpeg 提取音频。用户全程零操作。

其它平台如遇 Cookie 风控：用浏览器扩展（如 "Get cookies.txt LOCALLY"）导出对应站点 Cookie，
将 Netscape 格式内容放入 `backend/cookies/cookies.txt` 即可，无需重启服务。

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
