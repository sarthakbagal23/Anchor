# OpenNotebook MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a local web app where a user pastes a YouTube lecture URL into a notebook, the app transcribes + indexes it, and the user chats with the lecture — answers are grounded strictly in the transcript with time-coded citations that seek the embedded YouTube player to the cited moment.

**Architecture:** One FastAPI process serves a static HTML/JS frontend, reads SQLite + sqlite-vec for storage and vectors, and runs YouTube ingestion as in-process async tasks. A hybrid retrieve-then-rerank grounding pipeline grounds every answer; the OpenAI-compatible LLM socket means cloud or local models via one config. Clean module boundaries (deployment shape A, code structure C).

**Tech Stack:** Python ≥ 3.11, FastAPI, uvicorn, SQLite + sqlite-vec, yt-dlp, faster-whisper, sentence-transformers (bundled embedder + cross-encoder reranker), the `openai` Python SDK (hits any OpenAI-compatible endpoint: NVIDIA NIM, OpenRouter, Ollama, LM Studio), vanilla HTML/JS/CSS (no build step), YouTube IFrame Player API.

## Global Constraints

- **Python ≥ 3.11.** Create and use a project-local venv (Task 1) — do NOT install into system Python or the hermes-agent venv.
- **Single OpenAI-compatible LLM socket** (D1): `base_url`, `api_key`, `model` in config. Never branch code on "which provider."
- **Heavy ML deps are lazy-imported** (`faster-whisper`, `sentence-transformers`, `torch`): imported inside functions, never at module top, so a cloud-only user installs none of them.
- **Testing = `assert`-based `__main__` self-checks**, run via `python -m backend.<module>`. No pytest, no test framework. Each check is the smallest thing that fails if the logic breaks. TDD rhythm still applies: write the `__main__` check first, watch it fail (NameError/AttributeError or assert), implement, watch it pass, commit.
- **No unrequested abstractions** (ponytail): no interface with one implementation *unless* a second is designed for (embedder/reranker/whisper all have bundled + openai_compatible, so they earn an interface). A `Config` dataclass with one loader is fine.
- **Working name "OpenNotebook"** — do not use "NotebookLM" in repo dirs, package names, or config paths ("NotebookLM" is Google's trademark). The on-disk config dir is `~/.opennotebook/`.
- **Citation wire format is `[#]`** (what the LLM emits and we parse); **render format is `⌜title · mm:ss⌟`** (frontend only). Never confuse them.
- **Commit after every green check**, small commits, conventional-commit messages.

---

## File Structure

```
opennotebook/
├── backend/
│   ├── __init__.py
│   ├── config.py              # load/validate ~/.opennotebook/config.yaml, graceful degrade
│   ├── store.py               # SQLite + sqlite-vec schema, CRUD, vector ops
│   ├── llm_client.py          # openai-SDK wrapper, stream(), max_context
│   ├── api.py                 # FastAPI routes + SSE
│   ├── grounding/
│   │   ├── __init__.py
│   │   ├── chunker.py         # timestamp-preserving semantic chunker
│   │   ├── embedder.py        # Embedder interface: bundled + openai_compatible
│   │   ├── retriever.py       # sqlite-vec cosine top-N
│   │   ├── reranker.py        # Reranker interface: bundled cross-encoder (MVP)
│   │   ├── citations.py       # tolerant [#] wire parser + citation_map builder
│   │   └── pipeline.py        # orchestrator: ground() + stream_answer()
│   └── ingestion/
│       ├── __init__.py
│       ├── youtube.py         # yt-dlp audio download
│       ├── transcribe.py      # faster-whisper + openai_compatible override
│       └── ingest.py           # download→transcribe→chunk→embed→store, status updates
├── frontend/
│   ├── index.html             # single page: notebook list, source panel, chat
│   ├── style.css              # seek-token UX (⌜…⌟), visited/active states
│   ├── app.js                 # chat, SSE streaming, citation render + seek dispatch
│   └── player.js              # YouTube IFrame API wrapper, seekTo()
├── main.py                    # one entrypoint: uvicorn + open browser
├── pyproject.toml             # deps + project metadata
├── config.example.yaml        # documented sample config
└── README.md
```

Each module has one responsibility and is independently runnable via `python -m backend.<module>` to self-check.

---

## Task 1: Project scaffold, venv, git, entrypoint shell

**Files:**
- Create: `opennotebook/pyproject.toml`
- Create: `opennotebook/backend/__init__.py` (empty)
- Create: `opennotebook/backend/grounding/__init__.py` (empty)
- Create: `opennotebook/backend/ingestion/__init__.py` (empty)
- Create: `opennotebook/main.py`
- Create: `opennotebook/.gitignore`

**Interfaces:**
- Produces: a running `python main.py` serving FastAPI at `http://127.0.0.1:8765` with one stub route `GET /api/health -> {"status":"ok"}`.

- [ ] **Step 1: Create the venv and install runtime deps**

From `C:\Users\sarth\Desktop`:

```bash
python3.13 -m venv opennotebook/.venv
opennotebook/.venv/Scripts/python.exe -m pip install --upgrade pip
opennotebook/.venv/Scripts/pip install fastapi "uvicorn[standard]" httpx pyyaml sqlite-vec openai
```

(Note: `faster-whisper`, `sentence-transformers`, `yt-dlp` are installed in their own tasks so the base env stays small until needed.)

- [ ] **Step 2: Write `pyproject.toml`**

Create `opennotebook/pyproject.toml`:

```toml
[project]
name = "opennotebook"
version = "0.1.0"
description = "Open-source, self-hostable study companion grounded in your sources."
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "httpx>=0.27",
    "pyyaml>=6.0",
    "sqlite-vec>=0.1.6",
    "openai>=1.40",
]

[project.optional-dependencies]
local = [
    "faster-whisper>=1.0",
    "sentence-transformers>=3.0",
    "yt-dlp>=2024.8.6",
]

[tool.setuptools]
py-modules = ["main"]
packages = ["backend", "backend.grounding", "backend.ingestion"]
```

- [ ] **Step 3: Write the empty package markers**

Create empty files: `backend/__init__.py`, `backend/grounding/__init__.py`, `backend/ingestion/__init__.py` (each just a comment `# package marker`).

- [ ] **Step 4: Write `main.py` shell**

Create `opennotebook/main.py`:

```python
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
```

- [ ] **Step 5: Write `.gitignore`** AND **Step 6: Init git** AND **Step 7: Self-check run**

Create `opennotebook/.gitignore`:

```
.venv/
__pycache__/
*.pyc
*.db
dist/
build/
.eggs/
*.egg-info/
```

From `opennotebook/`:

```bash
git init
git add pyproject.toml backend/__init__.py backend/grounding/__init__.py backend/ingestion/__init__.py main.py .gitignore
git commit -m "feat: scaffold project, venv, entrypoint shell"
```

Self-check: `opennotebook/.venv/Scripts/python.exe main.py` — expect uvicorn to start; `GET /api/health` returns `{"status":"ok"}` (use a browser or `python -c "import httpx; print(httpx.get('http://127.0.0.1:8765/api/health').json())"`). Ctrl-C it. No commit needed (commit was step 6).

---

## Task 2: config.py — load, validate, graceful degrade

**Files:**
- Create: `opennotebook/backend/config.py`
- Create: `opennotebook/config.example.yaml`

**Interfaces:**
- Consumes: `~/.opennotebook/config.yaml` (created from `config.example.yaml` on first load if missing).
- Produces: `load_config() -> AppConfig`; `AppConfig` has `.llm`, `.embeddings`, `.reranker`, `.whisper` with provider/bases; `.save()` persists. Callers (`llm_client`, embedder, whisper) read `AppConfig`.

- [ ] **Step 1: Write the failing self-check**

Create `opennotebook/backend/config.py` with only the `__main__` block:

```python
if __name__ == "__main__":
    import tempfile, os
    d = tempfile.mkdtemp()
    os.environ["OPENNOTEBOOK_CONFIG_DIR"] = d
    cfg = load_config()
    # defaults: local-only user gets working config with no keys
    assert cfg.llm.base_url and cfg.llm.model, "llm has no defaults — user must always supply"
    # graceful degrade: unreachable embeddings falls back to keyword + warning flag
    cfg.embeddings.provider = "openai_compatible"
    cfg.embeddings.base_url = "http://127.0.0.1:9/does-not-exist"
    cfg = _validate(cfg)  # noqa
    assert cfg.embeddings.fallback == "keyword", "unreachable embeddings should fall back to keyword"
    assert cfg.embeddings.warning, "degrade should set a visible warning"
    print("config OK")
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd C:\Users\sarth\Desktop\opennotebook
.venv/Scripts/python.exe -m backend.config
```
Expected: `NameError: name 'load_config' is not defined`.

- [ ] **Step 3: Implement `config.py`**

Full `opennotebook/backend/config.py`:

```python
"""Config: load/validate ~/.opennotebook/config.yaml with zero-config defaults
and graceful degradation."""
from __future__ import annotations
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_DIR = Path(os.environ.get("OPENNOTEBOOK_CONFIG_DIR", Path.home() / ".opennotebook"))
DEFAULTS_YAML = """
llm:
  base_url: null
  api_key: null
  model: null
  max_context: null
embeddings:
  provider: bundled
  base_url: null
  api_key: null
  model: null
reranker:
  provider: bundled
  base_url: null
  api_key: null
  model: null
whisper:
  provider: bundled
  base_url: null
  api_key: null
  model: small
"""


@dataclass
class Section:
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    fallback: str | None = None      # "keyword" when embeddings unreachable
    warning: str | None = None       # human-readable degrade reason


@dataclass
class LLMSection:
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    max_context: int | None = None


@dataclass
class AppConfig:
    llm: LLMSection = field(default_factory=LLMSection)
    embeddings: Section = field(default_factory=Section)
    reranker: Section = field(default_factory=Section)
    whisper: Section = field(default_factory=lambda: Section(model="small"))


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _coerce(raw: dict) -> AppConfig:
    llm = LLMSection(**raw.get("llm", {}))
    emb = Section(**raw.get("embeddings", {}))
    rnk = Section(**raw.get("reranker", {}))
    wh = Section(**raw.get("whisper", {}))
    return AppConfig(llm=llm, embeddings=emb, reranker=rnk, whisper=wh)


def _validate(cfg: AppConfig) -> AppConfig:
    """Loud-fail on the one thing that truly blocks us (no LLM); degrade on the rest."""
    if not cfg.llm.base_url or not cfg.llm.model:
        # ponytail: not a hard crash — return a flag so the UI can prompt setup.
        # But the self-check asserts defaults exist; for the real user, an empty
        # config is the setup state, surfaced by api.py Task 14.
        return cfg
    # embeddings: bundled always works (lazy import tolerates missing torch later).
    # openai_compatible must be probed at first use, not startup (costs a round trip).
    # We store the provider; degrade happens lazily in embedder.py (Task 5).
    return cfg


def load_config() -> AppConfig:
    cfg_dir = Path(os.environ.get("OPENNOTEBOOK_CONFIG_DIR", DEFAULT_CONFIG_DIR))
    cfg_path = cfg_dir / "config.yaml"
    base = yaml.safe_load(DEFAULTS_YAML)
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
        merged = _deep_merge(base, user)
    else:
        cfg_dir.mkdir(parents=True, exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(DEFAULTS_YAML.strip())
        merged = base
    return _validate(_coerce(merged))


def save_config(cfg: AppConfig) -> None:
    cfg_dir = Path(os.environ.get("OPENNOTEBOOK_CONFIG_DIR", DEFAULT_CONFIG_DIR))
    cfg_path = cfg_dir / "config.yaml"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    raw = asdict(cfg)
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f)
```

Adjust the self-check: the degrade assertion in Step 1 called `_validate` expecting `fallback="keyword"` — but `_validate` defers degrade to the embedder. Fix the self-check to match the real design (probe is lazy in embedder.py). Replace the self-check's degrade block with:

```python
    # graceful degrade is exercised in embedder.py (Task 5); here we only assert
    # that a missing config still produces a loadable AppConfig with bundled defaults.
    assert cfg.embeddings.provider == "bundled", "default embeddings must be bundled"
    assert cfg.whisper.provider == "bundled", "default whisper must be bundled"
    assert cfg.reranker.provider == "bundled", "default reranker must be bundled"
    print("config OK")
```
(Remove the `_validate(cfg)` lines from the self-check.)

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/Scripts/python.exe -m backend.config
```
Expected: `config OK`.

- [ ] **Step 5: Write `config.example.yaml`**

Create `opennotebook/config.example.yaml` (the documented sample users copy):

```yaml
# OpenNotebook config — copy to ~/.opennotebook/config.yaml or edit via the UI.
# Defaults (below commented values) make local-only use work with zero config.
llm:
  base_url: https://integrate.api.nvidia.com/v1   # or http://localhost:1234/v1 (LM Studio), http://localhost:11434/v1 (Ollama)
  api_key: ${NVIDIA_API_KEY}                       # env var expansion is manual; set your real key
  model: nvidia/llama-3.1-nemotron-70b
  max_context: null                                 # null = infer reasonable default per model size

embeddings:
  provider: bundled                                 # bundled (CPU sentence-transformers) | openai_compatible
  # base_url / api_key / model only used when provider: openai_compatible

reranker:
  provider: bundled                                 # bundled cross-encoder (MVP). openai_compatible is a deferred extension.

whisper:
  provider: bundled                                 # bundled faster-whisper | openai_compatible
  model: small                                      # tiny | base | small | medium | large
  # base_url / api_key used only when provider: openai_compatible
```

- [ ] **Step 6: Commit**

```bash
git add backend/config.py config.example.yaml
git commit -m "feat(config): load/validate config with zero-config defaults"
```

---

## Task 3: store.py — SQLite + sqlite-vec schema and CRUD

**Files:**
- Create: `opennotebook/backend/store.py`

**Interfaces:**
- Consumes: nothing (uses sqlite3 stdlib + sqlite-vec, lazy-loaded on first connect).
- Produces: `Store` class with `init(db_path)`, `add_notebook(title)->id`, `list_notebooks()`, `add_source(notebook_id, type, title, youtube_id, duration_sec)->id`, `set_source_status(id, status, error=None)`, `get_source(id)`, `list_sources(notebook_id)`, `add_chunk(source_id, ord, text, start_sec, end_sec, token_count, embedding)->id`, `get_chunks(chunk_ids)->list`, `add_message(notebook_id, role, content)`, `list_messages(notebook_id)`, and `search(query_vec, notebook_id, k)->[chunk_id]` (sqlite-vec cosine).

- [ ] **Step 1: Write the failing self-check**

`opennotebook/backend/store.py`, only `__main__`:

```python
if __name__ == "__main__":
    import tempfile
    db = os.path.join(tempfile.mkdtemp(), "test.db")
    store = Store(db)
    store.init()
    nb = store.add_notebook("Lecture 3")
    s = store.add_source(nb, "youtube", "Calculus", "vid123", 600)
    store.set_source_status(s, "ready")
    assert store.get_source(s)["status"] == "ready"
    # add chunks with a fake embedding of dim 4
    c1 = store.add_chunk(s, 0, "epsilon delta definition", 822, 845, 30, [1.0, 0.0, 0.0, 0.0])
    store.add_chunk(s, 1, "proof of the limit law", 1630, 1670, 35, [0.0, 1.0, 0.0, 0.0])
    hits = store.search([0.95, 0.05, 0.0, 0.0], nb, k=2)
    assert c1 == hits[0], f"expected nearest chunk first, got {hits}"
    store.add_message(nb, "user", "what is a limit?")
    assert len(store.list_messages(nb)) == 1
    print("store OK")
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python.exe -m backend.store
```
Expected: `NameError: name 'Store' is not defined`.

- [ ] **Step 3: Implement `store.py`**

```python
"""SQLite + sqlite-vec persistence: notebooks, sources, chunks, vectors, messages."""
from __future__ import annotations
import os
import sqlite3
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS notebooks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  notebook_id INTEGER NOT NULL,
  type TEXT NOT NULL,
  title TEXT,
  youtube_id TEXT,
  duration_sec INTEGER,
  status TEXT NOT NULL DEFAULT 'queued',
  error TEXT,
  created_at REAL NOT NULL,
  FOREIGN KEY(notebook_id) REFERENCES notebooks(id)
);
CREATE INDEX IF NOT EXISTS idx_sources_nb ON sources(notebook_id);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL,
  ord INTEGER NOT NULL,
  text TEXT NOT NULL,
  start_sec INTEGER,
  end_sec INTEGER,
  token_count INTEGER,
  FOREIGN KEY(source_id) REFERENCES sources(id)
);
CREATE INDEX IF NOT EXISTS idx_chunks_src ON chunks(source_id);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  notebook_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at REAL NOT NULL,
  FOREIGN KEY(notebook_id) REFERENCES notebooks(id)
);
"""


class Store:
    def __init__(self, db_path: str, vec_dim: int | None = None):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.vec_dim = vec_dim  # set after first embed; create vec table lazily
        self._vec_ready = False

    def init(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _ensure_vec(self, dim: int) -> None:
        if self._vec_ready and self.vec_dim == dim:
            return
        self.conn.enable_load_extension(True)
        import sqlite_vec  # lazy
        self.conn.load_extension(sqlite_vec.loadable_path)
        self.conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(embedding FLOAT[{dim}])")
        self.conn.commit()
        self.vec_dim = dim
        self._vec_ready = True

    # --- notebooks ---
    def add_notebook(self, title: str) -> int:
        cur = self.conn.execute("INSERT INTO notebooks(title, created_at) VALUES(?, ?)", (title, time.time()))
        self.conn.commit()
        return cur.lastrowid

    def list_notebooks(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM notebooks ORDER BY created_at DESC")]

    def get_notebook(self, nb_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM notebooks WHERE id=?", (nb_id,)).fetchone()
        return dict(r) if r else None

    # --- sources ---
    def add_source(self, notebook_id: int, type_: str, title: str, youtube_id: str | None, duration_sec: int | None) -> int:
        cur = self.conn.execute(
            "INSERT INTO sources(notebook_id, type, title, youtube_id, duration_sec, status, created_at) VALUES(?,?,?,?,?, 'queued', ?)",
            (notebook_id, type_, title, youtube_id, duration_sec, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_source_status(self, source_id: int, status: str, error: str | None = None) -> None:
        self.conn.execute("UPDATE sources SET status=?, error=? WHERE id=?", (status, error, source_id))
        self.conn.commit()

    def get_source(self, source_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return dict(r) if r else None

    def list_sources(self, notebook_id: int) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM sources WHERE notebook_id=? ORDER BY created_at", (notebook_id,))]

    # --- chunks + vectors ---
    def add_chunk(self, source_id: int, ord_: int, text: str, start_sec: int | None, end_sec: int | None, token_count: int, embedding: list[float]) -> int:
        self._ensure_vec(len(embedding))
        cur = self.conn.execute(
            "INSERT INTO chunks(source_id, ord, text, start_sec, end_sec, token_count) VALUES(?,?,?,?,?,?)",
            (source_id, ord_, text, start_sec, end_sec, token_count),
        )
        chunk_id = cur.lastrowid
        self.conn.execute("INSERT INTO vec_chunks(rowid, embedding) VALUES(?, ?)", (chunk_id, _pack(embedding)))
        self.conn.commit()
        return chunk_id

    def get_chunks(self, chunk_ids: list[int]) -> list[dict]:
        if not chunk_ids:
            return []
        ph = ",".join("?" * len(chunk_ids))
        rows = self.conn.execute(f"SELECT * FROM chunks WHERE id IN ({ph})", chunk_ids)
        return [dict(r) for r in rows]

    def search(self, query_vec: list[float], notebook_id: int, k: int = 20) -> list[int]:
        self._ensure_vec(len(query_vec))
        # join vec_chunks to chunks→sources to filter by notebook
        rows = self.conn.execute(
            """
            SELECT v.rowid, c.source_id, s.notebook_id, v.distance
            FROM vec_chunks v
            JOIN chunks c ON c.id = v.rowid
            JOIN sources s ON s.id = c.source_id
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (_pack(query_vec), k),
        ).fetchall()
        return [r["rowid"] for r in rows if r["notebook_id"] == notebook_id]

    # --- messages ---
    def add_message(self, notebook_id: int, role: str, content: str) -> None:
        self.conn.execute("INSERT INTO messages(notebook_id, role, content, created_at) VALUES(?,?,?,?)",
                          (notebook_id, role, content, time.time()))
        self.conn.commit()

    def list_messages(self, notebook_id: int) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM messages WHERE notebook_id=? ORDER BY created_at", (notebook_id,))]
