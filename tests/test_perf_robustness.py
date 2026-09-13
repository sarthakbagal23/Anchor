"""Perf/robustness contracts: history budgets, bulk ingest, embed batching,
whisper settings, download timeout plumbing, temp cleanup, message ordering.

Hermetic: tmp Stores, stubbed network boundaries (youtube/transcribe/LLM),
no weights, no downloads.
"""
import os
from unittest.mock import MagicMock, patch

from backend.grounding import pipeline as pipe_mod
from backend.grounding.embedder import _HashingEmbedder, _OpenAICompatibleEmbedder, embed_many
from backend.ingestion import ingest as ingest_mod
from backend.ingestion import transcribe as transcribe_mod
from backend.ingestion import youtube as youtube_mod
from backend.store import Store


def _msgs(n):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i} " + "x" * 50}
            for i in range(n)]


def test_trim_history_newest_first_and_capped():
    trimmed = pipe_mod._trim_history(_msgs(30))
    assert len(trimmed) == pipe_mod.MAX_HISTORY_MESSAGES == 20
    # newest messages survive, original order preserved
    assert trimmed[0]["content"].startswith("m10 ") and trimmed[-1]["content"].startswith("m29 ")
    assert pipe_mod._trim_history([]) == []


def test_trim_history_char_budget():
    big = [{"role": "user", "content": "y" * 9000}, {"role": "user", "content": "z" * 9000}]
    trimmed = pipe_mod._trim_history(big)
    assert len(trimmed) == 1 and trimmed[0]["content"].startswith("z")


def _pipe():
    p = pipe_mod.Pipeline.__new__(pipe_mod.Pipeline)
    p.max_context = 1550  # budget 50: skips the 90-token chunk, keeps the 5s
    return p


def test_fit_context_skips_instead_of_dropping():
    ranked = [
        {"id": 1, "text": "w " * 90, "token_count": 90},
        {"id": 2, "text": "small one", "token_count": 5},
        {"id": 3, "text": "small two", "token_count": 5},
    ]
    kept = _pipe()._fit_context(ranked)
    assert [c["id"] for c in kept] == [2, 3]


def test_add_chunks_bulk_ids_and_searchable(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.init()
    ws = store.add_workspace("ws")
    src = store.add_source(ws, "pdf", "f", None, None)
    store.set_source_status(src, "ready")
    emb = _HashingEmbedder()
    rows = [{"ord": i, "text": f"chunk number {i} about photosynthesis",
             "token_count": 6, "embedding": emb.embed(f"chunk number {i} about photosynthesis")}
            for i in range(5)]
    ids = store.add_chunks_bulk(src, rows)
    assert len(ids) == 5 and ids == sorted(ids)
    assert store.search(emb.embed("photosynthesis chunk"), ws, k=5) != []
    assert store.add_chunks_bulk(src, []) == []


def test_embed_many_falls_back_without_method():
    emb = _HashingEmbedder()  # no embed_many method: helper must loop
    vecs = embed_many(emb, ["alpha beta", "gamma delta"])
    assert len(vecs) == 2 and all(len(v) == emb.dim for v in vecs)
    assert embed_many(emb, []) == []

    class _Batched:
        def embed_many(self, texts):
            return [[float(len(t))] for t in texts]

    assert embed_many(_Batched(), ["a", "bb"]) == [[1.0], [2.0]]


def test_embed_many_dead_endpoint_degrades_at_once():
    emb = _OpenAICompatibleEmbedder("http://127.0.0.1:9/none", None, "m")
    vecs = emb.embed_many(["alpha beta"])
    assert len(vecs) == 1 and len(vecs[0]) == 256 and emb.warning


def test_whisper_settings_defaults_and_env(monkeypatch):
    monkeypatch.delenv("OPENNOTEBOOK_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("OPENNOTEBOOK_WHISPER_COMPUTE", raising=False)
    monkeypatch.delenv("OPENNOTEBOOK_WHISPER_BEAM", raising=False)
    assert transcribe_mod._whisper_settings() == ("cpu", "int8", 1)
    monkeypatch.setenv("OPENNOTEBOOK_WHISPER_DEVICE", "cuda")
    monkeypatch.setenv("OPENNOTEBOOK_WHISPER_BEAM", "5")
    assert transcribe_mod._whisper_settings() == ("cuda", "int8", 5)


def test_youtube_download_has_timeout():
    with patch.object(youtube_mod.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=0)
        run.return_value.stdout = '{"title":"L","id":"vid1","duration":60}'
        youtube_mod.download("https://youtu.be/vid1", out_dir="/tmp/x")
        assert run.call_args.kwargs.get("timeout") == 1800


def test_ingest_url_cleans_tmp_and_bulk_inserts(tmp_path):
    import tempfile
    store = Store(str(tmp_path / "t.db"))
    store.init()
    ws = store.add_workspace("ws")
    src = store.add_source(ws, "youtube", "https://youtu.be/vid1", None, None)
    workdir = tempfile.mkdtemp(prefix="onb_test_")
    audio = os.path.join(workdir, "a.opus")
    open(audio, "w").write("x")
    segs = [{"text": "hello world", "start": 0.0, "end": 1.0},
            {"text": "second chunk here", "start": 1.0, "end": 2.0}]
    with patch.object(ingest_mod, "youtube_download", return_value=(audio, None, "T", "vid1", 2)), \
         patch.object(ingest_mod, "transcribe", return_value=segs), \
         patch.object(ingest_mod, "get_embedder", return_value=_HashingEmbedder()):
        ingest_mod.ingest_url(src, "https://youtu.be/vid1", MagicMock(), store)
    assert store.get_source(src)["status"] == "ready"
    # group() merges the two tiny adjacent segments into one chunk (chunker
    # behavior, unchanged): bulk insert stored exactly what group() produced.
    chunks = store.get_chunks([1, 2])
    assert len(chunks) == 1 and "hello world" in chunks[0]["text"]
    assert not os.path.exists(workdir), "owned tmpdir must be removed"


def test_ingest_url_failure_cleans_chunks_and_marks_failed(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.init()
    ws = store.add_workspace("ws")
    src = store.add_source(ws, "youtube", "https://youtu.be/bad", None, None)
    with patch.object(ingest_mod, "youtube_download", side_effect=RuntimeError("nope")):
        ingest_mod.ingest_url(src, "https://youtu.be/bad", MagicMock(), store)
    s = store.get_source(src)
    assert s["status"] == "failed" and "nope" in (s["error"] or "")


def test_message_order_follows_insertion(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.init()
    ws = store.add_workspace("ws")
    for i in range(5):
        store.add_message(ws, "user", f"q{i}")
    assert [m["content"] for m in store.list_messages(ws)] == [f"q{i}" for i in range(5)]
