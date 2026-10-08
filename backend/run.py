import os, sys, uvicorn
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.main import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