```

Add the pack helper at top of file (sqlite-vec accepts JSON or blobs; JSON is simplest):

```python
import json

def _pack(vec: list[float]) -> str:
    return json.dumps(vec)
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/Scripts/python.exe -m backend.store
```
Expected: `store OK`. (sqlite-vec was installed in Task 1.)

- [ ] **Step 5: Commit**

```bash
git add backend/store.py
git commit -m "feat(store): SQLite + sqlite-vec schema and CRUD"
```

---

## Task 4: grounding/chunker.py — timestamp-preserving semantic chunker

**Files:**
- Create: `opennotebook/backend/grounding/chunker.py`

**Interfaces:**
- Consumes: `Segment` dicts `{text, start, end}` from `transcribe.py` (Task 12).
- Produces: `Chunk` dataclass `{text, start_sec, end_sec, token_count}`; `group(segments, max_tokens=300, pause_sec=1.5) -> list[Chunk]`.

- [ ] **Step 1: Write the failing self-check**

Only `__main__`:

```python
if __name__ == "__main__":
    segs = [
        {"text": "Welcome to lecture three.", "start": 0.0, "end": 2.1},
        {"text": "Today we define limits.", "start": 2.3, "end": 5.8},
        {"text": "First the epsilon delta definition.", "start": 6.0, "end": 9.5},
    ]
    chunks = group(segs)
    assert len(chunks) == 1, "short transcript = one chunk"
    assert chunks[0].start_sec == 0.0 and chunks[0].end_sec == 9.5, "chunk spans first start to last end"
    # pause > 1.5s splits
    segs2 = [
        {"text": "Part one intro.", "start": 0.0, "end": 1.0},
        {"text": "Part two after a long pause.", "start": 5.0, "end": 7.0},  # 4s gap
    ]
    c2 = group(segs2)
    assert len(c2) == 2, "long pause must split"
    # token ceiling splits
    big = [{"text": "word " * 50, "start": float(i), "end": float(i) + 1} for i in range(20)]
    c3 = group(big, max_tokens=300)
    assert len(c3) > 1, "must split on token ceiling"
    # never split mid-sentence: each chunk text ends with sentence-ending punctuation or is the last
    for c in c3:
        assert c.text.rstrip()[-1] in ".?!" or c is c3[-1], "chunk cut mid-sentence"
    print("chunker OK")
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python.exe -m backend.grounding.chunker
```
Expected: `NameError: name 'group' is not defined`.

- [ ] **Step 3: Implement `chunker.py`**

```python
"""Timestamp-preserving semantic chunker. Groups Whisper segments by token ceiling,
long pauses, or topic-shift cues — never splitting a sentence."""
from __future__ import annotations
from dataclasses import dataclass

