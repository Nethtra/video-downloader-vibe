"""测试 iesdouyin 分享页在不同 UA/Cookie 下的 SSR 数据可用性"""
import re
import json
import time
import urllib.request


def gen_ttwid() -> str:
    req = urllib.request.Request(
        "https://ttwid.bytedance.com/ttwid/union/register/",
        data=json.dumps({"region": "cn", "aid": 1768, "needFid": False,
                         "service": "www.ixigua.com",
                         "migrate_info": {"ticket": "", "source": "node"},
                         "cbUrlProtocol": "https", "union": True}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        for h, v in r.getheaders():
            if h.lower() == "set-cookie" and "ttwid=" in v:
                return v.split("ttwid=")[1].split(";")[0]
    raise RuntimeError("ttwid fail")


UAS = {
    "iphone_safari": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "android_chrome": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "douyin_webview": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148_aweme_28.0.0",
    "desktop_chrome": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
}

ttwid = gen_ttwid()
URL = "https://www.iesdouyin.com/share/video/7692329355342679331"

for ua_name, ua in UAS.items():
    for cookie_label, cookie in [("无cookie", ""), (f"ttwid", f"ttwid={ttwid}")]:
        try:
            req = urllib.request.Request(URL, headers={
                "User-Agent": ua,
                **({"Cookie": cookie} if cookie else {}),
            })
            with urllib.request.urlopen(req, timeout=20) as r:
                html = r.read().decode("utf-8", "ignore")
            has_router = "_ROUTER_DATA" in html
            has_item = '"item_list"' in html and "play_addr" in html
            m = re.search(r'"desc":"([^"]{0,40})', html)
            print(f"[{ua_name} + {cookie_label}] size={len(html)} router={has_router} item+playaddr={has_item} desc={m.group(1) if m else '-'}")
        except Exception as e:
            print(f"[{ua_name} + {cookie_label}] ERROR: {str(e)[:80]}")
