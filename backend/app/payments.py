"""支付抽象层：Stripe Checkout（真实收款）+ Mock（本地无密钥回退）。

- 订单持久化在 SQLite（db.py），状态机 created -> paying -> paid / failed
- 一次性支付（mode=payment）：月卡=+30 天、年卡=+365 天，从现有到期时间叠加
- 幂等三层（任一重放都不会重复开通会员）：
    1) 创建 Checkout Session 传 idempotency_key="order_"+order_id（Stripe 侧防重）
    2) processed_webhook_events 表按 event.id 去重（Stripe 会重发事件）
    3) mark_order_paid 原子 UPDATE ... WHERE status!='paid'（并发只履约一次）
- 未配置 STRIPE_API_KEY 时自动回退 MockProvider，本地可完整联调
"""
import os
import uuid

from . import db
from .envutil import load_env

load_env()

STRIPE_API_KEY = os.getenv("STRIPE_API_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# ---------- 会员方案（price 元用于展示；days 为购买后延长天数） ----------
PLANS = [
    {
        "id": "free",
        "name": "免费体验",
        "price": 0,
        "period": "",
        "days": 0,
        "features": ["标准清晰度下载", "单视频下载", "下载文件 30 分钟内有效", "每日 3 次 AI 视频总结"],
        "highlight": False,
    },
    {
        "id": "vip_month",
        "name": "会员 · 月卡",
        "price": 9.9,
        "period": "/月",
        "days": 30,
        "features": ["全部清晰度（最高 4K）", "批量下载队列", "仅音频提取", "优先下载通道", "AI 视频总结不限次"],
        "highlight": True,
    },
    {
        "id": "vip_year",
        "name": "会员 · 年卡",
        "price": 88,
        "period": "/年",
        "days": 365,
        "features": ["月卡全部权益", "折合 7.3 元/月", "新功能抢先体验", "AI 视频总结不限次"],
        "highlight": False,
    },
]

# 前端 locale -> Stripe Checkout locale（zh-CN 传 zh）
_STRIPE_LOCALES = {"zh": "zh", "en": "en", "ja": "ja", "ko": "ko", "es": "es", "fr": "fr"}


class PaymentProvider:
    """支付渠道抽象接口。"""

    name = "abstract"

    def create_payment(self, order: dict) -> dict:
        raise NotImplementedError

    def query_payment(self, session_id: str) -> str:
        raise NotImplementedError

    def verify_notify(self, payload: bytes, sig: str) -> dict:
        raise NotImplementedError


class MockProvider(PaymentProvider):
    """模拟支付：前端弹出模拟收银台，点击"我已完成支付"走统一履约。
    仅在未配置 STRIPE_API_KEY 时可用（本地开发联调）。"""

    name = "mock"

    def create_payment(self, order: dict) -> dict:
        return {
            "pay_type": "mock_qr",
            "pay_url": f"/mock-pay/{order['order_id']}",  # 前端模拟收银台
        }

    def query_payment(self, session_id: str) -> str:
        return "paying"  # mock 无外部状态，一切以 confirm/履约为准

    def verify_notify(self, payload: bytes, sig: str) -> dict:
        raise ValueError("mock provider has no webhook")


class StripeProvider(PaymentProvider):
    """Stripe Checkout Session（托管支付页）：服务端创建会话 -> 302 跳转 -> webhook 履约。"""

    name = "stripe"

    def create_payment(self, order: dict) -> dict:
        import stripe
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "quantity": 1,
                "price_data": {
                    "currency": "cny",
                    "unit_amount": order["amount_cents"],  # 分
                    "product_data": {"name": order["plan_name"]},
                },
            }],
            success_url=f"{SITE_BASE_URL}/?pay=success&order={order['order_id']}",
            cancel_url=f"{SITE_BASE_URL}/#pricing",
            customer_email=order.get("user_email"),
            locale=order.get("stripe_locale") or "auto",
            client_reference_id=order["order_id"],
            metadata={
                "order_id": order["order_id"],
                "user_id": str(order["user_id"]),
                "plan_id": order["plan_id"],
            },
            idempotency_key=f"order_{order['order_id']}",  # 幂等第一层
        )
        return {"pay_type": "stripe_redirect", "pay_url": session.url,
                "session_id": session.id}

    def query_payment(self, session_id: str) -> str:
        import stripe
        session = stripe.checkout.Session.retrieve(session_id)
        return "paid" if session.payment_status == "paid" else "paying"

    def verify_notify(self, payload: bytes, sig: str) -> dict:
        """验签并解析事件；签名/格式非法时抛异常（调用方转 400）。"""
        import stripe
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        return {"id": event["id"], "type": event["type"],
                "data": event["data"]["object"]}


