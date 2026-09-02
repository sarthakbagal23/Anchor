"""SQLite + sqlite-vec persistence: workspaces, sources, chunks, vectors, messages."""
from __future__ import annotations
import json
import os
import re
import sqlite3
import time
import threading
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  unit_id INTEGER,
  created_at REAL NOT NULL,
  FOREIGN KEY(unit_id) REFERENCES units(id)
);
CREATE TABLE IF NOT EXISTS courses(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  exam_type TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS units(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  course_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  created_at REAL NOT NULL,
  FOREIGN KEY(course_id) REFERENCES courses(id)
);
CREATE INDEX IF NOT EXISTS idx_units_course ON units(course_id);
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace_id INTEGER NOT NULL,
  unit_id INTEGER,
  type TEXT NOT NULL,
  title TEXT,
  youtube_id TEXT,
  duration_sec INTEGER,
  status TEXT NOT NULL DEFAULT 'queued',
  error TEXT,
  file_path TEXT,
  created_at REAL NOT NULL,
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id),
  FOREIGN KEY(unit_id) REFERENCES units(id)
);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL,
  ord INTEGER NOT NULL,
  text TEXT NOT NULL,
  start_sec INTEGER,
  end_sec INTEGER,
  page_num INTEGER,
  y_top REAL,
  token_count INTEGER,
  FOREIGN KEY(source_id) REFERENCES sources(id)
);
CREATE INDEX IF NOT EXISTS idx_chunks_src ON chunks(source_id);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at REAL NOT NULL,
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
);
CREATE TABLE IF NOT EXISTS learning_objectives(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  unit_id INTEGER NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'extracted',
  statement TEXT NOT NULL,
  objective_type TEXT NOT NULL DEFAULT 'concept',
  confidence REAL,
  ced_unit_number INTEGER,
  ced_topic_number INTEGER,
  ced_skill_code TEXT,
  content_hash TEXT,
  created_at REAL NOT NULL,
  FOREIGN KEY(unit_id) REFERENCES units(id)
);
CREATE INDEX IF NOT EXISTS idx_objectives_unit_srctype ON learning_objectives(unit_id, source_type);
CREATE TABLE IF NOT EXISTS objective_chunks(
  objective_id INTEGER NOT NULL,
  chunk_id INTEGER NOT NULL,
  similarity REAL,
  rank INTEGER,
  PRIMARY KEY(objective_id, chunk_id),
  FOREIGN KEY(objective_id) REFERENCES learning_objectives(id),
  FOREIGN KEY(chunk_id) REFERENCES chunks(id)
);
CREATE INDEX IF NOT EXISTS idx_ojc_chunk ON objective_chunks(chunk_id);
CREATE TABLE IF NOT EXISTS study_guides(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace_id INTEGER NOT NULL,
  unit_id INTEGER,
  objective_count INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id),
  FOREIGN KEY(unit_id) REFERENCES units(id)
);
CREATE INDEX IF NOT EXISTS idx_guides_ws ON study_guides(workspace_id);
CREATE TABLE IF NOT EXISTS guide_sections(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guide_id INTEGER NOT NULL,
  ord INTEGER NOT NULL,
  objective_id INTEGER,
  skill_code TEXT,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  citations TEXT NOT NULL DEFAULT '{}',
  covered INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(guide_id) REFERENCES study_guides(id),
  FOREIGN KEY(objective_id) REFERENCES learning_objectives(id)
);
CREATE INDEX IF NOT EXISTS idx_guide_sections_guide ON guide_sections(guide_id);
"""


def _pack(vec: list[float]) -> str:
    return json.dumps(vec)


class Store:
    def __init__(self, db_path: str, vec_dim: int | None = None):
        self.db_path = db_path
        self._local = threading.local()
        self.vec_dim = vec_dim
        self._vec_ready = False
        # Force a connection now (main thread), running migrations on a real conn.
        self.conn

    @property
    def conn(self) -> sqlite3.Connection:
        """A per-thread sqlite connection to the same file. FastAPI serves requests
        from a worker threadpool, so one shared connection across threads can trip
        sqlite3's 'bad parameter or other API misuse' misuse errors (a cursor from
        one thread being invalidated by another thread's execute on the same handle).
        Giving every thread its own connection makes concurrent requests safe."""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, check_same_thread=False)
            c.row_factory = sqlite3.Row
            # WAL lets concurrent readers keep going while a writer commits, instead
            # of blocking on an exclusive rollback-journal lock. Persistent per DB file.
            c.execute("PRAGMA journal_mode=WAL")
            # Give a thread a chance to wait for a lock before failing under contention.
            c.execute("PRAGMA busy_timeout=10000")
            self._local.conn = c
        return c

    def init(self) -> None:
        # Legacy migration runs BEFORE executescript: if the old "notebooks" table still
        # exists and there is no "workspaces" table yet, rename it in place FIRST.
        # Otherwise executescript below would create an empty workspaces table and this
        # rename would be skipped (ws_exists would be True), orphaning the old data.
        if not self._table_exists("workspaces"):
            if self._table_exists("notebooks"):
                self.conn.execute("ALTER TABLE notebooks RENAME TO workspaces")

        self.conn.executescript(SCHEMA)

        # Ensure unit_id exists on workspaces (whether newly created or migrated).
        ws_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(workspaces)")}
        if "unit_id" not in ws_cols:
            self.conn.execute("ALTER TABLE workspaces ADD COLUMN unit_id INTEGER")

        # Migrate old notebook_id -> workspace_id on sources and messages.
        src_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(sources)")}
        if "unit_id" not in src_cols:
            self.conn.execute("ALTER TABLE sources ADD COLUMN unit_id INTEGER")
        if "file_path" not in src_cols:
            self.conn.execute("ALTER TABLE sources ADD COLUMN file_path TEXT")
        if "notebook_id" in src_cols and "workspace_id" not in src_cols:
            self.conn.execute("ALTER TABLE sources RENAME COLUMN notebook_id TO workspace_id")

        msg_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(messages)")}
        if "notebook_id" in msg_cols and "workspace_id" not in msg_cols:
            self.conn.execute("ALTER TABLE messages RENAME COLUMN notebook_id TO workspace_id")

        chunk_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(chunks)")}
        if "y_top" not in chunk_cols:
            self.conn.execute("ALTER TABLE chunks ADD COLUMN y_top REAL")

        course_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(courses)")}
        if "exam_type" not in course_cols:
            self.conn.execute("ALTER TABLE courses ADD COLUMN exam_type TEXT")

        gs_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(guide_sections)")}
        if "covered" not in gs_cols:
            self.conn.execute("ALTER TABLE guide_sections ADD COLUMN covered INTEGER NOT NULL DEFAULT 0")

        # Indexes referencing the (possibly just migrated) columns are created here after
        # the ALTERs so they don't fail on an old DB where the columns didn't yet exist
        # when the SCHEMA executescript ran.
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_unit ON sources(unit_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_workspaces_unit ON workspaces(unit_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_ws ON sources(workspace_id)")
        self.conn.commit()

    def _table_exists(self, name: str) -> bool:
        return self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    def _ensure_vec(self, dim: int) -> None:
        # Runs per-thread: every connection must load sqlite-vec and (re)create the
        # vec table before use, so we ignore the shared _vec_ready flag here.
        import sqlite_vec  # lazy
        self.conn.enable_load_extension(True)
        self.conn.load_extension(sqlite_vec.loadable_path())
        # Reconcilire dim: a vec0 table created by an earlier session (e.g. a 256-dim
        # fallback embedder) cannot accept a different dim. If the stored schema's dim
        # differs, rebuild it empty with the current embedder's dim.
        row = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='vec_chunks'"
        ).fetchone()
        if row is not None and row["sql"] is not None:
            m = re.search(r"FLOAT\[(\d+)\]", row["sql"])
            if m and int(m.group(1)) != dim:
                self.conn.execute("DROP TABLE vec_chunks")
        self.conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(embedding FLOAT[{dim}])"
        )
        self._vec_ready = True
        self.vec_dim = dim

    def _load_vec_module(self) -> None:
        """Load the sqlite-vec extension (no table create). Needed before any statement
        touches the vec_chunks virtual table, since the module may not be loaded if no
        embedding has been inserted in this connection/process yet."""
        import sqlite_vec  # lazy
        self.conn.enable_load_extension(True)
        self.conn.load_extension(sqlite_vec.loadable_path())
        self._vec_ready = True

    # --- workspaces ---
    def add_workspace(self, title: str, unit_id: int | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO workspaces(title, unit_id, created_at) VALUES(?, ?, ?)",
            (title, unit_id, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def create_course_unit_workspace(self, title: str) -> tuple[int, int, int]:
        """Create a Course, its first Unit, and the backing Workspace (chat session)
        together. Returns (workspace_id, course_id, unit_id)."""
        course_id = self.add_course(title)
        unit_id = self.add_unit(course_id, "Unit 1")
        ws_id = self.add_workspace(title, unit_id)
        return ws_id, course_id, unit_id

    def list_workspaces(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM workspaces ORDER BY created_at DESC")]

    def get_workspace(self, ws_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM workspaces WHERE id=?", (ws_id,)).fetchone()
        return dict(r) if r else None

    # --- courses ---
    def add_course(self, title: str, exam_type: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO courses(title, exam_type, created_at) VALUES(?, ?, ?)",
            (title, exam_type, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_course_exam_type(self, course_id: int, exam_type: str | None) -> None:
        self.conn.execute("UPDATE courses SET exam_type=? WHERE id=?", (exam_type, course_id))
        self.conn.commit()

    def list_courses(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM courses ORDER BY created_at DESC")]

    def get_course(self, course_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        return dict(r) if r else None

    def delete_course(self, course_id: int) -> None:
        unit_ids = [r[0] for r in self.conn.execute("SELECT id FROM units WHERE course_id=?", (course_id,))]
        if unit_ids:
            ph = ",".join("?" * len(unit_ids))
            self.conn.execute(f"UPDATE sources SET unit_id=NULL WHERE unit_id IN ({ph})", unit_ids)
            self.conn.execute(f"UPDATE workspaces SET unit_id=NULL WHERE unit_id IN ({ph})", unit_ids)
        self.conn.execute("DELETE FROM units WHERE course_id=?", (course_id,))
        self.conn.execute("DELETE FROM courses WHERE id=?", (course_id,))
        self.conn.commit()

    # --- units ---
    def add_unit(self, course_id: int, title: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO units(course_id, title, created_at) VALUES(?, ?, ?)",
            (course_id, title, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_units(self, course_id: int) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM units WHERE course_id=? ORDER BY created_at", (course_id,)
            )
        ]

    def get_unit(self, unit_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM units WHERE id=?", (unit_id,)).fetchone()
        return dict(r) if r else None

    def delete_unit(self, unit_id: int) -> None:
        self.delete_unit_objectives(unit_id)
        self.conn.execute("UPDATE sources SET unit_id=NULL WHERE unit_id=?", (unit_id,))
        self.conn.execute("UPDATE workspaces SET unit_id=NULL WHERE unit_id=?", (unit_id,))
        self.conn.execute("DELETE FROM units WHERE id=?", (unit_id,))
        self.conn.commit()

    # --- workspaces ---
    def delete_workspace(self, workspace_id: int) -> None:
        self.delete_study_guides(workspace_id)
        for src_id in [r[0] for r in self.conn.execute("SELECT id FROM sources WHERE workspace_id=?", (workspace_id,))]:
            self.delete_source(src_id)
        self.conn.execute("DELETE FROM messages WHERE workspace_id=?", (workspace_id,))
        self.conn.execute("DELETE FROM workspaces WHERE id=?", (workspace_id,))
        self.conn.commit()

    # --- source <-> unit ---
    def assign_source_to_unit(self, source_id: int, unit_id: int) -> None:
        self.conn.execute("UPDATE sources SET unit_id=? WHERE id=?", (unit_id, source_id))
        self.conn.commit()

    def list_unit_sources(self, unit_id: int) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM sources WHERE unit_id=? ORDER BY created_at", (unit_id,)
            )
        ]

    # --- sources ---
    def add_source(
        self, workspace_id: int, type_: str, title: str, youtube_id: str | None, duration_sec: int | None,
        unit_id: int | None = None,
    ) -> int:
        if unit_id is None:
            ws = self.conn.execute("SELECT unit_id FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
            unit_id = ws["unit_id"] if ws else None
        cur = self.conn.execute(
            "INSERT INTO sources(workspace_id, unit_id, type, title, youtube_id, duration_sec, status, created_at) "
            "VALUES(?,?,?,?,?,?, 'queued', ?)",
            (workspace_id, unit_id, type_, title, youtube_id, duration_sec, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_source_status(self, source_id: int, status: str, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE sources SET status=?, error=? WHERE id=?", (status, error, source_id)
        )
        self.conn.commit()

    def set_source_file_path(self, source_id: int, file_path: str) -> None:
        self.conn.execute("UPDATE sources SET file_path=? WHERE id=?", (file_path, source_id))
        self.conn.commit()

    def get_source(self, source_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return dict(r) if r else None

    def list_sources(self, workspace_id: int) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM sources WHERE workspace_id=? ORDER BY created_at", (workspace_id,)
            )
        ]

    def delete_source(self, source_id: int) -> None:
        if self._table_exists("vec_chunks"):
            self._load_vec_module()
            self.conn.execute("DELETE FROM vec_chunks WHERE rowid IN (SELECT id FROM chunks WHERE source_id=?)", (source_id,))
        self.conn.execute(
            "DELETE FROM objective_chunks WHERE chunk_id IN (SELECT id FROM chunks WHERE source_id=?)",
            (source_id,),
        )
        self.conn.execute("DELETE FROM chunks WHERE source_id=?", (source_id,))
        self.conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
        self.conn.commit()

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
        page_num: int | None = None,
        y_top: float | None = None,
    ) -> int:
        self._ensure_vec(len(embedding))
        cur = self.conn.execute(
            "INSERT INTO chunks(source_id, ord, text, start_sec, end_sec, page_num, y_top, token_count) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (source_id, ord_, text, start_sec, end_sec, page_num, y_top, token_count),
        )
        chunk_id = cur.lastrowid
        try:
            self.conn.execute(
                "INSERT INTO vec_chunks(rowid, embedding) VALUES(?, ?)", (chunk_id, _pack(embedding))
            )
        except Exception:
            self.conn.rollback()
            raise
        self.conn.commit()
        return chunk_id

    def get_chunks(self, chunk_ids: list[int]) -> list[dict]:
        if not chunk_ids:
            return []
        ph = ",".join("?" * len(chunk_ids))
        rows = self.conn.execute(f"SELECT * FROM chunks WHERE id IN ({ph})", chunk_ids)
        return [dict(r) for r in rows]

    def search(self, query_vec: list[float], workspace_id: int, k: int = 20) -> list[int]:
        self._ensure_vec(len(query_vec))
        rows = self.conn.execute(
            """
            SELECT v.rowid, c.source_id, s.workspace_id, v.distance
            FROM vec_chunks v
            JOIN chunks c ON c.id = v.rowid
            JOIN sources s ON s.id = c.source_id
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (_pack(query_vec), k),
        ).fetchall()
        return [r["rowid"] for r in rows if r["workspace_id"] == workspace_id]

    def search_unit(self, query_vec: list[float], unit_id: int, k: int = 20) -> list[tuple[int, float]]:
        """Top-k (chunk_id, cosine_distance) restricted to one unit's sources.

        Pulls a generous candidate set from the vec index (sqlite-vec can't filter
        by unit in the MATCH clause), then keeps the k nearest chunks whose
        source belongs to the unit."""
        self._ensure_vec(len(query_vec))
        rows = self.conn.execute(
            """
            SELECT v.rowid, c.source_id, s.unit_id, v.distance
            FROM vec_chunks v
            JOIN chunks c ON c.id = v.rowid
            JOIN sources s ON s.id = c.source_id
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (_pack(query_vec), max(k * 8, 200)),
        ).fetchall()
        return [(r["rowid"], r["distance"]) for r in rows if r["unit_id"] == unit_id][:k]

    # --- learning objectives + objective-chunk coverage ---
    def add_learning_objective(
        self,
        unit_id: int,
        statement: str,
        objective_type: str = "concept",
        source_type: str = "extracted",
        confidence: float | None = None,
        ced_unit_number: int | None = None,
        ced_topic_number: int | None = None,
        ced_skill_code: str | None = None,
        content_hash: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO learning_objectives("
            "unit_id, source_type, statement, objective_type, confidence, "
            "ced_unit_number, ced_topic_number, ced_skill_code, content_hash, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (unit_id, source_type, statement, objective_type, confidence,
             ced_unit_number, ced_topic_number, ced_skill_code, content_hash, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_objective(self, objective_id: int) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM learning_objectives WHERE id=?", (objective_id,)
        ).fetchone()
        return dict(r) if r else None

    def list_unit_objectives(self, unit_id: int, source_type: str | None = None) -> list[dict]:
        q = "SELECT * FROM learning_objectives WHERE unit_id=? "
        args: list = [unit_id]
        if source_type is not None:
            q += "AND source_type=? "
            args.append(source_type)
        q += "ORDER BY created_at, id"
        return [dict(r) for r in self.conn.execute(q, args)]

    def delete_unit_objectives(self, unit_id: int, source_type: str | None = None) -> None:
        q = "SELECT id FROM learning_objectives WHERE unit_id=? "
        args: list = [unit_id]
        if source_type is not None:
            q += "AND source_type=? "
            args.append(source_type)
        ids = [r[0] for r in self.conn.execute(q, args)]
        if not ids:
            return
        params = ",".join("?" * len(ids))
        self.conn.execute(
            f"DELETE FROM objective_chunks WHERE objective_id IN ({params})", ids
        )
        self.conn.execute(f"DELETE FROM learning_objectives WHERE id IN ({params})", ids)
        self.conn.commit()

    def set_objective_chunks(
        self, objective_id: int, chunk_ids: list[int], similarities: list[float]
    ) -> None:
        """Replace the chunk-coverage set for one objective (delete + insert)."""
        self.conn.execute("DELETE FROM objective_chunks WHERE objective_id=?", (objective_id,))
        rows = [
            (objective_id, cid, round(float(similarities[i]), 6), i)
            for i, cid in enumerate(chunk_ids)
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO objective_chunks(objective_id, chunk_id, similarity, rank) "
            "VALUES(?,?,?,?)",
            rows,
        )
        self.conn.commit()

    def get_objective_chunks(self, objective_id: int) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM objective_chunks WHERE objective_id=? ORDER BY rank",
                (objective_id,),
            )
        ]

    def get_objectives_covered_by_chunk(self, chunk_id: int) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                """
                SELECT oc.objective_id, oc.similarity, o.unit_id, o.source_type, o.statement
                FROM objective_chunks oc
                JOIN learning_objectives o ON o.id = oc.objective_id
                WHERE oc.chunk_id=?
                """,
                (chunk_id,),
            )
        ]

    # --- study guides ---
    def create_study_guide(self, workspace_id: int, unit_id: int | None) -> int:
        cur = self.conn.execute(
            "INSERT INTO study_guides(workspace_id, unit_id, created_at) VALUES(?,?,?)",
            (workspace_id, unit_id, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_guide_objective_count(self, guide_id: int, count: int) -> None:
        self.conn.execute(
            "UPDATE study_guides SET objective_count=? WHERE id=?", (count, guide_id)
        )
        self.conn.commit()

    def add_guide_section(
        self, guide_id: int, ord_: int, title: str, body: str, citations: dict,
        objective_id: int | None = None, skill_code: str | None = None, covered: bool = True,
    ) -> int:
        import json as _json
        cur = self.conn.execute(
            "INSERT INTO guide_sections(guide_id, ord, objective_id, skill_code, title, body, citations, covered) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (guide_id, ord_, objective_id, skill_code, title, body, _json.dumps(citations), 1 if covered else 0),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_latest_study_guide(self, workspace_id: int) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM study_guides WHERE workspace_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
        return dict(r) if r else None

    def get_study_guide(self, guide_id: int) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM study_guides WHERE id=?", (guide_id,)
        ).fetchone()
        return dict(r) if r else None

    def get_guide_sections(self, guide_id: int) -> list[dict]:
        import json as _json
        rows = self.conn.execute(
            "SELECT * FROM guide_sections WHERE guide_id=? ORDER BY ord", (guide_id,)
        )
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["citations"] = _json.loads(d.get("citations") or "{}")
            except Exception:
                d["citations"] = {}
            out.append(d)
        return out

    def delete_study_guides(self, workspace_id: int) -> None:
        ids = [r[0] for r in self.conn.execute(
            "SELECT id FROM study_guides WHERE workspace_id=?", (workspace_id,)
        )]
        if not ids:
            return
        ph = ",".join("?" * len(ids))
        self.conn.execute(f"DELETE FROM guide_sections WHERE guide_id IN ({ph})", ids)
        self.conn.execute(f"DELETE FROM study_guides WHERE id IN ({ph})", ids)
        self.conn.commit()

    # --- messages ---
    def add_message(self, workspace_id: int, role: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO messages(workspace_id, role, content, created_at) VALUES(?,?,?,?)",
            (workspace_id, role, content, time.time()),
        )
        self.conn.commit()

    def list_messages(self, workspace_id: int) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM messages WHERE workspace_id=? ORDER BY created_at", (workspace_id,)
            )
        ]


if __name__ == "__main__":
    import tempfile

    # --- create Course + Unit + Workspace together (the "New workspace" path) ---
    db = os.path.join(tempfile.mkdtemp(), "test.db")
    store = Store(db)
    store.init()
    ws, course_id, unit_id = store.create_course_unit_workspace("AP Statistics")
    assert store.get_course(course_id)["title"] == "AP Statistics"
    unit = store.get_unit(unit_id)
    assert unit["course_id"] == course_id and unit["title"] == "Unit 1"
    assert store.get_workspace(ws)["unit_id"] == unit_id

    # a source added to this workspace auto-attaches to its course's unit
    s = store.add_source(ws, "youtube", "Calculus", "vid123", 600)
    store.set_source_status(s, "ready")
    assert store.get_source(s)["unit_id"] == unit_id
    assert [x["id"] for x in store.list_unit_sources(unit_id)] == [s]

    c1 = store.add_chunk(s, 0, "epsilon delta definition", 822, 845, 30, [1.0, 0.0, 0.0, 0.0])
    store.add_chunk(s, 1, "proof of the limit law", 1630, 1670, 35, [0.0, 1.0, 0.0, 0.0])
    hits = store.search([0.95, 0.05, 0.0, 0.0], ws, k=2)
    assert hits and c1 == hits[0], f"expected nearest chunk first, got {hits}"
    store.add_message(ws, "user", "what is a limit?")
    assert len(store.list_messages(ws)) == 1

    # learning objectives (extracted vs CED import) + objective-chunk coverage
    o1 = store.add_learning_objective(unit_id, "define a limit", source_type="extracted", confidence=0.9)
    o2 = store.add_learning_objective(
        unit_id, "describe the distribution of a quantitative variable", objective_type="skill",
        source_type="ced_import", ced_unit_number=1, ced_topic_number=6, ced_skill_code="2.A",
    )
    assert [o["id"] for o in store.list_unit_objectives(unit_id, source_type="extracted")] == [o1]
    ced_objs = store.list_unit_objectives(unit_id, source_type="ced_import")
    assert [o["id"] for o in ced_objs] == [o2]
    ob = store.get_objective(o2)
    assert ob["ced_unit_number"] == 1 and ob["ced_topic_number"] == 6 and ob["ced_skill_code"] == "2.A"
    store.set_objective_chunks(o2, [c1], [0.813])
    oc = store.get_objective_chunks(o2)
    assert len(oc) == 1 and oc[0]["chunk_id"] == c1 and oc[0]["similarity"] == 0.813
    covered = store.get_objectives_covered_by_chunk(c1)
    assert [x["objective_id"] for x in covered] == [o2]
    store.delete_unit_objectives(unit_id, source_type="ced_import")
    assert store.list_unit_objectives(unit_id, source_type="ced_import") == []
    assert [o["id"] for o in store.list_unit_objectives(unit_id, source_type="extracted")] == [o1]

    # study guide persistence: create guide + sections, fetch latest, cascade delete
    gid = store.create_study_guide(ws, unit_id)
    store.set_guide_objective_count(gid, 1)
    store.add_guide_section(gid, 0, "Define a limit", "epsilon-delta definition.", {"1": {"chunk_id": c1}}, objective_id=o1)
    guide = store.get_latest_study_guide(ws)
    assert guide["id"] == gid and guide["objective_count"] == 1
    secs = store.get_guide_sections(gid)
    assert len(secs) == 1 and secs[0]["title"] == "Define a limit"
    assert secs[0]["citations"]["1"]["chunk_id"] == c1
    assert store.get_study_guide(gid)["workspace_id"] == ws
    store.delete_study_guides(ws)
    assert store.get_latest_study_guide(ws) is None
    assert store.get_guide_sections(gid) == []

    # exam_type on courses (CED routing flag)
    store.set_course_exam_type(course_id, "AP")
    assert store.get_course(course_id)["exam_type"] == "AP"
    fresh = store.add_course("Chemistry", exam_type=None)
    assert store.get_course(fresh)["exam_type"] is None

    # unit-scoped vector search (reuses the same vec index as search())
    c2 = store.add_chunk(s, 2, "mean and median summary statistics", 700, 740, 30, [0.0, 0.0, 1.0, 0.0])
    uh = store.search_unit([0.0, 0.0, 0.9, 0.0], unit_id, k=1)
    assert uh and uh[0][0] == c2 and uh[0][1] < 0.2, f"unit search should find summary-stats chunk, got {uh}"
    other_unit = store.add_unit(course_id, "Unit 2")
    assert store.search_unit([0.0, 0.0, 0.9, 0.0], other_unit, k=1) == [], "other unit must not leak chunks"

    # stand-alone course/unit CRUD + explicit source attachment to a second unit
    course2 = store.add_course("Chemistry")
    unit_a = store.add_unit(course2, "Unit 1")
    unit_b = store.add_unit(course2, "Unit 2")
    assert [u["title"] for u in store.list_units(course2)] == ["Unit 1", "Unit 2"]
    store.add_source(ws, "pdf", "notes.pdf", None, None, unit_id=unit_b)
    assert len(store.list_unit_sources(unit_b)) == 1

    # deleting a unit clears its sources' and workspaces' unit reference
    store.delete_unit(unit_b)
    assert store.get_workspace(ws)["unit_id"] == unit_id, "workspace should stay linked to its own unit"
    assert len(store.list_unit_sources(unit_b)) == 0

    # deleting the workspace's own unit clears the workspace link too
    store.delete_unit(unit_id)
    assert store.get_workspace(ws)["unit_id"] is None
    assert store.get_source(s)["unit_id"] is None

    print("store OK")
