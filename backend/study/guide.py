"""Study Guide generator: turn a unit's learning objectives into a persisted,
source-grounded study guide.

For each objective on the workspace's unit we ground it against the workspace's
source chunks (same retrieval + context-fit + grounded-prompt machinery as chat,
via the grounding Pipeline) and ask the LLM to write a short study section. Each
section is saved to the study_guides / guide_sections tables together with its
citation map, so the guide is a persisted artifact the frontend can render (and
regenerate) later without re-running the LLM.
"""
from __future__ import annotations

from backend.config import AppConfig
from backend.grounding.citations import build_map
from backend.grounding.pipeline import Pipeline, _fmt_ts
from backend.llm_client import get_llm
from backend.store import Store

# How many chunks we ground each objective against (fits under the context budget).
RETRIEVE_K = 12

# An objective counts as "covered" (evidence present) when its best matching chunk
# reaches this cosine similarity (1 - sqlite-vec distance), the same threshold the
# CED coverage pass uses. Below it we treat the topic as not in the sources.
MIN_COVERAGE_SIMILARITY = 0.3

# User-facing reason when a unit has zero objectives. Extraction now runs
# automatically when a source finishes indexing, so this only happens when the
# unit has no ready sources yet (or extraction found nothing) — clients can
# show it verbatim instead of a generic connectivity error.
NO_OBJECTIVES_MSG = (
    "This unit has no learning objectives yet. Objectives are created "
    "automatically when a source finishes indexing — add a source, wait for "
    "it to be ready, then try again."
)


def _similarity(distance: float) -> float:
    return max(0.0, min(1.0, 1.0 - distance))

SYSTEM = (
    "You are Anchor, helping a student write a crisp study guide section for "
    "one learning objective. Base the section on the source evidence supplied below; "
    "cite the evidence inline as [#] where it supports a claim. If the source does not "
    "cover part of the objective, say so briefly and clearly rather than guessing. "
    "Keep it to a short paragraph or a few bullets that a student can review quickly. "
    "Do not repeat the objective as a heading."
)


