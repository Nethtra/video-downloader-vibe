import os
import shutil
import threading
import time
import uuid
from pathlib import Path

import yt_dlp

from . import douyin as douyin_mod

HAS_FFMPEG = shutil.which("ffmpeg") is not None

# ---------- yt-dlp 全局增强 ----------
# 新版 yt-dlp 解析 YouTube 需要 JS 运行时（解 n-challenge 签名），
# 并建议启用官方远程挑战求解脚本，否则格式缺失/签名失败。
os.environ.pop("NODE_OPTIONS", None)  # 剔除宿主注入的 node 启动参数（会限制 fs 权限导致求解脚本报错）
# 清除会话注入的代理环境变量（WorkBuddy 每个会话注入的代理端口都不同且不稳定）。
# 清掉后 yt-dlp 的代理解析链会落到 Windows 注册表系统代理（用户自己的代理软件，
# 稳定可控）；若服务器需要指定代理，用 backend/proxy.txt。
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)


def _node_version(path: str) -> tuple[int, int]:
    """读取 node 版本号（EJS/签名求解对 node 版本敏感：v20 实测失败、v22 通过）。"""
    try:
        import re
        import subprocess
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
        m = re.match(r"v(\d+)\.(\d+)", (out.stdout or "").strip())
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return (0, 0)


def _find_node() -> str | None:
    """定位 node：PATH → Windows 常见位置 → WorkBuddy 托管目录。
    多个 node 共存时选版本最高的（不能拿第一个就用：系统 node 20 会让
    YouTube 签名求解静默失败，表现为年龄限制视频被 18+ 墙拦截）。"""
    import glob
    found = shutil.which("node")
    candidates = [found] if found else []
    candidates += [
        r"C:\Program Files\nodejs\node.exe",
        os.path.expanduser(r"~\.workbuddy\binaries\node\versions\*\node.exe"),
    ]
    best, best_ver, seen = None, (0, 0), set()
    for pattern in candidates:
        paths = sorted(glob.glob(pattern), reverse=True) if "*" in pattern else [pattern]
        for path in paths:
            if path in seen or not os.path.isfile(path):
                continue
            seen.add(path)
            ver = _node_version(path)
            if ver > best_ver:
                best, best_ver = path, ver
    return best


_node_path = _find_node()
YTDLP_ENHANCEMENTS = {}
if _node_path:
    YTDLP_ENHANCEMENTS = {
        "js_runtimes": {"node": {"path": _node_path}},
        "remote_components": ["ejs:github"],
    }

BASE_DIR = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)
COOKIES_FILE = BASE_DIR / "cookies" / "cookies.txt"  # Netscape 格式，可选

# 可选代理：backend/proxy.txt 每行一个代理地址（如 http://127.0.0.1:7890），
# 用于服务器访问 YouTube 等外网平台；文件不存在则用系统默认行为。
PROXY_FILE = BASE_DIR / "proxy.txt"

# 是否启用 cookies.txt：实测 yt-dlp 自动回写的过期访客 Cookie 会导致
# YouTube 被降级到 360p（无 Cookie 反而能拿全 4K），故默认关闭。
# 若某平台确需 Cookie，把 backend/cookies/enabled 空文件建出来即可开启。
COOKIES_ENABLED_MARKER = BASE_DIR / "cookies" / "enabled"


def _get_proxy() -> str | None:
    """代理解析优先级：
    1. backend/proxy.txt（站长显式指定，每行一个地址）
    2. Windows 系统代理（注册表，如 Clash/v2ray 开启系统代理后写入的 127.0.0.1:xxxx）
    3. 都没有则返回 None（用环境默认）
    显式返回注册表代理很重要：沙箱/某些终端会注入不可用的 http_proxy 环境变量，
    显式指定可覆盖环境变量，保证走真正可用的系统代理。"""
    try:
        if PROXY_FILE.exists():
            value = PROXY_FILE.read_text(encoding="utf-8").strip()
            if value:
                return value.splitlines()[0].strip()
    except OSError:
        pass
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        enable, _ = winreg.QueryValueEx(k, "ProxyEnable")
        if enable:
            server, _ = winreg.QueryValueEx(k, "ProxyServer")
            if server and "=" not in server:  # 简单格式 127.0.0.1:5933
                return f"http://{server}"
    except Exception:  # 非 Windows 平台无 winreg（ImportError）等，一律跳过注册表探测
        pass
    return None

FILE_TTL_SECONDS = 30 * 60  # 文件保留 30 分钟后清理


def _apply_cookies(opts: dict):
    """可选的 Cookie 支持（默认关闭，见 COOKIES_ENABLED_MARKER 注释）。
    抖音走 douyin.py 自研免 Cookie 通道，不依赖此文件。"""
    if COOKIES_ENABLED_MARKER.exists() and COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)


