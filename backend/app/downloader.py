import os
import shutil
import threading
import time
import uuid
from pathlib import Path

import yt_dlp

from . import douyin as douyin_mod

HAS_FFMPEG = shutil.which("ffmpeg") is not None

BASE_DIR = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)
COOKIES_FILE = BASE_DIR / "cookies" / "cookies.txt"  # Netscape 格式，可选

FILE_TTL_SECONDS = 30 * 60  # 文件保留 30 分钟后清理


def _apply_cookies(opts: dict):
    """若存在 cookies/cookies.txt 则启用（其它平台可选的 Cookie 配置）。
    抖音已走 douyin.py 自研无 Cookie 通道，不再依赖此文件。"""
    if COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)

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


def analyze(url: str) -> dict:
    """解析视频信息，返回标题、封面、可选清晰度列表等。"""
    if is_douyin_url(url):
        return douyin_mod.analyze(url)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    _apply_cookies(opts)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

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


def _run_download(task_id: str, url: str, format_id: str, mode: str):
    outtmpl = str(DOWNLOAD_DIR / f"{task_id}.%(ext)s")

    def merge_hook(d):
        if d.get("status") == "finished":
            with _lock:
                task = _tasks.get(task_id)
                if task:
                    task["status"] = "processing"
                    task["progress"] = 99

    fmt = format_id
    if mode == "audio":
        if not HAS_FFMPEG:
            raise RuntimeError("服务端未安装 ffmpeg，暂不支持音频提取")
        fmt = "bestaudio/best"
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

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
        # 找到实际产物文件
        produced = sorted(DOWNLOAD_DIR.glob(f"{task_id}.*"))
        final = None
        for p in produced:
            if p.suffix.lower() in (".mp4", ".m4a", ".mkv", ".webm", ".mp3"):
                final = p
        if final is None and produced:
            final = produced[-1]
        with _lock:
            task = _tasks.get(task_id)
            if task:
                if final:
                    task.update({
                        "status": "done",
                        "progress": 100,
                        "filename": final.name,
                        "filesize": final.stat().st_size,
                    })
                else:
                    task.update({"status": "error", "error": "下载完成但未找到产物文件"})
    except Exception as e:
        with _lock:
            task = _tasks.get(task_id)
            if task:
                task.update({"status": "error", "error": str(e)[:500]})


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


def start_download(url: str, format_id: str = "best", mode: str = "video") -> str:
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
        _executor_instance().submit(_run_douyin_download, task_id, url, format_id, mode)
    else:
        _executor_instance().submit(_run_download, task_id, url, format_id, mode)
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
