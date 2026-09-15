"""Config/data path resolution and the OpenNotebook->Anchor migration.

The rename must never strand settings or study data: ANCHOR_CONFIG_DIR wins,
OPENNOTEBOOK_CONFIG_DIR still works, otherwise ~/.anchor with a one-time copy
from ~/.opennotebook. All copies, never moves; helpers never raise.
"""
from pathlib import Path

from backend.config import config_dir, migrate_db, migrate_file


def test_migrate_db_copies_wal_content(tmp_path):
    # Regression: a plain file copy of a WAL-mode database silently drops every
    # transaction since the last checkpoint. migrate_db must carry them over.
    import sqlite3

    legacy = tmp_path / "old.db"
    src = sqlite3.connect(str(legacy))
    src.execute("PRAGMA journal_mode=WAL")
    src.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    for i in range(50):
        src.execute("INSERT INTO t(v) VALUES(?)", (f"row{i}",))
    src.commit()  # committed, but still sitting in the -wal sidecar
    assert (tmp_path / "old.db-wal").exists()
    target = tmp_path / "new.db"
    assert migrate_db(target, legacy, "database") == target
    dst = sqlite3.connect(str(target))
    assert dst.execute("SELECT COUNT(*) FROM t").fetchone() == (50,)
    assert dst.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    src.close()
    dst.close()


def test_anchor_env_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("ANCHOR_CONFIG_DIR", str(tmp_path / "a"))
    monkeypatch.setenv("OPENNOTEBOOK_CONFIG_DIR", str(tmp_path / "o"))
    assert config_dir() == tmp_path / "a"


def test_legacy_env_still_honored(monkeypatch, tmp_path):
    monkeypatch.delenv("ANCHOR_CONFIG_DIR", raising=False)
    monkeypatch.setenv("OPENNOTEBOOK_CONFIG_DIR", str(tmp_path / "o"))
    assert config_dir() == tmp_path / "o"


def test_migrate_file_copies_once_and_never_raises(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "config.yaml").write_text("llm: {}", encoding="utf-8")
    target = tmp_path / "new" / "anchor.db"
    assert migrate_file(target, legacy / "config.yaml", "settings") == target
    assert target.read_text(encoding="utf-8") == "llm: {}"
    # idempotent re-run, then legacy deletion still resolves to target
    assert migrate_file(target, legacy / "config.yaml", "settings") == target


def test_migrate_dir_copies_tree(tmp_path):
    legacy = tmp_path / "old"
    (legacy / "data").mkdir(parents=True)
    (legacy / "data" / "x.pdf").write_bytes(b"pdf")
    target = tmp_path / "anchor"
    assert migrate_file(target, legacy, "settings") == target
    assert (target / "data" / "x.pdf").read_bytes() == b"pdf"


def test_migrate_missing_legacy_returns_target(tmp_path):
    target = tmp_path / "anchor.db"
    assert migrate_file(target, tmp_path / "nope.db", "database") == target
    assert not target.exists()


def test_default_points_at_anchor(monkeypatch, tmp_path):
    # neither env var: default dir name is .anchor (migration from .opennotebook
    # only triggers against the real HOME, exercised manually, not here)
    monkeypatch.delenv("ANCHOR_CONFIG_DIR", raising=False)
    monkeypatch.delenv("OPENNOTEBOOK_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert config_dir() == tmp_path / ".anchor"
