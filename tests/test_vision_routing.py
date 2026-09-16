"""Vision-routing rule: the vision path triggers on the question's own grounded
evidence (a page_num in the top passages), never on workspace composition."""
from unittest.mock import patch

from backend.config import AppConfig, LLMSection
from backend.grounding.visual import VisualPipeline, evidence_anchors_pdf
from backend.store import Store


def _cmap(*pages):
    return {i + 1: {"chunk_id": i, "page_num": p} for i, p in enumerate(pages)}


def test_empty_evidence_is_text():
    assert evidence_anchors_pdf({}) is False
    assert evidence_anchors_pdf(None) is False


def test_video_only_evidence_is_text():
    assert evidence_anchors_pdf(_cmap(None, None, None)) is False


def test_pdf_anchor_at_top_is_vision():
    # only passage [1] counts: it is always the relevance winner, while
    # positions 2+ are interleave order, not relevance order (a barely-related
    # chunk can sit at [2] above far better matches).
    assert evidence_anchors_pdf(_cmap(4, None, None)) is True


def test_pdf_below_top_stays_text():
    assert evidence_anchors_pdf(_cmap(None, None, 7)) is False
    assert evidence_anchors_pdf(_cmap(None, None, None, None, 9)) is False
    assert evidence_anchors_pdf(_cmap(None, 7, None), top_n=2) is True


class _FakeBase:
    """Stand-in for Pipeline: canned ground(), no embedder/weights/network."""

    def __init__(self, msgs, cmap):
        self._msgs, self._cmap = msgs, cmap

    def ground(self, *a, **k):
        return self._msgs, self._cmap


def test_no_vision_model_answers_text_without_error(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.init()
    ws = store.add_workspace("ws")
    src = store.add_source(ws, "pdf", "notes.pdf", None, None)
    store.set_source_status(src, "ready")
    real_pdf = tmp_path / "notes.pdf"
    real_pdf.write_bytes(b"%PDF-1.4 fake")
    store.set_source_file_path(src, str(real_pdf))
    msgs = [{"role": "user", "content": "q"}]
    cmap = {1: {"chunk_id": 5, "source_id": src, "page_num": 2, "text": "t"}}
    cfg = AppConfig(llm=LLMSection(base_url="http://x/v1", model="m", vision_model=None))

    class _FakeLLM:
        def stream(self, messages):
            return iter(["plain ", "text"])

    vp = VisualPipeline.__new__(VisualPipeline)
    vp.store, vp.cfg, vp.base = store, cfg, _FakeBase(msgs, cmap)
    with patch("backend.llm_client.get_llm", return_value=_FakeLLM()), \
         patch("backend.grounding.visual.pdfrender.render_page", return_value="/tmp/p.pdf"), \
         patch("backend.grounding.visual._img_b64", return_value="AAA"):
        out = vp.answer("q", ws, [])
    assert out["answer"] == "plain text" and out["page"] is None
    assert out["annotations"] == []
