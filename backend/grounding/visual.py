"""Visual grounding over PDF pages: render relevant pages to images and let a
multimodal model "see" them, plus best-effort annotation directives.

Annotation contract: the model may emit zero or more tags of the form
    <annotate page="N" x="0..1" y="0..1" w="0..1" h="0..1" label="..."/>
Coordinates are fractions of the page image (0..1, origin top-left).
Invalid/missing tags are dropped — annotations are strictly best-effort.
"""
from __future__ import annotations
import base64
import logging
import re
from pathlib import Path

from backend.config import AppConfig
from backend.store import Store
from backend.grounding.pipeline import Pipeline
from backend.ingestion import pdfrender

logger = logging.getLogger(__name__)

VISION_SYSTEM = (
    "You are Anchor, a study assistant that can SEE the page images of a PDF the "
    "student is studying. You are given a page image along with grounded text extracted "
    "from the study material and the student's question. Answer the question clearly, "
    "citing [n] for the grounded passages, and explain concepts directly on the visual "
    "(diagrams, equations, figures) when the page image shows them.\n"
    "You MUST cite your sources inline using [1], [2], etc. corresponding to the "
    "passage numbers in the SOURCE EVIDENCE block. Every factual claim from the "
    "evidence needs a citation. If you refer to something visible on the page image, "
    "also cite the relevant passage number.\n"
    "When the page image shows something you are explaining, you MUST also point at it "
    "with one or more annotation tags placed on their own line AFTER the explanation, "
    "each exactly like this (mind the quotes and = signs):\n"
    "<annotate page=\"1\" x=\"0.12\" y=\"0.30\" w=\"0.5\" h=\"0.08\" label=\"the integral here\"/>\n"
    "Rules: x,y is the top-left corner and w,h is the size, all as 0..1 fractions of the "
    "image. Only annotate a region you can actually see, keep labels short, and do not "
    "guess coordinates deeper than the visual truth. If nothing on the page is worth "
    "pointing at, emit NO tags. You must not use markdown backticks around the tag.\n"
    "Draw on several different passages of the source evidence and cite each one you "
    "use — an answer that leans on a single passage is incomplete when more evidence "
    "is provided."
)

# How many grounded passages the vision model actually sees. Small vision models
# follow citation instructions far better over short evidence, so this stays low
# even though the text path uses the full context budget. The passages are
# already interleaved across sources by the pipeline, so the top-K span them.
VISION_EVIDENCE_K = 6


ANNOT_TAG = re.compile(
    r"<annotate\s+page\s*=\s*[\"']?(\d+)[\"']?\s*"
    r"x\s*=\s*[\"']?([0-9.]+)[\"']?\s*"
    r"y\s*=\s*[\"']?([0-9.]+)[\"']?\s*"
    r"w\s*=\s*[\"']?([0-9.]+)[\"']?\s*"
    r"h\s*=\s*[\"']?([0-9.]+)[\"']?\s*"
    r"(?:\s+label\s*=\s*[\"']([^\"']*)[\"'])?[\"']?\s*/?>",
    re.IGNORECASE,
)


def _img_b64(path: Path) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def parse_annotations(text: str) -> list[dict]:
    """Return [(page, x, y, w, h, label), ...] with clamped 0..1 fractions."""
    out = []
    for m in ANNOT_TAG.finditer(text):
        page = int(m.group(1))
        vals = []
        ok = True
        for g in (2, 3, 4, 5):
            v = float(m.group(g))
            if not (0.0 <= v <= 1.0):
                ok = False
                break
            vals.append(v)
        if not ok:
            continue
        out.append({
            "page": page, "x": vals[0], "y": vals[1],
            "w": vals[2], "h": vals[3], "label": m.group(6) or "",
        })
    return out


def strip_annotations(text: str) -> str:
    return ANNOT_TAG.sub("", text).strip()