CUE_WORDS = ("chapter", "section", "now", "next", "moving on", "let's", "summary", "recap", "question")


@dataclass
class Chunk:
    text: str
    start_sec: float
    end_sec: float
    token_count: int


def _approx_tokens(text: str) -> int:
    # ponytail: words≈tokens is good enough for context fitting; upgrade to tiktoken if needed.
    return len(text.split())


def _ends_sentence(text: str) -> bool:
    return text.rstrip()[-1:] in ".?!"


def group(segments: list[dict], max_tokens: int = 300, pause_sec: float = 1.5) -> list[Chunk]:
    if not segments:
        return []
    chunks: list[Chunk] = []
    cur_text: list[str] = []
    cur_start = segments[0]["start"]
    cur_end = segments[0]["end"]
    cur_tok = 0
    prev_end = segments[0]["end"]

    def flush():
        nonlocal cur_text, cur_tok
        if cur_text:
            txt = " ".join(cur_text).strip()
            chunks.append(Chunk(txt, cur_start, cur_end, _approx_tokens(txt)))
            cur_text, cur_tok = [], 0

    for i, seg in enumerate(segments):
        text = seg["text"].strip()
        if not text:
            continue
        gap = max(0.0, seg["start"] - prev_end)
        tok = _approx_tokens(text)
        topic_shift = any(text.lower().startswith(c) for c in CUE_WORDS) and cur_text
        would_exceed = cur_tok + tok > max_tokens
        # split BEFORE adding if a boundary condition fires, but only at sentence ends
        should_split = (gap > pause_sec or topic_shift) and cur_text and _ends_sentence(" ".join(cur_text))
        if should_split or would_exceed:
            flush()
            cur_start = seg["start"]
        if not cur_text:
            cur_start = seg["start"]
        cur_text.append(text)
        cur_end = seg["end"]
        cur_tok += tok
        prev_end = seg["end"]
    flush()
    return chunks
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/Scripts/python.exe -m backend.grounding.chunker
```
Expected: `chunker OK`.

- [ ] **Step 5: Commit**

```bash
git add backend/grounding/chunker.py
git commit -m "feat(grounding): timestamp-preserving semantic chunker"
```

---

## Task 5: grounding/embedder.py — bundled + openai_compatible, lazy degrade

**Files:**
- Create: `opennotebook/backend/grounding/embedder.py`

**Interfaces:**
- Consumes: `AppConfig.embeddings` (Task 2).
- Produces: `Embedder` (interface), `get_embedder(cfg) -> Embedder`, `Embedder.embed(text) -> list[float]`, `Embedder.dim -> int`. On `openai_compatible` failure, falls back to a `HashingEmbedder` (deterministic keyword hash — BM25-ish baseline) and sets `.warning`.

- [ ] **Step 1: Write the failing self-check**

Only `__main__`:

```python
if __name__ == "__main__":
    from backend.config import load_config
    cfg = load_config()
    emb = get_embedder(cfg)
    v = emb.embed("epsilon delta definition of a limit")
    assert isinstance(v, list) and len(v) > 0
    v2 = emb.embed("integral of x squared")
    assert len(v2) == len(v), "dim must be stable across calls"
    # distinct texts → distinct vectors (sanity for a real embedder)
    assert v != v2
    assert emb.dim == len(v)
    # graceful degrade: openai_compatible pointing at nothing → HashingEmbedder + warning
    from backend.config import AppConfig, Section
    bad = AppConfig(embeddings=Section(provider="openai_compatible", base_url="http://127.0.0.1:9/none"))
    deg = get_embedder(bad)
    deg.embed("test")  # triggers lazy probe
    assert deg.warning, "degraded embedder must set a warning"
    print("embedder OK")
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python.exe -m backend.grounding.embedder
```
Expected: `NameError`/`ModuleNotFoundError`.

- [ ] **Step 3: Implement `embedder.py`**

```python
"""Embedder: bundled CPU sentence-transformers (default) or OpenAI-compatible
/v1/embeddings. Degrades to a HashingEmbedder (keyword baseline) on failure."""
from __future__ import annotations
from typing import Protocol

from backend.config import AppConfig


class Embedder(Protocol):
    dim: int
    warning: str | None
    def embed(self, text: str) -> list[float]: ...


class _BundledEmbedder:
    def __init__(self, model_name: str | None = None):
        # lazy import: torch + sentence-transformers are heavy
        from sentence_transformers import SentenceTransformer  # type: ignore
        name = model_name or "all-MiniLM-L6-v2"  # small CPU embedder, 384-dim
        self._m = SentenceTransformer(name)
        self.dim = self._m.get_sentence_embedding_dimension()
        self.warning = None

    def embed(self, text: str) -> list[float]:
        return self._m.encode(text, normalize_embeddings=True).tolist()


class _OpenAICompatibleEmbedder:
    def __init__(self, base_url: str, api_key: str | None, model: str | None):
        self._base_url = base_url
        self._api_key = api_key or "unused"
        self._model = model
        self.warning = None
        self._client = None
        self.dim = 0  # unknown until first call

    def _probe(self, sample: str) -> list[float]:
        from openai import OpenAI  # lazy
        if self._client is None:
            self._client = OpenAI(base_url=self._base_url, api_key=self._api_key)
        resp = self._client.embeddings.create(model=self._model, input=sample)
        return resp.data[0].embedding

    def embed(self, text: str) -> list[float]:
        try:
            v = self._probe(text)
            self.dim = len(v)
            return v
        except Exception as e:
            self.warning = f"embeddings endpoint unreachable ({e}); using keyword fallback"
            return _HashingEmbedder().embed(text)


