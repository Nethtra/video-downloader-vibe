"""
AI 视频总结模块 v2（SSE 流式版）。

流程：视频链接 → 提取字幕（yt-dlp subtitles/automatic_captions，保留时间线）
      → 拿不到字幕时降级为「标题+简介(+B站热门评论)」素材
      → LLM 流式总结（SSE 增量推给前端）→ 结构化解析（概述/要点/章节/思维导图）
      → 内存任务表存最终结果与字幕时间线。

设计借鉴：
- downloader.py 的内存任务表 + 线程池模式（独立线程池，互不挤占）
- payments.py 的 Provider 抽象模式（换 LLM 渠道只需替换 provider 实例）

v2 变更：
- 字幕解析保留时间线（transcript），为后续字幕提取功能铺路
- LLM 改为流式输出（chat_stream），前端 SSE 实时渲染
- 输出格式改为 markdown 分节 + ```mindmap JSON 围栏，服务端解析成结构化数据
- B站降级时抓取热门评论增强素材（无需登录）
"""
import json
import os
import re
import threading
import time
import datetime
import urllib.request
import urllib.error
import uuid
from pathlib import Path

import yt_dlp

from . import douyin as douyin_mod
from .downloader import (_apply_cookies, is_douyin_url,
                         YTDLP_ENHANCEMENTS, _get_proxy)

# ---------- 配置（读取 backend/.env，不引入 python-dotenv） ----------

def _load_env():
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        except OSError:
            pass

_load_env()

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "")  # "mock" 仅本地联调用
AI_DAILY_LIMIT = int(os.getenv("AI_DAILY_LIMIT", "3"))

SUMMARY_TTL = 30 * 60        # 总结结果保留 30 分钟，与下载文件 TTL 一致
MAX_SUBTITLE_CHARS = 16000   # 字幕文本预算（约 10k token，保证长视频总结素材充足）
MAX_TRANSCRIPT_ITEMS = 400   # 字幕时间线最多保留条数
LLM_TIMEOUT = 90
MIN_SUBTITLE_LEN = 50        # 字幕太短视为无效，走降级

# 有 key 或显式 mock 模式才可用
HAS_LLM = bool(LLM_API_KEY) or LLM_PROVIDER == "mock"

# LLM 是国内服务，强制直连（绕过系统代理，行为稳定，同 douyin.py 的做法）
_direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
# 字幕 CDN 可能是国内（B站）也可能是国外（YouTube），先走默认网络栈
# （系统代理开启时国外可通），失败再退直连兜底。
_default_opener = urllib.request.build_opener()

_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


# ---------- LLM Provider 抽象 ----------

class LLMProvider:
    """大模型渠道抽象。换渠道只需实现 chat_stream / chat_json 并替换 provider。"""

    name = "abstract"

    def chat_stream(self, system: str, user: str):
        """流式输出，增量 yield 文本片段。"""
        raise NotImplementedError

    def chat_json(self, system: str, user: str) -> str:
        """一次性输出（流式失败时的回退路径）。"""
        raise NotImplementedError


