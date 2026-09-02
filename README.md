# Study Sessions (working name)

An open-source, self-hostable study environment for students with an upcoming
test. Create a **course**, name the **unit**, paste a YouTube lecture, then choose
how much time you have and whether you need a Smart Cram or Deep Study session.

Every answer is grounded strictly in the transcript, with **time-coded citations
that seek the video to the exact moment** the lecturer explained it.

Bring your own model — cloud (NVIDIA NIM, OpenRouter, OpenAI) or local (Ollama, LM
Studio) — through one OpenAI-compatible config. Zero-config defaults handle
embeddings, reranking, and transcription locally.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows; `source .venv/bin/activate` on macOS/Linux
pip install -e ".[local]"       # installs fastapi + faster-whisper + sentence-transformers + yt-dlp
python main.py                  # opens http://127.0.0.1:8765 in your browser
```

On first run, edit your config at `~/.opennotebook/config.yaml`: set an LLM
`base_url`, `api_key`, and `model`. Everything else works with bundled defaults.

## Config

See [`config.example.yaml`](config.example.yaml). Key fields:

| Key | Default | Notes |
|-----|---------|-------|
| `llm.base_url` | — | Any OpenAI-compatible endpoint (NVIDIA NIM, OpenRouter, Ollama, LM Studio) |
| `llm.model` | — | Model name for that endpoint |
| `embeddings.provider` | `bundled` | `bundled` (CPU MiniLM) or `openai_compatible` |
| `reranker.provider` | `bundled` | `bundled` (cross-encoder). Remote reranker is a v1.5 extension |
| `whisper.provider` | `bundled` | `bundled` (faster-whisper) or `openai_compatible` |
| `whisper.model` | `small` | `tiny|base|small|medium|large` |

## How it works

1. **Ingest**: `yt-dlp` pulls audio → `faster-whisper` transcribes with timestamps →
   a timestamp-preserving chunker groups segments → chunks are embedded and stored
   in SQLite + sqlite-vec.
2. **Ground**: each question is embedded → sqlite-vec retrieves top-20 chunks → a
   cross-encoder reranks → the top passages are fit to the model's context window
   (so local 8k models still work) → a strict "answer only from passages + cite [#]"
   prompt is streamed back.
3. **Cite**: `[#]` markers are parsed and mapped to `(source, timestamp)`; the
   frontend renders them as `⌜Title · mm:ss⌟` tokens. Click → the YouTube player
   seeks to that moment.

## Why

This is not another general-purpose AI notebook. The core workflow is:

**Course → Unit → Study Session → Practice → Review**

It starts with the student's real constraint—"I have 30 minutes"—and turns their
class material into grounded explanations, high-value concepts, practice prompts,
and direct links back to the lecture.

## Scope (MVP)

YouTube sources only. PDF/TXT/Markdown/DOCX ingestion, Audio Overview (podcast),
PDF page rendering, and a Tauri desktop shell are designed-for and explicitly
deferred (see `docs/superpowers/specs/2026-08-06-opennotebook-design.md` §13).

## License

MIT.
