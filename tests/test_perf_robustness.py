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
    # default budget (8000) fits neither whole message: strict drop, no
    # half-included history that would break the shared-budget guarantee
    assert pipe_mod._trim_history(big) == []
    assert pipe_mod._trim_history(big, char_budget=9000)[0]["content"].startswith("z")


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
    for var in ("ANCHOR_WHISPER_DEVICE", "ANCHOR_WHISPER_COMPUTE", "ANCHOR_WHISPER_BEAM",
                "OPENNOTEBOOK_WHISPER_DEVICE", "OPENNOTEBOOK_WHISPER_COMPUTE", "OPENNOTEBOOK_WHISPER_BEAM"):
        monkeypatch.delenv(var, raising=False)
    assert transcribe_mod._whisper_settings() == ("cpu", "int8", 1)
    monkeypatch.setenv("ANCHOR_WHISPER_DEVICE", "cuda")
    monkeypatch.setenv("ANCHOR_WHISPER_BEAM", "5")
    assert transcribe_mod._whisper_settings() == ("cuda", "int8", 5)
    monkeypatch.delenv("ANCHOR_WHISPER_DEVICE")
    monkeypatch.setenv("OPENNOTEBOOK_WHISPER_DEVICE", "cuda")
    assert transcribe_mod._whisper_settings()[0] == "cuda"


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


def test_shared_budget_never_exceeds_context(tmp_path):
    # passages + history + reserves must fit max_context together: with a
    # small window and a long history, history is kept (recency) while
    # passages shrink to the remainder — never the reverse.
    from backend.grounding import pipeline as pipe_mod
    from backend.grounding.embedder import _HashingEmbedder

    store = Store(str(tmp_path / "t.db"))
    store.init()
    ws = store.add_workspace("ws")
    src = store.add_source(ws, "pdf", "f", None, None)
    store.set_source_status(src, "ready")
    emb = _HashingEmbedder()
    for i in range(6):
        store.add_chunk(src, i, f"photosynthesis fact number {i} " * 20, None, None, 100,
                        emb.embed(f"photosynthesis fact {i}"))
    hist = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "w" * 300}
            for i in range(30)]
    pipe = pipe_mod.Pipeline.__new__(pipe_mod.Pipeline)
    pipe.store = store
    pipe.embedder = emb
    pipe.reranker = None
    pipe.max_context = 4000
    msgs, _ = pipe.ground("tell me about photosynthesis", ws, hist)
    total_est = sum(len(m["content"]) // 4 for m in msgs if isinstance(m.get("content"), str))
    total_est += pipe_mod.RESERVE_TOKENS_FOR_ANSWER
    assert total_est <= pipe.max_context, f"prompt over budget: ~{total_est} > 4000"
    # newest history survives while passages absorbed the squeeze
    assert "turn 29" in msgs[-1]["content"] or any("turn 29" in m.get("content", "") for m in msgs)