class StudyGuideGenerator:
    def __init__(self, store: Store, cfg: AppConfig):
        self.store = store
        self.cfg = cfg
        self.pipeline = Pipeline(store, cfg)
        self.llm = get_llm(cfg)

    def _objectives_for_workspace(self, workspace_id: int) -> list[dict]:
        ws = self.store.get_workspace(workspace_id)
        if not ws or not ws.get("unit_id"):
            return []
        return self.store.list_unit_objectives(ws["unit_id"])

    def _ground_block(self, query: str, workspace_id: int, unit_id: int) -> tuple[str, dict, float]:
        """Retrieve + fit + assemble the 'SOURCE EVIDENCE' block and citation map
        for one objective query, scoped to the workspace's unit. Returns
        (block, cmap, top_similarity) where top_similarity is the best matching
        chunk's cosine similarity (used to decide whether the topic is covered)."""
        if not unit_id:
            return "", {}, 0.0
        qv = self.pipeline.embedder.embed(query)
        ranked = self.store.search_unit(qv, unit_id, k=RETRIEVE_K)  # (id, distance)
        top_similarity = _similarity(ranked[0][1]) if ranked else 0.0
        chunks = self.store.get_chunks([cid for cid, _ in ranked]) if ranked else []
        if self.pipeline.reranker and chunks:
            chunks = self.pipeline.reranker.rerank(query, chunks)
        kept = self.pipeline._fit_context(chunks)
        if not kept:
            return "", {}, top_similarity
        src_ids = {c["source_id"] for c in kept}
        sources = {s["id"]: s for s in [self.store.get_source(sid) for sid in src_ids] if s}
        blocks, passage_chunks = [], []
        for i, c in enumerate(kept, start=1):
            s = sources.get(c["source_id"], {})
            title = s.get("title") or "source"
            ts = _fmt_ts(c.get("start_sec"))
            header = f'[{i}] (Source: "{title}"'
            if ts:
                header += f", {ts}"
            header += ") "
            blocks.append(header + c["text"])
            passage_chunks.append({
                "passage": i,
                "chunk_id": c["id"],
                "source_title": title,
                "source_id": c["source_id"],
                "start_sec": c.get("start_sec"),
                "end_sec": c.get("end_sec"),
                "page_num": c.get("page_num"),
                "y_top": c.get("y_top"),
                "text": c["text"],
            })
        cmap = build_map(passage_chunks)
        return "\n\n".join(blocks), cmap, top_similarity

    def _write_section(self, objective: dict, workspace_id: int, unit_id: int) -> dict:
        title = objective["statement"]
        skill = objective.get("ced_skill_code")
        passages, cmap, top_similarity = self._ground_block(title, workspace_id, unit_id)
        covered = top_similarity >= MIN_COVERAGE_SIMILARITY and bool(passages)
        if not covered:
            body = (
                "No source chunks matched this objective yet — add material covering it "
                "and regenerate the guide. (Not covered in the sources.)"
            )
            return {"title": title, "skill_code": skill, "body": body, "citations": {}, "covered": False}
        user = (
            f"SOURCE EVIDENCE FROM THE STUDY MATERIAL:\n{passages}\n\n"
            f"LEARNING OBJECTIVE: {title}"
        )
        msgs = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ]
        body = (self.llm.complete(msgs, max_tokens=400) or "").strip()
        if not body:
            body = ("Source material was found for this objective, but the writer returned an "
                    "empty section. Try regenerating the guide.")
        return {"title": title, "skill_code": skill, "body": body, "citations": cmap, "covered": True}

    def _resolve(self, workspace_id: int) -> tuple[dict, int, list]:
        """Return (workspace, unit_id, objectives), raising ValueError for a
        missing workspace or a workspace with no unit."""
        ws = self.store.get_workspace(workspace_id)
        if not ws or not ws.get("unit_id"):
            raise ValueError("Workspace has no unit." if ws else "Workspace not found.")
        unit_id = ws["unit_id"]
        return ws, unit_id, self._objectives_for_workspace(workspace_id)

    def _prepare(self, workspace_id: int) -> tuple[int | None, list, int]:
        """Validate the workspace and return (guide_id, objectives, unit_id) or
        raise ValueError with a friendly message if no unit/objectives exist."""
        _, unit_id, objectives = self._resolve(workspace_id)
        if not objectives:
            raise ValueError(NO_OBJECTIVES_MSG)
        guide_id = self.store.create_study_guide(workspace_id, unit_id)
        self.store.set_guide_objective_count(guide_id, len(objectives))
        return guide_id, objectives, unit_id

    def generate_stream(self, workspace_id: int):
        """Generate a study guide yielding progress events as each section is ready.

        Yields (index, total, section) for each completed section (index 1-based),
        then one final (None, total, summary) event where summary is
        {guide, sections, uncovered} once every section is persisted. This lets a
        caller stream sections to the client one at a time instead of waiting for
        the whole unit (24-26 LLM calls) to finish.

        A unit with zero objectives is a normal user-facing state, not a crash:
        it yields a single terminal (None, 0, {guide: None, sections: [],
        uncovered: 0, error}) so SSE clients can show the real reason instead of
        misreading an abrupt stream end as a connectivity failure."""
        _, unit_id, objectives = self._resolve(workspace_id)
        if not objectives:
            yield None, 0, {
                "guide": None,
                "sections": [],
                "uncovered": 0,
                "error": NO_OBJECTIVES_MSG,
            }
            return
        guide_id = self.store.create_study_guide(workspace_id, unit_id)
        self.store.set_guide_objective_count(guide_id, len(objectives))
        total = len(objectives)
        uncovered = 0
        for i, obj in enumerate(objectives, start=1):
            sec = self._write_section(obj, workspace_id, unit_id)
            if not sec["covered"]:
                uncovered += 1
            self.store.add_guide_section(
                guide_id, i - 1, sec["title"], sec["body"], sec["citations"],
                objective_id=obj["id"], skill_code=sec["skill_code"], covered=sec["covered"],
            )
            yield i, total, sec
        sections = self.store.get_guide_sections(guide_id)
        yield None, total, {
            "guide": self.store.get_study_guide(guide_id),
            "sections": sections,
            "uncovered": uncovered,
        }

    def generate(self, workspace_id: int) -> dict | None:
        """Generate (and persist) a study guide for the workspace's unit.

        Returns {guide, sections, uncovered}, or a terminal {guide: None,
        sections: [], uncovered: 0, error} payload when the unit has no
        objectives, or None if the workspace is missing/has no unit.
        `uncovered` is the number of objectives whose best source
        match fell below the coverage threshold."""
        try:
            gen = self.generate_stream(workspace_id)
            *_, (_, _, summary) = gen
            return summary
        except ValueError:
            return None

    def fetch(self, workspace_id: int) -> dict | None:
        """Return the latest persisted guide for a workspace (no generation)."""
        guide = self.store.get_latest_study_guide(workspace_id)
        if not guide:
            return None
        sections = self.store.get_guide_sections(guide["id"])
        uncovered = sum(1 for s in sections if not s.get("covered"))
        return {"guide": guide, "sections": sections, "uncovered": uncovered}


