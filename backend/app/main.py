import asyncio
import json
import mimetypes
import re
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path

from . import downloader, payments, seo, summarizer

app = FastAPI(title="万能视频下载", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeReq(BaseModel):
    url: str


class DownloadReq(BaseModel):
    url: str
    format_id: str = "best"
    mode: str = "video"  # video | audio


class OrderReq(BaseModel):
    plan_id: str


class MockConfirmReq(BaseModel):
    order_id: str


@app.post("/api/analyze")
def analyze(req: AnalyzeReq):
    """异步解析：立即返回 analyze_id，前端轮询 GET /api/analyze/{id}。
    YouTube 等平台解析耗时长，同步等待会被网关超时掐断。"""
    url = req.url.strip()
    if not url.startswith(("http://", "https://")):
        # 兼容 App 分享口令等文本：提取其中第一个链接
        import re
        m = re.search(r'https?://[^\s，。、"\'）]+', url)
        if not m:
            raise HTTPException(400, "请输入视频链接（或包含链接的分享口令）")
        url = m.group(0)
    return {"analyze_id": downloader.start_analyze(url)}


@app.get("/api/analyze/{analyze_id}")
def analyze_status(analyze_id: str):
    task = downloader.get_analyze_task(analyze_id)
    if not task:
        raise HTTPException(404, "解析任务不存在或已过期")
    if task["status"] == "running":
        return {"status": "running"}
    if task["status"] == "error":
        err = task["error"] or ""
        if "confirm your age" in err:
            if downloader.COOKIES_FILE.exists():
                hint = ("该视频有年龄限制，已尝试站点配置的 YouTube Cookie 仍被拒绝："
                        "Cookie 可能已过期，或账号未通过 YouTube 年龄验证，请站长更新 cookies.txt")
            else:
                hint = ("该视频有年龄限制，YouTube 要求登录验证；"
                        "站长在 backend/cookies/cookies.txt 配置账号 Cookie 后即可支持此类视频")
        elif "not a bot" in err or "Sign in to confirm" in err:
            hint = "YouTube 风控拦截了服务器请求，请稍后重试"
        elif "Private" in err or "login" in err.lower():
            hint = "该视频需要登录或为私密视频，暂不支持"
        elif "Unsupported URL" in err:
            hint = "暂不支持该平台，请换个链接试试"
        elif "cookies" in err.lower():
            hint = "该平台需要站点配置访问 Cookie，请站长更新 backend/cookies/cookies.txt"
        elif "网络请求失败" in err or "超时" in err or "timed out" in err.lower():
            hint = "解析超时或网络不稳定，请稍后重试"
        else:
            # 兜底也透出真实原因片段（截断），便于远程定位网络/风控类问题
            brief = re.sub(r"\s+", " ", err.replace("ERROR:", "")).strip()[:120]
            hint = f"解析失败：{brief}" if brief else "解析失败，请确认链接是否正确、视频是否公开可见"
        return {"status": "error", "detail": hint, "raw": re.sub(r"\s+", " ", err)[:200]}
    return {"status": "done", "info": task["result"]}


@app.post("/api/download")
def download(req: DownloadReq):
    url = req.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "无效的视频链接")
    task_id = downloader.start_download(url, req.format_id, req.mode)
    return {"task_id": task_id}


@app.get("/api/task/{task_id}")
def task_status(task_id: str):
    task = downloader.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在或已过期")
    return task


@app.get("/api/file/{task_id}")
def download_file(task_id: str):
    path = downloader.get_file_path(task_id)
    if not path:
        raise HTTPException(404, "文件不存在或已过期（文件保留 30 分钟）")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, filename=path.name, media_type=media_type)


@app.get("/api/plans")
def plans():
    return payments.PLANS


@app.post("/api/pay/order")
def create_order(req: OrderReq):
    result = payments.create_order(req.plan_id)
    if "error" in result:
        raise HTTPException(400, "无效的付费方案")
    return result


@app.post("/api/pay/mock/confirm")
def mock_confirm(req: MockConfirmReq):
    result = payments.confirm_mock_payment(req.order_id)
    if "error" in result:
        raise HTTPException(404, "订单不存在")
    return result


@app.get("/api/pay/order/{order_id}")
def query_order(order_id: str):
    result = payments.query_order(order_id)
    if "error" in result:
        raise HTTPException(404, "订单不存在")
    return result


# ---------- AI 视频总结（新增功能，独立模块 summarizer.py） ----------

class SummaryReq(BaseModel):
    url: str


