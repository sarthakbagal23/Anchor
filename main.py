"""OpenNotebook — open the local web app."""
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles

from backend.api import build_app
from backend.config import load_config
from backend.store import Store

DB_PATH = Path(__file__).parent / "opennotebook.db"
FRONTEND_DIR = Path(__file__).parent / "frontend"

store = Store(str(DB_PATH))
store.init()
cfg = load_config()

# Print config summary so user knows what model is loaded
print(f"[OpenNotebook] LLM: {cfg.llm.model or '(not set)'} @ {cfg.llm.base_url or '(not set)'}")
if not cfg.llm.base_url or not cfg.llm.model:
    print("[OpenNotebook] WARNING: LLM not configured — chat will fail. Edit ~/.opennotebook/config.yaml")

app = build_app(store, cfg)

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


@app.middleware("http")
async def no_cache_frontend(request, call_next):
    """Force the browser to revalidate static files on every load so frontend
    edits (app.js, pdfview.js, etc.) are picked up instead of a stale cached copy."""
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


def main():
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:8765")).start()
    uvicorn.run(app, host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
