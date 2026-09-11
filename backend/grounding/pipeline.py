"""Grounding orchestrator: retrieve -> rerank -> context-fit -> grounded
prompt -> stream + citation map."""
from __future__ import annotations
import json
from typing import Iterator

from backend.config import AppConfig
from backend.store import Store
from backend.grounding.embedder import get_embedder, _HashingEmbedder
from backend.grounding.retriever import retrieve
from backend.grounding.reranker import get_reranker
from backend.grounding.citations import build_map

RESERVE_TOKENS_FOR_ANSWER = 1500
SYSTEM = (
    "You are OpenNotebook, a careful study assistant. Your job is to help a student "
    "understand their class material, prepare for assessment, and identify what to review. "
    "Use the source evidence supplied below as the primary authority. Refer to it naturally "
    "as the lecture, notes, or source material—never as 'passages'. "
    "For every claim supported by source evidence, cite the evidence as [#]. "
    "If the evidence is timestamped, the application will turn that citation into a clickable "
    "video timestamp; do not invent timestamps or citation numbers. "
    "When the source evidence is incomplete, answer the supported part first and say plainly "
    "what the source does not establish. Do not turn a partial answer into 'Not covered in "
    "the sources.' "
    "If the student asks for general knowledge or goes beyond the source, give a concise, "
    "useful answer labeled 'Beyond this source:' and distinguish it from what the lecture says. "
    "If the student asks about images, slides, diagrams, or what appears on screen, be honest: "
    "this version can read the transcript but cannot inspect video visuals yet. "
    "Never pretend to have seen a visual. Keep explanations student-friendly and actionable. "
    "Format answers for studying: use a short descriptive heading when helpful, short paragraphs, "
    "bullets or numbered steps for sequences, bold for key terms, and backticks for code. "
    "Keep citation numbers inline with the sentence they support; never put a citation on its own line. "
    "Draw on several different passages across the supplied evidence and cite each one you use — "
    "an answer that leans on a single passage is incomplete when more evidence is provided."
)


def _interleave_by_source(ranked: list[dict]) -> list[dict]:
    """Round-robin chunks across sources, preserving within-source rank order.

    Without this, a tight context budget fills up on whatever source dominates
    the head of the ranked list, and the answer's citations can come from a
    single source even when the workspace has several covering the topic."""
    buckets: dict = {}
    order: list = []
    for c in ranked:
        sid = c.get("source_id")
        if sid not in buckets:
            buckets[sid] = []
            order.append(sid)
        buckets[sid].append(c)
    queues = [list(buckets[sid]) for sid in order]
    out: list[dict] = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out


class Pipeline:
    def __init__(self, store: Store, cfg: AppConfig):
        self.store = store
        self.cfg = cfg
        self.embedder = get_embedder(cfg)
        try:
            self.reranker = get_reranker(cfg)
        except Exception:
            self.reranker = None
        self.max_context = cfg.llm.max_context or 8192

    def _stage_passages(self, query: str, workspace_id: int) -> list[dict]:
        ids = retrieve(query, workspace_id, self.store, self.embedder, k=20)
        if not ids:
            return []
        chunks = self.store.get_chunks(ids)
        if self.reranker:
            ranked = self.reranker.rerank(query, chunks)
        else:
            ranked = chunks
        # Spread citations across sources so one dominant source can't crowd out
        # the rest before the context budget is spent.
        return _interleave_by_source(ranked)

    def _fit_context(self, ranked: list[dict]) -> list[dict]:
        budget = self.max_context - RESERVE_TOKENS_FOR_ANSWER
        kept, used = [], 0
        for c in ranked:
            tc = c.get("token_count") or len(c["text"].split())
            if used + tc > budget:
                break
            kept.append(c)
            used += tc
        return kept

    def _build_prompt(self, query: str, kept: list[dict], chat_history: list[dict]) -> list[dict]:
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
        passages_block = "\n\n".join(blocks)
        user = f"SOURCE EVIDENCE FROM THE STUDY MATERIAL:\n{passages_block}\n\nSTUDENT QUESTION: {query}"
        messages = [{"role": "system", "content": SYSTEM}]
        for m in chat_history:
            messages.append({"role": m["role"], "content": m["content"]})
        messages.append({"role": "user", "content": user})
        self._last_citation_map = build_map(passage_chunks)
        return messages

    def ground(self, query: str, workspace_id: int, chat_history: list[dict] | None = None) -> tuple[list[dict], dict]:
        chat_history = chat_history or []
        ranked = self._stage_passages(query, workspace_id)
        kept = self._fit_context(ranked)
        if not kept:
            msgs = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": query}]
            self._last_citation_map = {}
            return msgs, {}
        msgs = self._build_prompt(query, kept, chat_history)
        return msgs, self._last_citation_map

    def stream_answer(self, query: str, workspace_id: int, chat_history: list[dict] | None = None,
                      llm_stream=None) -> Iterator[str]:
        msgs, cmap = self.ground(query, workspace_id, chat_history or [])
        if llm_stream is None:
            from backend.llm_client import get_llm
            llm_stream = get_llm(self.cfg).stream
        full = []
        for delta in llm_stream(msgs):
            full.append(delta)
            yield delta
        yield "__CITATIONS__" + json.dumps(cmap)