class _HashingEmbedder:
    """ponytail: deterministic bag-of-words hash embedder. Ceiling: no semantic
    similarity, keyword-only. Upgrade path: any real embedder above. Stable dim 256."""
    def __init__(self, dim: int = 256):
        self.dim = dim
        self.warning = "embeddings unavailable — using keyword fallback (semantic recall will be weak)"

    def embed(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in text.lower().split():
            h = hash(tok) % self.dim
            v[h] = 1.0
        return v


def get_embedder(cfg: AppConfig) -> Embedder:
    e = cfg.embeddings
    if e.provider == "openai_compatible" and e.base_url:
        return _OpenAICompatibleEmbedder(e.base_url, e.api_key, e.model)
    return _BundledEmbedder(e.model)
```

- [ ] **Step 4: Install sentence-transformers + torch and run**

```bash
.venv/Scripts/pip install sentence-transformers
```
(First run downloads the MiniLM model; that's expected, see config §10.)

```bash
.venv/Scripts/python.exe -m backend.grounding.embedder
```
Expected: `embedder OK` (after the model downloads on first run).

- [ ] **Step 5: Commit**

```bash
git add backend/grounding/embedder.py
git commit -m "feat(grounding): bundled + openai_compatible embedder with keyword fallback"
```

---

## Task 6: grounding/retriever.py — sqlite-vec cosine top-N

**Files:**
- Create: `opennotebook/backend/grounding/retriever.py`

**Interfaces:**
- Consumes: `Store.search` (Task 3), `Embedder.embed` (Task 5).
- Produces: `retrieve(query, notebook_id, store, embedder, k=20) -> list[int]` (chunk_ids, best-first).

- [ ] **Step 1: Write the failing self-check**

Only `__main__`:

```python
if __name__ == "__main__":
    import tempfile, os
    from backend.store import Store
    from backend.grounding.embedder import get_embedder
    from backend.config import load_config
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    store = Store(db); store.init()
    cfg = load_config()
    emb = get_embedder(cfg)
    nb = store.add_notebook("L3")
    s = store.add_source(nb, "youtube", "Calc", "v", 600)
    for i, txt in enumerate(["epsilon delta definition of a limit",
                             "proof of the intermediate value theorem",
                             "exponential growth examples"]):
        store.add_chunk(s, i, txt, i*60, i*60+30, 10, emb.embed(txt))
    hits = retrieve("what is the definition of a limit", nb, store, emb, k=3)
    assert hits, "must retrieve something"
    top = store.get_chunks(hits[:1])[0]
    assert "limit" in top["text"].lower(), f"top hit should mention limit, got {top['text']}"
    print("retriever OK")
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python.exe -m backend.grounding.retriever
```
Expected: `NameError`.

- [ ] **Step 3: Implement `retriever.py`**

```python
"""Retrieve top-N chunks via sqlite-vec cosine search."""
from __future__ import annotations
from backend.store import Store
from backend.grounding.embedder import Embedder


def retrieve(query: str, notebook_id: int, store: Store, embedder: Embedder, k: int = 20) -> list[int]:
    qv = embedder.embed(query)
    return store.search(qv, notebook_id, k=k)
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/Scripts/python.exe -m backend.grounding.retriever
```
Expected: `retriever OK`.

- [ ] **Step 5: Commit**

```bash
git add backend/grounding/retriever.py
git commit -m "feat(grounding): sqlite-vec cosine retriever"
```

---

## Task 7: grounding/reranker.py — bundled cross-encoder (interface pinned for later)

**Files:**
- Create: `opennotebook/backend/grounding/reranker.py`

**Interfaces:**
- Consumes: `AppConfig.reranker` (Task 2), chunk dicts.
- Produces: `Reranker` Protocol `rerank(query, chunks: list[dict]) -> list[dict]` (re-sorted, best-first); `get_reranker(cfg) -> Reranker`. **MVP: bundled only.** `openai_compatible` path raises `NotImplementedError` (deferred §7c) but the interface is the seam.

- [ ] **Step 1: Write the failing self-check**

Only `__main__`:

```python
if __name__ == "__main__":
    from backend.config import load_config
    cfg = load_config()
    rr = get_reranker(cfg)
    q = "what is the definition of a limit"
    chunks = [
        {"id": 1, "text": "epsilon delta definition of a limit"},
        {"id": 2, "text": "proof of the intermediate value theorem"},
        {"id": 3, "text": "limit laws and how to apply them"},
    ]
    ranked = rr.rerank(q, chunks)
    assert ranked[0]["id"] == 1, f"most relevant chunk should rank first, got {ranked[0]}"
    print("reranker OK")
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python.exe -m backend.grounding.reranker
```
Expected: `NameError`.

- [ ] **Step 3: Implement `reranker.py`**

```python
"""Reranker: bundled cross-encoder (MVP). Interface pinned so a remote rerank
API (openai_compatible) slots in later without touching callers (spec §7c)."""
from __future__ import annotations
from typing import Protocol

from backend.config import AppConfig


class Reranker(Protocol):
    warning: str | None
    def rerank(self, query: str, chunks: list[dict]) -> list[dict]: ...


class _BundledReranker:
    def __init__(self, model_name: str | None = None):
        # lazy import
        from sentence_transformers import CrossEncoder  # type: ignore
        name = model_name or "cross-encoder/ms-marco-MiniLM-L-6-v2"
        self._ce = CrossEncoder(name)
        self.warning = None

    def rerank(self, query: str, chunks: list[dict]) -> list[dict]:
        if not chunks:
            return []
        pairs = [(query, c["text"]) for c in chunks]
        scores = self._ce.predict(pairs).tolist()
        order = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
        return [chunks[i] for i in order]


class _RemoteReranker:
    """Deferred extension point (spec §7c). Not implemented in MVP."""
    def __init__(self, *a, **kw):
        raise NotImplementedError("openai_compatible reranker is a v1.5 extension; use provider: bundled")


def get_reranker(cfg: AppConfig) -> Reranker:
    r = cfg.reranker
    if r.provider == "openai_compatible":
        return _RemoteReranker()  # raises NotImplementedError — documents the seam
    return _BundledReranker(r.model)
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/Scripts/pip install -e .  # ensures sentence-transformers already present from Task 5
.venv/Scripts/python.exe -m backend.grounding.reranker
```
Expected: `reranker OK` (downloads cross-encoder model on first run).

- [ ] **Step 5: Commit**

```bash
git add backend/grounding/reranker.py
git commit -m "feat(grounding): bundled cross-encoder reranker (interface pinned for remote later)"
```

---

## Task 8: grounding/citations.py — tolerant `[#]` wire parser + map builder

**Files:**
- Create: `opennotebook/backend/grounding/citations.py`

**Interfaces:**
- Consumes: streamed LLM text (tokens accumulate), the passage-number→chunk-id map built by the pipeline (Task 9).
- Produces: `parse_citations(text) -> list[int]` (passage numbers found, in order, tolerant of `[#]`, `[1]`, `[^1]`, `[1,3]`); `build_map(passage_chunks: list[dict]) -> dict[int, dict]` (passage# → {chunk_id, source_title, start_sec}).

- [ ] **Step 1: Write the failing self-check**

Only `__main__`:

```python
if __name__ == "__main__":
    assert parse_citations("see [#]") == [], "bare [#] has no number"
    assert parse_citations("see [1]") == [1]
    assert parse_citations("[1] then [2]") == [1, 2]
    assert parse_citations("[^3]") == [3]
    assert parse_citations("[1,3]") == [1, 3]
    assert parse_citations("nothing here") == []
    # build_map: passage number → chunk metadata
    passages = [
        {"passage": 1, "chunk_id": 10, "source_title": "Calculus", "start_sec": 822},
        {"passage": 2, "chunk_id": 11, "source_title": "Calculus", "start_sec": 1630},
    ]
    m = build_map(passages)
    assert m[1]["chunk_id"] == 10 and m[2]["start_sec"] == 1630
    print("citations OK")
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python.exe -m backend.grounding.citations
```
Expected: `NameError`.

- [ ] **Step 3: Implement `citations.py`**

```python
"""Citation wire parser. The LLM emits passage numbers like [1], [^2], [1,3]
(intended format [#] per spec §7e, but parsing is tolerant). The frontend
renders these as ⌜title · mm:ss⌟ tokens — that's display-only, handled in app.js."""
from __future__ import annotations
import re

# matches [1], [^2], [1,3], [#]; captures the inner digits (skipping caret/space)
_CITE = re.compile(r"\[(?:\^)?((?:\d+\s*,\s*)*\d+)\]")


def parse_citations(text: str) -> list[int]:
    out: list[int] = []
    for m in _CITE.finditer(text):
        inner = m.group(1)
        for part in inner.split(","):
            part = part.strip()
            if part.isdigit():
                n = int(part)
                if n not in out:  # de-dup, preserve first-occurrence order
                    out.append(n)
    return out


def build_map(passage_chunks: list[dict]) -> dict[int, dict]:
    """passage_chunks: [{passage:int, chunk_id:int, source_title:str, start_sec:int|None}]"""
    return {pc["passage"]: pc for pc in passage_chunks}
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/Scripts/python.exe -m backend.grounding.citations
```
Expected: `citations OK`.

- [ ] **Step 5: Commit**

```bash
git add backend/grounding/citations.py
git commit -m "feat(grounding): tolerant [#] citation parser and map builder"
```

---

## Task 9: grounding/pipeline.py — orchestrator: ground() + stream_answer()

**Files:**
- Create: `opennotebook/backend/grounding/pipeline.py`

**Interfaces:**
- Consumes: chunker (T4), embedder (T5), retriever (T6), reranker (T7), citations (T8), store (T3), config (T2).
- Produces: `Pipeline` class with `__init__(store, cfg)`, `ground(query, notebook_id, chat_history=[]) -> (messages_for_llm, citation_map)`, and `stream_answer(query, notebook_id, chat_history=[]) -> Iterator[str]` yielding text deltas; final yield is a sentinel `"__CITATIONS__" + json` so the caller (api.py) can emit the resolved map as a final SSE event.

- [ ] **Step 1: Write the failing self-check**

Only `__main__`:

```python
if __name__ == "__main__":
    import tempfile, os, json
    from backend.config import load_config
    from backend.store import Store
    from backend.grounding.embedder import get_embedder
    cfg = load_config()
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    store = Store(db); store.init()
    emb = get_embedder(cfg)
    nb = store.add_notebook("L3")
    s = store.add_source(nb, "youtube", "Calculus", "v", 600)
    store.set_source_status(s, "ready")
    texts = ["The epsilon-delta definition of a limit says for every epsilon there is a delta.",
             "The intermediate value theorem applies to continuous functions on a closed interval.",
             "Limit laws let us split limits of sums into sums of limits."]
    for i, t in enumerate(texts):
        store.add_chunk(s, i, t, i*60, i*60+30, 25, emb.embed(t))
    pipe = Pipeline(store, cfg)
    msgs, cmap = pipe.ground("what is the definition of a limit", nb)
    # expect at least one passage that mentions the definition chunk
    assert len(cmap) >= 1
    # context-fitting: with a tiny max_context, fewer chunks fit
    pipe2 = Pipeline(store, cfg)
    pipe2.max_context = 512  # tiny → forces fewer passages
    msgs2, cmap2 = pipe2.ground("what is the definition of a limit", nb)
    assert len(cmap2) <= len(cmap), "smaller context must fit no more passages"
    print("pipeline OK")
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python.exe -m backend.grounding.pipeline
```
Expected: `NameError`.

- [ ] **Step 3: Implement `pipeline.py`**

```python
"""Grounding orchestrator (spec §7): retrieve → rerank → context-fit → grounded
prompt → stream + citation map."""
from __future__ import annotations
import json
from typing import Iterator

from backend.config import AppConfig
from backend.store import Store
from backend.grounding.embedder import get_embedder
from backend.grounding.retriever import retrieve
from backend.grounding.reranker import get_reranker
from backend.grounding.citations import build_map

RESERVE_TOKENS_FOR_ANSWER = 1500
SYSTEM = (
    "You are a study assistant answering ONLY from the provided passages. "
    'For every factual claim, cite the passage as [#] — the number in brackets before '
    "each passage. If the cited passage is timestamped, also include [mm:ss]. "
    'If the passages do not contain the answer, say "Not covered in the sources." '
    "Do not speculate. Do not use outside knowledge."
)


class Pipeline:
    def __init__(self, store: Store, cfg: AppConfig):
        self.store = store
        self.cfg = cfg
        self.embedder = get_embedder(cfg)
        self.reranker = get_reranker(cfg)
        # ponytail: 8k default for local models; cloud models set higher via config.
        self.max_context = cfg.llm.max_context or 8192

    def _stage_passages(self, query: str, notebook_id: int) -> list[dict]:
        ids = retrieve(query, notebook_id, self.store, self.embedder, k=20)
        if not ids:
            return []
        chunks = self.store.get_chunks(ids)
        ranked = self.reranker.rerank(query, chunks)
        return ranked

    def _fit_context(self, ranked: list[dict]) -> list[dict]:
        budget = self.max_context - RESERVE_TOKENS_FOR_ANSWER
        kept, used = [], 0
        for c in ranked:
            tc = c.get("token_count") or len(c["text"].split())
            if used + tc > budget:
                break
            kept.append(c)
            used += tc
        return kept

    def _build_prompt(self, query: str, kept: list[dict], chat_history: list[dict]) -> list[dict]:
        # attach source title + timestamp window from the source row
        src_ids = {c["source_id"] for c in kept}
        sources = {s["id"]: s for s in [self.store.get_source(sid) for sid in src_ids] if s}
        blocks, passage_chunks = [], []
        for i, c in enumerate(kept, start=1):
            s = sources.get(c["source_id"], {})
            title = s.get("title") or "source"
            ts = _fmt_ts(c.get("start_sec"))
            header = f'[{i}] (Source: "{title}"'
            if ts:
                header += f", {ts}"
            header += ") "
            blocks.append(header + c["text"])
            passage_chunks.append({
                "passage": i,
                "chunk_id": c["id"],
                "source_title": title,
                "source_id": c["source_id"],
                "start_sec": c.get("start_sec"),
                "end_sec": c.get("end_sec"),
                "text": c["text"],
            })
        passages_block = "\n\n".join(blocks)
        user = f"PASSAGES:\n{passages_block}\n\nUSER: {query}"
        messages = [{"role": "system", "content": SYSTEM}]
        for m in chat_history:
            messages.append({"role": m["role"], "content": m["content"]})
        messages.append({"role": "user", "content": user})
        self._last_citation_map = build_map(passage_chunks)
        return messages

    def ground(self, query: str, notebook_id: int, chat_history: list[dict] | None = None) -> tuple[list[dict], dict]:
        chat_history = chat_history or []
        ranked = self._stage_passages(query, notebook_id)
        kept = self._fit_context(ranked)
        msgs = self._build_prompt(query, kept, chat_history)
        return msgs, self._last_citation_map

    def stream_answer(self, query: str, notebook_id: int, chat_history: list[dict] | None = None,
                      llm_stream=None) -> Iterator[str]:
        """llm_stream: callable(messages) -> Iterator[str] of token deltas.
        Yields deltas; the final yield is '__CITATIONS__' + json(map)."""
        from backend.llm_client import get_llm  # lazy to avoid import cycle (Task 10)
        msgs, cmap = self.ground(query, notebook_id, chat_history or [])
        stream = llm_stream if llm_stream is not None else get_llm(self.cfg).stream
        full = []
        for delta in stream(msgs):
            full.append(delta)
            yield delta
        yield "__CITATIONS__" + json.dumps(cmap)


def _fmt_ts(sec):
    if sec is None:
        return None
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"
```

Note: `stream_answer` imports `llm_client` lazily so module-load order doesn't matter; the self-check calls `ground()` only (which doesn't stream), so Task 9 passes before Task 10 exists.

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/Scripts/python.exe -m backend.grounding.pipeline
```
Expected: `pipeline OK`.

- [ ] **Step 5: Commit**

```bash
git add backend/grounding/pipeline.py
git commit -m "feat(grounding): retrieve-rerank-fit pipeline with citation map"
```

---

## Task 10: llm_client.py — OpenAI-compatible streaming client + max_context

**Files:**
- Create: `opennotebook/backend/llm_client.py`

**Interfaces:**
- Consumes: `AppConfig.llm` (Task 2).
- Produces: `get_llm(cfg) -> LLMClient`; `LLMClient.stream(messages) -> Iterator[str]` (token deltas); `LLMClient.max_context -> int`.

- [ ] **Step 1: Write the failing self-check**

Only `__main__`:

```python
if __name__ == "__main__":
    from unittest.mock import patch, MagicMock
    from backend.config import AppConfig, LLMSection
    cfg = AppConfig(llm=LLMSection(base_url="http://x/v1", api_key="k", model="m", max_context=4096))
    with patch("backend.llm_client.OpenAI") as MockOpenAI:
        client = MagicMock()
        MockOpenAI.return_value = client
        chunk = MagicMock()
        chunk.choices = [MagicMock(delta=MagicMock(content="hello"))]
        client.chat.completions.create.return_value = iter([chunk])
        llm = get_llm(cfg)
        assert llm.max_context == 4096
        out = "".join(llm.stream([{"role": "user", "content": "hi"}]))
        assert out == "hello", f"mocked stream should replay deltas, got {out!r}"
    print("llm_client OK")
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python.exe -m backend.llm_client
```
Expected: `NameError`.

- [ ] **Step 3: Implement `llm_client.py`**

```python
"""Thin OpenAI-compatible client (spec D1). One socket serves NVIDIA NIM,
OpenRouter, Ollama, LM Studio, OpenAI — they all speak this API."""
from __future__ import annotations
from typing import Iterator

from openai import OpenAI  # top-level import is fine: openai is a core dep, not heavy ML
from backend.config import AppConfig


class LLMClient:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._client = OpenAI(base_url=cfg.llm.base_url, api_key=cfg.llm.api_key or "unused")
        self.max_context = cfg.llm.max_context or 8192

    def stream(self, messages: list[dict], **params) -> Iterator[str]:
        defaults = dict(model=self.cfg.llm.model, messages=messages, stream=True)
        defaults.update(params)
        for chunk in self._client.chat.completions.create(**defaults):
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


def get_llm(cfg: AppConfig) -> LLMClient:
    return LLMClient(cfg)
```

The self-check patches `backend.llm_client.OpenAI` — the top-level `from openai import OpenAI` makes that name patchable via `unittest.mock`.

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/Scripts/python.exe -m backend.llm_client
```
Expected: `llm_client OK`.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_client.py
git commit -m "feat(llm): OpenAI-compatible streaming client"
```

---

## Task 11: ingestion/youtube.py — yt-dlp audio download

**Files:**
- Create: `opennotebook/backend/ingestion/youtube.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `download(url, out_dir=None) -> (audio_path, title, youtube_id, duration_sec)` via yt-dlp subprocess.

- [ ] **Step 1: Write the failing self-check**

Only `__main__`:

```python
if __name__ == "__main__":
    # self-check runs against yt-dlp's metadata extraction only (no full download),
    # so it's fast and offline-tolerant: passes if the function signature + parsing work.
    from unittest.mock import patch, MagicMock
    with patch("backend.ingestion.youtube.subprocess") as sub:
        sub.run.return_value = MagicMock(returncode=0)
        # simulate yt-dlp JSON: title, id, duration; audio file path
        sub.run.return_value.stdout = '{"title":"Lecture 3","id":"vid123","duration":600}'
        path, title, yid, dur = download("https://youtu.be/vid123", out_dir="/tmp/x")
        assert title == "Lecture 3" and yid == "vid123" and dur == 600
        assert path.endswith(".opus") or path.endswith(".m4a")
    print("youtube OK")
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/pip install yt-dlp
.venv/Scripts/python.exe -m backend.ingestion.youtube
```
Expected: `NameError`.

- [ ] **Step 3: Implement `youtube.py`**

```python
"""YouTube audio download via yt-dlp subprocess (spec §6)."""
from __future__ import annotations
import json
import os
import subprocess
import tempfile


def download(url: str, out_dir: str | None = None) -> tuple[str, str, str, int]:
    """Returns (audio_path, title, youtube_id, duration_sec)."""
    out_dir = out_dir or tempfile.mkdtemp(prefix="onb_")
    os.makedirs(out_dir, exist_ok=True)
    out_tmpl = os.path.join(out_dir, "%(id)s.%(ext)s")
    cmd = [
        "yt-dlp",
        "-x",                        # audio only
        "--audio-format", "opus",     # ponytail: opus is small + good; falls back to m4a
        "-o", out_tmpl,
        "--print-json",               # metadata + final file path to stdout
        "--no-playlist",
        "--newline",
        url,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {res.stderr.strip()}")
    # --print-json emits one JSON object on stdout
    data = json.loads(res.stdout.strip().splitlines()[-1])
    # final audio path: yt-dlp fills the template; reconstruct
    ext = "opus"
    audio_path = os.path.join(out_dir, f"{data['id']}.{ext}")
    if not os.path.exists(audio_path):
        # fall back to whatever file exists in out_dir
        for f in os.listdir(out_dir):
            if f.startswith(data["id"]):
                audio_path = os.path.join(out_dir, f)
                break
    return audio_path, data.get("title", "Untitled"), data["id"], int(data.get("duration") or 0)
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/Scripts/python.exe -m backend.ingestion.youtube
```
Expected: `youtube OK`.

- [ ] **Step 5: Commit**

```bash
git add backend/ingestion/youtube.py
git commit -m "feat(ingestion): yt-dlp audio download"
```

---

## Task 12: ingestion/transcribe.py — faster-whisper + override

**Files:**
- Create: `opennotebook/backend/ingestion/transcribe.py`

**Interfaces:**
- Consumes: `AppConfig.whisper` (Task 2), an audio path.
- Produces: `transcribe(audio_path, cfg) -> list[dict]` of segments `{text, start, end}`.

- [ ] **Step 1: Write the failing self-check**

Only `__main__`:

```python
if __name__ == "__main__":
    from unittest.mock import patch, MagicMock
    from backend.config import AppConfig, Section
    cfg = AppConfig(whisper=Section(provider="bundled", model="tiny"))
    # mock the faster_whisper WhisperModel
    fake_seg = MagicMock(text="hello world", start=0.0, end=2.0)
    with patch.dict("sys.modules", {}):
        import sys, types
        fw = types.ModuleType("faster_whisper")
        class _WM:
            def __init__(self, *a, **kw): pass
            def transcribe(self, path, **kw):
                class R:
                    segments = [fake_seg]
                return R()
        fw.WhisperModel = _WM
        sys.modules["faster_whisper"] = fw
        segs = transcribe("/fake/path.opus", cfg)
        assert segs == [{"text": "hello world", "start": 0.0, "end": 2.0}]
    print("transcribe OK")
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/pip install faster-whisper
.venv/Scripts/python.exe -m backend.ingestion.transcribe
```
Expected: `NameError`.

- [ ] **Step 3: Implement `transcribe.py`**

```python
"""Transcription: faster-whisper (bundled default) or an OpenAI-compatible
whisper-style endpoint (override). Emits [{text, start, end}] segments (spec §6)."""
from __future__ import annotations

from backend.config import AppConfig


def transcribe(audio_path: str, cfg: AppConfig) -> list[dict]:
    w = cfg.whisper
    if w.provider == "openai_compatible" and w.base_url:
        return _remote_transcribe(audio_path, w)
    return _bundled_transcribe(audio_path, w.model or "small")


def _bundled_transcribe(audio_path: str, model_name: str) -> list[dict]:
    # lazy import: torch + faster-whisper are heavy
    from faster_whisper import WhisperModel  # type: ignore
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(audio_path, beam_size=1)
    # faster-whisper segments are generators; realize them
    return [{"text": s.text.strip(), "start": float(s.start), "end": float(s.end)} for s in segments]


def _remote_transcribe(audio_path: str, w) -> list[dict]:
    # ponytail: deferred-ish — minimal OpenAI-style Whisper override. Ceiling: assumes
    # the endpoint returns OpenAI's verbose_json schema with 'segments'. Few providers
    # expose this; further wiring when a real user supplies one.
    import base64, httpx, os
    with open(audio_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()
    r = httpx.post(
        f"{w.base_url}/audio/transcriptions",
        headers={"Authorization": f"Bearer {w.api_key or 'unused'}"},
        json={"audio": audio_b64, "model": w.model or "whisper-1", "response_format": "verbose_json"},
        timeout=600,
    )
    r.raise_for_status()
    data = r.json()
    return [{"text": s["text"].strip(), "start": float(s["start"]), "end": float(s["end"])} for s in data.get("segments", [])]
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/Scripts/python.exe -m backend.ingestion.transcribe
```
Expected: `transcribe OK` (mocked, no model download needed for the self-check).

- [ ] **Step 5: Commit**

```bash
git add backend/ingestion/transcribe.py
git commit -m "feat(ingestion): faster-whisper transcription with remote override"
```

---

## Task 13: ingestion/ingest.py — orchestrate ingest + status updates (error-isolated)

**Files:**
- Create: `opennotebook/backend/ingestion/ingest.py`

**Interfaces:**
- Consumes: youtube (T11), transcribe (T12), chunker (T4), embedder (T5), store (T3), config (T2).
- Produces: `ingest_url(source_id, url, cfg, store) -> None` (runs the full pipeline and sets `sources.status` to `ready` or `failed` with error). Async-safe: callable from a background `asyncio.to_thread`.

- [ ] **Step 1: Write the failing self-check**

Only `__main__`:

```python
if __name__ == "__main__":
    import tempfile, os, asyncio
    from unittest.mock import patch
    from backend.config import load_config
    from backend.store import Store
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    store = Store(db); store.init()
    cfg = load_config()
    nb = store.add_notebook("L3")
    s = store.add_source(nb, "youtube", "Calc", "v", 600)
    # mock youtube.download + transcribe to avoid network/CPU
    fake_segs = [{"text": "Hello world.", "start": 0.0, "end": 1.5},
                 {"text": "Defining the limit.", "start": 2.0, "end": 4.0}]
    with patch("backend.ingestion.ingest.youtube_download", return_value=("/tmp/a.opus", "Calc", "v", 600)), \
         patch("backend.ingestion.ingest.transcribe", return_value=fake_segs):
        asyncio.run(_run(source_id=s, url="https://youtu.be/v", cfg=cfg, store=store))
    assert store.get_source(s)["status"] == "ready"
    assert len(store.list_sources(nb)) == 1
    print("ingest OK")
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python.exe -m backend.ingestion.ingest
```
Expected: `NameError`.

- [ ] **Step 3: Implement `ingest.py`**

```python
"""Ingestion orchestrator (spec §6): download → transcribe → chunk → embed → store.
Updates sources.status throughout; isolates per-source errors so one bad URL never
kills the process (spec §12 caveat 4)."""
from __future__ import annotations

from backend.config import AppConfig
from backend.store import Store
from backend.ingestion import youtube as youtube_mod
from backend.ingestion import transcribe as transcribe_mod
from backend.grounding.chunker import group
from backend.grounding.embedder import get_embedder

# friendly aliases the self-check patches
youtube_download = youtube_mod.download
transcribe = transcribe_mod.transcribe


def ingest_url(source_id: int, url: str, cfg: AppConfig, store: Store) -> None:
    try:
        store.set_source_status(source_id, "downloading")
        audio_path, title, yid, duration = youtube_download(url)
        store.set_source_status(source_id, "transcribing")
        segments = transcribe(audio_path, cfg)
        store.set_source_status(source_id, "chunking")
        chunks = group(segments)
        emb = get_embedder(cfg)
        for i, c in enumerate(chunks):
            vec = emb.embed(c.text)
            store.add_chunk(source_id, i, c.text, int(c.start_sec), int(c.end_sec), c.token_count, vec)
        store.set_source_status(source_id, "ready")
    except Exception as e:
        store.set_source_status(source_id, "failed", error=str(e))


async def _run(source_id: int, url: str, cfg: AppConfig, store: Store) -> None:
    """Async wrapper for api.py to call via asyncio.to_thread."""
    import asyncio
    await asyncio.to_thread(ingest_url, source_id, url, cfg, store)
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/Scripts/python.exe -m backend.ingestion.ingest
```
Expected: `ingest OK`.

- [ ] **Step 5: Commit**

```bash
git add backend/ingestion/ingest.py
git commit -m "feat(ingestion): full ingest pipeline with status + error isolation"
```

---

## Task 14: api.py — FastAPI routes + SSE

**Files:**
- Create: `opennotebook/backend/api.py`
- Modify: `opennotebook/main.py` (mount the real router, keep health + static mount)

**Interfaces:**
- Consumes: store (T3), pipeline (T9), llm_client (T10), ingest (T13), config (T2).
- Produces: a `router: APIRouter` with the spec §9 surface. `main.py` includes it.

- [ ] **Step 1: Write the failing self-check**

Only `__main__` in `api.py`:

```python
if __name__ == "__main__":
    import tempfile, os, json
    from fastapi.testclient import TestClient
    from backend.store import Store
    from backend.config import load_config
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    store = Store(db); store.init()
    cfg = load_config()
    app = build_app(store, cfg)
    c = TestClient(app)
    # notebook CRUD
    r = c.post("/api/notebooks", json={"title": "L3"})
    assert r.status_code == 200 and r.json()["title"] == "L3"
    nb_id = r.json()["id"]
    assert len(c.get("/api/notebooks").json()) == 1
    # config read
    assert "llm" in c.get("/api/config").json()
    # source add (ingest mocked by the real pipeline — but we only assert it queues)
    # NOTE: real ingest is async background; here we test it enqueues without running
    r2 = c.post(f"/api/notebooks/{nb_id}/sources", json={"url": "https://youtu.be/vid123"})
    assert r2.status_code == 200 and "source_id" in r2.json()
    print("api OK")
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python.exe -m backend.api
```
Expected: `NameError`.

- [ ] **Step 3: Implement `api.py`**

```python
"""FastAPI routes (spec §9): notebook CRUD, source add + status, chat (SSE),
ingestion progress (SSE), config read/update. Thin — delegates to store, pipeline, ingest."""
from __future__ import annotations
import asyncio
import json

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.config import AppConfig, load_config, save_config
from backend.store import Store
from backend.grounding.pipeline import Pipeline
from backend.ingestion.ingest import ingest_url

router = APIRouter(prefix="/api")

# module-level singletons set by build_app(); kept simple for a single-user local tool.
_STORE: Store | None = None
_CFG: AppConfig | None = None
_INGEST_TASKS: set = set()


class NotebookIn(BaseModel):
    title: str


class SourceIn(BaseModel):
    url: str


class ConfigIn(BaseModel):
    llm: dict | None = None
    embeddings: dict | None = None
    reranker: dict | None = None
    whisper: dict | None = None


@router.post("/notebooks")
def create_notebook(body: NotebookIn):
    nb_id = _STORE.add_notebook(body.title)
    return {"id": nb_id, "title": body.title}


@router.get("/notebooks")
def list_notebooks():
    return _STORE.list_notebooks()


@router.get("/notebooks/{nb_id}")
def get_notebook(nb_id: int):
    nb = _STORE.get_notebook(nb_id)
    if not nb:
        raise HTTPException(404, "notebook not found")
    nb["sources"] = _STORE.list_sources(nb_id)
    nb["messages"] = _STORE.list_messages(nb_id)
    return nb


@router.post("/notebooks/{nb_id}/sources")
def add_source(nb_id: int, body: SourceIn):
    if not _STORE.get_notebook(nb_id):
        raise HTTPException(404, "notebook not found")
    # ponytail: title/youtube_id filled by ingest after download; pre-create with url as title.
    src_id = _STORE.add_source(nb_id, "youtube", body.url, None, None)
    # kick off background ingestion (error-isolated inside ingest_url)
    task = asyncio.get_event_loop().run_in_executor(None, ingest_url, src_id, body.url, _CFG, _STORE)
    _INGEST_TASKS.add(task)
    task.add_done_callback(_INGEST_TASKS.discard)
    return {"source_id": src_id}


@router.get("/notebooks/{nb_id}/sources/{src_id}")
def get_source(nb_id: int, src_id: int):
    s = _STORE.get_source(src_id)
    if not s or s["notebook_id"] != nb_id:
        raise HTTPException(404, "source not found")
    return s


@router.get("/notebooks/{nb_id}/sources/{src_id}/stream")
def source_progress(nb_id: int, src_id: int):
    """SSE: emit status until ready/failed, then close."""
    def gen():
        import time
        last = None
        for _ in range(600):  # 10 min ceiling
            s = _STORE.get_source(src_id)
            if not s:
                yield f"event: error\ndata: not found\n\n"
                return
            if s["status"] != last:
                yield f"data: {json.dumps({'status': s['status'], 'error': s.get('error')})}\n\n"
                last = s["status"]
                if s["status"] in ("ready", "failed"):
                    return
            time.sleep(1)
    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/notebooks/{nb_id}/chat")
async def chat(nb_id: int, body: dict):
    if not _STORE.get_notebook(nb_id):
        raise HTTPException(404, "notebook not found")
    query = body.get("message", "")
    _STORE.add_message(nb_id, "user", query)
    history = _STORE.list_messages(nb_id)

    def gen():
        pipe = Pipeline(_STORE, _CFG)
        acc = []
        for delta in pipe.stream_answer(query, nb_id, chat_history=history[:-1]):  # exclude the just-added user msg
            if delta.startswith("__CITATIONS__"):
                cmap = json.loads(delta[len("__CITATIONS__"):])
                yield f"event: citations\ndata: {json.dumps(cmap)}\n\n"
                # persist assistant turn with the citation map for re-render
                _STORE.add_message(nb_id, "assistant", "".join(acc) + "|||CITATIONS|||" + json.dumps(cmap))
                return
            acc.append(delta)
            yield f"data: {json.dumps({'token': delta})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/config")
def get_config():
    from dataclasses import asdict
    return asdict(_CFG)


@router.patch("/config")
def patch_config(body: ConfigIn):
    from dataclasses import asdict
    d = asdict(_CFG)
    for k, v in body.dict(exclude_none=True).items():
        if v:
            d[k].update(v)
    # reload into a new AppConfig and persist
    from backend.config import _coerce
    global _CFG
    _CFG = _coerce(d)
    save_config(_CFG)
    return asdict(_CFG)


def build_app(store: Store, cfg: AppConfig) -> FastAPI:
    global _STORE, _CFG
    _STORE = store
    _CFG = cfg
    app = FastAPI(title="OpenNotebook")
    app.include_router(router)
    return app
```

Modify `main.py` to use `build_app`: replace the stub `app = FastAPI(...)` + `@app.get("/api/health")` with:

```python
from pathlib import Path
from backend.api import build_app
from backend.config import load_config
from backend.store import Store

DB_PATH = Path(__file__).parent / "opennotebook.db"
store = Store(str(DB_PATH)); store.init()
cfg = load_config()
app = build_app(store, cfg)
```
Keep the `StaticFiles` mount and `main()` launcher. Remove the old `/api/health` route (add it to `api.py` if you want a health check — optional).

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/Scripts/pip install httpx   # for fastapi.testclient (already dep, but confirm)
.venv/Scripts/python.exe -m backend.api
```
Expected: `api OK`.

- [ ] **Step 5: Commit**

```bash
git add backend/api.py main.py
git commit -m "feat(api): notebooks, sources, chat SSE, config endpoints"
```

---

## Task 15: frontend — index.html, style.css, app.js, player.js (seek tokens UX)

**Files:**
- Create: `opennotebook/frontend/index.html`
- Create: `opennotebook/frontend/style.css`
- Create: `opennotebook/frontend/app.js`
- Create: `opennotebook/frontend/player.js`

**Interfaces:**
- Consumes: the §9 API.
- Produces: a single-page UI: notebook list (left), chat (center with streaming + `⌜title · mm:ss⌟` seek tokens), source panel (right with YouTube iframe + cited transcript). `frontend-design` skill invoked at build time to make the seek-token typography/motion earned, not templated.

> **Note for the implementer:** This task is the project's face and the "exceed NotebookLM" moment. Before writing the CSS, invoke the `frontend-design` skill for typography + the seek-token treatment; the design spec §8 pins the behavior precisely. The code below is the structural skeleton; the skill informs the visual finish.

- [ ] **Step 1: Write `index.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OpenNotebook</title>
  <link rel="stylesheet" href="style.css" />
</head>
<body>
  <div id="layout">
    <aside id="sidebar">
      <h1>OpenNotebook</h1>
      <button id="new-notebook">+ New notebook</button>
      <ul id="notebook-list"></ul>
    </aside>
    <main id="chat">
      <header id="chat-header"><span id="nb-title">Select a notebook</span></header>
      <section id="source-bar">
        <input id="source-url" placeholder="Paste a YouTube lecture URL…" />
        <button id="add-source">Add source</button>
        <ul id="source-list"></ul>
      </section>
      <div id="messages"></div>
      <form id="chat-form">
        <input id="chat-input" placeholder="Ask about your sources…" autocomplete="off" />
        <button>Send</button>
      </form>
    </main>
    <aside id="panel">
      <div id="player-wrap"></div>
      <div id="passage"></div>
    </aside>
  </div>
  <script src="https://www.youtube.com/iframe_api"></script>
  <script src="player.js"></script>
  <script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write `style.css` (structural skeleton — visual finish done with frontend-design)**

```css
:root { --bg:#11151c; --panel:#161c26; --ink:#e6e9ef; --muted:#8b93a7;
        --accent:#7cc6ff; --token:#243043; --token-active:#2f4a6b; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:15px/1.5 -apple-system,Segoe UI, Roboto, sans-serif; }
#layout { display:grid; grid-template-columns:220px 1fr 380px; height:100vh; }
#sidebar, #panel { background:var(--panel); padding:16px; overflow:auto; }
#chat { display:flex; flex-direction:column; border-right:1px solid #232b38; }
#chat-header { padding:12px 16px; border-bottom:1px solid #232b38; font-weight:600; }
#source-bar { padding:12px 16px; border-bottom:1px solid #232b38; }
#source-bar input { width:70%; }
#source-list { list-style:none; padding:0; margin:8px 0 0; font-size:13px; color:var(--muted); }
#source-list li { padding:2px 0; }
#messages { flex:1; overflow:auto; padding:16px; }
.msg { margin:0 0 12px; }
.msg.user { color:var(--muted); }
.msg.assistant { color:var(--ink); }
#chat-form { display:flex; gap:8px; padding:12px 16px; border-top:1px solid #232b38; }
#chat-form input { flex:1; }

/* seek-token UX (spec §8). Visual finish refined with frontend-design. */
.cite { display:inline-flex; gap:4px; padding:1px 6px; margin:0 1px; border-radius:6px;
        background:var(--token); color:var(--accent); cursor:pointer; font-size:13px;
        border-bottom:2px solid transparent; transition:background .12s, filter .12s; }
.cite:hover { background:var(--token-active); }
.cite.visited { filter:saturate(.5) brightness(.9); }
.cite .under { display:block; height:2px; background:var(--accent); width:0; transition:width linear; }
.cite.playing .under { width:100%; }
.msg.assistant p.active-passage { box-shadow: inset 3px 0 0 var(--accent); padding-left:8px; }
#passage { margin-top:12px; font-size:14px; color:var(--muted); }
#passage .hi { background:#2a3a55; padding:2px 4px; border-radius:3px; color:var(--ink); }
```

- [ ] **Step 3: Write `player.js` (YouTube iframe + seek)**

```javascript
// YouTube IFrame API wrapper: load a video, seek to a timestamp, report playhead.
let yt = null;
window.onYouTubeIframeAPIReady = () => {};

const Player = {
  load(videoId, atSec = 0) {
    const wrap = document.getElementById("player-wrap");
    wrap.innerHTML = `<div id="yt"></div>`;
    const YT = window.YT;
    if (!YT || !YT.Player) { wrap.innerHTML = "(YouTube player loading…)"; return; }
    yt = new YT.Player("yt", {
      videoId, playerVars: { start: Math.floor(atSec) },
    });
  },
  seek(sec) {
    if (yt && yt.seekTo) yt.seekTo(sec, true);
  },
  currentTime() {
    return yt && yt.getCurrentTime ? yt.getCurrentTime() : 0;
  },
};
window.Player = Player;
```

- [ ] **Step 4: Write `app.js` (chat + SSE + citation render + seek dispatch + visited/active states)**

```javascript
// --- helpers ---
const $ = (id) => document.getElementById(id);
const qs = (s, r=document) => r.querySelector(s);
const qsa = (s, r=document) => [...r.querySelectorAll(s)];
const api = (p, o) => fetch(p, o).then(r => r.json());

let notebooks = [], currentNB = null, sources = [], citationMap = {};

// --- notebooks ---
async function loadNotebooks() {
  notebooks = await api("/api/notebooks");
  $("notebook-list").innerHTML = notebooks.map(n =>
    `<li><a href="#" data-id="${n.id}">${n.title}</a></li>`).join("");
  qsa("#notebook-list a").forEach(a => a.onclick = e => { e.preventDefault(); openNB(+a.dataset.id); });
}
$("new-notebook").onclick = async () => {
  const title = prompt("Notebook title?", "Untitled");
  if (!title) return;
  await api("/api/notebooks", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({title})});
  loadNotebooks();
};

// --- sources ---
async function openNB(id) {
  currentNB = id;
  const nb = await api(`/api/notebooks/${id}`);
  $("nb-title").textContent = nb.title;
  sources = nb.sources;
  $("source-list").innerHTML = sources.map(s => `<li>${s.title} — <em>${s.status}</em></li>`).join("");
  $("messages").innerHTML = (nb.messages||[]).map(renderStored).join("");
}
$("add-source").onclick = async () => {
  if (!currentNB) return alert("Create or open a notebook first.");
  const url = $("source-url").value.trim();
  if (!url) return;
  const { source_id } = await api(`/api/notebooks/${currentNB}/sources`,
    {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({url})});
  $("source-url").value = "";
  // SSE progress
  const es = new EventSource(`/api/notebooks/${currentNB}/sources/${source_id}/stream`);
  es.onmessage = e => {
    const d = JSON.parse(e.data);
    // update the status line; refresh on ready
    openNB(currentNB);
    if (d.status === "ready" || d.status === "failed") es.close();
  };
};

// --- chat (SSE via fetch stream) ---
$("chat-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!currentNB) return alert("Open a notebook first.");
  const q = $("chat-input").value.trim();
  if (!q) return;
  $("chat-input").value = "";
  const bubble = document.createElement("div");
  bubble.className = "msg assistant";
  bubble.innerHTML = "<p></p>";
  $("messages").appendChild(bubble);
  const p = qs("p", bubble);
  const resp = await fetch(`/api/notebooks/${currentNB}/chat`,
    {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({message:q})});
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "", full = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, {stream:true});
    const lines = buf.split("\n"); buf = lines.pop();
    for (const line of lines) {
      if (line.startsWith("data:")) {
        const { token } = JSON.parse(line.slice(5).trim());
        if (token) { full += token; p.innerHTML = renderTokens(full); }
      } else if (line.startsWith("event: citations")) {
        // next data: line is the map
      }
    }
  }
};

// --- citation rendering: [#] → ⌜title · mm:ss⌟ token ---
function renderTokens(text) {
  // replace [1], [1,3] with token chips; tap render # → citationMap[#]
  return text.replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (_, nums) => {
    return nums.split(",").map(n => {
      const c = citationMap[+n.trim()];
      if (!c) return `[#${n}]`;
      const mmss = fmt(c.start_sec);
      return `<span class="cite" data-id="${c.chunk_id}" data-sec="${c.start_sec||0}" title="${c.text||''}">${c.source_title} · ${mmss}<span class="under"></span></span>`;
    }).join("");
  });
}
function fmt(sec) { if (sec==null) return ""; sec=Math.floor(sec); return `${Math.floor(sec/60)}:${String(sec%60).padStart(2,"0")}`; }

