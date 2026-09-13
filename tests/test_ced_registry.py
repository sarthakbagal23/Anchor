"""CED registry: every manifest on disk is registered, every entry loads and
round-trips, aliases resolve, units map by number and title, and the real
post-ingest import works for a non-Statistics course.

Hermetic: tmp Store + hashing embedder, no network, no weights.
"""
import re

from backend.grounding.embedder import _HashingEmbedder
from backend.objectives import ced
from backend.store import Store


def test_every_file_registered_and_valid():
    files = {p.name for p in ced.CED_DATA_DIR.glob("*.json")}
    assert set(ced._CED_COURSES.values()) == files
    for title in ced._CED_COURSES:
        m = ced._load_manifest(title)
        assert m["exam_type"] == "AP" and m["units"]
        for u in m["units"]:
            for t in u["topics"]:
                assert t["objectives"]
                for o in t["objectives"]:
                    assert re.fullmatch(r"[1-5]\.[A-Z]+", o["skill_code"])
                    assert 10 < len(o["statement"]) <= 300


def test_titles_and_aliases():
    assert ced.infer_course_key("AP Calculus AB") == "AP Calculus AB"
    assert ced.infer_course_key("  apush ") == "AP United States History"
    assert ced.infer_course_key("AP CSA") == "AP Computer Science A"
    assert ced.infer_course_key("ap stats") == "AP Statistics"
    assert ced.infer_course_key("AP Gov") == "AP United States Government & Politics"
    # ambiguous or unknown titles must not guess
    assert ced.infer_course_key("AP Calculus") is None
    assert ced.infer_course_key("Linear Algebra") is None


def test_find_ced_unit_numbers_titles_guards():
    m = ced._load_manifest("AP Calculus AB")
    assert ced.find_ced_unit(m, "Unit 1")[0] == 1
    assert ced.find_ced_unit(m, "Unit 3: Applications")[0] == 3
    assert ced.find_ced_unit(m, "Unit 2: Derivatives: Rules, Implicit & Related Rates (mine)")[0] == 2
    assert ced.find_ced_unit(m, "Unit 99") is None
    assert ced.find_ced_unit(m, "") is None
    # leading digits glued to a word are not unit numbers ("2D Arrays" is a
    # real CSA unit title and must match by title, never as unit 2)
    csa = ced._load_manifest("AP Computer Science A")
    assert ced.find_ced_unit(csa, "2D Arrays")[0] == 5


def _smoke(tmp_path, course_title, unit_title, expect_min):
    store = Store(str(tmp_path / "ced.db"))
    store.init()
    cid = store.add_course(course_title)
    uid = store.add_unit(cid, unit_title)
    ws = store.add_workspace("quiz", uid)
    src = store.add_source(ws, "pdf", f"{course_title} notes", None, None, unit_id=uid)
    hed = _HashingEmbedder()
    store.add_chunk(src, 0, "Introductory material with key terms.", None, None, 10,
                    hed.embed("intro"))
    store.set_source_status(src, "ready")
    res = ced.apply_ced_to_source(store, hed, src)
    assert res["detected"] and res["pipeline"] == "ced_import"
    assert res["imported"] >= expect_min
    assert store.get_course(cid)["exam_type"] == "AP"
    # second source re-imports idempotently, no dupes
    src2 = store.add_source(ws, "pdf", "more notes", None, None, unit_id=uid)
    store.set_source_status(src2, "ready")
    res2 = ced.apply_ced_to_source(store, hed, src2)
    assert res2["detected"]
    n = len(store.list_unit_objectives(uid, source_type="ced_import"))
    assert n == res["imported"]


def test_import_smoke_calculus(tmp_path):
    _smoke(tmp_path, "AP Calculus AB", "Unit 1", 2)


def test_import_smoke_alias_course(tmp_path):
    # a casually-named course ("apush") flows end to end: detection matches the
    # alias, apply stamps exam_type, objectives import into Unit 5.
    store = Store(str(tmp_path / "ced2.db"))
    store.init()
    cid = store.add_course("apush")
    assert ced.infer_course_key("apush") == "AP United States History"
    uid = store.add_unit(cid, "Unit 5")
    ws = store.add_workspace("quiz", uid)
    src = store.add_source(ws, "pdf", "apush period 5 notes", None, None, unit_id=uid)
    hed = _HashingEmbedder()
    store.add_chunk(src, 0, "Westward expansion and sectional conflict.", None, None, 10,
                    hed.embed("intro"))
    store.set_source_status(src, "ready")
    res = ced.apply_ced_to_source(store, hed, src)
    assert res["detected"] and res["course_key"] == "AP United States History"
    assert res["imported"] >= 2
    assert store.get_course(cid)["exam_type"] == "AP"
