"""Extraction for non-AP units: parser robustness + hook behavior.

All hermetic: the LLM is a stub object (no network), chunks go into a real
Store on a tmp dir. Embedding values never matter here — extraction samples
chunk *text* only, never runs similarity — so the hashing embedder's
per-process randomization can't flake these tests.
"""
import json

from backend.config import AppConfig, LLMSection
from backend.grounding.embedder import _HashingEmbedder
from backend.objectives import extract
from backend.store import Store


class _StubLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        return self.reply


def _db(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.init()
    return store


def _ready_source(store, text="Photosynthesis converts light to chemical energy."):
    cid = store.add_course("Biology 101")
    uid = store.add_unit(cid, "Unit 1")
    ws = store.add_workspace("ws", uid)
    src = store.add_source(ws, "pdf", "bio-notes.pdf", None, None, unit_id=uid)
    emb = _HashingEmbedder()
    store.add_chunk(src, 0, text, None, None, 10, emb.embed(text))
    store.set_source_status(src, "ready")
    return store, src, uid


def test_parse_json_array_and_types():
    out = extract._parse_objectives('[{"statement": "Explain bias", "type": "skill"}]')
    assert out == [{"statement": "Explain bias", "type": "skill"}]
    # unknown types collapse to concept; overlong statements are dropped
    out = extract._parse_objectives(json.dumps([
        {"statement": "Ok", "type": "weird"},
        {"statement": "x" * 301},
    ]))
    assert out == [{"statement": "Ok", "type": "concept"}]


def test_parse_fences_and_wrapped_objects():
    out = extract._parse_objectives('```json\n[{"statement": "Define mean"}]\n```')
    assert out == [{"statement": "Define mean", "type": "concept"}]
    out = extract._parse_objectives('{"objectives": [{"statement": "Calculate variance", "type": "skill"}]}')
    assert out == [{"statement": "Calculate variance", "type": "skill"}]


def test_parse_bullets_but_not_prose():
    out = extract._parse_objectives('- Explain bias\n- Define mean')
    assert [o["statement"] for o in out] == ["Explain bias", "Define mean"]
    # a rambling non-list reply must yield nothing, not one junk objective
    assert extract._parse_objectives('not json at all {{{') == []
    assert extract._parse_objectives('') == []


def test_hook_creates_extracted_objectives(tmp_path):
    store = _db(tmp_path)
    store, src, uid = _ready_source(store)
    llm = _StubLLM(json.dumps([
        {"statement": "Explain photosynthesis", "type": "concept"},
        {"statement": "Calculate energy yield", "type": "skill"},
    ]))
    res = extract.extract_objectives_for_source(store, AppConfig(), src, llm=llm)
    assert res["reason"] == "ok" and len(res["objectives"]) == 2
    objs = store.list_unit_objectives(uid)
    assert len(objs) == 2
    assert all(o["source_type"] == "extracted" for o in objs)
    assert {o["objective_type"] for o in objs} == {"concept", "skill"}


def test_hook_idempotent_and_skips_seeded_units(tmp_path):
    store = _db(tmp_path)
    store, src, uid = _ready_source(store)
    llm = _StubLLM(json.dumps([{"statement": "Explain photosynthesis"}]))
    first = extract.extract_objectives_for_source(store, AppConfig(), src, llm=llm)
    assert first["reason"] == "ok"
    second = extract.extract_objectives_for_source(store, AppConfig(), src, llm=llm)
    assert second["objectives"] == [] and second["reason"] == "unit already has objectives"
    assert llm.calls == 1, "second run must not call the LLM at all"
    assert len(store.list_unit_objectives(uid)) == 1


def test_hook_expected_states_never_raise(tmp_path):
    store = _db(tmp_path)
    _, src, _ = _ready_source(store)
    # not ready
    assert extract.extract_objectives_for_source(store, AppConfig(), 99999, llm=_StubLLM("[]"))["reason"] == "source missing"
    store.set_source_status(src, "failed")
    r = extract.extract_objectives_for_source(store, AppConfig(), src, llm=_StubLLM("[]"))
    assert "status" in r["reason"]
    store.set_source_status(src, "ready")
    # LLM failure and unusable LLM output are reasons, not exceptions
    r = extract.extract_objectives_for_source(store, AppConfig(), src, llm=_StubLLM("[]"))
    assert r["reason"] == "llm returned no usable objectives"
    assert store.list_unit_objectives(r["unit_id"]) == []

    class _Boom:
        def complete(self, *a, **k):
            raise RuntimeError("provider down")

    r = extract.extract_objectives_for_source(store, AppConfig(), src, llm=_Boom())
    assert "llm failed" in r["reason"]
    # no LLM configured and none injected
    r = extract.extract_objectives_for_source(store, AppConfig(llm=LLMSection()), src)
    assert r["reason"] == "llm not configured"