class OpenAICompatProvider(LLMProvider):
    """OpenAI 兼容 /chat/completions 实现（DeepSeek / 智谱 GLM 等）。"""

    name = "openai_compat"

    def chat_stream(self, system: str, user: str):
        body = {
            "model": LLM_MODEL,
            "temperature": 0.3,
            "stream": True,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        req = urllib.request.Request(
            f"{LLM_BASE_URL}/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LLM_API_KEY}",
            },
        )
        try:
            with _direct_opener.open(req, timeout=LLM_TIMEOUT) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = (chunk.get("choices") or [{}])[0].get("delta", {}).get("content")
                        if delta:
                            yield delta
                    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
                        continue
        except (urllib.error.URLError, OSError, TimeoutError):
            # 流式失败（网络/代理问题）回退到一次性输出，保证功能不中断
            yield self.chat_json(system, user)

    def chat_json(self, system: str, user: str) -> str:
        last_err = None
        for attempt in range(2):  # 网络异常/5xx 重试 1 次
            req = self._build_request(system, user, use_json_mode=True)
            try:
                with _direct_opener.open(req, timeout=LLM_TIMEOUT) as r:
                    data = json.loads(r.read().decode("utf-8", "ignore"))
                    return data["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "ignore")[:300]
                except Exception:
                    pass
                if e.code < 500:
                    if "response_format" in str(body) or e.code == 400:
                        req = self._build_request(system, user, use_json_mode=False)
                        try:
                            with _direct_opener.open(req, timeout=LLM_TIMEOUT) as r:
                                data = json.loads(r.read().decode("utf-8", "ignore"))
                                return data["choices"][0]["message"]["content"]
                        except Exception as e2:
                            last_err = e2
                    raise RuntimeError(f"AI 服务请求失败（{e.code}），请检查模型配置或稍后重试")
                last_err = e
            except (urllib.error.URLError, OSError, TimeoutError, KeyError, json.JSONDecodeError) as e:
                last_err = e
            if attempt < 1:
                time.sleep(2)
        raise RuntimeError("AI 服务暂时不可用，请稍后重试")

    def _build_request(self, system: str, user: str, use_json_mode: bool):
        body = {
            "model": LLM_MODEL,
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if use_json_mode:
            body["response_format"] = {"type": "json_object"}
        return urllib.request.Request(
            f"{LLM_BASE_URL}/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LLM_API_KEY}",
            },
        )


_MOCK_OUTPUT = """## 概述
这是一条模拟总结：视频围绕一个有趣的主题展开讲解，结构清晰、案例丰富，适合快速了解该领域的核心思路。

## 核心要点
- 模拟要点一：先介绍了背景与要解决的问题
- 模拟要点二：给出了核心方法与实现步骤
- 模拟要点三：用真实案例验证了方法效果

## 章节大纲
- **[0:00]** 开场与背景介绍：说明视频主题与要解决的问题
- **[1:35]** 核心方法讲解：拆解关键步骤与原理
- **[5:10]** 案例演示与总结：完整示例演示并给出行动建议

## 思维导图
```mindmap
{"title":"模拟主题","children":[{"title":"背景","children":[{"title":"痛点"},{"title":"目标"}]},{"title":"方法","children":[{"title":"步骤一"},{"title":"步骤二"}]},{"title":"总结","children":[{"title":"要点回顾"}]}]}
```"""


class MockLLMProvider(LLMProvider):
    """本地联调用：流式返回固定示例输出，不花一分钱。"""

    name = "mock"

    def chat_stream(self, system: str, user: str):
        for i in range(0, len(_MOCK_OUTPUT), 16):
            yield _MOCK_OUTPUT[i:i + 16]
            time.sleep(0.05)

    def chat_json(self, system: str, user: str) -> str:
        time.sleep(2)
        return _MOCK_OUTPUT


provider: LLMProvider = MockLLMProvider() if LLM_PROVIDER == "mock" else OpenAICompatProvider()


# ---------- Prompt ----------

SYSTEM_PROMPT = """你是视频内容总结助手。根据给定的素材（视频字幕或视频基本信息）输出一份详实、具体的中文总结。
严格按以下 markdown 格式输出，不要输出任何其它内容：

## 概述
（2~3 句话概括视频主题、讲了什么以及核心结论，总长不超过 150 字）

## 核心要点
- 要点一（共 5~8 条，每条 40~120 字）
- 要点二

## 章节大纲
- **[分:秒]** 章节标题：用 2~3 句话说明这一章讲了什么、有哪些关键细节与结论（4~10 个）
- **[分:秒]** 章节标题：说明

## 思维导图
```mindmap
{"title":"主题","children":[{"title":"分支","children":[{"title":"子节点"}]}]}
```

质量要求：
1. 宁可具体、不要笼统：保留素材中的关键数字、专有名词、方法步骤与因果关系，禁止"讲了很多内容""非常重要"这类空话；
2. 核心要点之间不要重复，每条覆盖视频的不同侧面。

格式约束：
1. 素材没有时间信息时，整个「## 章节大纲」小节省略不写；章节时间戳必须是纯数字格式 [分:秒]，不要写「约」「大约」等前缀，也不要写成时间区间；
2. 素材中的字幕每行行首带有 [分:秒] 时间戳：章节大纲的时间必须取自素材中该章节首句的真实时间戳，禁止自行估算；
3. 除章节行的时间戳加粗外，全文不要使用 markdown 加粗/斜体标记（**、*、__），直接输出纯文本；
4. 思维导图必须是合法 JSON，最多 3 层、每层最多 5 个节点；
5. 素材只是视频基本信息（无字幕）时：概述与要点必须基于素材如实概括，不得编造视频内容；
6. 字幕被截断时只总结已有部分，不要猜测截断之后的内容。"""


# ---------- 字幕提取（保留时间线） ----------

def _subtitle_lang_rank(lang: str, video_lang: str) -> tuple:
    """字幕语言排序分（越小越优）：中文系 > 视频原语言 > 英文 > 其它；同组内非 -orig 优先。"""
    l = str(lang).lower()
    v = (video_lang or "").lower()
    if l in ("zh-hans", "zh-cn", "zh", "yue") or l.startswith("zh") or l == "ai-zh":
        group = 0
    elif v and (l == v or l.startswith(v)):
        group = 1
    elif l.startswith("en"):
        group = 2
    else:
        group = 3
    orig_penalty = 1 if "-orig" in l else 0
    return (group, orig_penalty, l)


def _pick_subtitle(info: dict) -> tuple | None:
    """从 info 的字幕字典里挑一条最优：手动字幕优先于自动字幕，
    语言优先级：中文 > 视频原语言（如日语视频选 ja）> 英文 > 其它。
    任何语言都能交给大模型总结，因此保底选任意可用轨道。
    返回 (url, ext) 或 None。"""
    video_lang = str(info.get("language") or "").lower()
    for store in (info.get("subtitles") or {}, info.get("automatic_captions") or {}):
        if not store:
            continue
        ranked = sorted(store.keys(), key=lambda l: _subtitle_lang_rank(l, video_lang))
        for lang in ranked:
            cand = _first_usable(store.get(lang) or [])
            if cand:
                return cand
    return None


def _first_usable(entries: list) -> tuple | None:
    """在一条语言的候选格式里挑带 url 的，优先 json3/vtt（时间线信息全）。"""
    ext_priority = ["json3", "vtt", "srt", "srv3", "srv1", "json"]
    by_ext = {}
    for e in entries or []:
        if e.get("url"):
            by_ext.setdefault(e.get("ext", ""), e)
    for ext in ext_priority:
        if ext in by_ext:
            return by_ext[ext]["url"], ext
    for e in entries or []:
        if e.get("url"):
            return e["url"], e.get("ext", "vtt")
    return None


def _http_get_text(url: str, referer: str) -> str:
    """GET 字幕内容。opener 尝试顺序：
    1. 自动探测到的系统代理（_get_proxy()，如注册表里的 Clash，国外 CDN 必需）
    2. 默认网络栈（环境变量代理）
    3. 直连兜底
    遇到 429（YouTube timedtext 限流）退避重试。伪装 UA/Referer。"""
    headers = {"User-Agent": _BROWSER_UA, "Referer": referer}

    def _openers():
        ops = []
        sys_proxy = _get_proxy()
        if sys_proxy:
            ops.append(urllib.request.build_opener(urllib.request.ProxyHandler(
                {"http": sys_proxy, "https": sys_proxy})))
        ops += [_default_opener, _direct_opener]
        return ops

    last_err = None
    for attempt in range(3):  # 共 3 轮；429 时退避
        for opener in _openers():
            try:
                req = urllib.request.Request(url, headers=headers)
                with opener.open(req, timeout=25) as r:
                    return r.read().decode("utf-8", "ignore")
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 429:
                    break  # 换下一轮退避重试（该出口被限流，立即换 opener 意义不大）
            except Exception as e:
                last_err = e
        if attempt < 2:
            time.sleep(3 + attempt * 3)
    raise RuntimeError(f"字幕下载失败（{type(last_err).__name__}: {last_err}），请稍后重试")


_TIME_LINE = re.compile(r"^\s*(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3}\s*-->\s*", re.M)
_TS_PARSE = re.compile(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,]\d{3}")
_TAG = re.compile(r"</?[^>]+>")
_STYLE = re.compile(r"\{\\[^}]*\}")
_META_LINE = re.compile(r"^\s*(Kind|Language|NAME)\s*:\s*", re.I)  # WEBVTT 头部元信息行


def _ts_to_sec(ts: str) -> int:
    m = _TS_PARSE.match(ts.strip())
    if not m:
        return 0
    h, mnt, s = (m.group(1) or "0"), m.group(2), m.group(3)
    return int(h) * 3600 + int(mnt) * 60 + int(s)


def _parse_caption(raw: str, ext: str) -> tuple[str, list]:
    """把字幕原始内容解析为 (纯文本, 时间线)。
    时间线 = [{"time": 开始秒, "text": "该句文本"}, ...]（按 MAX_TRANSCRIPT_ITEMS 截断）"""
    segments = []  # [(start_sec, text)]

    if ext in ("json3", "json"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return "", []
        for ev in data.get("events") or []:
            segs = ev.get("segs") or []
            line = "".join(s.get("utf8", "") for s in segs).replace("\n", " ").strip()
            if not line:
                continue
            start_ms = ev.get("tStartMs") or ev.get("start") or 0
            segments.append((int(start_ms // 1000), line))
    else:
        # vtt / srt：按行扫描，时间行更新当前 cue 起始时间，文本行归属最近时间
        cur_start = 0
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.upper().startswith(("WEBVTT", "NOTE", "STYLE")) or _META_LINE.match(line):
                continue
            if "-->" in stripped:
                cur_start = _ts_to_sec(stripped.split("-->")[0])
                continue
            if _TIME_LINE.match(stripped):
                continue
            text = _TAG.sub("", _STYLE.sub("", stripped)).strip()
            if not text or stripped.isdigit():
                continue
            segments.append((cur_start, text))

    # 去重合并 + 截断时间线
    merged = []
    for t, txt in segments:
        if merged and merged[-1][1] == txt:
            continue
        merged.append((t, txt))
    transcript = [{"time": t, "text": txt} for t, txt in merged[:MAX_TRANSCRIPT_ITEMS]]
    text = "\n".join(txt for _, txt in merged)
    return text, transcript


def _truncate(text: str, limit: int = MAX_SUBTITLE_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n（字幕过长已截断，请仅总结以上内容）"


def _fmt_ts(sec: int) -> str:
    """秒 → 分:秒（章节时间戳与素材标注统一格式）。"""
    m, s = divmod(max(0, int(sec)), 60)
    return f"{m}:{s:02d}"


def _timed_text(transcript: list) -> str:
    """把时间线渲染为带 [分:秒] 前缀的素材文本，让 LLM 的章节时间有真实依据。"""
    return "\n".join(f"[{_fmt_ts(item['time'])}] {item['text']}" for item in transcript)


# ---------- B站字幕通道（dm/view 弹幕池接口，无需登录、无需签名、无需 Cookie） ----------
# 参考 liyupi/free-video-downloader 的方案：
#   BV号 --纯本地算法--> aid；pagelist 接口（免登录）拿 cid；
#   x/v2/dm/view?aid&oid=cid&type=1 返回字幕列表（原生 zh + ai-zh + 多语 AI 翻译），
#   字幕 json 直链（aisubtitle.hdslb.com）无需鉴权，直接下载解析为文本+时间线。

_BV_XOR = 23442827791579
_BV_MASK = 2251799813685247
_BV_ALPHABET = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"


def _bv2av(bvid: str) -> int | None:
    """BV 号转 aid（纯本地算法，无需任何网络请求）。"""
    try:
        b = list(bvid)
        b[3], b[9] = b[9], b[3]
        b[4], b[7] = b[7], b[4]
        tmp = 0
        for ch in b[3:]:
            tmp = tmp * 58 + _BV_ALPHABET.index(ch)
        return (tmp ^ _BV_XOR) & _BV_MASK
    except (ValueError, IndexError):
        return None


def _bili_get(url: str, referer: str = "https://www.bilibili.com/", timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": _BROWSER_UA, "Referer": referer,
    })
    with _direct_opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _bili_bvid_cid(bvid: str) -> int | None:
    """拿 cid：pagelist 接口（无需登录，实测稳定）。"""
    try:
        pl = _bili_get(f"https://api.bilibili.com/x/player/pagelist?bvid={bvid}",
                       referer=f"https://www.bilibili.com/video/{bvid}/")
        if pl.get("code") == 0 and pl.get("data"):
            return pl["data"][0]["cid"]
    except Exception:
        pass
    return None


def _parse_bili_subtitle_json(raw: str) -> tuple[str, list]:
    """B站 字幕 json：{"body":[{"content":"...","from":1.2,"to":3.4},...]}"""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "", []
    body = data.get("body") or []
    merged = []
    for item in body:
        txt = str(item.get("content") or "").strip()
        if not txt:
            continue
        t = int(float(item.get("from") or 0))
        if merged and merged[-1][1] == txt:
            continue
        merged.append((t, txt))
    transcript = [{"time": t, "text": txt} for t, txt in merged[:MAX_TRANSCRIPT_ITEMS]]
    text = "\n".join(txt for _, txt in merged)
    return text, transcript


def _bili_download_subtitle(sub_url: str, referer: str) -> tuple[str, list]:
    if sub_url.startswith("//"):
        sub_url = "https:" + sub_url
    elif sub_url.startswith("http://"):
        sub_url = "https://" + sub_url[7:]
    req = urllib.request.Request(sub_url, headers={
        "User-Agent": _BROWSER_UA, "Referer": referer,
    })
    with _direct_opener.open(req, timeout=20) as r:
        raw = r.read().decode("utf-8", "ignore")
    return _parse_bili_subtitle_json(raw)


def fetch_bili_subtitles(url: str) -> tuple[str, list] | None:
    """B站字幕通道（dm/view，无需登录/签名/Cookie）。返回 (纯文本, 时间线) 或 None。
    字幕优先级：原生中文 > AI 中文 > 其它语言。"""
    try:
        m = re.search(r"(BV[\w]+)", url)
        if not m:
            return None
        bvid = m.group(1)
        aid = _bv2av(bvid)
        cid = _bili_bvid_cid(bvid)
        if not aid or not cid:
            return None

        referer = f"https://www.bilibili.com/video/{bvid}/"
        dm = _bili_get(f"https://api.bilibili.com/x/v2/dm/view?aid={aid}&oid={cid}&type=1",
                       referer=referer)
        subtitles = ((dm.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        if not subtitles:
            return None

        def _rank(s):
            lan = str(s.get("lan") or "")
            if lan in ("zh", "zh-Hans", "zh-CN"):
                return 0
            if lan == "ai-zh":
                return 1
            return 2
        subtitles.sort(key=_rank)

        for s in subtitles:
            if not s.get("subtitle_url"):
                continue
            text, transcript = _bili_download_subtitle(s["subtitle_url"], referer)
            if text.strip():
                return _truncate(text.strip()), transcript
        return None
    except Exception:
        return None


def fetch_bili_full(url: str) -> tuple | None:
    """B站纯通道兜底（yt-dlp 被 IP 风控时的降级）：pagelist + dm/view。
    标题等元数据可能缺失（用占位），字幕可用。任一步失败返回 None。"""
    try:
        m = re.search(r"(BV[\w]+)", url)
        if not m:
            return None
        bvid = m.group(1)
        aid = _bv2av(bvid)
        cid = _bili_bvid_cid(bvid)
        if not aid or not cid:
            return None
        meta = {
            "title": f"B站视频 {bvid}",
            "webpage_url": f"https://www.bilibili.com/video/{bvid}",
            "duration": None,
            "platform": "BiliBili",
        }
        material = f"平台：BiliBili\n标题：{meta['title']}\n（元数据接口暂不可用，仅基于字幕总结）"
        subs = fetch_bili_subtitles(url)
        if subs and len(subs[0]) > MIN_SUBTITLE_LEN:
            material += (f"\n视频字幕内容（每行行首的[分:秒]是该句开始时间）：\n"
                         f"{_truncate(_timed_text(subs[1]))}")
            return material, "subtitles", meta, subs[1]
        return material, "meta", meta, []
    except Exception:
        return None


# ---------- B站热门评论（降级增强，无需登录） ----------

def _fetch_bili_comments(aid, limit: int = 8) -> list[str]:
    """抓 B站热门评论作补充素材；失败静默返回空。"""
    if not aid:
        return []
    url = f"https://api.bilibili.com/x/v2/reply?type=1&oid={aid}&sort=1&ps=20"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _BROWSER_UA, "Referer": "https://www.bilibili.com/",
        })
        with _direct_opener.open(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
        replies = ((data.get("data") or {}).get("replies")) or []
        out = []
        for rep in replies:
            msg = (rep.get("content") or {}).get("message", "").strip()
            like = rep.get("like") or 0
            if msg and len(msg) > 5:
                out.append(f"{msg}（{like}赞）")
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


# ---------- 素材组装 ----------

def _build_material(url: str) -> tuple[str, str, dict, list]:
    """返回 (material_text, basis, meta, transcript)。
    basis: "subtitles"（基于字幕）| "meta"（基于视频信息降级）。"""
    # 抖音：复用自研通道的解析（不碰其内部），天然无字幕 → meta 降级
    if is_douyin_url(url):
        a = douyin_mod.analyze(url)
        material = (f"平台：抖音\n标题/简介：{a.get('title') or '（无）'}\n"
                    f"作者：{a.get('uploader') or '（未知）'}\n"
                    f"时长：{a.get('duration') or '未知'} 秒")
        meta = {"title": a.get("title"), "webpage_url": a.get("webpage_url"),
                "duration": a.get("duration"), "platform": "Douyin"}
        return material, "meta", meta, []

    def _ydl_variants():
        """与 downloader.analyze 一致的降级链：完整增强 → 原始配置。"""
        def base():
            o = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": True,
                # 防止代理挂起时无限卡死（YouTube 等慢/不可达平台快速失败）
                "socket_timeout": 30,
                "retries": 2,
            }
            _apply_cookies(o)
            return o
        full = base()
        full.update(YTDLP_ENHANCEMENTS)
        proxy = _get_proxy()
        if proxy:
            full["proxy"] = proxy
            bare = base()
            bare["proxy"] = proxy
        else:
            bare = base()
        return [full] if full == bare else [full, bare]

    info = None
    last_err = None
    for opts in _ydl_variants():
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            break
        except Exception as e:
            last_err = e
            info = None
    if info is None:
        # B站特例：yt-dlp 被风控时，用 dm/view 纯通道兜底（免登录）
        if "bilibili.com" in url:
            fb = fetch_bili_full(url)
            if fb:
                return fb
        raise last_err

    webpage_url = info.get("webpage_url") or url
    meta = {
        "title": info.get("title"),
        "webpage_url": webpage_url,
        "duration": info.get("duration"),
        "platform": info.get("extractor_key") or info.get("extractor") or "unknown",
    }

    sub = None
    transcript = []
    text = ""
    try:
        sub = _pick_subtitle(info)
        if sub:
            raw = _http_get_text(sub[0], webpage_url)
            text, transcript = _parse_caption(raw, sub[1])
    except Exception:
        text, transcript = "", []

    if text and len(text.strip()) > MIN_SUBTITLE_LEN:
        # 素材带 [分:秒] 时间戳：章节大纲的时间才能取真实值（否则 LLM 只能按比例瞎猜）
        material = (f"视频标题：{meta['title'] or ''}\n"
                    f"视频字幕内容（每行行首的[分:秒]是该句开始时间）：\n"
                    f"{_truncate(_timed_text(transcript))}")
        return material, "subtitles", meta, transcript

    # B站：yt-dlp 拿不到时走自研 dm/view 通道（免登录免 Cookie）
    if meta["platform"] == "BiliBili":
        bili = fetch_bili_subtitles(url)
        if bili and len(bili[0]) > MIN_SUBTITLE_LEN:
            material = (f"视频标题：{meta['title'] or ''}\n"
                        f"视频字幕内容（每行行首的[分:秒]是该句开始时间）：\n"
                        f"{_truncate(_timed_text(bili[1]))}")
            return material, "subtitles", meta, bili[1]

    # 降级：标题+UP主+时长+简介（B站再补热门评论，改善降级总结质量）
    desc = (info.get("description") or "")[:2000]
    material = (f"平台：{meta['platform']}\n标题：{meta['title'] or '（无）'}\n"
                f"UP主/作者：{info.get('uploader') or info.get('channel') or '（未知）'}\n"
                f"时长：{meta['duration'] or '未知'} 秒\n简介：{desc or '（无）'}")
    if meta["platform"] == "BiliBili":
        comments = _fetch_bili_comments(info.get("id"))
        if comments:
            material += "\n热门评论（可辅助理解视频内容，注意甄别）：" + "；".join(comments)
    return material, "meta", meta, []


# ---------- LLM 输出解析（markdown 分节 + mindmap 围栏） ----------

_MINDMAP_FENCE = re.compile(r"```mindmap\s*(\{.*?\})\s*```", re.S)
# 章节行：- [分:秒] 标题：说明。容忍 LLM 的常见变体：
#   ** 加粗可有可无、时间戳前缀「约/大约/~」、时间区间 [00:00-03:10]（取起点）、全角冒号 02：00
_CHAPTER_LINE = re.compile(
    r"^-\s*\*{0,2}\[(?:大约|约)?\s*(\d{1,2}[：:]\d{1,2}(?:[：:]\d{1,2})?)"
    r"(?:[-–~—至]\s*\d{1,2}[：:]\d{1,2}(?:[：:]\d{1,2})?)?\]\*{0,2}\s*(.+)$"
)


def _norm_mindmap_node(node, depth):
    if not isinstance(node, dict) or depth > 3:
        return None
    children = []
    for ch in (node.get("children") or [])[:5]:
        n = _norm_mindmap_node(ch, depth + 1)
        if n:
            children.append(n)
    title = str(node.get("title") or "").strip()[:40]
    if not title:
        return None
    return {"title": title, "children": children}


def _parse_mindmap(raw: str) -> dict | None:
    m = _MINDMAP_FENCE.search(raw)
    candidate = m.group(1) if m else raw  # 没有围栏时尝试整体当 JSON（兼容输出跑偏）
    try:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            return None
        obj = json.loads(candidate[start:end + 1])
    except json.JSONDecodeError:
        return None
    mm = _norm_mindmap_node(obj, 1)
    return mm if mm and mm.get("children") else None


def _ts_str_to_sec(ts: str) -> int:
    ts = ts.replace("：", ":")  # 兼容 LLM 输出的全角冒号
    parts = [int(x) for x in ts.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def _parse_llm_output(raw: str) -> dict:
    """把 LLM 的 markdown 输出解析为结构化 result（容忍格式偏差）。"""
    result = {"overview": "", "points": [], "chapters": [], "mindmap": None}

    result["mindmap"] = _parse_mindmap(raw)
    text = _MINDMAP_FENCE.sub("", raw)  # 去掉导图围栏，便于分节

    def section(name):
        m = re.search(rf"#+\s*{name}[^\n]*\n(.*?)(?=\n#+\s|\Z)", text, re.S)
        return m.group(1).strip() if m else ""

    ov_lines = [l.strip() for l in section("概述").splitlines() if l.strip()]
    result["overview"] = "".join(ov_lines)[:200]  # 概述允许多句

    for line in section("核心要点").splitlines():
        line = line.strip()
        if line.startswith(("-", "•", "*")):
            p = line.lstrip("-•* ").strip()
            if p:
                # LLM 常把要点词加粗（**xx**：），剥掉加粗标记避免前端裸露星号
                result["points"].append(p.replace("**", "").replace("__", "")[:220])
    result["points"] = result["points"][:8]

    for line in section("章节大纲").splitlines():
        m = _CHAPTER_LINE.match(line.strip())
        if m:
            ts = m.group(1).replace("：", ":")  # label 统一半角，展示与跳转参数一致
            rest = m.group(2)
            title, _, detail = rest.partition("：")
            if not detail:  # 兼容半角冒号分隔
                title, _, detail = rest.partition(":")
            clean = lambda x: x.strip().replace("**", "").replace("__", "")
            result["chapters"].append({
                "time": _ts_str_to_sec(ts),
                "label": ts,
                "title": clean(title)[:60] or ts,
                "detail": clean(detail)[:300],
            })
    result["chapters"] = result["chapters"][:12]

    # 兜底：完全没解析出内容（LLM 输出跑偏）→ 整段当概述
    if not result["overview"] and not result["points"]:
        fallback = text.strip()
        if fallback:
            result["overview"] = fallback[:150]
    return result


# ---------- IP 每日限流 ----------

_quota = {}          # ip -> {"date": "2026-10-09", "count": n}
_quota_lock = threading.Lock()


def _today() -> str:
    return datetime.date.today().isoformat()


def quota_of(ip: str) -> dict:
    with _quota_lock:
        rec = _quota.get(ip)
        used = rec["count"] if rec and rec["date"] == _today() else 0
        return {"used": used, "limit": AI_DAILY_LIMIT, "remaining": max(0, AI_DAILY_LIMIT - used)}


def _consume_quota(ip: str):
    with _quota_lock:
        today = _today()
        rec = _quota.get(ip)
        if not rec or rec["date"] != today:
            rec = {"date": today, "count": 0}
            _quota[ip] = rec
        if rec["count"] >= AI_DAILY_LIMIT:
            raise RuntimeError("今日免费总结次数已用完，开通会员即可不限次使用")
        rec["count"] += 1


# ---------- 总结任务（素材准备用独立线程池，不挤占下载 workers） ----------

_summaries = {}      # task_id -> task dict
_lock = threading.Lock()
_executor = None


def _executor_instance():
    global _executor
    if _executor is None:
        from concurrent.futures import ThreadPoolExecutor
        _executor = ThreadPoolExecutor(max_workers=2)
    return _executor


def start_summary(url: str, ip: str) -> str:
    if not HAS_LLM:
        raise RuntimeError("AI 总结功能正在开通中，敬请期待")
    _consume_quota(ip)  # 先扣额度，失败直接抛可读错误
    task_id = uuid.uuid4().hex[:12]
    with _lock:
        _summaries[task_id] = {
            "task_id": task_id,
            "status": "fetching_subtitles",
            "url": url,
            "basis": None,
            "title": None,
            "webpage_url": None,
            "duration": None,
            "platform": None,
            "material": None,
            "transcript": [],
            "stream_buf": [],   # LLM 增量输出缓冲（SSE 从这里搬运）
            "result": None,
            "error": None,
            "created_at": time.time(),
        }
    _executor_instance().submit(_prepare_material, task_id, url)
    return task_id


def _prepare_material(task_id: str, url: str):
    """阶段 1（后台线程）：提取字幕/组装素材，完成后自动提交阶段 2。"""
    try:
        material, basis, meta, transcript = _build_material(url)
        with _lock:
            _summaries[task_id].update(
                status="summarizing", basis=basis, material=material,
                transcript=transcript, **meta)
        _executor_instance().submit(_run_llm, task_id)
    except Exception as e:
        with _lock:
            _summaries[task_id].update(status="error", error=str(e)[:300])


def _run_llm(task_id: str):
    """阶段 2（后台线程）：流式调 LLM，增量写入 stream_buf；
    即使前端断开 SSE，任务也会在后台完成并存库。"""
    with _lock:
        task = _summaries.get(task_id)
        material = task.get("material") if task else None
    if not material:
        with _lock:
            _summaries[task_id].update(status="error", error="素材缺失")
        return
    raw_parts = []
    try:
        for delta in provider.chat_stream(SYSTEM_PROMPT, f"视频素材：\n{material}"):
            raw_parts.append(delta)
            with _lock:
                if task_id in _summaries:
                    _summaries[task_id]["stream_buf"].append(delta)
        raw = "".join(raw_parts)
        result = _parse_llm_output(raw)
        with _lock:
            _summaries[task_id].update(status="done", result=result)
    except Exception as e:
        with _lock:
            _summaries[task_id].update(status="error", error=str(e)[:300])


def get_stream_chunk(task_id: str, start: int) -> tuple[str, int]:
    """取 stream_buf 从 start 下标开始的增量文本。返回 (text, 新下标)。"""
    with _lock:
        task = _summaries.get(task_id)
        if not task:
            return "", start
        buf = task["stream_buf"]
        text = "".join(buf[start:])
        return text, len(buf)


def get_task(task_id: str) -> dict | None:
    with _lock:
        t = _summaries.get(task_id)
        if not t:
            return None
        # 不对外暴露素材原文与流缓冲（体积大且无展示需求）
        return {k: v for k, v in t.items() if k not in ("material", "stream_buf")}


def cleanup_expired():
    """清理过期任务（超 TTL 一律清，防止异常中断的卡死任务泄漏）。"""
    now = time.time()
    with _lock:
        expired = [tid for tid, t in _summaries.items()
                   if now - t["created_at"] > SUMMARY_TTL]
        for tid in expired:
            _summaries.pop(tid, None)
