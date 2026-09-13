"""The no-objectives message is a user-facing contract: study guide, practice
quiz, and both stream variants show it verbatim, so it must come from exactly
one place and must tell the user what to do (add a source, wait for indexing).
"""
from backend.study.guide import NO_OBJECTIVES_MSG


def test_single_source_of_truth():
    from backend.practice import generator as practice_mod

    assert practice_mod.NO_OBJECTIVES_MSG is NO_OBJECTIVES_MSG


def test_message_is_actionable():
    assert NO_OBJECTIVES_MSG
    lowered = NO_OBJECTIVES_MSG.lower()
    assert "add a source" in lowered
    assert "ready" in lowered or "index" in lowered
    assert "isn't built yet" not in lowered
