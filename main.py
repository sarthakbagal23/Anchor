"""OpenNotebook entrypoint: launch the local web app and open the browser."""
import webbrowser
import threading

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

app = FastAPI(title="OpenNotebook")

# ponytail: stub route replaced by backend.api in Task 14. Kept here only so
# `python main.py` is green from day one.
@app.get("/api/health")
def health():
    return {"status": "ok"}

FRONTEND_DIR = Path(__file__).parent / "frontend"

def main():
    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:8765")).start()
    uvicorn.run(app, host="127.0.0.1", port=8765)

if __name__ == "__main__":
    main()
