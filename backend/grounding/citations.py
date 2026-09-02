"""Citation wire parser. The LLM emits passage numbers like [1], [^2], [1,3]
(intended format [#] per spec §7e, but parsing is tolerant). The frontend
renders these as ⌜title · mm:ss⌟ tokens — that's display-only, handled in app.js."""
from __future__ import annotations
import re

# matches [1], [^2], [1,3], [#]; captures the inner digits (skipping caret/space).
# [#] (no digits) is intentionally rejected — the LLM never emits that bad form.
_CITE = re.compile(r"\[(?:\^)?((?:\d+\s*,\s*)*\d+)\]")


def parse_citations(text: str) -> list[int]:
    out: list[int] = []
    for m in _CITE.finditer(text):
        inner = m.group(1)
        for part in inner.split(","):
            part = part.strip()
            if part.isdigit():
                n = int(part)
                if n not in out:  # de-dup, preserve first-occurrence order
                    out.append(n)
    return out


def build_map(passage_chunks: list[dict]) -> dict[int, dict]:
    """passage_chunks: passage metadata, optionally including page_num."""
    return {pc["passage"]: pc for pc in passage_chunks}


if __name__ == "__main__":
    # Wire-format tolerance (§7f): [#], [1], [3], [^2], [1,3]
    assert parse_citations("nothing here") == []
    assert parse_citations("see [1]") == [1]
    assert parse_citations("[1] then [2]") == [1, 2]
    assert parse_citations("[^3]") == [3]
    assert parse_citations("[1,3]") == [1, 3]
    assert parse_citations("[1,3,5]") == [1, 3, 5]
    assert parse_citations("[1, 1, 2]") == [1, 2], "de-dup preserves first occurrence"
    # build_map: passage number → chunk metadata for the renderer
    passages = [
        {"passage": 1, "chunk_id": 10, "source_title": "Calculus", "start_sec": 822},
        {"passage": 2, "chunk_id": 11, "source_title": "Calculus", "start_sec": 1630},
    ]
    m = build_map(passages)
    assert m[1]["chunk_id"] == 10 and m[2]["start_sec"] == 1630
    print("citations OK")