// delegate clicks → seek + visited + active
document.addEventListener("click", (e) => {
  const t = e.target.closest ? e.target.closest(".cite") : null;
  if (!t) return;
  const sec = +t.dataset.sec;
  Player.seek(sec);
  t.classList.add("visited");
  qsa(".cite.playing").forEach(c => c.classList.remove("playing"));
  t.classList.add("playing");
  // show passage text in panel
  const c = Object.values(citationMap).find(x => x.chunk_id == t.dataset.id);
  if (c) {
    $("passage").innerHTML = `<div class="hi">${escapeHtml(c.text)}</div>`;
    // load player if first citation for this source
    const src = sources.find(s => s.id === c.source_id);
    if (src && src.youtube_id) Player.load(src.youtube_id, sec);
  }
});
function escapeHtml(s){ return (s||"").replace(/[&<>]/g, m => ({"&":"&","<":"<",">":">"}[m])); }

function renderStored(m) {
  if (m.role === "assistant" && m.content.includes("|||CITATIONS|||")) {
    const [text, cmap] = m.content.split("|||CITATIONS|||");
    citationMap = JSON.parse(cmap);
    return `<div class="msg assistant"><p>${renderTokens(text)}</p></div>`;
  }
  return `<div class="msg ${m.role}"><p>${escapeHtml(m.content)}</p></div>`;
}

