"""
支付抽象层 + 模拟支付实现。

后续接入真实渠道（如虎皮椒 / 蓝兔支付 / 微信支付商户号）时，
只需实现 PaymentProvider 接口并替换 provider 实例，业务代码零改动。
"""
import threading
import time
import uuid

_lock = threading.Lock()
_orders = {}

# ---------- 会员方案（演示定价，可自行修改） ----------
PLANS = [
    {
        "id": "free",
        "name": "免费体验",
        "price": 0,
        "period": "",
        "features": ["标准清晰度下载", "单视频下载", "下载文件 30 分钟内有效"],
        "highlight": False,
    },
    {
        "id": "vip_month",
        "name": "会员 · 月卡",
        "price": 9.9,
        "period": "/月",
        "features": ["全部清晰度（最高 4K）", "批量下载队列", "仅音频提取", "优先下载通道"],
        "highlight": True,
    },
    {
        "id": "vip_year",
        "name": "会员 · 年卡",
        "price": 88,
        "period": "/年",
        "features": ["月卡全部权益", "折合 7.3 元/月", "新功能抢先体验"],
        "highlight": False,
    },
]


class PaymentProvider:
    """支付渠道抽象接口。接真实渠道时实现这三个方法即可。"""

    name = "abstract"

    def create_payment(self, order: dict) -> dict:
        raise NotImplementedError

    def query_payment(self, order_id: str) -> str:
        raise NotImplementedError

    def verify_notify(self, payload: dict) -> bool:
        raise NotImplementedError


class MockProvider(PaymentProvider):
    """模拟支付：前端弹出模拟收银台，用户点击\"我已完成支付\"即回调成功。
    仅用于开发联调，上线前替换为真实 Provider。"""

    name = "mock"

    def create_payment(self, order: dict) -> dict:
        return {
            "pay_type": "mock_qr",
            "pay_url": f"/mock-pay/{order['order_id']}",  # 前端模拟收银台路由
        }

    def query_payment(self, order_id: str) -> str:
        with _lock:
            return _orders.get(order_id, {}).get("status", "unknown")

    def verify_notify(self, payload: dict) -> bool:
        return payload.get("sign") == "mock-ok"


provider: PaymentProvider = MockProvider()


def create_order(plan_id: str) -> dict:
    plan = next((p for p in PLANS if p["id"] == plan_id), None)
    if not plan or plan["price"] <= 0:
        return {"error": "invalid_plan"}
    order_id = "R" + uuid.uuid4().hex[:14].upper()
    order = {
        "order_id": order_id,
        "plan_id": plan_id,
        "plan_name": plan["name"],
        "amount": plan["price"],
        "status": "created",  # created -> paying -> paid
        "created_at": time.time(),
    }
    with _lock:
        _orders[order_id] = order
    pay = provider.create_payment(order)
    order["status"] = "paying"
    return {**order, "pay": pay}


def confirm_mock_payment(order_id: str) -> dict:
    """模拟渠道的\"支付成功回调\"。真实渠道会由服务器间通知代替。"""
    with _lock:
        order = _orders.get(order_id)
        if not order:
            return {"error": "order_not_found"}
        order["status"] = "paid"
        order["paid_at"] = time.time()
        return {"ok": True, "order": {k: order[k] for k in ("order_id", "plan_name", "amount", "status")}}


def query_order(order_id: str) -> dict:
    with _lock:
        order = _orders.get(order_id)
        if not order:
            return {"error": "order_not_found"}
        return {k: order[k] for k in ("order_id", "plan_id", "plan_name", "amount", "status")}
