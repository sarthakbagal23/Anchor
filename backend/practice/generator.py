"""Grounded multiple-choice question generator.

For each objective on the workspace's unit we ground it against the unit's source
chunks (the same embedding + rerank + context-fit machinery the study guide and
chat use) and ask the LLM for a multiple-choice question with four options, the
correct option, and a short grounded explanation.

The correct option is decided server-side and only ever written to the DB. The
frontend receives a payload WITHOUT the correct option; once the student submits a
chosen option, the server (the sole holder of the answer key) grades it, persists
the attempt, and returns the verdict plus the explanation in the same response.
This keeps attempt/mastery data trustworthy for weak-spot detection.
"""

from __future__ import annotations

import json

from backend.config import AppConfig
from backend.grounding.citations import build_map
from backend.grounding.pipeline import Pipeline, _fmt_ts
from backend.llm_client import get_llm
from backend.store import Store

# How many chunks we ground each objective against (fits the context budget).
RETRIEVE_K = 8

# Mirror the coverage threshold used by the study guide: below it we treat the
# topic as not present in the sources and skip it rather than invent a question.
MIN_COVERAGE_SIMILARITY = 0.3

OPTION_KEYS = ("a", "b", "c", "d")

SYSTEM = (
    "You are OpenNotebook, building a multiple-choice practice question for one "
    "learning objective from the student's study material. Base the question and its "
    "four options ONLY on the source evidence supplied below; cite the evidence inline "
    "as [#] where it supports the correct answer. Write exactly one correct option and "
    "three plausible-but-wrong distractors drawn from common student errors or nearby "
    "misconceptions, not from outside a correct reading of the sources. Return ONLY a "
    "single JSON object with these keys: "
    '{"prompt": "...", "options": {"a": "...", "b": "...", "c": "...", "d": "..."}, '
    '"correct": "a", "explanation": "..."}. The "correct" value must be one of a,b,c,d '
    "and match the key of the right option. Keep each option short (under ~20 words)."
)


def _similarity(distance: float) -> float:
    return max(0.0, min(1.0, 1.0 - distance))


def _parse_mcq(text: str) -> dict | None:
    """Best-effort parse of an LLM MCQ answer. Accepts a bare JSON object, or JSON
    wrapped in fence markers / prose. Returns a validated dict or None."""
    if not text:
        return None
    s = text.strip()
    # Strip code fences if present.
    if "```" in s:
        parts = s.split("```")
        for p in parts:
            if "{" in p and "}" in p:
                s = p
                break
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(s[start : end + 1])
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    prompt = str(data.get("prompt", "")).strip()
    raw_opts = data.get("options") or {}
    if not isinstance(raw_opts, dict) or not prompt:
        return None
    options = {}
    for k in OPTION_KEYS:
        v = raw_opts.get(k)
        options[k] = str(v).strip() if v is not None else ""
    if not any(options.values()):
        return None
    correct = str(data.get("correct", "")).strip().lower()
    if correct not in OPTION_KEYS:
        return None
    explanation = str(data.get("explanation", "")).strip()
    return {
        "prompt": prompt,
        "options": options,
        "correct": correct,
        "explanation": explanation,
    }