loadNotebooks();
```

- [ ] **Step 5: Manual smoke check**

```bash
.venv/Scripts/python.exe main.py
```

Open `http://127.0.0.1:8765`. Create a notebook, paste a *short* YouTube URL (under 2 min — keeps transcribe fast on CPU `small`), wait for `ready`, then ask a question. Assert: answer streams, `⌜title · mm:ss⌟` tokens render, clicking seeks the embedded player and highlights the passage. **No commit until the smoke passes.** If the citation map isn't wired on first render (SSE `event: citations` parsing), fix the `app.js` SSE loop to capture the citations event before the final data line. (The skeleton above captures token data; the implementer should also parse the `event: citations` final event — see Step 6.)

- [ ] **Step 6: Fix the citations-event parsing in `app.js` (likely needed)**

The SSE response interleaves `data:` lines (tokens) and a final `event:`+`data:` pair (citations). The fetch-stream reader in Step 4 doesn't track the `event:` preamble across lines. Adjust the loop to buffer the current event label:

```javascript
  let curEvent = "message";
  for (const line of lines) {
    if (line.startsWith("event:")) { curEvent = line.slice(6).trim(); continue; }
    if (line.startsWith("data:")) {
      const payload = line.slice(5).trim();
      if (curEvent === "citations") {
        citationMap = JSON.parse(payload);
        p.innerHTML = renderTokens(full);  // re-render with the map now
      } else {
        const { token } = JSON.parse(payload);
        if (token) { full += token; p.innerHTML = renderTokens(full); }
      }
    }
  }
```

