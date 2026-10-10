"""SQLite 持久化：用户 / 会话 / 订单 / webhook 事件去重。

约定：
- 所有时间字段存 UTC ISO8601 字符串（datetime.now(timezone.utc).isoformat()）
- 每次调用新建连接（sqlite 连接不可跨线程共享），WAL 模式提升并发读写
- 其他业务模块只调本模块函数，不直接写 SQL
- 库文件 backend/data/app.db（自动创建目录，已加入 .gitignore）
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent      # backend/
DB_DIR = BASE_DIR / "data"
DB_PATH = DB_DIR / "app.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  email         TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,              -- 格式 "scrypt$salt_hex$hash_hex"
  vip_until     TEXT,                       -- ISO8601 UTC，NULL=非会员
  created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,              -- secrets.token_urlsafe(32)
  user_id    INTEGER NOT NULL REFERENCES users(id),
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
  order_id          TEXT PRIMARY KEY,       -- "R" + uuid 前 14 位大写
  user_id           INTEGER NOT NULL REFERENCES users(id),
  plan_id           TEXT NOT NULL,
  amount_cents      INTEGER NOT NULL,       -- 分（990 / 8800），避免浮点
  currency          TEXT NOT NULL DEFAULT 'cny',
  status            TEXT NOT NULL DEFAULT 'created',  -- created|paying|paid|failed
  stripe_session_id TEXT,
  created_at        TEXT NOT NULL,
  paid_at           TEXT
);
CREATE TABLE IF NOT EXISTS processed_webhook_events (
  event_id     TEXT PRIMARY KEY,            -- stripe event.id，幂等去重
  type         TEXT,
  processed_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _exec(query: str, params=(), fetch: str | None = None):
    """执行单条 SQL：fetch=None 返回 rowcount（写操作），'one'/'all' 返回行。"""
    conn = get_conn()
    try:
        cur = conn.execute(query, params)
        if fetch == "one":
            row = cur.fetchone()
        elif fetch == "all":
            row = cur.fetchall()
        else:
            row = cur.rowcount
        conn.commit()
        return row
    finally:
        conn.close()


def init_db():
    conn = get_conn()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ---------- users ----------

def create_user(email: str, password_hash: str) -> None:
    _exec("INSERT INTO users(email, password_hash, created_at) VALUES(?,?,?)",
          (email, password_hash, _now()))


def get_user_by_email(email: str) -> sqlite3.Row | None:
    return _exec("SELECT * FROM users WHERE email=?", (email,), fetch="one")


def get_user(user_id: int) -> sqlite3.Row | None:
    return _exec("SELECT * FROM users WHERE id=?", (user_id,), fetch="one")


def extend_vip(user_id: int, days: int) -> str:
    """延长会员有效期：从 max(now, 现有 vip_until) 起加 days（重复购买叠加）。
    BEGIN IMMEDIATE 写锁防并发读改写；返回新的 vip_until。"""
    now = datetime.now(timezone.utc)
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT vip_until FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            conn.rollback()
            raise ValueError("user_not_found")
        base = now
        if row["vip_until"]:
            try:
                until = datetime.fromisoformat(row["vip_until"])
                if until > base:
                    base = until
            except ValueError:
                pass  # 旧数据损坏时从当前时间起算
        new_until = (base + timedelta(days=days)).isoformat()
        conn.execute("UPDATE users SET vip_until=? WHERE id=?", (new_until, user_id))
        conn.commit()
        return new_until
    finally:
        conn.close()


# ---------- sessions ----------

def create_session(token: str, user_id: int, expires_at: str) -> None:
    _exec("INSERT INTO sessions(token, user_id, expires_at, created_at) VALUES(?,?,?,?)",
          (token, user_id, expires_at, _now()))


def get_session_user(token: str) -> sqlite3.Row | None:
    """按 token 查会话对应用户；过期自动清除并返回 None。"""
    if not token:
        return None
    row = _exec(
        """SELECT u.id, u.email, u.vip_until, u.created_at, s.expires_at
           FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token=?""", (token,), fetch="one")
    if not row:
        return None
    try:
        expired = datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc)
    except ValueError:
        expired = True
    if expired:
        delete_session(token)
        return None
    return row


def delete_session(token: str) -> None:
    _exec("DELETE FROM sessions WHERE token=?", (token,))


# ---------- orders ----------

def create_order(order_id: str, user_id: int, plan_id: str,
                 amount_cents: int, currency: str = "cny") -> None:
    _exec("""INSERT INTO orders(order_id, user_id, plan_id, amount_cents, currency,
             status, created_at) VALUES(?,?,?,?,?,'created',?)""",
          (order_id, user_id, plan_id, amount_cents, currency, _now()))


def get_order(order_id: str) -> sqlite3.Row | None:
    return _exec("SELECT * FROM orders WHERE order_id=?", (order_id,), fetch="one")


def set_order_paying(order_id: str, session_id: str) -> None:
    _exec("UPDATE orders SET status='paying', stripe_session_id=? WHERE order_id=? AND status='created'",
          (session_id, order_id))


def set_order_failed(order_id: str) -> None:
    """仅未支付订单标记失败（已支付的不动，避免异步失败事件误伤）。"""
    _exec("UPDATE orders SET status='failed' WHERE order_id=? AND status!='paid'",
          (order_id,))


def mark_order_paid(order_id: str) -> bool:
    """原子置 paid：WHERE status!='paid' 保证并发/重放下只有一次生效。
    返回 True 表示本次调用完成了状态迁移（调用方据此履约，幂等第三层）。"""
    rc = _exec("UPDATE orders SET status='paid', paid_at=? WHERE order_id=? AND status!='paid'",
               (_now(), order_id))
    return rc == 1


# ---------- webhook 事件去重（幂等第二层） ----------

def add_event(event_id: str, type_: str) -> bool:
    """INSERT OR IGNORE：返回 False 表示该事件已处理过（Stripe 会重发事件）。"""
    rc = _exec("INSERT OR IGNORE INTO processed_webhook_events(event_id, type, processed_at) VALUES(?,?,?)",
               (event_id, type_, _now()))
    return rc == 1


# ---------- 维护 ----------

def cleanup_expired() -> None:
    """清理过期会话。挂到 main.py startup 的定时清理循环。"""
    _exec("DELETE FROM sessions WHERE expires_at < ?", (_now(),))