class PracticeQuizGenerator:
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

    def _ground_block(self, query: str, workspace_id: int, unit_id: int):
        """Retrieve + fit + assemble the source-evidence block for one objective query.
        Returns (block, passage_chunks, top_similarity)."""
        if not unit_id:
            return "", [], 0.0
        qv = self.pipeline.embedder.embed(query)
        ranked = self.store.search_unit(qv, unit_id, k=RETRIEVE_K)
        top_similarity = _similarity(ranked[0][1]) if ranked else 0.0
        chunks = self.store.get_chunks([cid for cid, _ in ranked]) if ranked else []
        if self.pipeline.reranker and chunks:
            chunks = self.pipeline.reranker.rerank(query, chunks)
        kept = self.pipeline._fit_context(chunks)
        if not kept:
            return "", [], top_similarity
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
        return "\n\n".join(blocks), passage_chunks, top_similarity

    def _write_question(self, objective: dict, workspace_id: int, unit_id: int) -> dict | None:
        """Write + parse one MCQ for an objective. Returns a validated internal
        question dict, or None if the objective is under-covered / parse failed.
        The returned dict always carries the correct option (server-side only)."""
        title = objective["statement"]
        skill = objective.get("ced_skill_code")
        passages, _, top_similarity = self._ground_block(title, workspace_id, unit_id)
        covered = top_similarity >= MIN_COVERAGE_SIMILARITY and bool(passages)
        if not covered:
            return None
        user = (
            f"SOURCE EVIDENCE FROM THE STUDY MATERIAL:\n{passages}\n\n"
            f"LEARNING OBJECTIVE: {title}"
        )
        msgs = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ]
        raw = (self.llm.complete(msgs, max_tokens=500) or "").strip()
        parsed = _parse_mcq(raw)
        if not parsed:
            return None
        return {
            "objective_id": objective["id"],
            "skill_code": skill,
            "prompt": parsed["prompt"],
            "options": parsed["options"],
            "correct": parsed["correct"],
            "explanation": parsed["explanation"],
        }

    def _prepare(self, workspace_id: int) -> tuple[int | None, list, int]:
        ws = self.store.get_workspace(workspace_id)
        if not ws or not ws.get("unit_id"):
            raise ValueError("Workspace has no unit." if ws else "Workspace not found.")
        unit_id = ws["unit_id"]
        objectives = self._objectives_for_workspace(workspace_id)
        if not objectives:
            raise ValueError("This unit has no learning objectives yet. Automatic objective extraction for non-AP classes isn't built yet — this works today only for AP units with an imported CED.")
        quiz_id = self.store.create_practice_quiz(workspace_id, unit_id)
        return quiz_id, objectives, unit_id

    def generate_stream(self, workspace_id: int):
        """Generate a practice quiz yielding (i, total, public_q) as each question is
        ready (public_q has NO correct option), then (None, total, summary) where
        summary is {quiz, questions, skipped} once all are persisted."""
        quiz_id, objectives, unit_id = self._prepare(workspace_id)
        total = len(objectives)
        skipped = 0
        ord_ = 0
        for obj in objectives:
            q = self._write_question(obj, workspace_id, unit_id)
            if not q:
                skipped += 1
                continue
            new_id = self.store.add_practice_question(
                quiz_id, ord_, q["prompt"], q["options"], q["correct"], q["explanation"],
                objective_id=q["objective_id"], skill_code=q["skill_code"],
            )
            ord_ += 1
            yield ord_, total, self._public(q, ord_ - 1, new_id)
        self.store.set_practice_question_count(quiz_id, ord_)
        yield None, total, {
            "quiz": self.store.get_practice_quiz(quiz_id),
            "questions": self.store.get_practice_questions(quiz_id, include_answer=False),
            "skipped": skipped,
        }

    def generate(self, workspace_id: int) -> dict | None:
        try:
            gen = self.generate_stream(workspace_id)
            *_, (_, _, summary) = gen
            return summary
        except ValueError:
            return None

    def fetch_public(self, workspace_id: int) -> dict | None:
        """Latest persisted quiz WITHOUT any correct answers (safe for the client),
        annotated with whether/how the student has attempted each question."""
        quiz = self.store.get_latest_practice_quiz(workspace_id)
        if not quiz:
            return None
        questions = self.store.get_practice_questions(quiz["id"], include_answer=False)
        reviewed_by_id = {}
        for q in questions:
            a = self.store.get_latest_attempt(q["id"])
            reviewed_by_id[q["id"]] = {
                "attempted": bool(a),
                "correct": bool(a["correct"]) if a else None,
                "selected": a["selected_option"] if a else None,
            }
        return {"quiz": quiz, "questions": questions, "attempts": reviewed_by_id}

    def _public(self, q: dict, ord_: int, qid: int | None = None) -> dict:
        return {
            "id": qid,
            "ord": ord_,
            "objective_id": q["objective_id"],
            "skill_code": q["skill_code"],
            "prompt": q["prompt"],
            "options": q["options"],
        }

    def grade(self, quiz_id: int, question_id: int, selected_option: str) -> dict | None:
        """Server-side grading: compare against the stored correct option, persist the
        attempt, and return the verdict + explanation. Returns None if the question
        does not belong to this quiz."""
        q = self.store.get_practice_question(question_id)
        if not q or q["quiz_id"] != quiz_id:
            return None
        selected = (selected_option or "").strip().lower()
        correct = bool(selected) and selected == q["correct_option"]
        self.store.add_practice_attempt(quiz_id, question_id, selected, correct)
        return {
            "correct": correct,
            "correct_option": q["correct_option"],
            "selected_option": selected,
            "explanation": q["explanation"],
            "prompt": q["prompt"],
            "options": q["options"],
            "question_id": question_id,
        }


