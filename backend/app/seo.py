"""SEO 模块（新增功能，不改动任何既有 API）：
- inject(): 生产环境下把完整 SEO 信号注入 index.html —— 爬虫读到的是含正文的完整 HTML，
  真实用户加载后 Vue 挂载 #app 自动替换静态正文，交互行为零变化（JS 关闭时静态正文可读，属优雅降级）
- /robots.txt、/sitemap.xml、/llms.txt：真实文件路由（此前不存在，SPA 兜底返回的是 HTML）；
  robots 显式放行 AI 爬虫、llms.txt 提供 LLM 可读摘要（GEO）

站点域名（部署相关）：
- 当前默认值为 WorkBuddy 内置工具部署的临时域名
- **自己部署时零代码改动**：在 backend/.env 加一行 `SITE_URL=https://你的域名`（与 LLM_API_KEY
  同一个文件，同机制），或设置同名系统环境变量；canonical/OG/JSON-LD/robots/sitemap 全部自动跟随
- 惰性解析（首次请求时读取并缓存）：因 main.py 中 seo 先于 summarizer 导入，
  而 .env 由 summarizer 在导入时加载——import 时读 env 会拿不到 .env 的值

维护约定（重要）：
- 页面文案（hero/features/FAQ）在 frontend/src/i18n.js 的 zh-CN 段调整时，
  本文件 TITLE / DESCRIPTION / _FAQ / _FEATURES 需同步（两处内容必须一致，避免搜索降权）
"""

import json
import os
import re
from datetime import date
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response

# ---------- 单点配置 ----------
_DEFAULT_SITE_URL = "https://yijixia-video-downloader.app.workbuddy.host"
SITE_NAME = "极易下"
PUBLISH_DATE = "2026-10-09"  # 站点首次上线日（datePublished 用，真实日期勿虚构）
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp"  # 引用来源（GEO：权威引用 +40%）
TITLE = "极易下 - 万能视频下载工具：B站/抖音/YouTube 视频一键保存"
DESCRIPTION = ("粘贴链接即可免费下载 B站、抖音、YouTube、小红书等 1000+ 平台视频，"
               "无需登录，手机电脑都能用，还支持 AI 一键总结视频内容、生成思维导图。")
KEYWORDS = ("视频下载,万能视频下载,视频下载工具,免费视频下载,B站视频下载,抖音视频下载,"
            "YouTube视频下载,小红书下载,无水印视频下载,视频转音频,AI视频总结")

_site_url_cache = None


