"""Retrieve top-N chunks via sqlite-vec cosine search."""
from __future__ import annotations
from backend.store import Store
from backend.grounding.embedder import Embedder


def retrieve(query: str, workspace_id: int, store: Store, embedder: Embedder, k: int = 20) -> list[int]:
    qv = embedder.embed(query)
    return store.search(qv, workspace_id, k=k)


if __name__ == "__main__":
    import tempfile
    import os
    # embedder override: install the hashing fallback so no heavy deps are needed for this check.
    os.environ["OPENNOTEBOOK_CONFIG_DIR"] = tempfile.mkdtemp()

    from backend.store import Store
    from backend.grounding.embedder import _HashingEmbedder

    db = os.path.join(tempfile.mkdtemp(), "t.db")
    store = Store(db)
    store.init()
    emb = _HashingEmbedder()
    # Inject 3 chunks whose texts differ; with stable hashing, the hashtag tokens differ.
    ws = store.add_workspace("L3")
    s = store.add_source(ws, "youtube", "Calc", "v", 600)
    store.set_source_status(s, "ready")
    # Use distinct sets of words so each chunk's hashing-vector is distinct enough to be retrieved by overlap.
    target_text = "limit definition epsilon delta"
    chunk_a = store.add_chunk(s, 0, target_text, 0, 30, 6, emb.embed(target_text))
    store.add_chunk(s, 1, "intermediate value theorem continuous functions", 60, 90, 6,
                    emb.embed("intermediate value theorem continuous functions"))
    store.add_chunk(s, 2, "exponential growth examples", 120, 150, 4,
                    emb.embed("exponential growth examples"))
    hits = retrieve(target_text, ws, store, emb, k=3)
    assert hits, "must retrieve something"
    # First hit must be the chunk whose hash-vector best overlaps the query vector.
    assert chunk_a == hits[0], f"top hit should be the matching chunk, got {hits} (chunk_a={chunk_a})"
    print("retriever OK")
