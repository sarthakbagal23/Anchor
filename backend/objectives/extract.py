"""AI learning-objective extraction for non-AP units.

The AP path (ced.py) imports objectives from a registered CED manifest. This
module covers everything else: after a source finishes indexing, sample its
chunks, ask the chat LLM for the distinct testable objectives, and store them
with source_type="extracted" (the schema default — the table was designed for
this). Downstream code (study guide, practice quiz) reads objectives via
list_unit_objectives() and never cares which pipeline produced them.

Idempotency: extraction skips any unit that already has objectives, and skips
individual statements already present (normalized compare), so re-running
ingest or adding a second source never duplicates objectives.
"""
from __future__ import annotations

import hashlib
import json
import re

MAX_OBJECTIVES = 8
SAMPLE_CHARS = 12000

EXTRACT_SYSTEM = (
    "You list learning objectives for a student study guide. "
    "Read the study material below and reply with ONLY a JSON array of objects, "
    'each like {"statement": "...", "type": "concept"} where type is "concept" '
    "for knowledge/understanding or \"skill\" for something the student must DO "
    "(calculate, interpret, design, distinguish). "
    "Rules: one testable idea per statement; start each statement with a verb "
    "(Explain, Calculate, Distinguish, ...); no numbering, no markdown, no "
    "commentary outside the JSON array."
)


def _parse_objectives(raw: str) -> list[dict]:
    """Best-effort parse of the LLM reply into [{statement, type}]. Never raises."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        data = json.loads(text)
    except Exception:
        data = None
    if isinstance(data, dict):
        for key in ("objectives", "items", "results"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        # Fallback: one objective per bullet/numbered line. Bare prose lines
        # are NOT objectives (a model that rambles instead of listing must
        # yield nothing, not one junk objective per sentence).
        data = [
            {"statement": re.sub(r"^[\-\*\d\.\)\s]+", "", ln).strip(), "type": "concept"}
            for ln in text.splitlines()
            if re.match(r"^\s*[\-\*]|^\s*\d+[\.\)]", ln)
            and re.sub(r"^[\-\*\d\.\)\s]+", "", ln).strip()
        ]
    out = []
    for item in data:
        if isinstance(item, str):
            item = {"statement": item, "type": "concept"}
        if not isinstance(item, dict):
            continue
        stmt = str(item.get("statement") or "").strip()
        if not stmt or len(stmt) > 300:
            continue
        typ = str(item.get("type") or "concept").strip().lower()
        out.append({"statement": stmt, "type": typ if typ == "skill" else "concept"})
    return out


def _content_hash(source_id: int, statement: str) -> str:
    return hashlib.sha1(f"{source_id}:{statement.strip().lower()}".encode()).hexdigest()


def _norm(statement: str) -> str:
    return re.sub(r"\s+", " ", statement.strip().lower())


def extract_objectives_for_source(store, cfg, source_id: int, llm=None,
                                  max_objectives: int = MAX_OBJECTIVES) -> dict:
    """Post-ingest hook for non-AP sources. Returns a result dict, never raises
    for expected states (missing source, not ready, no unit, unit already has
    objectives, LLM unconfigured/failed) — only unexpected DB errors propagate,
    and the caller (_wrap_ingest) isolates even those."""
    result: dict = {
        "source_id": source_id,
        "unit_id": None,
        "pipeline": "extracted",
        "objectives": [],
        "reason": "",
    }
    src = store.get_source(source_id)
    if not src:
        result["reason"] = "source missing"
        return result
    if src.get("status") != "ready":
        result["reason"] = f"status={src.get('status')}"
        return result
    unit_id = src.get("unit_id")
    if not unit_id:
        result["reason"] = "source not attached to a unit"
        return result
    result["unit_id"] = unit_id
    if store.list_unit_objectives(unit_id):
        result["reason"] = "unit already has objectives"
        return result

    if llm is None:
        llm_cfg = getattr(cfg, "llm", None)
        if not llm_cfg or not getattr(llm_cfg, "base_url", None) or not getattr(llm_cfg, "model", None):
            result["reason"] = "llm not configured"
            return result
        from backend.llm_client import get_llm
        llm = get_llm(cfg)

    rows = store.conn.execute(
        "SELECT text FROM chunks WHERE source_id=? ORDER BY ord", (source_id,)
    ).fetchall()
    sample, used = [], 0
    for r in rows:
        t = r[0] or ""
        if used + len(t) > SAMPLE_CHARS:
            break
        sample.append(t)
        used += len(t)
    if not sample:
        result["reason"] = "source has no indexable text"
        return result

    try:
        raw = llm.complete(
            [
                {"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": "STUDY MATERIAL:\n" + "\n\n".join(sample)},
            ],
            max_tokens=600,
        )
    except Exception as e:
        result["reason"] = f"llm failed: {e}"
        return result
    parsed = _parse_objectives(raw or "")[:max_objectives]
    if not parsed:
        result["reason"] = "llm returned no usable objectives"
        return result

    existing = {_norm(o["statement"]) for o in store.list_unit_objectives(unit_id)}
    ids = []
    for item in parsed:
        if _norm(item["statement"]) in existing:
            continue
        oid = store.add_learning_objective(
            unit_id,
            item["statement"],
            objective_type=item["type"],
            source_type="extracted",
            confidence=0.7,
            content_hash=_content_hash(source_id, item["statement"]),
        )
        ids.append(oid)
        existing.add(_norm(item["statement"]))
    result["objectives"] = ids
    result["reason"] = "ok" if ids else "all statements already present"
    return result


if __name__ == "__main__":
    # Hermetic checks: parser robustness only (no store, no network).
    assert _parse_objectives('[{"statement": "Explain bias", "type": "skill"}]') == [
        {"statement": "Explain bias", "type": "skill"}
    ]
    assert _parse_objectives('```json\n[{"statement": "Define mean"}]\n```') == [
        {"statement": "Define mean", "type": "concept"}
    ]
    assert _parse_objectives('{"objectives": [{"statement": "Calculate variance", "type": "skill"}]}') == [
        {"statement": "Calculate variance", "type": "skill"}
    ]
    assert _parse_objectives('- Explain bias\n- Define mean') == [
        {"statement": "Explain bias", "type": "concept"},
        {"statement": "Define mean", "type": "concept"},
    ]
    assert _parse_objectives('not json at all {{{') == []
    assert _parse_objectives('') == []
    long_stmt = "x" * 301
    assert _parse_objectives(json.dumps([{"statement": long_stmt}])) == []
    assert _parse_objectives(json.dumps([{"statement": "Ok", "type": "weird"}])) == [
        {"statement": "Ok", "type": "concept"}
    ]
    assert _content_hash(1, "Explain Bias ") == _content_hash(1, "explain bias")
    assert _content_hash(1, "a") != _content_hash(2, "a")
    assert _norm("  Explain   Bias\n") == "explain bias"
    print("extract OK")