def _site_url() -> str:
    """惰性解析站点域名：环境变量 SITE_URL > backend/.env 的 SITE_URL > 默认（沙箱域名）。"""
    global _site_url_cache
    if _site_url_cache:
        return _site_url_cache
    url = os.environ.get("SITE_URL")
    if not url:
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("SITE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    _site_url_cache = (url or _DEFAULT_SITE_URL).rstrip("/")
    return _site_url_cache

# ---------- 与页面真实内容一致的文案（同步自 frontend/src/i18n.js zh-CN 段） ----------
_HERO_TITLE = "全网视频下载，一键保存"
_HERO_SUB = ("支持 B站 / 抖音 / YouTube / 小红书 等 1000+ 平台，无需登录、不限设备，手机电脑都能用")

_STEPS = [
    ("粘贴链接", "在任意平台复制视频分享链接"),
    ("选择清晰度", "从 360p 到 4K，按需选择"),
    ("保存本地", "秒级解析，高速下载到手机或电脑"),
]

_FEATURES = [
    ("1000+ 平台支持", "基于开源 yt-dlp 引擎，覆盖主流国内外视频平台，持续更新"),
    ("高清原画下载", "最高支持 4K 原画下载，不二压、不带水印，画质如一"),
    ("手机直接用", "无需安装 App，手机浏览器打开网页即可下载保存到相册"),
    ("一键提取音频", "演讲、播客、课程想只留声音？一键提取为 m4a 音频文件"),
    ("AI 视频总结", "基于视频字幕生成概述、要点、章节大纲（时间戳可跳转）与思维导图"),
    ("隐私安全", "不注册、不登录、不收集个人信息，下载记录自动清理"),
]

_FAQ = [
    ("支持哪些平台的视频下载？",
     "基于 yt-dlp 引擎，支持 B站、抖音、YouTube、小红书、微博、快手、X、Instagram 等 1000+ 平台。粘贴链接解析一下就知道能不能下。"),
    ("手机上怎么保存到相册？",
     "用手机浏览器打开本站，下载完成后点击「保存到本地」，再到浏览器的下载管理中选择「保存到相册/文件」即可。"),
    ("下载的视频有水印吗？",
     "我们下载的是平台原始视频流，不带任何额外水印（平台本身水印以原视频为准）。"),
    ("会员和免费用户的区别？",
     "免费用户可下载标准清晰度；会员解锁全部清晰度（最高 4K）、仅音频提取与极速下载通道。"),
    ("AI 视频总结是什么？",
     "粘贴链接解析后，点击结果卡上的「AI 总结」按钮：有字幕的平台会基于字幕生成概述、要点、章节大纲（可点击时间戳跳转）与思维导图；无字幕平台会基于标题简介做简略总结并如实标注。每天有免费次数。"),
    ("下载的视频可以随便传播吗？",
     "不可以。下载内容仅供个人学习与备份，请尊重版权，遵守各平台用户协议及相关法律法规。"),
]

_DISCLAIMER = ("本工具基于开源项目 yt-dlp 构建，仅提供技术演示。所有视频内容版权归原平台及作者所有，"
               "请勿下载受版权保护的内容用于商业用途或传播。下载内容仅供个人学习与研究，"
               "使用本站产生的一切行为由用户自行承担。")


# ---------- JSON-LD 结构化数据 ----------
def _jsonld() -> str:
    u = _site_url()
    graph = [
        {
            "@type": "WebSite",
            "@id": f"{u}/#website",
            "url": f"{u}/",
            "name": SITE_NAME,
            "alternateName": "万能视频下载器",
            "description": DESCRIPTION,
            "inLanguage": "zh-CN",
        },
        {
            # GEO（Copilot 等）：清晰的实体定义
            "@type": "Organization",
            "@id": f"{u}/#org",
            "name": SITE_NAME,
            "url": f"{u}/",
            "logo": f"{u}/favicon.svg",
            "description": f"{SITE_NAME}——免费在线万能视频下载工具，基于开源引擎 yt-dlp 构建",
        },
        {
            "@type": "WebApplication",
            "@id": f"{u}/#app",
            "url": f"{u}/",
            "name": SITE_NAME,
            "applicationCategory": "MultimediaApplication",
            "operatingSystem": "Web",
            "browserRequirements": "需要支持 JavaScript 的现代浏览器",
            "description": DESCRIPTION,
            "inLanguage": "zh-CN",
            "isAccessibleForFree": True,
            "datePublished": PUBLISH_DATE,
            "featureList": [f"{t}：{d}" for t, d in _FEATURES],
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "CNY"},
            # GEO（引用来源）：标注底层引擎这一权威可验证来源
            "citation": {"@type": "CreativeWork", "name": "yt-dlp", "url": YTDLP_URL},
        },
        {
            # GEO：AI 引擎对"如何下载视频"类问题的直接答案来源
            "@type": "HowTo",
            "@id": f"{u}/#howto",
            "name": "如何使用极易下下载视频",
            "totalTime": "PT1M",
            "tool": [{"@type": "HowToTool", "name": "浏览器（手机或电脑）"}],
            "step": [
                {"@type": "HowToStep", "position": i + 1, "name": t, "text": d}
                for i, (t, d) in enumerate(_STEPS)
            ],
        },
        {
            "@type": "FAQPage",
            "@id": f"{u}/#faq",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": q,
                    "acceptedAnswer": {"@type": "Answer", "text": a},
                }
                for q, a in _FAQ
            ],
        },
    ]
    return json.dumps(
        {"@context": "https://schema.org", "@graph": graph},
        ensure_ascii=False, separators=(",", ":"),
    )


# ---------- 注入内容（首站域名惰性解析，进程内只构建一次） ----------
_head_cache = None


