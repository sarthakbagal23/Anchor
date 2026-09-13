"""Weak-spot aggregation: attempts -> per-objective accuracy, worst first.

Uses a real Store on a tmp dir. The generator is constructed via __new__ (no
__init__) on purpose: weak_spots() is store-only, and __init__ would build the
grounding Pipeline (embedder/reranker incl. model weights) that this
aggregation never touches.
"""
from backend.practice.generator import PracticeQuizGenerator
from backend.store import Store


def _ws(store):
    cid = store.add_course("Biology 101")
    uid = store.add_unit(cid, "Unit 1")
    return store.add_workspace("ws", uid), uid


def _seed(store, ws, uid):
    o1 = store.add_learning_objective(uid, "Explain photosynthesis", source_type="extracted")
    o2 = store.add_learning_objective(uid, "Calculate energy yield", source_type="extracted")
    qz = store.create_practice_quiz(ws, uid)
    q1 = store.add_practice_question(qz, 0, "p1", {"a": "1"}, "a", "e1", objective_id=o1)
    q2 = store.add_practice_question(qz, 1, "p2", {"a": "1"}, "a", "e2", objective_id=o1)
    q3 = store.add_practice_question(qz, 2, "p3", {"a": "1"}, "a", "e3", objective_id=o2)
    return qz, (o1, o2), (q1, q2, q3)


def _gen(store):
    gen = PracticeQuizGenerator.__new__(PracticeQuizGenerator)
    gen.store = store
    return gen


def test_empty_without_quiz(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.init()
    ws, _ = _ws(store)
    out = _gen(store).weak_spots(ws)
    assert out == {"quiz_id": None,
                   "overall": {"attempted": 0, "correct": 0, "accuracy": None},
                   "objectives": []}


def test_worst_first_and_unattempted_excluded(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.init()
    ws, uid = _ws(store)
    qz, (o1, o2), (q1, q2, q3) = _seed(store, ws, uid)
    store.add_practice_attempt(qz, q1, "a", True)    # o1: 1/1
    store.add_practice_attempt(qz, q2, "b", False)   # o1: 1/2
    # q3 (o2) never attempted -> o2 must not appear
    out = _gen(store).weak_spots(ws)
    assert out["quiz_id"] == qz
    assert out["overall"] == {"attempted": 2, "correct": 1, "accuracy": 0.5}
    assert len(out["objectives"]) == 1
    row = out["objectives"][0]
    assert row["objective_id"] == o1 and row["statement"] == "Explain photosynthesis"
    assert (row["attempted"], row["correct"], row["accuracy"]) == (2, 1, 0.5)


def test_latest_attempt_wins_and_ordering(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.init()
    ws, uid = _ws(store)
    qz, (o1, o2), (q1, q2, q3) = _seed(store, ws, uid)
    store.add_practice_attempt(qz, q1, "b", False)   # o1 wrong...
    store.add_practice_attempt(qz, q1, "a", True)    # ...then right: counts once, correct
    store.add_practice_attempt(qz, q2, "b", False)   # o1: 1/2
    store.add_practice_attempt(qz, q3, "b", False)   # o2: 0/1 -> worst, first
    out = _gen(store).weak_spots(ws)
    assert [r["objective_id"] for r in out["objectives"]] == [o2, o1]
    assert out["objectives"][0]["accuracy"] == 0.0
    assert out["objectives"][1]["accuracy"] == 0.5
    assert out["overall"] == {"attempted": 3, "correct": 1, "accuracy": 1 / 3}


def test_unknown_workspace_returns_empty(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.init()
    assert _gen(store).weak_spots(424242)["objectives"] == []