class VisualPipeline:
    def __init__(self, store: Store, cfg: AppConfig, base: Pipeline | None = None):
        self.store = store
        self.cfg = cfg
        self.base = base or Pipeline(store, cfg)

    def answer(self, query: str, workspace_id: int,
               chat_history: list[dict] | None = None) -> dict:
        """Return {answer, citations, annotations, page, source_title}."""
        from backend.config import data_dir as _dir
        from backend.llm_client import get_llm

        msgs, cmap = self.base.ground(query, workspace_id, chat_history or [])
        llm = get_llm(self.cfg)

        # Shrink the evidence to what a small vision model can actually follow:
        # keep the top interleaved passages (already spread across sources) and
        # renumber them 1..M, so the map the client receives matches exactly what
        # the model saw. Without this the model gets ~20 passages plus an image
        # and answers from its own weights, citing nothing.
        ordered = [cmap[k] for k in sorted(cmap.keys())][:VISION_EVIDENCE_K]
        if ordered and len(ordered) < len(cmap):
            temp_chunks = [{
                "id": pc["chunk_id"], "source_id": pc["source_id"],
                "text": pc["text"], "start_sec": pc.get("start_sec"),
                "end_sec": pc.get("end_sec"), "page_num": pc.get("page_num"),
            } for pc in ordered]
            msgs, cmap = self.base._build_prompt(query, temp_chunks, chat_history or [])
        if isinstance(msgs[-1].get("content"), str):
            # Trailing reminder: models obey instructions at the end of the
            # prompt far more reliably than ones buried in the system message.
            msgs[-1]["content"] += (
                "\n\nCite the passages you rely on inline as [#], drawing on "
                "several different passages — not just one."
            )

        # Pick a PDF source from the cited passages (most relevant first = lowest passage number)
        # and grab a page to visualize.
        page_img_b64 = None
        shown = None
        for passage_num in sorted(cmap.keys()):
            pc = cmap[passage_num]
            if pc.get("page_num") is None:
                continue
            s = self.store.get_source(pc["source_id"])
            if not s or s.get("type") != "pdf" or not s.get("file_path"):
                continue
            if not Path(s["file_path"]).exists():
                continue
            try:
                img = pdfrender.render_page(_dir(self.cfg), s["id"], s["file_path"], pc["page_num"])
            except Exception:
                continue
            page_img_b64 = _img_b64(img)
            shown = {"page_num": pc["page_num"], "source_id": s["id"],
                     "source_title": s.get("title") or "PDF"}
            break

        if page_img_b64 is None:
            # No PDF page could be rendered -> normal grounded text answer.
            full = list(llm.stream(msgs))
            return {"answer": "".join(full), "citations": cmap,
                    "annotations": [], "page": None, "source_title": None}

        # Only call the vision model if one is explicitly configured. Sending an image to a
        # non-vision model (e.g. the standard chat model) raises an API error and 500s the
        # request, so we hard-guard against it and degrade to text instead.
        if not self.cfg.llm.vision_model:
            logger.warning("No vision_model configured; answering PDF question as text.")
            full = []
            for delta in llm.stream(msgs):
                full.append(delta)
            return {"answer": "".join(full), "citations": cmap,
                    "annotations": [], "page": None, "source_title": None}

        prompt = list(msgs)
        prompt[0] = {"role": "system", "content": VISION_SYSTEM}
        base_text = prompt[-1].get("content", "")
        if isinstance(base_text, str):
            prompt[-1] = {"role": "user", "content": base_text}
        try:
            raw = llm.vision(prompt, page_image_b64=page_img_b64, max_tokens=1400)
        except Exception as e:
            # Vision call failed (bad/non-vision model, rate limit, API error). Never 500:
            # degrade to a normal grounded text answer so the user still gets help.
            logger.warning("vision call failed, falling back to text answer: %s", e)
            full = []
            for delta in llm.stream(msgs):
                full.append(delta)
            return {"answer": "".join(full), "citations": cmap,
                    "annotations": [], "page": None, "source_title": None}
        annotations = parse_annotations(raw)
        answer = strip_annotations(raw)
        # The vision model sees the shown page; pin every annotation to it so the frontend
        # always overlays boxes on the page image it actually displays (not a guessed page).
        for a in annotations:
            a["page"] = shown["page_num"]
        # Never fabricate citation markers: if the model cited nothing, the answer is
        # shown without citation tokens rather than a made-up "[1]" that would point
        # at an arbitrary passage (usually the video intro at 0:00).
        return {"answer": answer, "citations": cmap,
                "annotations": annotations, "page": shown}