def _is_age_gate(err: str) -> bool:
    """YouTube 年龄墙：服务端强制要求已登录且通过年龄验证的账号会话，
    匿名通道（含 tv_embedded/web_embedded 等所有 player_client）实测全部被拦。"""
    return "confirm your age" in (err or "").lower()


def _maybe_cookie_retry(opts: dict) -> dict | None:
    """命中年龄墙且站长配置了 cookies.txt 时，返回带 Cookie 的重试配置。
    Cookie 只在此时启用（定向降级），普通视频仍匿名解析，
    避免过期访客 Cookie 导致的 360p 降级问题。"""
    if COOKIES_FILE.exists():
        retry = dict(opts)
        retry["cookiefile"] = str(COOKIES_FILE)
        return retry
    return None

_tasks = {}
_lock = threading.Lock()
_executor = None


def _executor_instance():
    global _executor
    if _executor is None:
        from concurrent.futures import ThreadPoolExecutor
        _executor = ThreadPoolExecutor(max_workers=3)
    return _executor


def _clean_option(label: str) -> str:
    for junk in (" - ", "[", "]"):
        label = label.replace(junk, " ")
    return " ".join(label.split())[:60]


def is_douyin_url(url: str) -> bool:
    return "douyin.com" in url or "douyin" in url.lower()


# ---------- 异步解析任务（慢平台如 YouTube 可能超过网关超时，改为提交+轮询） ----------
_analyze_tasks = {}
ANALYZE_TTL = 10 * 60


def start_analyze(url: str) -> str:
    analyze_id = uuid.uuid4().hex[:12]
    with _lock:
        _analyze_tasks[analyze_id] = {"status": "running", "result": None, "error": None,
                                      "created_at": time.time()}
    _executor_instance().submit(_run_analyze, analyze_id, url)
    return analyze_id


def _run_analyze(analyze_id: str, url: str):
    try:
        result = analyze(url)
        with _lock:
            t = _analyze_tasks.get(analyze_id)
            if t:
                t.update({"status": "done", "result": result})
    except Exception as e:
        with _lock:
            t = _analyze_tasks.get(analyze_id)
            if t:
                t.update({"status": "error", "error": str(e)[:500]})


def get_analyze_task(analyze_id: str) -> dict | None:
    with _lock:
        return _analyze_tasks.get(analyze_id)


def analyze(url: str) -> dict:
    """解析视频信息，返回标题、封面、可选清晰度列表等。
    增强配置（JS 运行时/远程求解）失败时自动回退原始配置，保证不比改造前差。"""
    if is_douyin_url(url):
        return douyin_mod.analyze(url)

    def _base_opts():
        return {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            # 防止代理挂起时无限卡死（快速失败转可读报错）
            "socket_timeout": 30,
            "retries": 2,
        }

    def _variants():
        """尝试序列：完整增强 → 原始配置（ej 求解组件需联网下载，无代理环境可能失败）。"""
        base = _base_opts()
        _apply_cookies(base)
        base.update(YTDLP_ENHANCEMENTS)
        proxy = _get_proxy()
        if proxy:
            base["proxy"] = proxy
        bare = _base_opts()
        _apply_cookies(bare)
        if proxy:
            bare["proxy"] = proxy
        # 配置相同（无增强可用）时只试一次
        return [base] if base == bare else [base, bare]

    last_err = None
    info = None
    for opts in _variants():
        try:
            info = None
            for attempt in range(3):
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                # YouTube 的 SABR 限制按请求随机下发：偶尔只给 360p 渐进式。
                # 现代视频几乎都有 ≥720p，检测到"疑似被降级"就重试（新请求会换访客身份）。
                heights = [f.get("height") or 0 for f in (info.get("formats") or [])
                           if f.get("vcodec") != "none"]
                if max(heights or [0]) >= 480 or attempt == 2:
                    break
            break
        except Exception as e:
            last_err = e
            # 年龄墙定向降级：匿名被拦且站长配置了 cookies.txt 时，带 Cookie 再试一次
            if _is_age_gate(str(e)):
                cookie_opts = _maybe_cookie_retry(opts)
                if cookie_opts:
                    try:
                        with yt_dlp.YoutubeDL(cookie_opts) as ydl:
                            info = ydl.extract_info(url, download=False)
                        break
                    except Exception as e2:
                        last_err = e2
            continue
    if info is None:
        raise last_err  # 交给路由层转可读中文

    # 整理可选格式：按分辨率分组，每个分辨率取最优 mp4 优先项
    candidates = {}
    audio_only = None
    for f in info.get("formats", []):
        if f.get("vcodec") == "none" and f.get("acodec") != "none":
            if audio_only is None or (f.get("filesize") or 0) > (audio_only.get("filesize") or 0):
                audio_only = f
            continue
        if f.get("vcodec") == "none":
            continue
        # 无 ffmpeg 时仅保留"视频+音频合一"的渐进式格式（无需合并）
        if not HAS_FFMPEG and f.get("acodec") == "none":
            continue
        height = f.get("height")
        if not height:
            continue
        height = min(height, 2160)
        ext = f.get("ext", "mp4")
        score = (1 if ext == "mp4" else 0, f.get("tbr") or 0)
        if height not in candidates or score > candidates[height]["_score"]:
            candidates[height] = {
                "_score": score,
                "format_id": f["format_id"],
                "ext": ext,
                "filesize": f.get("filesize") or f.get("filesize_approx"),
            }

    formats = []
    for height in sorted(candidates, reverse=True):
        c = candidates[height]
        formats.append({
            "format_id": c["format_id"],
            "label": f"{height}p",
            "height": height,
            "ext": "mp4",
            "filesize": c["filesize"],
        })
    if audio_only and HAS_FFMPEG:
        formats.append({
            "format_id": audio_only["format_id"],
            "label": "仅音频",
            "height": 0,
            "ext": audio_only.get("ext", "m4a"),
            "filesize": audio_only.get("filesize") or audio_only.get("filesize_approx"),
        })
    if not formats:
        formats.append({"format_id": "best", "label": "最佳画质", "height": 0, "ext": "mp4", "filesize": None})

    duration = info.get("duration")
    return {
        "title": info.get("title") or "未知标题",
        "thumbnail": info.get("thumbnail") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": duration,
        "platform": info.get("extractor_key") or info.get("extractor") or "unknown",
        "webpage_url": info.get("webpage_url") or url,
        "formats": formats,
    }


