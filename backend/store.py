"""SQLite + sqlite-vec persistence: notebooks, sources, chunks, vectors, messages."""
from __future__ import annotations
import json
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


def _pack(vec: list[float]) -> str:
    return json.dumps(vec)


class Store:
    def __init__(self, db_path: str, vec_dim: int | None = None):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.vec_dim = vec_dim
        self._vec_ready = False

    def init(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _ensure_vec(self, dim: int) -> None:
        if self._vec_ready and self.vec_dim == dim:
            return
        self.conn.enable_load_extension(True)
        import sqlite_vec  # lazy
        self.conn.load_extension(sqlite_vec.loadable_path())
        self.conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(embedding FLOAT[{dim}])"
        )
        self.conn.commit()
        self.vec_dim = dim
        self._vec_ready = True

    # --- notebooks ---
    def add_notebook(self, title: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO notebooks(title, created_at) VALUES(?, ?)", (title, time.time())
        )
        self.conn.commit()
        return cur.lastrowid

    def list_notebooks(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM notebooks ORDER BY created_at DESC")]

    def get_notebook(self, nb_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM notebooks WHERE id=?", (nb_id,)).fetchone()
        return dict(r) if r else None

    # --- sources ---
    def add_source(
        self, notebook_id: int, type_: str, title: str, youtube_id: str | None, duration_sec: int | None
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO sources(notebook_id, type, title, youtube_id, duration_sec, status, created_at) "
            "VALUES(?,?,?,?,?, 'queued', ?)",
            (notebook_id, type_, title, youtube_id, duration_sec, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_source_status(self, source_id: int, status: str, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE sources SET status=?, error=? WHERE id=?", (status, error, source_id)
        )
        self.conn.commit()

    def get_source(self, source_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return dict(r) if r else None

    def list_sources(self, notebook_id: int) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM sources WHERE notebook_id=? ORDER BY created_at", (notebook_id,)
            )
        ]

    # --- chunks + vectors ---
    def add_chunk(
        self,
        source_id: int,
        ord_: int,
        text: str,
        start_sec: int | None,
        end_sec: int | None,
        token_count: int,
        embedding: list[float],
    ) -> int:
        self._ensure_vec(len(embedding))
        cur = self.conn.execute(
            "INSERT INTO chunks(source_id, ord, text, start_sec, end_sec, token_count) "
            "VALUES(?,?,?,?,?,?)",
            (source_id, ord_, text, start_sec, end_sec, token_count),
        )
        chunk_id = cur.lastrowid
        self.conn.execute(
            "INSERT INTO vec_chunks(rowid, embedding) VALUES(?, ?)", (chunk_id, _pack(embedding))
        )
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
        self.conn.execute(
            "INSERT INTO messages(notebook_id, role, content, created_at) VALUES(?,?,?,?)",
            (notebook_id, role, content, time.time()),
        )
        self.conn.commit()

    def list_messages(self, notebook_id: int) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM messages WHERE notebook_id=? ORDER BY created_at", (notebook_id,)
            )
        ]


if __name__ == "__main__":
    import tempfile

    db = os.path.join(tempfile.mkdtemp(), "test.db")
    store = Store(db)
    store.init()
    nb = store.add_notebook("Lecture 3")
    s = store.add_source(nb, "youtube", "Calculus", "vid123", 600)
    store.set_source_status(s, "ready")
    assert store.get_source(s)["status"] == "ready"
    c1 = store.add_chunk(s, 0, "epsilon delta definition", 822, 845, 30, [1.0, 0.0, 0.0, 0.0])
    store.add_chunk(s, 1, "proof of the limit law", 1630, 1670, 35, [0.0, 1.0, 0.0, 0.0])
    hits = store.search([0.95, 0.05, 0.0, 0.0], nb, k=2)
    assert hits and c1 == hits[0], f"expected nearest chunk first, got {hits}"
    store.add_message(nb, "user", "what is a limit?")
    assert len(store.list_messages(nb)) == 1
    print("store OK")
