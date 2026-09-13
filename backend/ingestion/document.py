"""PDF text extraction for the first non-YouTube source milestone."""
from __future__ import annotations

import io
from pathlib import PurePath

from backend.config import AppConfig
from backend.grounding.embedder import get_embedder, embed_many
from backend.store import Store

SUPPORTED = {".pdf"}


def extract_pdf_pages(filename: str, data: bytes) -> list[tuple[int, str]]:
    if PurePath(filename).suffix.lower() not in SUPPORTED:
        raise ValueError("PDFs are the only document source supported in this step.")
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF support needs pypdf. Install the project dependencies again.") from exc
    reader = PdfReader(io.BytesIO(data))
    return [(number, page.extract_text() or "") for number, page in enumerate(reader.pages, start=1)]


def extract_pdf_text(filename: str, data: bytes) -> str:
    return "\n\n".join(text for _, text in extract_pdf_pages(filename, data)).strip()


def _chunks(text: str, max_words: int = 300) -> list[str]:
    words = text.split()
    return [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]


def _page_maps(pdf_path: str) -> dict[int, dict[int, tuple[float, int]]]:
    """Best-effort: per page, an ordered list of (y0_norm, end_char) landmarks derived
    from PyMuPDF text blocks, used to estimate a chunk's vertical position on the page.
    Returns {page_num: [(y0_fraction, cum_char_count), ...]}."""
    import pymupdf
    out: dict[int, dict[int, tuple[float, int]]] = {}
    with pymupdf.open(pdf_path) as doc:
        for pno in range(doc.page_count):
            page = doc[pno]
            blocks = page.get_text("blocks")
            pos: dict[int, tuple[float, int]] = {}
            i = 0  # running char offset as blocks come in order
            for b in blocks:
                x0, y0, x1, y1, text, *_ = b
                h = page.rect.height or 1
                pos[i] = (y0 / h, len(text))
                i += len(text)
            out[pno + 1] = pos
    return out


def _chunk_y(pmap: dict[int, tuple[float, int]], page_num: int, start_char: int) -> float | None:
    """Given per-page block landmarks, return the y0 fraction for a char offset (clamped)."""
    landmarks = pmap.get(page_num)
    if not landmarks:
        return None
    best_y, best_char = None, -1
    for c, (y0, length) in landmarks.items():
        if c <= start_char:
            if c >= best_char:
                best_char, best_y = c, y0
    return best_y


def ingest_pdf(source_id: int, filename: str, data: bytes, cfg: AppConfig, store: Store,
               file_path: str | None = None) -> None:
    try:
        store.set_source_status(source_id, "extracting")
        pages = [(page, text.strip()) for page, text in extract_pdf_pages(filename, data) if text.strip()]
        if not pages:
            raise ValueError("No selectable text found in this PDF.")
        store.set_source_status(source_id, "chunking")
        embedder = get_embedder(cfg)
        # Precompute vertical landmarks once so we can attach y_top to each chunk.
        pmap = _page_maps(file_path) if file_path else {}
        pending: list[dict] = []
        for page_num, text in pages:
            start_char = 0
            for chunk in _chunks(text):
                y = _chunk_y(pmap, page_num, start_char) if pmap else None
                pending.append({"ord": len(pending), "text": chunk,
                                "token_count": len(chunk.split()),
                                "embedding": None, "page_num": page_num, "y_top": y})
                start_char = start_char + len(chunk)
        # One batched embedding call + one bulk insert (single commit).
        vecs = embed_many(embedder, [r["text"] for r in pending])
        for r, vec in zip(pending, vecs):
            r["embedding"] = vec
        store.add_chunks_bulk(source_id, pending)
        store.conn.execute("UPDATE sources SET title=? WHERE id=?", (filename, source_id))
        store.conn.commit()
        store.set_source_status(source_id, "ready")
    except Exception as exc:
        try:
            store.delete_source_chunks(source_id)
        except Exception:
            pass
        store.set_source_status(source_id, "failed", error=str(exc))


if __name__ == "__main__":
    import os
    import tempfile
    from unittest.mock import patch
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (Sampling bias matters) Tj ET")
    page[NameObject("/Contents")] = stream
    output = io.BytesIO()
    writer.write(output)
    assert extract_pdf_pages("notes.pdf", output.getvalue()) == [(1, "Sampling bias matters")]
    from backend.config import load_config
    from backend.grounding.embedder import _HashingEmbedder
    store = Store(os.path.join(tempfile.mkdtemp(), "pdf.db"))
    store.init()
    source_id = store.add_source(store.add_workspace("PDF test"), "pdf", "notes.pdf", None, None)
    with patch("__main__.get_embedder", return_value=_HashingEmbedder()):
        ingest_pdf(source_id, "notes.pdf", output.getvalue(), load_config(), store)
    assert store.get_source(source_id)["status"] == "ready"
    assert store.get_chunks([1])[0]["page_num"] == 1
    try:
        extract_pdf_text("notes.txt", b"not a PDF")
    except ValueError:
        print("document PDF OK")
    else:
        raise AssertionError("non-PDF files must fail in the PDF milestone")
