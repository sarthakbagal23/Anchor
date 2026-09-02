"""Render PDF pages to PNG images (for the vision model + source-preview) and
expose the raw PDF file path. Uses PyMuPDF (fitz) for rendering.

Original PDFs are stored on disk at upload time (see api.upload_source). This
module produces per-page PNG thumbnails and caches them under the data dir.
"""
from __future__ import annotations
import os
from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF (new name; `fitz` is the deprecated alias)
except ImportError:  # pragma: no cover
    try:
        import fitz
    except ImportError:
        fitz = None


def page_count(pdf_path: str | os.PathLike) -> int:
    if fitz is None:
        raise RuntimeError("PyMuPDF not installed; cannot render PDF pages")
    with fitz.open(str(pdf_path)) as doc:
        return doc.page_count


def page_image_path(data_dir: Path, source_id: int, page_num: int) -> Path:
    d = data_dir / "pages" / str(source_id)
    return d / f"p{page_num}.png"


def render_page(data_dir: Path, source_id: int, file_path: str, page_num: int,
                zoom: float = 2.0) -> Path:
    """Render one 1-based page to a cached PNG; returns the PNG path."""
    out = page_image_path(data_dir, source_id, page_num)
    if out.exists():
        return out
    if fitz is None:
        raise RuntimeError("PyMuPDF not installed; cannot render PDF pages")
    out.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open(file_path) as doc:
        if page_num < 1 or page_num > doc.page_count:
            raise IndexError(f"page {page_num} out of range (1..{doc.page_count})")
        pix = doc[page_num - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        pix.save(str(out))
    return out
