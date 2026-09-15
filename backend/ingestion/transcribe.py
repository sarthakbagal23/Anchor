"""Transcription: faster-whisper (bundled default) or OpenAI-compatible endpoint.
Emits [{text, start, end}] segments."""
from __future__ import annotations
from backend.config import AppConfig
import os
import re
import threading

# Process-level model cache keyed by (model, device, compute): loading from
# disk costs tens of seconds, so without this every ingest pays full load.
# Transcriptions are serialized on a lock (cheap relative to the transcribe
# itself); concurrent ingests share the one loaded model instead of loading N.
_MODEL_CACHE: dict = {}
_MODEL_LOCK = threading.Lock()


def _whisper_settings():
    def env(*names, default):
        for name in names:
            value = os.environ.get(name)
            if value:
                return value
        return default

    return (
        env("ANCHOR_WHISPER_DEVICE", "OPENNOTEBOOK_WHISPER_DEVICE", default="cpu"),
        env("ANCHOR_WHISPER_COMPUTE", "OPENNOTEBOOK_WHISPER_COMPUTE", default="int8"),
        int(env("ANCHOR_WHISPER_BEAM", "OPENNOTEBOOK_WHISPER_BEAM", default="1")),
    )

def parse_vtt(vtt_path: str) -> list[dict]:
    with open(vtt_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    segments = []
    pattern = re.compile(r"(\d{2}:)?(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}:)?(\d{2}):(\d{2})\.(\d{3})")
    
    current_start, current_end = 0.0, 0.0
    
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "Style:")):
            continue
            
        m = pattern.search(line)
        if m:
            def _sec(h, m_, s, ms):
                return (int(h[:-1]) if h else 0) * 3600 + int(m_) * 60 + int(s) + int(ms) / 1000.0
            
            current_start = _sec(m.group(1), m.group(2), m.group(3), m.group(4))
            current_end = _sec(m.group(5), m.group(6), m.group(7), m.group(8))
            continue
            
        text = re.sub(r"<[^>]+>", "", line).strip()
        if text:
            # YouTube VTT roll-up captions repeat lines: extend on growth,
            # drop only exact duplicates. A substring test here would eat a
            # genuinely new short caption that happens to appear inside the
            # previous line.
            if segments and text == segments[-1]["text"]:
                continue
            if segments and segments[-1]["text"] in text:
                segments[-1]["text"] = text
                segments[-1]["end"] = current_end
                continue
            segments.append({"text": text, "start": current_start, "end": current_end})
            
    return segments



def transcribe(audio_path: str, cfg: AppConfig) -> list[dict]:
    w = cfg.whisper
    if w.provider == "openai_compatible" and w.base_url:
        return _remote_transcribe(audio_path, w)
    return _bundled_transcribe(audio_path, w.model or "small")


def _bundled_transcribe(audio_path: str, model_name: str) -> list[dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError("faster-whisper is not installed. Run `pip install faster-whisper` or `pip install -e .[local]`") from e
    device, compute, beam = _whisper_settings()
    key = (model_name, device, compute)
    model = _MODEL_CACHE.get(key)
    if model is None:
        model = WhisperModel(model_name, device=device, compute_type=compute)
        _MODEL_CACHE[key] = model
    with _MODEL_LOCK:
        segments, _info = model.transcribe(audio_path, beam_size=beam)
        return [{"text": s.text.strip(), "start": float(s.start), "end": float(s.end)} for s in segments]


def _remote_transcribe(audio_path: str, w) -> list[dict]:
    import httpx
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    r = httpx.post(
        f"{w.base_url}/audio/transcriptions",
        headers={"Authorization": f"Bearer {w.api_key or 'unused'}"},
        files={"file": ("audio.opus", audio_bytes, "audio/opus")},
        data={"model": w.model or "whisper-1", "response_format": "verbose_json"},
        timeout=600,
    )
    r.raise_for_status()
    data = r.json()
    return [{"text": s["text"].strip(), "start": float(s["start"]), "end": float(s["end"])} for s in data.get("segments", [])]


if __name__ == "__main__":
    import sys
    import types
    from unittest.mock import MagicMock
    from backend.config import AppConfig, Section
    cfg = AppConfig(whisper=Section(provider="bundled", model="tiny"))
    fake_seg = MagicMock(text=" hello world ", start=0.0, end=2.0)
    fw = types.ModuleType("faster_whisper")
    class _FakeWM:
        def __init__(self, *a, **kw): pass
        def transcribe(self, path, **kw):
            return iter([fake_seg]), MagicMock()
    fw.WhisperModel = _FakeWM
    sys.modules["faster_whisper"] = fw
    try:
        segs = transcribe("/fake/path.opus", cfg)
        assert segs == [{"text": "hello world", "start": 0.0, "end": 2.0}], f"got {segs}"
        print("transcribe OK")
    finally:
        del sys.modules["faster_whisper"]
