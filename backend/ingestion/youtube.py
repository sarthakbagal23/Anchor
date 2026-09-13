"""YouTube audio download via yt-dlp subprocess."""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import tempfile

_YT_ID_RES = [
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{6,})"),
    re.compile(r"[?&]v=([A-Za-z0-9_-]{6,})"),
    re.compile(r"/embed/([A-Za-z0-9_-]{6,})"),
    re.compile(r"/shorts/([A-Za-z0-9_-]{6,})"),
]


def extract_id(url: str) -> str | None:
    """Best-effort YouTube video id from common URL forms (for dedup checks)."""
    for rx in _YT_ID_RES:
        m = rx.search(url or "")
        if m:
            return m.group(1)
    return None


def download(url: str, out_dir: str | None = None) -> tuple[str, str | None, str, str, int]:
    """Returns (audio_path, vtt_path, title, youtube_id, duration_sec)."""
    out_dir = out_dir or tempfile.mkdtemp(prefix="onb_")
    os.makedirs(out_dir, exist_ok=True)
    out_tmpl = os.path.join(out_dir, "%(id)s.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp", "-f", "bestaudio",
        "--write-auto-sub", "--sub-format", "vtt", "--sub-langs", "en",
        "-o", out_tmpl, "--print-json", "--no-playlist", "--newline", url,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if res.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {res.stderr.strip()}")
    data = json.loads(res.stdout.strip().splitlines()[-1])
    
    audio_path = os.path.join(out_dir, f"{data['id']}.{data.get('ext', 'webm')}")
    if not os.path.exists(audio_path):
        for f in os.listdir(out_dir):
            if f.startswith(data["id"]) and not f.endswith(".vtt") and not f.endswith(".json"):
                audio_path = os.path.join(out_dir, f)
                break
                
    vtt_path = os.path.join(out_dir, f"{data['id']}.en.vtt")
    if not os.path.exists(vtt_path):
        vtt_path = None
        
    return audio_path, vtt_path, data.get("title", "Untitled"), data["id"], int(data.get("duration") or 0)


if __name__ == "__main__":
    from unittest.mock import patch, MagicMock
    with patch("__main__.subprocess") as sub:
        sub.run.return_value = MagicMock(returncode=0)
        sub.run.return_value.stdout = '{"title":"Lecture 3","id":"vid123","duration":600}'
        path, vtt, title, yid, dur = download("https://youtu.be/vid123", out_dir="/tmp/x")
        assert title == "Lecture 3" and yid == "vid123" and dur == 600
    print("youtube OK")
