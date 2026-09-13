"""Retrieval hardening: workspace-scoped candidate pools, ready-only filters,
failed-ingest cleanup, and dim-namespaced vectors.

All hermetic: real Store on tmp dirs, hashing embedder (embedding *values*
never matter for filter/migration assertions; the one ranking test uses a
wide exact-vs-partial overlap gap so hash collisions can't flip it).
"""
import struct

from backend.grounding.embedder import _HashingEmbedder
from backend.store import Store

EMB = _HashingEmbedder()


def _ws_with_source(store, title="ws", status="ready", stype="pdf"):
    ws = store.add_workspace(title)
    src = store.add_source(ws, stype, f"{title}-src", None, None)
    store.set_source_status(src, status)
    return ws, src


def _add(store, src, ord_, text):
    return store.add_chunk(src, ord_, text, None, None, 10, EMB.embed(text))


def test_search_candidate_pool_survives_noisy_corpus(tmp_path):
    # 25 exact-match decoys in another workspace must not crowd out this
    # workspace's partial matches (old code pulled a global k=20, so 20+
    # higher-ranking foreign chunks meant zero local results).
    store = Store(str(tmp_path / "t.db"))
    store.init()
    ws_a, src_a = _ws_with_source(store, "a")
    ws_b, src_b = _ws_with_source(store, "b")
    q = "alpha beta gamma"
    good = [_add(store, src_a, i, "alpha beta plus filler words here") for i in range(2)]
    for i in range(25):
        _add(store, src_b, i, q)
    hits = store.search(EMB.embed(q), ws_a, k=2)
    assert set(hits) == set(good)


def test_search_only_ready_sources(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.init()
    _, failed_src = _ws_with_source(store, "bad", status="failed")
    _, queued_src = _ws_with_source(store, "q", status="queued")
    ws_ok, ok_src = _ws_with_source(store, "ok")
    _add(store, failed_src, 0, "sampling bias matters greatly")
    _add(store, queued_src, 0, "sampling bias matters greatly")
    want = _add(store, ok_src, 0, "sampling bias matters greatly")
    assert store.search(EMB.embed("sampling bias"), ws_ok, k=5) == [want]


def test_search_unit_ready_only(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.init()
    cid = store.add_course("c")
    uid = store.add_unit(cid, "U1")
    ws = store.add_workspace("ws", uid)
    bad = store.add_source(ws, "pdf", "bad", None, None, unit_id=uid)
    good = store.add_source(ws, "pdf", "good", None, None, unit_id=uid)
    store.set_source_status(bad, "failed")
    store.set_source_status(good, "ready")
    _add(store, bad, 0, "photosynthesis converts light energy")
    want = _add(store, good, 0, "photosynthesis converts light energy")
    hits = store.search_unit(EMB.embed("photosynthesis"), uid, k=5)
    assert [cid for cid, _ in hits] == [want]


def test_failed_ingest_chunks_cleaned(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.init()
    ws, src = _ws_with_source(store, "ws", status="failed")
    _add(store, src, 0, "partial download chunk one")
    _add(store, src, 1, "partial download chunk two")
    assert len(store.search(EMB.embed("partial download"), ws, k=5)) == 0  # failed: filtered anyway
    store.delete_source_chunks(src)
    assert store.get_chunks([1, 2]) == []
    assert store.get_source(src)["status"] == "failed"  # row itself stays


def test_dim_namespacing_no_wipe(tmp_path):
    # switching embedder dims must not destroy existing vectors
    store = Store(str(tmp_path / "t2.db"))
    store.init()
    ws, src = _ws_with_source(store, "ws")
    v64 = [0.1] * 64
    v128 = [0.2] * 128
    c1 = store.add_chunk(src, 0, "first chunk", None, None, 2, v64)
    c2 = store.add_chunk(src, 1, "second chunk", None, None, 2, v128)
    assert store.search(v64, ws, k=5) == [c1]
    assert store.search(v128, ws, k=5) == [c2]


def test_legacy_vec_migration(tmp_path):
    # a pre-namespacing vec_chunks table migrates transparently on first use
    store = Store(str(tmp_path / "t3.db"))
    store.init()
    ws, src = _ws_with_source(store, "ws")
    cid = store.conn.execute(
        "INSERT INTO chunks(source_id, ord, text, token_count) VALUES(?,?,?,?)",
        (src, 0, "legacy chunk text", 3),
    ).lastrowid
    store._load_vec_module()
    store.conn.execute("CREATE VIRTUAL TABLE vec_chunks USING vec0(embedding FLOAT[8])")
    store.conn.execute(
        "INSERT INTO vec_chunks(rowid, embedding) VALUES(?, ?)",
        (cid, struct.pack("<8f", *([0.5] * 8))),
    )
    store.conn.commit()
    hits = store.search([0.5] * 8, ws, k=5)
    assert hits == [cid]
    names = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "vec_chunks" not in names and "vec_chunks_8" in names
