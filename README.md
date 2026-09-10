# OpenNotebook

**Study deeply. Cram intelligently.**

OpenNotebook is an open-source, self-hostable study environment. Everything is
organized as **Course → Unit → Sources → Study Session**: create a course, add a
unit, drop in lectures and PDFs, and study inside a workspace where every answer
links back to the material it came from.

![OpenNotebook chat with time-coded citations](docs/screenshots/chat-citations.png)

Drop in a lecture or PDF, ask questions, and get answers grounded in your
material with clickable citations back to the exact timestamp or page. On top of
that foundation: per-objective study guides and server-graded multiple-choice
practice quizzes, with per-question attempt history recorded as the raw material
for weak-spot tracking (not built yet).

Bring your own model — cloud (NVIDIA NIM, OpenRouter, OpenAI) or local (Ollama,
LM Studio) — through one OpenAI-compatible config. Zero-config defaults handle
embeddings, reranking, and transcription locally.

## Quick start

```bash
git clone https://github.com/sarthakbagal23/OpenNotebook.git
cd OpenNotebook
python -m venv .venv
.venv/Scripts/activate          # Windows; `source .venv/bin/activate` on macOS/Linux
pip install -e ".[local]"       # fastapi + faster-whisper + sentence-transformers + yt-dlp
python main.py                  # opens http://127.0.0.1:8765 in your browser
```

On first run, edit your config at `~/.opennotebook/config.yaml`: set an LLM
`base_url`, `api_key`, and `model`. Everything else works with bundled defaults
(see [`config.example.yaml`](config.example.yaml)).

## Screenshots

| Chat with citations | Practice quiz |
|---------------------|---------------|
| ![Chat with citations](docs/screenshots/chat-citations.png) | ![Practice quiz](docs/screenshots/practice-quiz.png) |

*Add your screenshots to `docs/screenshots/` and they'll appear above.*

## Current status (v0.1.0, early release)

Working: YouTube + PDF sources, grounded chat with citations, per-objective
study guides, and server-graded practice quizzes — all persisted per workspace
and regenerable from the UI.

Known limitations:

- **Learning objectives only exist for AP units.** Objectives come from a CED
  import that runs for AP courses; automatic objective extraction for non-AP
  classes isn't built yet. On a unit with no objectives, study-guide and
  practice-quiz generation say so explicitly instead of generating.
- **Local mode only.** Multi-user cloud auth lives on the unmerged,
  experimental `cloud-auth` branch and is not part of this release.

## How it works

1. **Ingest**: `yt-dlp` pulls audio → `faster-whisper` transcribes with timestamps →
   a timestamp-preserving chunker groups segments (PDFs are extracted per page) →
   chunks are embedded and stored in SQLite + sqlite-vec.
2. **Ground**: each question is embedded → sqlite-vec retrieves the top chunks → a
   cross-encoder reranks → the top passages are fit to the model's context window
   (so local 8k models still work) → a strict "answer only from passages + cite [#]"
   prompt is streamed back.
3. **Cite**: `[#]` markers are parsed and mapped to `(source, timestamp/page)`; the
   frontend renders them as clickable tokens. Click → the YouTube player seeks to
   that moment, or the PDF viewer jumps to that page.

Study guides and practice quizzes reuse the same retrieval to ground
per-objective sections and questions in your sources.

## Architecture

FastAPI serves the JSON API and the static frontend from one process
(`main.py`). Persistence is SQLite + sqlite-vec (workspaces, sources, chunks,
vectors, messages, objectives, guides, quizzes). All model access goes through
one OpenAI-compatible client, so cloud and local models are interchangeable.

## License

MIT — see [`LICENSE`](LICENSE). Issues and PRs welcome; this is early software,
so expect rough edges and report them.
