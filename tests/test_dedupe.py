"""Duplicate-source guard: same URL twice yields one source, one ingest."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import api as api_mod
from backend.config import AppConfig
from backend.grounding.embedder import _HashingEmbedder
from backend.ingestion.youtube import extract_id
from backend.store import Store


def test_extract_id_forms():
    assert extract_id("https://youtu.be/vid123") == "vid123"
    assert extract_id("https://www.youtube.com/watch?v=vid123&t=10s") == "vid123"
    assert extract_id("https://www.youtube.com/embed/vid123") == "vid123"
    assert extract_id("https://www.youtube.com/shorts/vid123") == "vid123"
    assert extract_id("https://example.com/novideo") is None
    assert extract_id("") is None


def _client(tmp_path):
    import os
    store = Store(os.path.join(str(tmp_path), "d.db"))
    store.init()
    app = api_mod.build_app(store, AppConfig())
    prev = (api_mod._STORE, api_mod._CFG)
    c = TestClient(app)
    return c, store, prev


def test_duplicate_url_returns_existing_source(tmp_path):
    c, store, prev = _client(tmp_path)
    try:
        ws = c.post("/api/workspaces", json={"title": "w"}).json()["id"]
        url = "https://youtu.be/dupvid"
        with patch.object(api_mod, "ingest_url") as ing, \
             patch("backend.grounding.embedder.get_embedder", return_value=_HashingEmbedder()):
            first = c.post(f"/api/workspaces/{ws}/sources", json={"url": url})
            second = c.post(f"/api/workspaces/{ws}/sources", json={"url": url})
        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["source_id"] == second.json()["source_id"]
        # exactly one source row, one ingest kicked off
        assert len(store.list_sources(ws)) == 1
        assert ing.call_count == 1
    finally:
        api_mod._STORE, api_mod._CFG = prev