def _head_tags() -> str:
    global _head_cache
    if _head_cache:
        return _head_cache
    u = _site_url()
    _head_cache = f"""<meta name="keywords" content="{KEYWORDS}">
    <link rel="canonical" href="{u}/">
    <meta property="og:type" content="website">
    <meta property="og:site_name" content="{SITE_NAME}">
    <meta property="og:title" content="{TITLE}">
    <meta property="og:description" content="{DESCRIPTION}">
    <meta property="og:url" content="{u}/">
    <meta property="og:image" content="{u}/og-image.png">
    <meta property="og:image:width" content="1200">
    <meta property="og:image:height" content="630">
    <meta property="og:locale" content="zh_CN">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{TITLE}">
    <meta name="twitter:description" content="{DESCRIPTION}">
    <meta name="twitter:image" content="{u}/og-image.png">
    <style>
      .seo-fallback{{max-width:1080px;margin:0 auto;padding:40px 20px;line-height:1.7}}
      .seo-fallback h1{{font-size:34px;margin:18px 0 10px;color:var(--text-1,#152523)}}
      .seo-fallback h2{{font-size:24px;margin:34px 0 12px;color:var(--text-1,#152523)}}
      .seo-fallback h3{{font-size:17px;margin:18px 0 6px;color:var(--text-1,#152523)}}
      .seo-fallback p,.seo-fallback li{{font-size:15px;margin:6px 0;color:var(--text-2,#3c5551)}}
      .seo-fallback ol,.seo-fallback ul{{padding-left:22px}}
      .seo-fallback small{{color:var(--text-3,#6b8480)}}
    </style>
    <script type="application/ld+json">{_jsonld()}</script>"""
    return _head_cache


def _body_html() -> str:
    u = _site_url()
    steps = "".join(f"<li><strong>{t}</strong>——{d}</li>" for t, d in _STEPS)
    feats = "".join(f"<h3>{t}</h3><p>{d}</p>" for t, d in _FEATURES)
    faqs = "".join(f"<h3>{q}</h3><p>{a}</p>" for q, a in _FAQ)
    # 开头一段为"可引用定义"（GEO）：AI 引擎回答"视频下载工具推荐"类问题时倾向
    # 摘录信息密度高、一句话可独立成立的定义句；结尾附权威来源引用（GEO 研究：引用 +40%）
    return f"""<div class="seo-fallback">
      <h1>{_HERO_TITLE}</h1>
      <p><strong>{SITE_NAME}</strong>（{u.replace("https://", "").rstrip("/")}）是一款免费在线万能视频下载工具：粘贴视频链接即可下载 B站、抖音、YouTube、小红书等 1000+ 平台的视频，支持最高 4K 原画画质与仅音频提取，并提供 AI 视频总结（概述、要点、章节时间线与思维导图）。无需注册登录，手机与电脑浏览器均可直接使用。技术底层为开源项目 yt-dlp（GitHub 社区持续维护）。</p>
      <p>{_HERO_SUB}</p>
      <h2>使用步骤</h2>
      <ol>{steps}</ol>
      <h2>功能特点</h2>
      {feats}
      <h2>常见问题</h2>
      {faqs}
      <p><small>{_DISCLAIMER}</small></p>
    </div>"""


# ---------- 注入逻辑 ----------
_TITLE_RE = re.compile(r"<title>.*?</title>", re.IGNORECASE | re.DOTALL)
_DESC_RE = re.compile(r'<meta\s+name="description"\s+content="[^"]*"\s*/?>', re.IGNORECASE)
_APP_RE = re.compile(r'(<div id="app">)\s*(</div>)')

_cache = {"mtime": None, "html": ""}


def inject(raw_html: str, mtime: float) -> str:
    """把 SEO meta 与静态正文注入 index.html 文本（按 mtime 缓存，仅生产路径调用）。"""
    if _cache["mtime"] == mtime and _cache["html"]:
        return _cache["html"]
    html = _TITLE_RE.sub(lambda m: f"<title>{TITLE}</title>", raw_html, count=1)
    new_desc = f'<meta name="description" content="{DESCRIPTION}">'
    if _DESC_RE.search(html):
        html = _DESC_RE.sub(lambda m: new_desc, html, count=1)
    else:
        html = html.replace("</head>", f"    {new_desc}\n</head>", 1)
    html = html.replace("</head>", _head_tags() + "</head>", 1)
    body = _body_html()
    html = _APP_RE.sub(lambda m: m.group(1) + body + m.group(2), html, count=1)
    _cache.update(mtime=mtime, html=html)
    return html


