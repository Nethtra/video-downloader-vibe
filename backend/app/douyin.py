"""
抖音无 Cookie 下载通道（自研，不依赖 yt-dlp 的抖音 Web API）。

原理：
1. 调用字节官方 ttwid 注册接口，**系统自行铸造访客凭证 ttwid**——
   这不是用户 Cookie，不含任何用户身份信息，也不需要用户提供任何东西。
2. 带移动端 UA + ttwid 请求 iesdouyin 移动分享页（风控远宽于 Web 版），
   页面内嵌 _ROUTER_DATA JSON，含标题/作者/封面/时长/播放地址。
3. 将 play_addr 中的 /playwm/（带水印）替换为 /play/ 得到无水印直链。

ttdwid 等凭证仅存于服务端内存，与用户无关；用户全程无需登录或提供任何凭证。
"""
import json
import re
import threading
import time
import urllib.request
import urllib.error

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

# 关键：urllib 默认会读取 Windows 系统代理（注册表），系统代理不可用时请求会
# 间歇性"连接被拒"。抖音是国内站点，直连即可——强制绕过一切代理设置，
# 保证行为稳定不受宿主机代理软件（Clash/V2Ray/沙箱残留代理）影响。
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url: str, headers: dict, timeout: int = 20, retries: int = 2) -> str:
    """带重试的 GET，网络异常统一转为可读的 RuntimeError。"""
    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=headers)
        try:
            with _opener.open(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(1 + attempt)  # 递增等待后重试
    raise RuntimeError(f"网络请求失败（{type(last_err).__name__}），请稍后重试")

_ttwid = None
_ttwid_time = 0.0
_ttwid_lock = threading.Lock()
TTWID_TTL = 5 * 24 * 3600  # 内存缓存 5 天，过期或失效自动重新铸造


def _gen_ttwid() -> str:
    req = urllib.request.Request(
        "https://ttwid.bytedance.com/ttwid/union/register/",
        data=json.dumps({
            "region": "cn", "aid": 1768, "needFid": False,
            "service": "www.ixigua.com",
            "migrate_info": {"ticket": "", "source": "node"},
            "cbUrlProtocol": "https", "union": True,
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with _opener.open(req, timeout=15) as r:
            for h, v in r.getheaders():
                if h.lower() == "set-cookie" and "ttwid=" in v:
                    return v.split("ttwid=")[1].split(";")[0]
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise RuntimeError(f"访客凭证铸造失败（{type(e).__name__}），请稍后重试")
    raise RuntimeError("访客凭证铸造失败，请稍后重试")


def _get_ttwid(force_refresh: bool = False) -> str:
    global _ttwid, _ttwid_time
    with _ttwid_lock:
        if not force_refresh and _ttwid and time.time() - _ttwid_time < TTWID_TTL:
            return _ttwid
        _ttwid = _gen_ttwid()
        _ttwid_time = time.time()
        return _ttwid


def extract_video_id(text: str) -> str | None:
    """从链接或分享口令文本中提取抖音视频 ID；支持短链 v.douyin.com。"""
    text = text.strip()
    # 从任意文本中提取第一个 URL（兼容 App 分享口令）
    m = re.search(r'https?://[^\s，。、"\'）]+', text)
    if m:
        text = m.group(0)
    # 长链形态 1：www.douyin.com/video/{id}、/note/{id}
    m = re.search(r'douyin\.com/(?:video|note)/(\d+)', text)
    if m:
        return m.group(1)
    # 长链形态 2：任意路径下带 modal_id 参数（精选页 /jingxuan?modal_id=、
    # 发现页 /discover?modal_id=、用户主页 /user/xxx?modal_id= 等）
    m = re.search(r'[?&]modal_id=(\d+)', text)
    if m:
        return m.group(1)
    # 短链：v.douyin.com/{code} —— 跟随重定向取真实 ID
    m = re.search(r'v\.douyin\.com/([\w-]+)', text)
    if m:
        req = urllib.request.Request(
            f"https://v.douyin.com/{m.group(1)}",
            headers={"User-Agent": MOBILE_UA},
        )
        try:
            # 不跟随重定向，直接读 Location（同样绕过系统代理）
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
            resp = opener.open(req, timeout=15)
            location = resp.headers.get("Location", "") or resp.geturl()
        except urllib.error.HTTPError as e:
            location = e.headers.get("Location", "") or e.geturl()
        except Exception:
            return None
        m = re.search(r'/(?:video|note)/(\d+)', location) or re.search(r'[?&]modal_id=(\d+)', location)
        if m:
            return m.group(1)
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch_share_html(video_id: str, force_refresh: bool = False) -> str:
    return _get(
        f"https://www.iesdouyin.com/share/video/{video_id}",
        headers={
            "User-Agent": MOBILE_UA,
            "Cookie": f"ttwid={_get_ttwid(force_refresh)}",
        },
    )


def _parse_item(html: str) -> dict:
    m = re.search(r'window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>', html, re.S)
    if not m:
        raise RuntimeError("分享页数据缺失")
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        raise RuntimeError("分享页数据异常，请稍后重试")
    page = (data.get("loaderData") or {}).get("video_(id)/page") or {}
    items = ((page.get("videoInfoRes") or {}).get("item_list")) or []
    if not items:
        raise RuntimeError("未取到视频数据（可能已删除或需审核）")
    return items[0]


def _probe_size(url: str) -> int | None:
    """Range 探测文件大小，失败不影响主流程。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": MOBILE_UA, "Referer": "https://www.douyin.com/",
        "Range": "bytes=0-0",
    })
    try:
        with _opener.open(req, timeout=15) as r:
            cr = r.headers.get("Content-Range")  # bytes 0-0/21254789
            if cr and "/" in cr:
                return int(cr.split("/")[-1])
    except Exception:
        pass
    return None


def _fetch_item(url: str) -> tuple[str, dict]:
    """从链接（或口令文本）解析视频 ID 并获取 item 数据，ttwid 失效自动重铸重试。"""
    video_id = extract_video_id(url)
    if not video_id:
        raise RuntimeError("无法从链接中识别抖音视频 ID")
    item = None
    for attempt in (False, True):  # 第二次强制刷新 ttwid 重试
        try:
            item = _parse_item(_fetch_share_html(video_id, force_refresh=attempt))
            break
        except RuntimeError:
            if attempt:
                raise
    return video_id, item


def _no_watermark_url(play_addr: dict, hd: bool = False) -> str:
    """由 play_addr 构造无水印直链。"""
    if hd and play_addr.get("uri"):
        return f"https://www.iesdouyin.com/aweme/v1/play/?video_id={play_addr['uri']}&ratio=1080p&line=0"
    raw = (play_addr.get("url_list") or [""])[0]
    return raw.replace("/playwm/", "/play/")


def get_download_url(url: str, format_id: str | None = None) -> str:
    """服务端内部使用：解析并返回指定格式的无水印直链。"""
    _, item = _fetch_item(url)
    play_addr = (item.get("video") or {}).get("play_addr") or {}
    return _no_watermark_url(play_addr, hd=(format_id == "nowm_hd"))


def analyze(url: str) -> dict:
    """解析抖音视频，返回与 yt-dlp analyze 兼容的结构。"""
    video_id, item = _fetch_item(url)

    video = item.get("video") or {}
    if item.get("images"):
        raise RuntimeError("暂不支持图集/图文内容，请换视频链接")

    play_addr = video.get("play_addr") or {}
    if not play_addr.get("url_list"):
        raise RuntimeError("未取到视频播放地址")
    main_url = _no_watermark_url(play_addr)

    duration_ms = item.get("duration") or video.get("duration") or 0
    try:
        duration = int(duration_ms) / 1000 if int(duration_ms) > 100000 else int(duration_ms)
    except (TypeError, ValueError):
        duration = None

    cover = ((video.get("cover") or {}).get("url_list") or [""])[0]
    author = (item.get("author") or {}).get("nickname") or ""

    # 两个可选格式：原画（分享页原始码率）与高清（1080p 构造接口）
    formats = [{
        "format_id": "nowm_std",
        "label": "无水印 原画",
        "height": 1080,
        "ext": "mp4",
        "filesize": _probe_size(main_url),
    }]
    if play_addr.get("uri"):
        formats.append({
            "format_id": "nowm_hd",
            "label": "无水印 高清",
            "height": 1080,
            "ext": "mp4",
            "filesize": _probe_size(_no_watermark_url(play_addr, hd=True)),
        })

    return {
        "title": (item.get("desc") or "抖音视频").strip() or "抖音视频",
        "thumbnail": cover,
        "uploader": author,
        "duration": duration,
        "platform": "Douyin",
        "webpage_url": f"https://www.douyin.com/video/{video_id}",
        "formats": formats,
        "_douyin": True,  # 标记：下载走自研通道
    }


def open_stream(url: str):
    """打开无水印直链的下载流（供下载器流式读取）。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": MOBILE_UA,
        "Referer": "https://www.douyin.com/",
    })
    return _opener.open(req, timeout=30)
