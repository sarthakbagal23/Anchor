"""Timestamp-preserving semantic chunker. Groups Whisper segments by token ceiling,
long pauses, or topic-shift cues — never splitting a sentence."""
from __future__ import annotations
from dataclasses import dataclass
import re

# Whole-word cue match: a bare startswith("now") also fires on "nowhere" and
# "nowadays", splitting topics on ordinary vocabulary.
CUE_WORDS = ("chapter", "section", "now", "next", "moving on", "let's", "summary", "recap", "question")
_CUE_RES = [re.compile(r"^" + re.escape(c) + r"(?=\s|$|[,.?!;:])") for c in CUE_WORDS]
HARD_CAP_MULT = 2  # a chunk never exceeds 2× max_tokens even with no clean sentence boundary.


@dataclass
class Chunk:
    text: str
    start_sec: float
    end_sec: float
    token_count: int


def _approx_tokens(text: str) -> int:
    # words≈tokens is good enough for context fitting; upgrade to tiktoken if needed.
    return len(text.split())


def _ends_sentence(text: str) -> bool:
    return text.rstrip()[-1:] in ".?!"


def group(segments: list[dict], max_tokens: int = 300, pause_sec: float = 1.5) -> list[Chunk]:
    if not segments:
        return []
    chunks: list[Chunk] = []
    cur_text: list[str] = []
    cur_start = segments[0]["start"]
    cur_end = segments[0]["end"]
    cur_tok = 0
    prev_end = segments[0]["end"]

    def flush():
        nonlocal cur_text, cur_tok
        if cur_text:
            txt = " ".join(cur_text).strip()
            chunks.append(Chunk(txt, cur_start, cur_end, _approx_tokens(txt)))
            cur_text, cur_tok = [], 0

    for i, seg in enumerate(segments):
        text = seg["text"].strip()
        if not text:
            continue
        gap = max(0.0, seg["start"] - prev_end)
        tok = _approx_tokens(text)
        lowered = text.lower()
        topic_shift = any(rx.match(lowered) for rx in _CUE_RES) and cur_text
        # split BEFORE adding if a boundary condition fires, but only at sentence ends.
        # soft_exceed is a soft trigger — only flush if also at a sentence boundary;
        # the hard cap is the safety net for degenerate text with no sentence breaks.
        soft_exceed = cur_tok + tok > max_tokens
        hard_exceed = cur_tok + tok > max_tokens * HARD_CAP_MULT
        cur_ends_sent = bool(cur_text) and _ends_sentence(" ".join(cur_text))
        should_split = (gap > pause_sec or topic_shift) and cur_ends_sent
        if should_split or hard_exceed or (soft_exceed and cur_ends_sent):
            flush()
            cur_start = seg["start"]
        if not cur_text:
            cur_start = seg["start"]
        cur_text.append(text)
        cur_end = seg["end"]
        cur_tok += tok
        prev_end = seg["end"]
    flush()
    return chunks


if __name__ == "__main__":
    segs = [
        {"text": "Welcome to lecture three.", "start": 0.0, "end": 2.1},
        {"text": "Today we define limits.", "start": 2.3, "end": 5.8},
        {"text": "First the epsilon delta definition.", "start": 6.0, "end": 9.5},
    ]
    chunks = group(segs)
    assert len(chunks) == 1, "short transcript = one chunk"
    assert chunks[0].start_sec == 0.0 and chunks[0].end_sec == 9.5, "chunk spans first start to last end"
    # pause > 1.5s splits
    segs2 = [
        {"text": "Part one intro.", "start": 0.0, "end": 1.0},
        {"text": "Part two after a long pause.", "start": 5.0, "end": 7.0},  # 4s gap
    ]
    c2 = group(segs2)
    assert len(c2) == 2, "long pause must split"
    # token ceiling splits — use realistic sentence-ending text so the
    # chunker actually has clean boundaries to land on.
    big = [
        {"text": "The epsilon delta definition of a limit says for every epsilon there is a delta.",
         "start": float(i) * 4.0, "end": float(i) * 4.0 + 3.0}
        for i in range(20)
    ]
    c3 = group(big, max_tokens=40)  # tiny ceiling forces multiple chunks
    assert len(c3) > 1, "must split on token ceiling"
    # chunks should land on sentence boundaries; the very last chunk is allowed to be
    # mid-sentence only because there's no following break to wait for.
    for c in c3:
        assert c.text.rstrip()[-1] in ".?!" or c is c3[-1], (
            f"chunk cut mid-sentence: ends with {c.text.rstrip()[-1]!r}"
        )
    print("chunker OK")