if __name__ == "__main__":
    import tempfile
    import os
    import math
    from unittest.mock import MagicMock

    from backend.config import load_config, Section
    from backend.store import Store

    class _NormEmbedder:
        """Deterministic normalized char-token embedder so sqlite-vec cosine
        similarities are realistic (near 1.0 for similar text, low for unrelated),
        which the coverage gate depends on. (The hashing stub yields no usable
        similarity, so it can't exercise the covered/uncovered path.)"""
        def __init__(self, dim: int = 128):
            self.dim = dim

        def embed(self, text: str) -> list[float]:
            v = [0.0] * self.dim
            for tok in text.lower().split():
                h = sum(ord(c) for c in tok) % self.dim
                v[h] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            return [x / n for x in v]

    os.environ["OPENNOTEBOOK_CONFIG_DIR"] = tempfile.mkdtemp()
    cfg = load_config()
    cfg.embeddings = Section(provider="bundled")
    cfg.llm.max_context = 4096

    db = os.path.join(tempfile.mkdtemp(), "g.db")
    store = Store(db)
    store.init()

    course_id = store.add_course("AP Statistics")
    unit_id = store.add_unit(course_id, "Unit 1: Exploring One-Variable Data")
    ws = store.add_workspace("AP Stats Unit 1", unit_id)

    emb = _NormEmbedder()
    s = store.add_source(ws, "youtube", "Calc lecture", "v", 600)
    store.set_source_status(s, "ready")
    # Chunk texts deliberately reuse the objective phrasing so the (crude, bag-of-words)
    # test embedder gives near-1.0 similarity for the covered objectives.
    texts = [
        "describe the distribution of a quantitative variable shape center and spread",
        "summarize a quantitative variable with center and spread mean median standard deviation",
        "graphing rational functions asymptotes and holes in the plane",
    ]
    for i, t in enumerate(texts):
        store.add_chunk(s, i, t, i * 60, i * 60 + 30, 25, emb.embed(t))
    store.add_learning_objective(unit_id, "describe the distribution of a quantitative variable",
                                 source_type="ced_import", ced_unit_number=1, ced_topic_number=6)
    store.add_learning_objective(unit_id, "summarize a quantitative variable with center and spread",
                                 source_type="ced_import", ced_unit_number=1, ced_topic_number=7)
    # An objective with no matching chunk must be surfaced as uncovered, not hidden.
    store.add_learning_objective(unit_id, "compute a confidence interval for a population mean using a t-distribution",
                                 source_type="ced_import", ced_unit_number=1, ced_topic_number=7)

    gen = StudyGuideGenerator(store, cfg)
    gen.pipeline.embedder = emb
    gen.pipeline.reranker = None
    fake = MagicMock()
    fake.complete.return_value = "Describe shape, center, and spread of the data. [1]"
    gen.llm = fake
    result = gen.generate(ws)
    assert result is not None and "guide" in result and "sections" in result
    assert len(result["sections"]) == 3, f"expected 3 sections, got {len(result['sections'])}"
    assert result["uncovered"] == 1, f"expected 1 uncovered, got {result['uncovered']}"
    for sec in result["sections"]:
        assert sec["body"], "each section must have a body"
        assert isinstance(sec["citations"], dict)
    returning = [s for s in result["sections"] if not s["covered"]]
    assert len(returning) == 1, "the uncovered section must carry covered=False"
    fetched = gen.fetch(ws)
    assert fetched is not None and len(fetched["sections"]) == 3
    assert fetched["uncovered"] == 1, "fetch must report the same uncovered count"

    # An empty writer response must not produce a blank section — fall back to a
    # readable placeholder (still covered, since source evidence was found).
    fake.complete.return_value = "   "
    coverable = store.list_unit_objectives(unit_id)[0]
    sec = gen._write_section(coverable, ws, unit_id)
    assert sec["covered"] is True and sec["body"].strip() != "", \
        "empty writer must fall back to a non-empty body"

    # Empty unit: the stream must end with an explicit terminal payload carrying
    # the real reason — not an abrupt end / exception the client misreads as a
    # connectivity failure — and generate() must surface the same message.
    ews, _, eunit = store.create_course_unit_workspace("Empty Unit")
    assert store.list_unit_objectives(eunit) == []
    events = list(gen.generate_stream(ews))
    assert events == [(None, 0, {"guide": None, "sections": [], "uncovered": 0,
                                 "error": NO_OBJECTIVES_MSG})], \
        f"empty unit must yield one terminal payload, got {events}"
    eres = gen.generate(ews)
    assert eres is not None and eres.get("guide") is None \
        and eres.get("error") == NO_OBJECTIVES_MSG, \
        "generate() must surface the same explicit reason"

    print("guide OK")