def _extract_url(text: str) -> str:
    """兼容分享口令：从文本中提取第一个链接。"""
    text = text.strip()
    if text.startswith(("http://", "https://")):
        return text
    m = re.search(r'https?://[^\s，。、"\'）]+', text)
    return m.group(0) if m else ""


def _client_ip(request: Request) -> str:
    """云沙箱在反代后，X-Forwarded-For 优先。"""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/api/summary")
def create_summary(req: SummaryReq, request: Request):
    url = _extract_url(req.url)
    if not url:
        raise HTTPException(400, "请输入视频链接（或包含链接的分享口令）")
    try:
        return {"task_id": summarizer.start_summary(url, _client_ip(request))}
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except downloader.yt_dlp.utils.DownloadError:
        raise HTTPException(400, "解析失败，请确认链接是否正确、视频是否公开可见")
    except Exception:
        import logging
        logging.getLogger("uvicorn.error").exception("summary 未处理异常: %s", url)
        raise HTTPException(400, "AI 总结服务暂时不可用，请稍后重试")


@app.get("/api/summary/quota")  # 注意：必须注册在 /{task_id} 之前，否则被当 task_id 吞掉
def summary_quota(request: Request):
    return summarizer.quota_of(_client_ip(request))


@app.get("/api/summary/{task_id}")
def summary_status(task_id: str):
    task = summarizer.get_task(task_id)
    if not task:
        raise HTTPException(404, "总结任务不存在或已过期")
    return task


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/summary/{task_id}/stream")
def summary_stream(task_id: str):
    """SSE 流式推送总结：status → meta → delta*（增量）→ done(结构化结果+字幕时间线) / error。
    素材提取与 LLM 调用都在后台线程执行，本路由只负责把增量搬运给前端；
    因此客户端断开/刷新都不会中断任务，重连可续看（增量从头重放）。"""
    def gen():
        yield _sse("status", {"phase": "fetching_subtitles"})
        # ---- 阶段 1：等待素材就绪（最长 150 秒）----
        waited = 0.0
        while waited < 150:
            task = summarizer.get_task(task_id)
            if not task:
                yield _sse("error", {"error": "总结任务不存在或已过期"})
                return
            if task["status"] == "error":
                yield _sse("error", {"error": task.get("error") or "总结失败"})
                return
            if task["status"] in ("summarizing", "done"):
                break
            time.sleep(0.5)
            waited += 0.5
            if waited % 5 < 0.5:
                yield ": keepalive\n\n"  # SSE 注释行，防代理断开空闲连接
        task = summarizer.get_task(task_id)
        if not task or task["status"] == "fetching_subtitles":
            yield _sse("error", {"error": "提取字幕超时，请稍后重试"})
            return
        if task["status"] == "error":
            yield _sse("error", {"error": task.get("error") or "总结失败"})
            return

        yield _sse("meta", {k: task.get(k) for k in
                            ("basis", "title", "webpage_url", "duration", "platform")})

        # ---- 阶段 2：搬运 LLM 增量（后台线程生产，这里消费）----
        sent = 0
        deadline = time.time() + 300
        while time.time() < deadline:
            text, sent = summarizer.get_stream_chunk(task_id, sent)
            if text:
                yield _sse("delta", {"t": text})
            task = summarizer.get_task(task_id)
            if not task:
                yield _sse("error", {"error": "总结任务不存在或已过期"})
                return
            if task["status"] == "done":
                yield _sse("done", {**{k: task.get(k) for k in
                                       ("basis", "title", "webpage_url", "duration", "platform")},
                                    "result": task.get("result"),
                                    "transcript": task.get("transcript") or []})
                return
            if task["status"] == "error":
                yield _sse("error", {"error": task.get("error") or "总结失败"})
                return
            time.sleep(0.2)
        yield _sse("error", {"error": "总结超时，请稍后重试"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ---------- 前端静态托管（发布时生效；开发时由 Vite 自己提供页面） ----------
# SEO 路由（robots/sitemap）必须注册在下方 catch-all 之前，否则会被吞掉
app.include_router(seo.router)

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "static"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        target = FRONTEND_DIST / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        index = FRONTEND_DIST / "index.html"
        # SEO 注入：爬虫读到含 meta/结构化数据/静态正文的完整 HTML；用户端 Vue 挂载后照常接管
        return HTMLResponse(seo.inject(index.read_text(encoding="utf-8"), index.stat().st_mtime))


@app.on_event("startup")
async def periodic_cleanup():
    async def cleaner():
        while True:
            await asyncio.sleep(600)
            try:
                downloader.cleanup_expired()
            except Exception:
                pass
            try:
                summarizer.cleanup_expired()
            except Exception:
                pass
    asyncio.create_task(cleaner())
