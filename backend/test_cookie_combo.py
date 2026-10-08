"""系统性测试：找出抖音 yt-dlp 解析所需的最小 Cookie 组合（全部为自动生成的访客凭证）"""
import json
import time
import urllib.request
import http.cookiejar
import secrets
import string
import sys

VIDEO = "https://www.douyin.com/video/7692329355342679331"


def gen_ttwid() -> str:
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
    with urllib.request.urlopen(req, timeout=15) as r:
        for h, v in r.getheaders():
            if h.lower() == "set-cookie" and "ttwid=" in v:
                return v.split("ttwid=")[1].split(";")[0]
    raise RuntimeError("ttwid 生成失败")


def gen_s_v_web_id() -> str:
    """仿 Douyin_TikTok_Download_API 的 verify_fp 生成逻辑"""
    chars = string.ascii_lowercase + string.digits
    parts = ["verify_", "".join(secrets.choice(chars) for _ in range(8)),
             "_", "".join(secrets.choice(chars) for _ in range(4)),
             "_", "".join(secrets.choice(chars) for _ in range(4)),
             "_", "".join(secrets.choice(chars) for _ in range(4)),
             "_", "".join(secrets.choice(chars) for _ in range(12))]
    return "".join(parts)


def write_cookiejar(pairs: list[tuple[str, str]]) -> str:
    exp = str(int(time.time()) + 86400)
    lines = ["# Netscape HTTP Cookie File"]
    for name, val in pairs:
        lines.append(f".douyin.com\tTRUE\t/\tTRUE\t{exp}\t{name}\t{val}")
    path = "cookies/_test_cookies.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def try_extract(cookiefile: str | None, label: str) -> bool:
    import yt_dlp
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "noplaylist": True, "simulate": True,
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"},
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(VIDEO, download=False)
        print(f"[PASS] {label}: {info.get('title', '')[:30]}")
        return True
    except Exception as e:
        print(f"[FAIL] {label}: {str(e)[:100]}")
        return False


if __name__ == "__main__":
    ttwid = gen_ttwid()
    svid = gen_s_v_web_id()
    print(f"ttwid OK: {ttwid[:30]}...")
    print(f"s_v_web_id OK: {svid}")

    results = {}
    results["A_无cookie"] = try_extract(None, "A: 无 Cookie")
    results["B_仅ttwid"] = try_extract(write_cookiejar([("ttwid", ttwid)]), "B: 仅 ttwid(自动生成)")
    results["C_ttwid+s_v_web_id"] = try_extract(
        write_cookiejar([("ttwid", ttwid), ("s_v_web_id", svid)]), "C: ttwid + s_v_web_id(自动生成)")

    print("\n=== 结果汇总 ===")
    for k, v in results.items():
        print(f"{k}: {'PASS' if v else 'FAIL'}")