def _progress_hook(task_id, status_dict):
    with _lock:
        task = _tasks.get(task_id)
        if not task:
            return
        if status_dict["status"] == "downloading":
            total = status_dict.get("total_bytes") or status_dict.get("total_bytes_estimate") or 0
            done = status_dict.get("downloaded_bytes") or 0
            task["progress"] = int(done * 100 / total) if total else task.get("progress", 0)
            task["speed"] = status_dict.get("speed")
            task["eta"] = status_dict.get("eta")
        elif status_dict["status"] == "finished":
            task["progress"] = max(task.get("progress", 0), 99)


def _run_download(task_id: str, url: str, format_id: str, mode: str,
                  max_height: int | None = None):
    outtmpl = str(DOWNLOAD_DIR / f"{task_id}.%(ext)s")

    def merge_hook(d):
        if d.get("status") == "finished":
            with _lock:
                task = _tasks.get(task_id)
                if task:
                    task["status"] = "processing"
                    task["progress"] = 99

    if mode == "audio":
        if not HAS_FFMPEG:
            raise RuntimeError("服务端未安装 ffmpeg，暂不支持音频提取")
        fmt = "bestaudio/best"
    elif max_height and HAS_FFMPEG:
        # 非会员清晰度门禁：无视前端传的 format_id（它可能指向更高清的流），
        # 整体替换为带 1080p 上限的表达式（仍走"视频流+音轨合并"防无声视频）
        fmt = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"
    elif format_id and format_id != "best":
        # DASH 平台（B站/YouTube 等）的单个清晰度是"无音轨的纯视频流"，
        # 必须显式拼接最佳音轨让 yt-dlp 用 ffmpeg 合并，否则下载出来的是无声视频。
        if HAS_FFMPEG:
            fmt = f"{format_id}+bestaudio/{format_id}/best"
        else:
            fmt = "best"  # 无 ffmpeg 无法合并，退回"音视频合一"的渐进式格式
    else:
        fmt = format_id or "best"
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "outtmpl": outtmpl,
        "format": fmt,
        "progress_hooks": [lambda d: _progress_hook(task_id, d)],
        "postprocessor_hooks": [merge_hook],
        "merge_output_format": "mp4",
        # 轻量风控：模拟浏览器
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Referer": url,
        },
    }
    if mode == "audio":
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]
    _apply_cookies(opts)
    opts.update(YTDLP_ENHANCEMENTS)
    proxy = _get_proxy()
    if proxy:
        opts["proxy"] = proxy

    def _attempt_download(o: dict):
        with yt_dlp.YoutubeDL(o) as ydl:
            ydl.extract_info(url, download=True)
        # 找到实际产物文件
        produced = sorted(DOWNLOAD_DIR.glob(f"{task_id}.*"))
        final = None
        for p in produced:
            if p.suffix.lower() in (".mp4", ".m4a", ".mkv", ".webm", ".mp3"):
                final = p
        if final is None and produced:
            final = produced[-1]
        return final

    def _mark_done(final):
        with _lock:
            task = _tasks.get(task_id)
            if task:
                task.update({
                    "status": "done",
                    "progress": 100,
                    "filename": final.name,
                    "filesize": final.stat().st_size,
                })

    def _mark_error(e: Exception):
        with _lock:
            task = _tasks.get(task_id)
            if task:
                task.update({"status": "error", "error": str(e)[:500]})

    try:
        final = _attempt_download(opts)
        if final is None:
            raise RuntimeError("下载完成但未找到产物文件")
        _mark_done(final)
    except Exception as e:
        # 年龄墙定向降级：匿名被拦且站长配置了 cookies.txt 时，带 Cookie 再试一次
        cookie_opts = _maybe_cookie_retry(opts) if _is_age_gate(str(e)) else None
        if cookie_opts:
            try:
                final = _attempt_download(cookie_opts)
                if final is None:
                    raise RuntimeError("下载完成但未找到产物文件")
                _mark_done(final)
            except Exception as e2:
                _mark_error(e2)
        else:
            _mark_error(e)


