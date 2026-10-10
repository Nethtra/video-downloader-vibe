# 05 - SEO 优化与运营清单（2026-10-10）

> 本文档沉淀 SEO 审计结论、本次改动的技术方案、以及需要站长手工完成的搜索引擎提交步骤。
> 前置文档：01-需求分析 / 02-方案设计 / 03-AI视频总结 / 04-功能扩展与前端改版。

## 1. 审计结论（改动前）

| 问题 | 等级 | 说明 |
|---|---|---|
| SPA 空壳 | **致命** | 线上 HTML body 只有一个 `<div id="app"></div>`，正文全靠 JS 渲染。百度基本不渲染 JS，抓到空页；必应/搜狗很弱；Google 会渲染但慢。不解决则收录概率极低 |
| robots.txt / sitemap.xml 不存在 | 高 | SPA 兜底路由把 HTML 顶了上去（200 但内容是网页），爬虫拿不到抓取规则与站点地图 |
| meta 信号缺失 | 高 | 无 canonical、无 Open Graph/Twitter Card（社交分享无卡片）、无 JSON-LD 结构化数据 |
| 搜索引擎零收录 | 中 | site: 查询各引擎均无结果，需要站长平台提交启动收录 |
| 单页内容局限 | 低 | "B站视频下载"等平台词缺少对应落地页，列为 P2（见 §5） |

已合格项：lang=zh-CN、H1 唯一、H2 层级合理、移动端响应式、HTTPS、主域无平台级爬虫屏蔽。

## 2. 技术方案：后端运行时注入（开闭原则）

**为什么选后端注入而不是构建时预渲染**：全站单页无路由，两者效果等价；后端注入不碰 vite 8 (rolldown) 构建链（预渲染插件兼容性有风险）、前端代码零改动。

**原理**：FastAPI 的 SPA 兜底路由返回 index.html 前，把 SEO 信号注入进去——
- `<head>`：替换/补全 title、description，追加 keywords、canonical、OG、Twitter Card、JSON-LD（`@graph`：WebSite + WebApplication + FAQPage）
- `<body>`：`<div id="app">` 内部填入与页面真实内容一致的静态正文（H1/步骤/功能/FAQ/免责声明）
- **爬虫**（不执行 JS）：直接读到完整 meta + 正文
- **真实用户**（执行 JS）：Vue 挂载后自动替换 `#app` 内的静态正文，交互零变化；JS 关闭时静态正文可读（优雅降级）
- 内容一致 → 不构成 cloaking；静态正文带独立兜底样式（App.vue 是 scoped 样式，编译产物选择器带 data-v 哈希，无法复用）

## 3. 改动清单

| 文件 | 类型 | 内容 |
|---|---|---|
| `backend/app/seo.py` | 新增 | SITE_URL 单点配置、TITLE/DESCRIPTION/KEYWORDS、FAQ/功能文案（同步自 i18n.js zh-CN）、JSON-LD、inject() 注入函数（按 index.html mtime 缓存）、robots.txt / sitemap.xml 路由 |
| `backend/app/main.py` | 微调 | `import seo`；`app.include_router(seo.router)`（必须在 catch-all 之前注册，否则被吞）；spa() 返回 `HTMLResponse(seo.inject(...))` |
| `frontend/public/og-image.png` | 新增 | 1200×630 分享卡片图（teal 品牌风），vite build 自动拷贝到 static/ |
| 前端源码 | **零改动** | — |

**维护约定（重要）**：
- **站点域名**：seo.py 惰性读取 `SITE_URL`（优先级：系统环境变量 > backend/.env > 默认沙箱域名）。
  自己部署时**零代码改动**——在 `backend/.env` 加一行 `SITE_URL=https://你的域名`（与 LLM_API_KEY 同一个文件），
  canonical/OG/Twitter/JSON-LD/robots.txt/sitemap.xml 全部自动跟随
- 页面文案（hero/features/FAQ）在 `frontend/src/i18n.js` zh-CN 段调整时，`seo.py` 的对应常量需同步（两处内容必须一致，不一致有搜索降权风险）

## 4. 站长提交清单（需要你手工完成，代码解决不了）

