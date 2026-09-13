"""Ingestion orchestrator (spec §6): download → transcribe → chunk → embed → store.
Updates sources.status throughout; isolates per-source errors so one bad URL never
kills the process (spec §12 caveat 4)."""
from __future__ import annotations

from backend.config import AppConfig
from backend.store import Store
from backend.ingestion import youtube as youtube_mod
from backend.ingestion import transcribe as transcribe_mod
from backend.grounding.chunker import group
from backend.grounding.embedder import get_embedder, embed_many

# friendly aliases the self-check patches
youtube_download = youtube_mod.download
transcribe = transcribe_mod.transcribe


def ingest_url(source_id: int, url: str, cfg: AppConfig, store: Store) -> None:
    tmpdir: str | None = None
    try:
        store.set_source_status(source_id, "downloading")
        audio_path, vtt_path, title, yid, duration = youtube_download(url)
        # download() owns its out_dir only when it created it (mkdtemp onb_*
        # prefix); caller-supplied dirs (tests, custom flows) are left alone.
        import os as _os
        cand = _os.path.dirname(_os.path.abspath(audio_path))
        if _os.path.basename(cand).startswith("onb_"):
            tmpdir = cand
        store.set_source_status(source_id, "transcribing")
        if vtt_path:
            segments = transcribe_mod.parse_vtt(vtt_path)
        else:
            segments = transcribe(audio_path, cfg)
        store.set_source_status(source_id, "chunking")
        chunks = group(segments)
        emb = get_embedder(cfg)
        # One batched embedding call + one bulk insert (single commit) instead
        # of per-chunk embed/commit: hundreds of chunks per lecture otherwise
        # means hundreds of fsyncs and model calls.
        vecs = embed_many(emb, [c.text for c in chunks])
        store.add_chunks_bulk(source_id, [
            {"ord": i, "text": c.text, "start_sec": int(c.start_sec),
             "end_sec": int(c.end_sec), "token_count": c.token_count,
             "embedding": vecs[i]}
            for i, c in enumerate(chunks)
        ])
        # Update the source with the extracted metadata
        store.conn.execute(
            "UPDATE sources SET title=?, youtube_id=?, duration_sec=? WHERE id=?",
            (title, yid, duration, source_id)
        )
        store.conn.commit()
        store.set_source_status(source_id, "ready")
    except Exception as e:
        # A failed source must not leave retrievable partial chunks behind:
        # search() only returns 'ready' sources, but deleting here keeps the
        # index clean and honors the failed status the UI shows.
        try:
            store.delete_source_chunks(source_id)
        except Exception:
            pass
        store.set_source_status(source_id, "failed", error=str(e))
    finally:
        # Every ingested video leaks audio+VTT into %TEMP% forever without this.
        if tmpdir:
            import shutil as _shutil
            try:
                _shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass


async def _run(source_id: int, url: str, cfg: AppConfig, store: Store) -> None:
    """Async wrapper for api.py to call via asyncio.to_thread."""
    import asyncio
    await asyncio.to_thread(ingest_url, source_id, url, cfg, store)

if __name__ == "__main__":
    import tempfile
    import os
    import asyncio
    from unittest.mock import patch
    from backend.config import load_config
    from backend.store import Store
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    store = Store(db)
    store.init()
    cfg = load_config()
    # Mock to use hashing embedder
    from backend.config import Section
    cfg.embeddings = Section(provider="bundled")
    ws = store.add_workspace("L3")
    s = store.add_source(ws, "youtube", "Calc", "v", 600)
    
    fake_segs = [{"text": "Hello world.", "start": 0.0, "end": 1.5},
                 {"text": "Defining the limit.", "start": 2.0, "end": 4.0}]
    
    with patch("__main__.youtube_download", return_value=("/tmp/a.opus", None, "Calc", "v", 600)), \
         patch("__main__.transcribe", return_value=fake_segs), \
         patch("__main__.get_embedder") as mock_get_emb:
        from backend.grounding.embedder import _HashingEmbedder
        mock_get_emb.return_value = _HashingEmbedder()
        asyncio.run(_run(source_id=s, url="https://youtu.be/v", cfg=cfg, store=store))
        
    assert store.get_source(s)["status"] == "ready"
    assert len(store.list_sources(ws)) == 1
    print("ingest OK")
