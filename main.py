"""Anchor — open the local web app."""
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles

from backend.api import build_app, add_security_middleware
from backend.config import load_config, data_dir, migrate_db
from backend.store import Store

FRONTEND_DIR = Path(__file__).parent / "frontend"

cfg = load_config()


def _resolve_db_path() -> Path:
    """Database lives under the data dir now (no longer loose in the repo
    root). First boot copies the legacy repo-root opennotebook.db over —
    copy, never move, so a failed migration can't strand study data."""
    target = data_dir(cfg) / "anchor.db"
    legacy = Path(__file__).parent / "opennotebook.db"
    return migrate_db(target, legacy, "database")


DB_PATH = _resolve_db_path()

store = Store(str(DB_PATH))
store.init()

# Print config summary so user knows what model is loaded
print(f"[Anchor] LLM: {cfg.llm.model or '(not set)'} @ {cfg.llm.base_url or '(not set)'}")
if not cfg.llm.base_url or not cfg.llm.model:
    print("[Anchor] WARNING: LLM not configured — chat will fail. Edit ~/.anchor/config.yaml")

app = build_app(store, cfg)

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


def _load_xsrf_token() -> str:
    """Stable per-machine CSRF token, created once in the config dir."""
    import os
    import secrets

    from backend.config import config_dir

    cfg_dir = config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "xsrf_token"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    try:
        path.write_text(token, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # Windows: best effort, config dir is already user-private
    except OSError:
        pass
    return token


XSRF_TOKEN = _load_xsrf_token()

# CSRF/DNS-rebind guard lives in backend.api so tests can mount it on an
# isolated app; the threat model is documented on add_security_middleware.
add_security_middleware(app, XSRF_TOKEN)


@app.middleware("http")
async def no_cache_frontend(request, call_next):
    """Force the browser to revalidate static files on every load so frontend
    edits (chat.js, pdfViewer.js, etc.) are picked up instead of a stale cached copy."""
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Anchor local server")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default 127.0.0.1; keep loopback unless you know why)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="don't auto-open the browser")
    args = parser.parse_args()
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