> ⚠️ **提交时机警告（2026-10-10 用户澄清后补充）**：`yijixia-video-downloader.app.workbuddy.host`
> 是 WorkBuddy 内置工具自动部署的临时域名，**不是你自己的部署**，后续你计划自己部署。
> 搜索引擎收录积累的权重挂在"提交时用的那个域名"上，换域名不会自动迁移——
> **建议等你自己部署、正式域名定下来后再做站长提交**；workbuddy 链接仅当测试/预览环境用。
> 如果已在临时域名上提交过收录，迁移时按 §4.1 处理。

> 原理：搜索引擎不会主动来抓新站，需要在站长平台验证域名 + 提交 sitemap 主动推送。
> 以下步骤里的域名以你自己部署的正式域名为准（记作 `https://你的域名/`）。

1. **Google Search Console**（https://search.google.com/search-console）
   - 添加资源 → 选"网址前缀"填 `https://你的域名/`
   - 验证方式推荐"HTML 文件"或"HTML 标记"（后者把 meta 标签发我，我加进 seo.py；HTML 文件放进 frontend/public/ 即可随构建发布）
   - 验证后：站点地图 → 提交 `https://你的域名/sitemap.xml`
   - 用"网址检查"输入首页 URL → 请求编入索引，通常 1~7 天开始收录
2. **Bing 站长工具**（https://www.bing.com/webmasters）
   - 支持直接从 Google Search Console 一键导入，验证后同样提交 sitemap；必应同时 feeds 雅虎/DuckDuckGo 等引擎
3. **百度搜索资源平台**（https://ziyuan.baidu.com）
   - 需百度账号验证站点；普通收录里提交 `https://你的域名/sitemap.xml`
   - 快速收录需要小程序/配额，普通提交即可，百度收录周期较长（2~4 周属正常）
4. **验证收录**：一周后搜 `site:你的域名`，出现结果即收录成功
5. **结构化数据验证**：把网址贴进 https://search.google.com/test/rich-results，应识别出 FAQ / WebApplication 富摘要

### 4.1 自己部署 / 换域名时的 SEO 迁移要点

自部署形态即 `docs/02 §6` 的"本地生产"：`npm run build` 后在服务器上 `python run.py`（读 `PORT`），域名/HTTPS 由你的 Nginx 或托管平台负责，**代码零改动**。SEO 相关步骤：

1. **部署后第一件事**：`backend/.env` 加 `SITE_URL=https://你的正式域名`，重启服务，`curl https://你的域名/robots.txt` 确认 Sitemap 行已是新域名
2. **提交新域名**：按 §4 用新域名走一遍 GSC/Bing/百度验证 + sitemap 提交
3. **若临时域名（workbuddy 子域）已提交过收录**：
   - GSC 用"地址变更"工具声明迁移；旧 sitemap 保留提交状态一段时间
   - 旧域名能否 301 取决于 WorkBuddy 沙箱是否还在跑这个应用——若停止部署则旧链接自然失效，权重迁移不完整。这正是"正式域名定了再提交"的原因
4. **不要两套域名同时提交**：两个域名内容相同且互相无 301 时，搜索引擎会自行挑选规范域名，分散权重

## 5. 后续方向（P2，本轮未做）

- **分平台 SEO 落地页**：`/bilibili`、`/douyin`、`/youtube` 等静态可收录页面，直接承接"B站视频下载"这类高意图长尾词（可与后端注入同一套机制实现）。**建议与自部署一起排期**——落地页在正式域名上发布才有意义
- **性能细节**：当前 JS/CSS 体积小（146KB/17KB），Core Web Vitals 大概率达标；上线后用 PageSpeed Insights 复测即可

## 6. GEO —— AI 搜索优化（2026-10-10 晚补充）

> GEO（Generative Engine Optimization）：让 AI 引擎（ChatGPT/Perplexity/豆包/Kimi 等）在回答
> "视频下载工具推荐""怎么下载 B站 视频"这类问题时**引用并推荐本站**。与传统 SEO 互补。

已实现（全部在 `backend/app/seo.py`，随 SPA 一起生效）：