def _run_douyin_download(task_id: str, url: str, format_id: str, mode: str):
    """抖音走自研通道：解析 → 无水印直链流式下载（进度自算）→ 可选 ffmpeg 提取音频。"""
    out = DOWNLOAD_DIR / f"{task_id}.mp4"
    try:
        video_url = douyin_mod.get_download_url(url, format_id)
        if not video_url:
            raise RuntimeError("未取到下载直链，请重试")

        done = 0
        with douyin_mod.open_stream(video_url) as resp, open(out, "wb") as f:
            total_hdr = resp.headers.get("Content-Length")
            total = int(total_hdr) if total_hdr and total_hdr.isdigit() else None
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    with _lock:
                        t = _tasks.get(task_id)
                        if t:
                            t["progress"] = int(done * 100 / total)
        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError("下载内容为空")

        filename = out.name
        if mode == "audio":
            if not HAS_FFMPEG:
                raise RuntimeError("服务端未安装 ffmpeg，暂不支持音频提取")
            audio = out.with_suffix(".m4a")
            import subprocess
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(out), "-vn",
                 "-c:a", "copy", str(audio)],
                check=True, timeout=300,
            )
            out.unlink(missing_ok=True)
            filename = audio.name

        with _lock:
            t = _tasks.get(task_id)
            if t:
                t.update({
                    "status": "done",
                    "progress": 100,
                    "filename": filename,
                    "filesize": (DOWNLOAD_DIR / filename).stat().st_size,
                })
    except Exception as e:
        with _lock:
            t = _tasks.get(task_id)
            if t:
                t.update({"status": "error", "error": str(e)[:500]})


def start_download(url: str, format_id: str = "best", mode: str = "video",
                   max_height: int | None = None) -> str:
    task_id = uuid.uuid4().hex[:12]
    with _lock:
        _tasks[task_id] = {
            "task_id": task_id,
            "status": "downloading",
            "progress": 0,
            "speed": None,
            "eta": None,
            "filename": None,
            "filesize": None,
            "error": None,
            "created_at": time.time(),
        }
    if is_douyin_url(url):
        # 抖音独立通道：单一清晰度，不适用会员门禁
        _executor_instance().submit(_run_douyin_download, task_id, url, format_id, mode)
    else:
        _executor_instance().submit(_run_download, task_id, url, format_id, mode,
                                    max_height=max_height)
    return task_id


def get_task(task_id: str) -> dict | None:
    with _lock:
        return _tasks.get(task_id)


def get_file_path(task_id: str) -> Path | None:
    task = get_task(task_id)
    if not task or task["status"] != "done" or not task.get("filename"):
        return None
    path = DOWNLOAD_DIR / task["filename"]
    return path if path.exists() else None


def cleanup_expired():
    """删除超时未取走的文件，防止磁盘被塞满。"""
    now = time.time()
    for p in DOWNLOAD_DIR.iterdir():
        try:
            if p.is_file() and now - p.stat().st_mtime > FILE_TTL_SECONDS:
                p.unlink()
        except OSError:
            pass
    with _lock:
        expired = [tid for tid, t in _tasks.items()
                   if now - t["created_at"] > FILE_TTL_SECONDS and t["status"] in ("done", "error")]
        for tid in expired:
            _tasks.pop(tid, None)
        expired_a = [aid for aid, t in _analyze_tasks.items()
                     if now - t["created_at"] > ANALYZE_TTL and t["status"] in ("done", "error")]
        for aid in expired_a:
            _analyze_tasks.pop(aid, None)