if __name__ == "__main__":
    import os
    import tempfile
    os.environ["OPENNOTEBOOK_CONFIG_DIR"] = tempfile.mkdtemp()
    from backend.grounding.pipeline import Pipeline
    pipes = ["parse: " + str(len(parse_annotations(
        'x <annotate page="1" x="0.1" y="0.2" w="0.3" h="0.1" label="the box"/> y '
        '<annotate page=1 x=0.4 y=0.5 w=0.6 h=0.7/> bad <annotate page="9" x="2" y="0" w="0" h="0"/>'))),
        "strip: " + strip_annotations('hello <annotate page="1" x="0" y="0" w="0" h="0" label="l"/> world')]
    for p in pipes:
        print("visual", p)
    assert len(parse_annotations('a <annotate page="1" x="0.1" y="0.2" w="0.3" h="0.1" label="x"/> b')) == 1
    assert len(parse_annotations('<annotate page="9" x="2" y="0" w="0" h="0"/>')) == 0, "out-of-range coords dropped"

    # evidence trim: 10 grounded passages -> the vision model sees the top 6
    # renumbered 1..6, and the returned map matches exactly what it saw (plus no
    # phantom citations are ever injected into a citation-less answer).
    from pathlib import Path as _Path
    from unittest.mock import patch as _patch
    from backend.config import load_config as _load_config
    from backend.store import Store as _Store
    _vdb = os.path.join(tempfile.mkdtemp(), "v.db")
    _vstore = _Store(_vdb)
    _vstore.init()
    _vws = _vstore.add_workspace("Viz", None)
    _vpdf = os.path.join(tempfile.mkdtemp(), "page.png")
    open(_vpdf, "wb").write(b"\x89PNG\r\n\x1a\nfakepng")
    _vsrc = _vstore.add_source(_vws, "pdf", "Notes", None, None)
    _vstore.conn.execute("UPDATE sources SET file_path=? WHERE id=?", (_vpdf, _vsrc))
    _vstore.conn.commit()
    _vch = _vstore.add_chunk(_vsrc, 0, "some pdf text", None, None, 1, [0.1] * 8)
    _big_cmap = {i: {"passage": i, "chunk_id": _vch, "source_title": "Notes",
                     "source_id": _vsrc, "start_sec": None, "end_sec": None,
                     "page_num": 1, "text": f"evidence piece {i}"} for i in range(1, 11)}

    class _FakeBase:
        """Duck-typed Pipeline: canned ground(), but the REAL _build_prompt so
        renumbering is genuinely exercised (no embedder download needed)."""
        def __init__(self, store):
            self.store = store
        def ground(self, *a, **k):
            return ([{"role": "system", "content": "s"},
                     {"role": "user", "content": "stub evidence"}],
                    dict(_big_cmap))
        def _build_prompt(self, query, kept, hist):
            return Pipeline._build_prompt(self, query, kept, hist)

    _vcfg = _load_config()
    _vcfg.llm.vision_model = "test-vision"
    _seen = {}
    def _fake_vision(self, messages, page_image_b64=None, model=None, **params):
        _seen["prompt"] = messages
        return "trimmed answer citing several passages [1] and [2]"
    with _patch("backend.ingestion.pdfrender.render_page", return_value=_Path(_vpdf)), \
         _patch("backend.llm_client.LLMClient.vision", _fake_vision):
        _out = VisualPipeline(_vstore, _vcfg, base=_FakeBase(_vstore)).answer("q", _vws, [])
    assert set(_out["citations"].keys()) == {1, 2, 3, 4, 5, 6}, \
        f"map must match the trimmed evidence, got {sorted(_out['citations'])}"
    _prompt_text = " ".join(m["content"] for m in _seen["prompt"] if isinstance(m.get("content"), str))
    assert "[6]" in _prompt_text and "[7]" not in _prompt_text, \
        "model must see exactly passages 1..6"
    assert "Cite the passages" in _prompt_text, "trailing citation reminder must be present"
    assert "[1]" in _out["answer"] and "[7]" not in _out["answer"], \
        "model-written citations survive; nothing phantom is added"

    # citation-less vision answer: returned verbatim, no "[1]" injected.
    def _quiet_vision(self, messages, page_image_b64=None, model=None, **params):
        return "a generic answer with no citations at all."
    with _patch("backend.ingestion.pdfrender.render_page", return_value=_Path(_vpdf)), \
         _patch("backend.llm_client.LLMClient.vision", _quiet_vision):
        _out2 = VisualPipeline(_vstore, _vcfg, base=_FakeBase(_vstore)).answer("q", _vws, [])
    assert "[1]" not in _out2["answer"], "must never fabricate a citation marker"
    print("visual OK")
