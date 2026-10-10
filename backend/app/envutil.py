"""统一 .env 加载：读取 backend/.env，逐行 os.environ.setdefault。

与 summarizer._load_env 逻辑一致（setdefault 幂等，两边共存无冲突）。
用法：需要读环境变量的模块在顶部先 load_env() 再 os.getenv()。
"""
import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env():
    if not ENV_FILE.exists():
        return
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, val)
    except Exception:
        pass  # .env 读取失败不阻断启动（环境变量本身也可生效）
