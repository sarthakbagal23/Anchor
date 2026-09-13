"""AP CED import path for learning_objectives.

Alternative to AI extraction: when a unit belongs to a course whose `exam_type`
is "AP" and a CED manifest exists for it, objectives are imported from the CED
instead of being extracted, then matched against the unit's chunks with the
same sqlite-vec cosine search used everywhere else.

source_type on learning_objectives distinguishes the two pipelines:
  'extracted'  - produced by AI extraction (dedup/clustering RUNS here)
  'ced_import' - produced by this importer   (dedup NEVER runs here)

The manifests live in ced_data/*.json and contain official unit/topic numbers +
skill codes plus short paraphrased objective statements (not verbatim CED prose),
sourced from official public College Board CED documents.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from backend.grounding.embedder import Embedder
from backend.store import Store

CED_DATA_DIR = Path(__file__).parent / "ced_data"

# Registered CED manifests: course title -> manifest file.
_CED_COURSES: dict[str, str] = {
    "AP Statistics": "ap_statistics.json",
    # Math & Computer Science
    "AP Precalculus": "ap_precalculus.json",
    "AP Calculus AB": "ap_calculus_ab.json",
    "AP Calculus BC": "ap_calculus_bc.json",
    "AP Computer Science A": "ap_computer_science_a.json",
    "AP Computer Science Principles": "ap_computer_science_principles.json",
    # Sciences
    "AP Biology": "ap_biology.json",
    "AP Chemistry": "ap_chemistry.json",
    "AP Environmental Science": "ap_environmental_science.json",
    "AP Physics 1": "ap_physics_1.json",
    "AP Physics 2": "ap_physics_2.json",
    "AP Physics C: Mechanics": "ap_physics_c_mechanics.json",
    "AP Physics C: Electricity & Magnetism": "ap_physics_c_em.json",
    # History & Social Sciences
    "AP World History: Modern": "ap_world_history_modern.json",
    "AP United States History": "ap_us_history.json",
    "AP European History": "ap_european_history.json",
    "AP Human Geography": "ap_human_geography.json",
    "AP Psychology": "ap_psychology.json",
    "AP United States Government & Politics": "ap_us_government.json",
    "AP Comparative Government & Politics": "ap_comparative_government.json",
    "AP African American Studies": "ap_african_american_studies.json",
    "AP Macroeconomics": "ap_macroeconomics.json",
    "AP Microeconomics": "ap_microeconomics.json",
    # English & Arts
    "AP English Language & Composition": "ap_english_language.json",
    "AP English Literature & Composition": "ap_english_literature.json",
    "AP Art History": "ap_art_history.json",
    "AP Music Theory": "ap_music_theory.json",
    "AP Studio Art": "ap_studio_art.json",
    # World Languages (one shared framework file for all six)
    "AP Spanish Language & Culture": "ap_world_language_culture.json",
    "AP French Language & Culture": "ap_world_language_culture.json",
    "AP German Language & Culture": "ap_world_language_culture.json",
    "AP Italian Language & Culture": "ap_world_language_culture.json",
    "AP Chinese Language & Culture": "ap_world_language_culture.json",
    "AP Japanese Language & Culture": "ap_world_language_culture.json",
    "AP Spanish Literature & Culture": "ap_spanish_literature.json",
    "AP Latin": "ap_latin.json",
    # Capstone
    "AP Seminar": "ap_seminar.json",
    "AP Research": "ap_research.json",
}

# Casual titles students actually type, normalized (lowercase, single spaces).
# Checked after exact match, so these can never shadow a real course title.
_COURSE_ALIASES: dict[str, str] = {
    "ap stats": "AP Statistics",
    "ap calc ab": "AP Calculus AB",
    "ap calc bc": "AP Calculus BC",
    "ap csa": "AP Computer Science A",
    "ap csp": "AP Computer Science Principles",
    "ap bio": "AP Biology",
    "ap chem": "AP Chemistry",
    "ap env sci": "AP Environmental Science",
    "ap environmental science": "AP Environmental Science",
    "ap phys 1": "AP Physics 1",
    "ap phys 2": "AP Physics 2",
    "apush": "AP United States History",
    "ap us history": "AP United States History",
    "ap world": "AP World History: Modern",
    "ap world history": "AP World History: Modern",
    "ap euro": "AP European History",
    "ap hug": "AP Human Geography",
    "ap human geo": "AP Human Geography",
    "ap psych": "AP Psychology",
    "ap gov": "AP United States Government & Politics",
    "ap government": "AP United States Government & Politics",
    "ap comp gov": "AP Comparative Government & Politics",
    "ap macro": "AP Macroeconomics",
    "ap micro": "AP Microeconomics",
    "ap lang": "AP English Language & Composition",
    "ap lit": "AP English Literature & Composition",
    "ap spanish lang": "AP Spanish Language & Culture",
    "ap french lang": "AP French Language & Culture",
    "ap german lang": "AP German Language & Culture",
    "ap italian lang": "AP Italian Language & Culture",
    "ap chinese lang": "AP Chinese Language & Culture",
    "ap japanese lang": "AP Japanese Language & Culture",
}


def _load_manifest(course_key: str) -> dict:
    path = CED_DATA_DIR / _CED_COURSES[course_key]
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def registered_courses() -> dict[str, str]:
    """course title -> CED exam_type for all manifests we can import."""
    out: dict[str, str] = {}
    for title in _CED_COURSES.keys():
        out[title] = _load_manifest(title)["exam_type"]
    return out


_AP_HINT_RE = re.compile(
    r"\bap\s*stats?\b|\badvanced\s+placement\b|ap statistics",
    re.IGNORECASE,
)


def infer_course_key(title: str) -> str | None:
    """Best-effort: does this DB course title correspond to a CED manifest?

    Order: exact title match, then the casual-alias table (apush, ap csa, ...),
    then the "AP<subject>" squished form. Aliases never shadow exact titles."""
    norm = re.sub(r"\s+", " ", (title or "")).strip().lower()
    for course in _CED_COURSES:
        if norm == course.lower():
            return course
    if norm in _COURSE_ALIASES:
        return _COURSE_ALIASES[norm]
    for course in _CED_COURSES:
        if norm.casefold() == f"ap{course.replace('AP ', '').lower()}":
            return course
    return None


def detect_source_ap_hint(store: Store, source_id: int) -> bool:
    """Cheap AP sniff on a source: title/file path, then the first few chunks.
    Only used as a hint — the authoritative gate is the course-title CED mapping."""
    src = store.get_source(source_id)
    if not src:
        return False
    for text in (src.get("title") or "", src.get("file_path") or ""):
        if _AP_HINT_RE.search(text):
            return True
    sample = store.conn.execute(
        "SELECT c.text FROM chunks c WHERE c.source_id=? ORDER BY c.ord LIMIT 3",
        (source_id,),
    ).fetchall()
    return any(_AP_HINT_RE.search(r[0]) for r in sample)


def detect_course_key(store: Store, source_id: int) -> str | None:
    """Which (if any) registered AP CED course does this source's course belong to?

    Signal order: course title match -> source title match -> AP hint fallback
    (when only one CED course is registered, treat the hint as that course)."""
    src = store.get_source(source_id)
    if not src:
        return None
    unit = store.get_unit(src.get("unit_id")) if src.get("unit_id") else None
    course = store.get_course(unit["course_id"]) if unit else None
    if course and infer_course_key(course["title"]):
        return infer_course_key(course["title"])
    if infer_course_key(src.get("title") or ""):
        return infer_course_key(src["title"])
    if detect_source_ap_hint(store, source_id) and len(_CED_COURSES) == 1:
        return next(iter(_CED_COURSES))
    return None


def apply_ced_to_source(
    store: Store, embedder: Embedder, source_id: int,
    k: int = 5, min_similarity: float = 0.25,
) -> dict:
    """Post-ingest hook: detect whether the source's course is an AP CED course.
    If so, stamp exam_type, import the unit's CED objectives, and run the
    chunk-coverage pass. Returns a result dict (no-op for non-AP sources)."""
    result: dict = {
        "source_id": source_id,
        "detected": False,
        "pipeline": "extracted",
        "imported": 0,
        "covered": 0,
        "reason": "not an AP CED course",
    }
    src = store.get_source(source_id)
    if not src or src.get("status") != "ready":
        result["reason"] = f"source status {src.get('status') if src else 'missing'}"
        return result
    unit = store.get_unit(src.get("unit_id")) if src.get("unit_id") else None
    if not unit:
        result["reason"] = "source not attached to a unit"
        return result

    course_key = detect_course_key(store, source_id)
    if not course_key:
        return result
    result["course_key"] = course_key
    store.set_course_exam_type(unit["course_id"], _load_manifest(course_key)["exam_type"])

    if route_unit(store, unit["id"]) != "ced_import":
        result["reason"] = "course is AP but unit has no CED mapping"
        return result

    created = import_ced_objectives(store, unit["id"], course_key)
    coverage = match_unit_objectives(store, embedder, unit["id"], k=k, min_similarity=min_similarity)
    covered = sum(1 for hits in coverage.values() if hits)
    result.update({
        "detected": True,
        "pipeline": "ced_import",
        "imported": len(created),
        "covered": covered,
        "reason": "ok",
    })
    return result


def find_ced_unit(manifest: dict, unit_title: str) -> tuple[int, dict] | None:
    """Map a DB unit title to a CED unit by ("Unit N" prefix, then title match).

    The number branch requires actual digits (a bare "Unit" with no number must
    not match), with a word boundary so titles like "2D Arrays" don't map to
    unit 2."""
    if not unit_title:
        return None
    m = re.match(r"(?:Unit\s+)?(\d+)\b", unit_title.strip(), re.IGNORECASE)
    cand_by_num = {}
    for u in manifest["units"]:
        cand_by_num[u["number"]] = u
        if u["title"].lower() in unit_title.lower():
            return u["number"], u
    if m and m.group(1):
        num = int(m.group(1))
        if num in cand_by_num:
            return num, cand_by_num[num]
    return None


def import_ced_objectives(store: Store, unit_id: int, course_key: str) -> list[int]:
    """Write ced_import rows for one unit's topics. Idempotent: wipes any prior
    ced_import rows for the unit, leaves extracted rows untouched."""
    manifest = _load_manifest(course_key)
    unit = store.get_unit(unit_id)
    if not unit:
        return []
    found = find_ced_unit(manifest, unit["title"])
    if not found:
        return []
    ced_unit_number, ced_unit = found

    store.delete_unit_objectives(unit_id, source_type="ced_import")
    created: list[int] = []
    for topic in ced_unit["topics"]:
        for obj in topic["objectives"]:
            oid = store.add_learning_objective(
                unit_id=unit_id,
                statement=obj["statement"],
                objective_type="skill" if obj.get("skill_code", "").startswith(("2.", "3.")) else "concept",
                source_type="ced_import",
                confidence=1.0,
                ced_unit_number=ced_unit_number,
                ced_topic_number=topic["number"],
                ced_skill_code=obj.get("skill_code"),
            )
            created.append(oid)
    return created


def match_unit_objectives(
    store: Store, embedder: Embedder, unit_id: int, k: int = 5, min_similarity: float = 0.3
) -> dict[int, list[dict]]:
    """Coverage pass: embed each ced_import objective and attach its top-k unit
    chunks via the existing sqlite-vec search. Returns objective_id -> chunks."""

    def _similarity(distance: float) -> float:
        # cosine distance from sqlite-vec -> similarity
        return max(0.0, min(1.0, 1.0 - distance))

    objectives = store.list_unit_objectives(unit_id, source_type="ced_import")
    coverage: dict[int, list[dict]] = {}
    for obj in objectives:
        qv = embedder.embed(obj["statement"])
        hits = []
        for chunk_id, distance in store.search_unit(qv, unit_id, k=k):
            sim = _similarity(distance)
            if sim < min_similarity:
                break  # ordered by distance, so all later are worse too
            hits.append((chunk_id, sim))
        store.set_objective_chunks(obj["id"], [c for c, _ in hits], [s for _, s in hits])
        coverage[obj["id"]] = [
            {"chunk_id": cid, "similarity": sim}
            for cid, sim in hits
        ]
    return coverage


def route_course(store: Store, course_id: int) -> str:
    """'ced_import' when this course has a matching CED manifest, else 'extracted'.

    The real gate is the CED mapping: we use import+match for AP courses that
    have a manifest; everything else stays on the extraction pipeline."""
    course = store.get_course(course_id)
    if not course:
        return "extracted"
    course_key = infer_course_key(course["title"])
    if not course_key:
        return "extracted"
    manifest = _load_manifest(course_key)
    if course.get("exam_type") != manifest["exam_type"]:
        return "extracted"
    return "ced_import"


def route_unit(store: Store, unit_id: int) -> str:
    """Per-unit routing. A unit takes the CED path only if its course does AND the
    unit title maps to a CED unit; otherwise it falls back to extraction."""
    unit = store.get_unit(unit_id)
    if not unit:
        return "extracted"
    if route_course(store, unit["course_id"]) != "ced_import":
        return "extracted"
    course_key = infer_course_key(store.get_course(unit["course_id"])["title"])
    manifest = _load_manifest(course_key)
    return "ced_import" if find_ced_unit(manifest, unit["title"]) else "extracted"


def extract_only_objectives(store: Store, unit_id: int) -> list[dict]:
    """Dedup/clustering must only ever see extracted objectives."""
    return store.list_unit_objectives(unit_id, source_type="extracted")


if __name__ == "__main__":
    import tempfile
    import os

    db = os.path.join(tempfile.mkdtemp(), "ced.db")
    store = Store(db)
    store.init()

    course_id = store.add_course("AP Statistics", exam_type="AP")
    unit_id = store.add_unit(course_id, "Unit 1: Exploring One-Variable Data")
    unit2 = store.add_unit(course_id, "No such CED unit")

    assert route_course(store, course_id) == "ced_import"
    assert route_unit(store, unit_id) == "ced_import"
    assert route_unit(store, unit2) == "extracted"
    assert infer_course_key("AP Statistics") == "AP Statistics"

    # unit-number mapping must work for bare "Unit N" titles (regression: the
    # old regex matched empty at ^ so the number branch never fired and only
    # full CED titles mapped)
    stats_manifest = _load_manifest("AP Statistics")
    assert find_ced_unit(stats_manifest, "Unit 3")[0] == 3
    assert find_ced_unit(stats_manifest, "Unit 1: Exploring One-Variable Data")[0] == 1
    assert find_ced_unit(stats_manifest, "Unit 99") is None

    created = import_ced_objectives(store, unit_id, "AP Statistics")
    assert len(created) == 11, f"expected Unit 1 objectives, got {len(created)}"
    objs = store.list_unit_objectives(unit_id, source_type="ced_import")
    assert all(o["source_type"] == "ced_import" for o in objs)
    assert all(o["ced_unit_number"] == 1 for o in objs)
    assert all(o["ced_topic_number"] for o in objs)
    assert store.list_unit_objectives(unit2, source_type="ced_import") == []

    # idempotent re-import
    import_ced_objectives(store, unit_id, "AP Statistics")
    assert len(store.list_unit_objectives(unit_id, source_type="ced_import")) == 11

    # extracted rows survive a CED re-import
    ex = store.add_learning_objective(unit_id, "teacher-added objective", source_type="extracted")
    import_ced_objectives(store, unit_id, "AP Statistics")
    assert store.get_objective(ex) is not None

    # non-AP course never routes to CED
    plain = store.add_course("Linear Algebra")
    u_plain = store.add_unit(plain, "Unit 1: Exploring One-Variable Data")
    assert route_course(store, plain) == "extracted"
    assert route_unit(store, u_plain) == "extracted"

    # --- automatic source detection + CED apply (the api post-ingest hook) ---
    from backend.grounding.embedder import _HashingEmbedder
    hed = _HashingEmbedder()

    # AP course via title: a ready source triggers import + a coverage pass
    ap_ws = store.add_workspace("ap stats quiz", unit_id)
    ap_src = store.add_source(ap_ws, "pdf", "1A.5 AP Statistics Notes", None, None, unit_id=unit_id)
    store.set_source_status(ap_src, "ready")
    assert detect_source_ap_hint(store, ap_src) is True
    assert detect_course_key(store, ap_src) == "AP Statistics"
    res = apply_ced_to_source(store, hed, ap_src)
    assert res["detected"] is True and res["pipeline"] == "ced_import"
    assert res["imported"] == 11
    assert store.get_course(course_id)["exam_type"] == "AP", "apply_ced_to_source must stamp exam_type"
    assert route_course(store, course_id) == "ced_import"

    # not-yet-ready source: no-op with a reason
    q = store.add_source(ap_ws, "pdf", "uploading", None, None, unit_id=unit_id)
    assert apply_ced_to_source(store, hed, q)["reason"] == "source status queued"

    # non-AP course with no AP hint in title/file: detection stays off
    lin_ws = store.add_workspace("Linear Algebra", u_plain)
    lin_src = store.add_source(lin_ws, "youtube", "intro to matrices", "v", 60, unit_id=u_plain)
    store.set_source_status(lin_src, "ready")
    assert detect_course_key(store, lin_src) is None
    assert apply_ced_to_source(store, hed, lin_src)["pipeline"] == "extracted"

    print("ced OK")