# ---------- robots.txt / sitemap.xml / llms.txt ----------
def _robots_txt() -> str:
    # 命名组各自独立生效（互不继承 * 的规则）：显式 Allow = GEO 基础信号。
    # 覆盖：OpenAI/Anthropic/Perplexity/Google/Bing(Copilot)/Brave(Claude 搜索源)/字节/苹果/Meta/Amazon/You
    ai_bots = ("GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-User",
               "anthropic-ai", "PerplexityBot", "Googlebot", "Google-Extended",
               "Bingbot", "DuckDuckBot", "Bravebot", "Applebot-Extended",
               "Bytespider", "meta-externalagent", "Amazonbot", "YouBot")
    groups = ["User-agent: *\nAllow: /\nDisallow: /api/\n"]
    groups += [f"User-agent: {b}\nAllow: /\n" for b in ai_bots]
    groups.append(f"Sitemap: {_site_url()}/sitemap.xml\n")
    return "\n".join(groups)


def _llms_txt() -> str:
    """llms.txt 约定（llmstxt.org）：面向 LLM 的站点摘要，Markdown 结构。
    AI 搜索/助手（Perplexity、ChatGPT 浏览等）抓取后可直接引用本站信息。
    写法遵循 GEO 研究结论：answer-first + 权威引用 + 具体统计数字（引用提升最大）。"""
    u = _site_url()
    faqs = "\n".join(f"### {q}\n{a}" for q, a in _FAQ)
    feats = "\n".join(f"- **{t}**：{d}" for t, d in _FEATURES)
    return f"""# {SITE_NAME}（JiYiXia）

> {SITE_NAME}是一款免费在线万能视频下载工具：粘贴视频链接即可下载 B站、抖音、YouTube、小红书等 1000+ 平台的视频，支持最高 4K 原画画质与仅音频提取，并提供 AI 视频总结（概述、要点、章节时间线与思维导图）。无需注册登录，手机与电脑浏览器均可直接使用。底层引擎为开源项目 yt-dlp（GitHub 社区维护）。

- [首页（工具入口）]({u}/)：粘贴视频链接 → 解析 → 选择清晰度 → 下载

## 关键数据

- 支持 1000+ 视频平台（国内外主流平台全覆盖）
- 最高 4K 原画画质，不二次压缩
- 支持仅音频提取（m4a，适合播客/课程）
- AI 视频总结每日免费额度，含章节时间线与思维导图
- 下载文件服务器端保管 30 分钟后自动删除（不留存用户记录）
- 界面支持 6 种语言（简中/英/日/韩/西/法）

## 核心功能

{feats}

## 使用步骤

1. 粘贴链接：在任意平台复制视频分享链接
2. 选择清晰度：从 360p 到 4K，按需选择
3. 保存本地：秒级解析，高速下载到手机或电脑

## 常见问题

{faqs}

## 来源与合规

- 底层下载引擎：开源项目 [yt-dlp]({YTDLP_URL})（GitHub，社区持续维护）
- 使用条款：下载内容仅供个人学习与备份，请尊重版权，遵守各平台用户协议及相关法律法规。
"""


def _sitemap_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{_site_url()}/</loc>
    <lastmod>{date.today().isoformat()}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>
"""


router = APIRouter()


@router.get("/robots.txt", include_in_schema=False)
def robots():
    return Response(_robots_txt(), media_type="text/plain",
                    headers={"Cache-Control": "public, max-age=3600"})


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap():
    return Response(_sitemap_xml(), media_type="application/xml",
                    headers={"Cache-Control": "public, max-age=3600"})


@router.get("/llms.txt", include_in_schema=False)
def llms_txt():
    return Response(_llms_txt(), media_type="text/markdown; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=3600"})