if STRIPE_API_KEY:
    import stripe as _stripe
    _stripe.api_key = STRIPE_API_KEY
    provider: PaymentProvider = StripeProvider()
else:
    provider = MockProvider()


def _plan(plan_id: str) -> dict | None:
    return next((p for p in PLANS if p["id"] == plan_id), None)


# ---------- 下单 ----------

def create_order(plan_id: str, user, locale: str = "zh") -> dict:
    """创建订单并请求支付渠道。user 为 db.users 行（必须已登录）。"""
    plan = _plan(plan_id)
    if not plan or plan["price"] <= 0:
        return {"error": "invalid_plan"}
    order_id = "R" + uuid.uuid4().hex[:14].upper()
    amount_cents = int(round(plan["price"] * 100))
    db.create_order(order_id, user["id"], plan_id, amount_cents)
    order = {
        "order_id": order_id,
        "user_id": user["id"],
        "plan_id": plan_id,
        "plan_name": plan["name"],
        "amount_cents": amount_cents,
        "user_email": user["email"],
        "stripe_locale": _STRIPE_LOCALES.get((locale or "zh").split("-")[0].lower(), "auto"),
    }
    try:
        pay = provider.create_payment(order)
    except Exception as e:
        import logging
        logging.getLogger("uvicorn.error").exception("创建支付失败: %s", order_id)
        db.set_order_failed(order_id)
        return {"error": "payment_create_failed", "detail": str(e)[:200]}
    db.set_order_paying(order_id, pay.get("session_id") or "")
    return {
        "order_id": order_id,
        "plan_id": plan_id,
        "plan_name": plan["name"],
        "amount": plan["price"],
        "status": "paying",
        "pay": pay,
    }


# ---------- 统一履约（mock confirm 与 Stripe webhook 共用） ----------

def fulfill_order(order_id: str) -> dict:
    """原子标记 paid -> 延长会员。幂等：重复调用返回当前状态，不重复延长。"""
    order = db.get_order(order_id)
    if not order:
        return {"error": "order_not_found"}
    plan = _plan(order["plan_id"])
    if not plan:
        return {"error": "invalid_plan"}
    if not db.mark_order_paid(order_id):
        # 已支付（或 failed）：直接返回现状，绝不再履约
        cur = db.get_order(order_id)
        user = db.get_user(order["user_id"])
        return {"order_id": order_id, "status": cur["status"],
                "vip_until": user["vip_until"] if user else None}
    vip_until = db.extend_vip(order["user_id"], plan["days"])
    return {"order_id": order_id, "status": "paid", "vip_until": vip_until}


def confirm_mock_payment(order_id: str) -> dict:
    """Mock 渠道的"支付成功回调"，与 webhook 共用统一履约。
    仅 mock provider 下可调用（路由层把关），生产环境不可达。"""
    return fulfill_order(order_id)


# ---------- 查询 ----------

def query_order(order_id: str) -> dict:
    """查询订单。Stripe 单在 paying 时主动向 Stripe 同步一次（webhook 未达的兜底）。"""
    order = db.get_order(order_id)
    if not order:
        return {"error": "order_not_found"}
    if (order["status"] == "paying" and order["stripe_session_id"]
            and provider.name == "stripe"):
        try:
            if provider.query_payment(order["stripe_session_id"]) == "paid":
                fulfill_order(order_id)
                order = db.get_order(order_id) or order
        except Exception:
            pass  # Stripe 查询失败不影响本地状态返回
    user = db.get_user(order["user_id"])
    return {
        "order_id": order["order_id"],
        "plan_id": order["plan_id"],
        "amount": order["amount_cents"] / 100,
        "status": order["status"],
        "vip_until": user["vip_until"] if user else None,
    }


# ---------- Stripe webhook ----------

def handle_webhook(payload: bytes, sig: str) -> dict:
    """验签 -> 事件去重 -> 履约。验签失败抛异常（路由转 400，Stripe 会重试）。"""
    if not STRIPE_WEBHOOK_SECRET:
        raise ValueError("webhook_secret_missing")
    info = provider.verify_notify(payload, sig)
    if not db.add_event(info["id"], info["type"]):
        return {"received": True, "duplicate": True}  # 幂等第二层
    obj = info["data"] or {}
    order_id = (obj.get("metadata") or {}).get("order_id") or obj.get("client_reference_id")
    if info["type"] in ("checkout.session.completed",
                        "checkout.session.async_payment_succeeded"):
        if order_id:
            return {"received": True, "fulfilled": fulfill_order(order_id)}
    elif info["type"] == "checkout.session.async_payment_failed":
        if order_id:
            db.set_order_failed(order_id)
            return {"received": True, "failed": order_id}
    else:
        # charge.refunded / charge.dispute.* 等事件：本期仅入库留痕，不自动撤销会员
        pass
    return {"received": True}