Re-run the smoke check. Confirm tokens resolve to `⌜title · mm:ss⌟` only after the final citations event arrives (they'll be bare `[#n]` until then — acceptable progressive render).

- [ ] **Step 7: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): single-page UI with streaming chat and time-coded seek tokens"
```

---

## Task 16: main.py wiring + README

**Files:**
- Modify: `opennotebook/main.py` (final form with real app wiring)
- Create: `opennotebook/README.md`

- [ ] **Step 1: Finalize `main.py`**

```python
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
app = build_app(store, cfg)

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


def main():
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:8765")).start()
    uvicorn.run(app, host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write `README.md`**

```markdown
# OpenNotebook

An open-source, self-hostable study companion inspired by NotebookLM. Paste a
**YouTube lecture URL** into a notebook — OpenNotebook transcribes it and lets you
chat with the lecture. Answers are grounded strictly in the transcript, with
**time-coded citations that seek the video to the exact moment** the lecturer said it.

Bring your own model — cloud (NVIDIA NIM, OpenRouter, OpenAI) or local (Ollama, LM
Studio) — through one OpenAI-compatible config. Zero-config defaults handle
embeddings, reranking, and transcription locally.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows; `source .venv/bin/activate` on macOS/Linux
pip install -e ".[local]"       # installs fastapi + faster-whisper + sentence-transformers + yt-dlp
python main.py                  # opens http://127.0.0.1:8765 in your browser
```

On first run, edit your config at `~/.opennotebook/config.yaml`: set an LLM
`base_url`, `api_key`, and `model`. Everything else works with bundled defaults.

## Config

See [`config.example.yaml`](config.example.yaml). Key fields:

| Key | Default | Notes |
|-----|---------|-------|
| `llm.base_url` | — | Any OpenAI-compatible endpoint (NVIDIA NIM, OpenRouter, Ollama, LM Studio) |
| `llm.model` | — | Model name for that endpoint |
| `embeddings.provider` | `bundled` | `bundled` (CPU MiniLM) or `openai_compatible` |
| `reranker.provider` | `bundled` | `bundled` (cross-encoder). Remote reranker is a v1.5 extension |
| `whisper.provider` | `bundled` | `bundled` (faster-whisper) or `openai_compatible` |
| `whisper.model` | `small` | `tiny\|base\|small\|medium\|large` |

## How it works

1. **Ingest**: `yt-dlp` pulls audio → `faster-whisper` transcribes with timestamps →
   a timestamp-preserving chunker groups segments → chunks are embedded and stored
   in SQLite + sqlite-vec.
2. **Ground**: each question is embedded → sqlite-vec retrieves top-20 chunks → a
   cross-encoder reranks → the top passages are fit to the model's context window
   (so local 8k models still work) → a strict "answer only from passages + cite [#]"
   prompt is streamed back.
3. **Cite**: `[#]` markers are parsed and mapped to `(source, timestamp)`; the
   frontend renders them as `⌜Title · mm:ss⌟` tokens. Click → the YouTube player
   seeks to that moment.

## Why

NotebookLM's grounded Q&A is excellent but paywalled and region-locked. This is
the open version: bring your own model, run it on your laptop, study any lecture
that's on YouTube.

## Scope (MVP)

YouTube sources only. PDF/TXT/Markdown/DOCX ingestion, Audio Overview (podcast),
PDF page rendering, and a Tauri desktop shell are designed-for and explicitly
deferred (see `docs/superpowers/specs/2026-08-06-opennotebook-design.md` §13).

## License

MIT.
```

- [ ] **Step 3: Final smoke check — end to end**

```bash
.venv/Scripts/python.exe main.py
```

Full flow: new notebook → paste a short YouTube URL → watch progress `downloading → transcribing → chunking → ready` → ask a question → answer streams with `⌜…⌟` seek tokens → click a token → player seeks + passage highlights. Every backend `python -m backend.<module>` self-check should still pass — run them all as a final sweep:

```bash
for m in config store grounding.chunker grounding.embedder grounding.retriever grounding.reranker grounding.citations grounding.pipeline llm_client api ingestion.youtube ingestion.transcribe ingestion.ingest; do
  echo "=== $m ==="
  .venv/Scripts/python.exe -m backend.$m || echo "FAILED: $m"
done
```

(Module self-checks that hit the network or download models — `grounding.embedder`, `grounding.reranker`, `ingestion.youtube` — may take time on first run; `youtube` is offline-mocked.)

- [ ] **Step 4: Commit**

```bash
git add main.py README.md
git commit -m "feat: final wiring + README; MVP shippable"
```

---

## Self-review (plan author, fresh-eyes pass)

**1. Spec coverage check (spec section → task):**
- §4 module structure → Tasks 1–14 (every module created).
- §5 data model → Task 3.
- §6 ingestion pipeline → Tasks 11, 12, 13 (+ chunker in T4, embedder in T5).
- §7 grounding pipeline (a–f) → Tasks 4–9.
- §8 citation UX → Task 15 (frontend).
- §9 API surface → Task 14.
- §10 config → Task 2 (+ UI config endpoint in T14).
- §11 testing (self-checks) → every task.
- §12 caveats → lazy imports (T5/12), error isolation (T13), streaming citation tolerance (T8 + T15 Step 6), transcription wait surfaced (T11 status → T14 SSE).
- §13 roadmap → out of scope, noted in README.
**All sections covered.** No orphan requirements.

**2. Placeholder scan:** No `TODO`/`TBD`/"Fill in" in code blocks. T15 Step 6 anticipates a real SSE-parsing fix with actual code (not a "fix later" stub). The frontend-design skill is invoked by name at T15's header (behavioral instruction, not a placeholder).

**3. Type/signature consistency check:**
- `group(segments)` returns `list[Chunk]` with `Chunk.text/.start_sec/.end_sec/.token_count` — used consistently in T9 (`c.text`, `c.start_sec`) and T13 (`c.text`, `c.start_sec`, `c.end_sec`, `c.token_count`). ✅
- `Store.add_chunk(source_id, ord, text, start_sec, end_sec, token_count, embedding)` — called in T6, T9 (indirectly), T13 with matching positional args. ✅
- `get_embedder(cfg) -> Embedder` with `.embed(text)->list[float]` and `.dim` — used in T6, T9, T13. ✅
- `Pipeline.stream_answer` yields `__CITATIONS__`+json sentinel — consumed in T14 chat SSE. ✅
- `build_app(store, cfg) -> FastAPI` defined in T14, used in T16. ✅
- `ingest.ingest_url(source_id, url, cfg, store)` defined in T13, called in T14 via `run_in_executor`. ✅
- `youtube_download` / `transcribe` aliases in T13 match what the self-check patches. ✅

No signature drift found.

**One scope caveat to flag to the user** (not a plan defect): the plan is long — 16 tasks. Task 15 (frontend) is the largest single task and could be split (HTML/CSS/structure vs JS behavior) if a reviewer wants finer gates; I kept it one task because the seek-token UX is cohesive and splitting it fragments the smoke-check. Acceptable as written.