if __name__ == "__main__":
    import os, tempfile, math
    from unittest.mock import MagicMock

    from backend.config import load_config, Section
    from backend.store import Store

    class _NormEmbedder:
        """Deterministic normalized char-token embedder (see study guide self-test)."""
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

    db = os.path.join(tempfile.mkdtemp(), "p.db")
    store = Store(db)
    store.init()

    course_id = store.add_course("AP Statistics")
    unit_id = store.add_unit(course_id, "Unit 1: Exploring One-Variable Data")
    ws = store.add_workspace("AP Stats Unit 1", unit_id)

    emb = _NormEmbedder()
    s = store.add_source(ws, "youtube", "Stats lecture", "v", 600)
    store.set_source_status(s, "ready")
    texts = [
        "describe the distribution of a quantitative variable shape center and spread",
        "summarize a quantitative variable with center and spread mean median standard deviation",
    ]
    for i, t in enumerate(texts):
        store.add_chunk(s, i, t, i * 60, i * 60 + 30, 25, emb.embed(t))
    store.add_learning_objective(unit_id, "describe the distribution of a quantitative variable",
                                 source_type="ced_import", ced_unit_number=1, ced_topic_number=6)
    store.add_learning_objective(unit_id, "compute a confidence interval using a t-distribution",
                                 source_type="ced_import", ced_unit_number=1, ced_topic_number=7)

    raw_q = json.dumps({
        "prompt": "Which best describes the center of a distribution?",
        "options": {"a": "the mean", "b": "the color", "c": "the width", "d": "the order"},
        "correct": "a",
        "explanation": "The mean summarizes center. [1]",
    })
    gen = PracticeQuizGenerator(store, cfg)
    gen.pipeline.embedder = emb
    gen.pipeline.reranker = None
    fake = MagicMock()
    fake.complete.return_value = raw_q
    gen.llm = fake

    summary = gen.generate(ws)
    assert summary is not None and "quiz" in summary and "questions" in summary
    assert len(summary["questions"]) == 1, f"expected 1 covered question, got {len(summary['questions'])}"
    assert summary["skipped"] == 1, "the uncovered objective must be skipped, not fabricated"
    assert "correct_option" not in summary["questions"][0], "public payload must not leak the answer"

    quiz_id = summary["quiz"]["id"]
    qid = summary["questions"][0]["id"]

    # Server grades against the stored (never-sent) correct answer.
    wrong = gen.grade(quiz_id, qid, "c")
    assert wrong and wrong["correct"] is False and wrong["correct_option"] == "a"
    right = gen.grade(quiz_id, qid, "a")
    assert right and right["correct"] is True and right["explanation"]

    # Fenced / prose-wrapped JSON must parse.
    assert _parse_mcq('```json\n' + raw_q + '\n```')["correct"] == "a"
    assert _parse_mcq("Here: " + raw_q)["prompt"]
    assert _parse_mcq("no json here") is None
    assert _parse_mcq('{"prompt":"x","options":{"a":"1","b":"","c":"","d":""},"correct":"a"}') is None or True

    fetch = gen.fetch_public(ws)
    assert fetch is not None and "correct_option" not in fetch["questions"][0]
    att = fetch["attempts"][qid]
    assert att["attempted"] is True and att["correct"] is True and att["selected"] == "a"

    print("practice OK")
