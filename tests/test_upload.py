"""Upload path: early size cap while streaming, valid PDFs still ingest."""
import io
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import api as api_mod
from backend.config import AppConfig
from backend.grounding.embedder import _HashingEmbedder
from backend.store import Store


def _client(tmp_path):
    import os
    store = Store(os.path.join(str(tmp_path), "u.db"))
    store.init()
    app = api_mod.build_app(store, AppConfig())
    prev = (api_mod._STORE, api_mod._CFG)
    return TestClient(app), store, prev


def _tiny_pdf_bytes():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (Sampling bias matters) Tj ET")
    page[NameObject("/Contents")] = stream
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_oversize_upload_rejected_early(tmp_path):
    c, _, prev = _client(tmp_path)
    try:
        ws = c.post("/api/workspaces", json={"title": "w"}).json()["id"]
        big = b"x" * (27 * 1024 * 1024)
        r = c.post(f"/api/workspaces/{ws}/uploads", files={"file": ("big.pdf", big)})
        assert r.status_code == 413, r.status_code
    finally:
        api_mod._STORE, api_mod._CFG = prev


def test_tiny_pdf_upload_accepted(tmp_path):
    c, store, prev = _client(tmp_path)
    try:
        ws = c.post("/api/workspaces", json={"title": "w"}).json()["id"]
        pdf = _tiny_pdf_bytes()
        with patch("backend.ingestion.document.get_embedder", return_value=_HashingEmbedder()), \
             patch("backend.grounding.embedder.get_embedder", return_value=_HashingEmbedder()):
            r = c.post(f"/api/workspaces/{ws}/uploads", files={"file": ("notes.pdf", pdf)})
        assert r.status_code == 200, r.text
        assert r.json()["filename"] == "notes.pdf"
        assert len(store.list_sources(ws)) == 1
    finally:
        api_mod._STORE, api_mod._CFG = prev