| 手段 | 说明 |
|---|---|
| **llms.txt**（`/llms.txt`） | llmstxt.org 约定的 LLM 可读站点摘要：一句话定义 + 核心功能 + 使用步骤 + FAQ 全文。AI 助手抓取后可直接引用 |
| **robots.txt 显式放行 AI 爬虫** | GPTBot / OAI-SearchBot / ChatGPT-User / ClaudeBot / Claude-User / PerplexityBot / Google-Extended / Applebot-Extended / Bytespider（字节·豆包）/ meta-externalagent / Amazonbot 各自独立 Allow 组 |
| **HowTo 结构化数据** | JSON-LD @graph 新增 HowTo（三步使用流程），AI 引擎对"如何下载视频"类问题的直接答案来源 |
| **可引用定义段** | 静态正文 H1 下新增一句信息密度高的完整定义（品牌 + 类别 + 平台数 + 关键能力 + 免费/免登录），AI 倾向摘录此类句子 |

**GEO 内容维护原则**：AI 引擎偏好"可直接摘录的完整事实句"。以后改文案时保持 llms.txt、
静态正文定义段、FAQ 三处事实一致（品牌名、平台数量、免费/付费边界），出现矛盾表述会降低 AI 引用信任度。

**效果观测**：在 ChatGPT/Perplexity/豆包里问"B站视频怎么下载""有哪些万能视频下载工具"，
看是否引用本站；AI 引用比传统收录见效更快（数天~数周），前提是正式域名已提交收录。

### 6.1 GEO 二轮增强（2026-10-10 晚，按 seo-geo 技能最佳实践）

依据 GEO 研究（普林斯顿九法：引用来源 +40%、统计数字 +37%、权威语气 +25%、流畅度 +15~30%，关键词堆砌 -10% 反效果）：

| 增强 | 对应方法 |
|---|---|
| llms.txt 新增「关键数据」块（1000+ 平台/4K/30 分钟保管/6 语言/每日免费额度）+「来源与合规」节引用 yt-dlp GitHub 仓库 | 统计数字 + 引用来源 |
| 定义段（静态正文）与 llms.txt 摘要句末尾加"技术底层为开源项目 yt-dlp（GitHub 社区维护）" | 引用来源（不写具体 star 数，避免数据过期） |
| JSON-LD @graph 新增 **Organization** 节点（name/url/logo/description）；WebApplication 加 **datePublished**（2026-10-09，真实首日）与 **citation** 指向 yt-dlp | 实体定义（Copilot）/ 新鲜度 / 引用 |
| robots.txt 爬虫组扩到 17 个：新增 **Googlebot、Bingbot**（Copilot 引用的前提是被 Bing 收录）、**Bravebot**（Claude 搜索源是 Brave）、**DuckDuckBot、anthropic-ai、YouBot** | 平台特定：ChatGPT/Copilot/Claude 各自的抓取链路 |

**未采用项（诚实边界）**：不做 aggregateRating（无真实评分数据，伪造违反 Google 政策）、不写虚构专家语录（Quotation 法要求真实来源）、PDF 化站点内容（Perplexity 偏好 PDF，单页工具站收益不足，列观测项）。

**ChatGPT 新鲜度规则**：30 天内有更新的内容被引用率高 3.2 倍——sitemap lastmod 已按日动态生成；
后续功能更新时记得同步 llms.txt 与定义段的事实（三处一致原则）。

## 7. 深色模式（2026-10-10 晚，体验项，与 SEO 无关但同批交付）

- **三态切换**：跟随系统（默认）/ 浅色 / 深色，导航栏下拉选择，localStorage('theme') 持久化；auto 模式监听 `prefers-color-scheme` 实时跟随
- **实现**：`src/theme.js`（与 i18n.js 同款零依赖模式）+ style.css `html.dark` 变量覆盖（语义变量默认值=原硬编码值，浅色外观零变化）+ index.html 防闪烁内联脚本（Vue 挂载前设好初始类）
- **适配范围**：全站表面（卡片/输入框/导航/弹窗/徽章/滚动条）+ MindMap 工具条与全屏遮罩；**canvas/SVG 导出物固定白底黑字不变**（下载文件不随主题）
- 验证脚本：`.workbuddy/verify-theme.js`（playwright-core + 系统 Edge，五场景含解析回归），可复用于后续主题改动回归
