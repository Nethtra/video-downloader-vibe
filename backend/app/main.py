import asyncio
import mimetypes

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path

from . import downloader, payments

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
    url = req.url.strip()
    if not url.startswith(("http://", "https://")):
        # 兼容 App 分享口令等文本：提取其中第一个链接
        import re
        m = re.search(r'https?://[^\s，。、"\'）]+', url)
        if not m:
            raise HTTPException(400, "请输入视频链接（或包含链接的分享口令）")
        url = m.group(0)
    try:
        return downloader.analyze(url)
    except downloader.yt_dlp.utils.DownloadError as e:
        msg = str(e)
        hint = "解析失败，请确认链接是否正确、视频是否公开可见"
        if "Unsupported URL" in msg:
            hint = "暂不支持该平台，请换个链接试试"
        elif "Private" in msg or "login" in msg.lower():
            hint = "该视频需要登录或为私密视频，暂不支持"
        elif "cookies" in msg.lower():
            hint = "该平台需要站点配置访问 Cookie（如抖音），请站长更新 backend/cookies/cookies.txt"
        raise HTTPException(400, hint)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        # 兜底：任何未预期异常都不返回 500 裸错误，给用户可读提示
        import logging
        logging.getLogger("uvicorn.error").exception("analyze 未处理异常: %s", url)
        raise HTTPException(400, "解析服务暂时不可用，请稍后重试")


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


# ---------- 前端静态托管（发布时生效；开发时由 Vite 自己提供页面） ----------
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "static"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        target = FRONTEND_DIST / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(FRONTEND_DIST / "index.html")


@app.on_event("startup")
async def periodic_cleanup():
    async def cleaner():
        while True:
            await asyncio.sleep(600)
            try:
                downloader.cleanup_expired()
            except Exception:
                pass
    asyncio.create_task(cleaner())