def _fmt_ts(sec):
    if sec is None:
        return None
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


if __name__ == "__main__":
    import tempfile, os
    os.environ["OPENNOTEBOOK_CONFIG_DIR"] = tempfile.mkdtemp()
    from backend.config import load_config, Section
    from backend.store import Store

    cfg = load_config()
    cfg.embeddings = Section(provider="bundled")
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    store = Store(db)
    store.init()

    emb = _HashingEmbedder()
    ws = store.add_workspace("L3")
    s = store.add_source(ws, "youtube", "Calculus", "v", 600)
    store.set_source_status(s, "ready")
    texts = [
        "The epsilon-delta definition of a limit says for every epsilon there is a delta.",
        "The intermediate value theorem applies to continuous functions on a closed interval.",
        "Limit laws let us split limits of sums into sums of limits.",
    ]
    for i, t in enumerate(texts):
        store.add_chunk(s, i, t, i * 60, i * 60 + 30, 25, emb.embed(t))

    pipe = Pipeline(store, cfg)
    pipe.embedder = emb
    pipe.reranker = None
    msgs, cmap = pipe.ground("what is the definition of a limit", ws)
    assert len(cmap) >= 1, f"expected at least 1 citation, got {len(cmap)}"
    assert "y_top" in cmap[1], "citation map must carry y_top so the PDF viewer can highlight"
    assert "Beyond this source:" in msgs[0]["content"]
    assert "SOURCE EVIDENCE FROM THE STUDY MATERIAL" in msgs[-1]["content"]

    pipe2 = Pipeline(store, cfg)
    pipe2.embedder = emb
    pipe2.reranker = None
    pipe2.max_context = 512
    msgs2, cmap2 = pipe2.ground("what is the definition of a limit", ws)
    assert len(cmap2) <= len(cmap), "smaller context must fit no more passages"

    # source diversity: a ranked list dominated by one source must interleave so
    # a tight budget still reaches the other sources (regression test for
    # single-source citation maps on multi-source workspaces).
    ranked = [
        {"id": 1, "source_id": 11, "text": "a1"},
        {"id": 2, "source_id": 11, "text": "a2"},
        {"id": 3, "source_id": 11, "text": "a3"},
        {"id": 4, "source_id": 12, "text": "b1"},
        {"id": 5, "source_id": 13, "text": "c1"},
        {"id": 6, "source_id": 12, "text": "b2"},
    ]
    assert [c["id"] for c in _interleave_by_source(ranked)] == [1, 4, 5, 2, 6, 3], \
        "round-robin must alternate sources while preserving within-source order"
    assert _interleave_by_source([]) == []
    single = [{"id": 1, "source_id": 11}, {"id": 2, "source_id": 11}]
    assert [c["id"] for c in _interleave_by_source(single)] == [1, 2], \
        "single-source input must keep its order"
    print("pipeline OK")
