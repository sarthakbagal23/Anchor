# OpenNotebook — Design Spec

> **Status: SUPERSEDED (kept as history).** This spec describes the August 2026
> MVP plan — including explicit non-goals (PDF ingestion, auto study guides)
> that have since shipped. For the current architecture, read
> `docs/PROJECT_CONTEXT.md` and `docs/superpowers/sdd/progress.md`. The
> original decisions below are preserved as a record, not as instructions.

> Working name. "NotebookLM" is Google's trademark — using it directly in the repo
> name is legally risky for an open-source project. "OpenNotebook" sidesteps it.
> Rename freely; nothing in the code couples to the name.

**Date:** 2026-08-06
**Status:** Draft, pending user review
**Author:** brainstormed with the user

## 1. Vision

An open-source, self-hostable study companion inspired by Google NotebookLM. You
drop a source into a notebook and chat with it — answers are grounded strictly in
your sources, with citations that point back to the exact passage.

The MVP's headline source type is **YouTube**: paste a lecture link, it transcribes
and indexes the transcript, and you ask questions of the lecture. Every answer cites
`[Lecture · mm:ss]`, and clicking the citation seeks the embedded YouTube player to
that moment. This is the feature every generic "chat with your PDF" tool misses, and
it is the project's differentiator.

Bring your own model — cloud or local — through a single OpenAI-compatible config.
Zero-config defaults (bundled local embedder, reranker, and Whisper) make it work
out of the box for a student with nothing but a laptop.

## 2. Goals & Non-Goals

### MVP goals
1. **Grounded Q&A chat** grounded strictly in the notebook's sources, with citations.
2. **Multi-source notebooks** — organize sources per notebook, per-notebook chat.
3. **YouTube source ingestion** — yt-dlp + Whisper → timestamped, searchable transcript.
4. **Time-coded citation seek tokens** — click a citation, the YouTube player jumps
   to that moment. The "exceed NotebookLM" move for video.
5. **BYO-everything config** — one OpenAI-compatible socket for the LLM; bundled
   defaults for embeddings, rerank, and Whisper, all overridable.
6. **Local-first single process** — one command launches a local web app.

### Explicit non-goals (v1.5+)
- Audio Overview / podcast generation (heavy TTS — separate project).
- Auto study-guide generation.
- PDF page rendering with highlight overlays (text-passage panel in MVP).
- PDF / TXT / Markdown / DOCX ingestion (designed for, not built in MVP).
- Desktop packaging via Tauri (the web app is Tauri-ready by construction; the
  wrapper is a later deliverable, not a rewrite).
- Multi-user / server deployment (single-user local tool).

## 3. Decisions Locked (with rationale)

| # | Decision | Why |
|---|----------|-----|
| D1 | LLM via OpenAI-compatible config (`base_url`, `api_key`, `model`) only | One socket covers NVIDIA NIM, OpenRouter, Claude (via OpenRouter/compat bridge), Ollama, LM Studio, OpenAI, Groq, Together. No provider lock-in. |
| D2 | Local models supported | Free — Ollama/LM Studio expose the same OpenAI API at localhost. No new code path. Constrains grounding design (small context windows). |
| D3 | Grounding = **hybrid retrieval + rerank** (Path C) | Matches/exceeds NotebookLM. Works on small-context local models by fitting fewer top chunks. Outperforms naive RAG and dies-context stuff-the-context. |
| D4 | Embeddings + reranker = **bundled local default, overridable** | Zero-config out of the box; power users point at their own `/v1/embeddings` style endpoint. Consistent with D1's BYO ethos. |
| D5 | YouTube-first ingestion; other source types later | Sharp, creative MVP nobody ships well. Timestamp citations → seek() is the demo. |
| D6 | Citation UX = **time-coded seek tokens**, not pills | Distinctive without being maximalist; every animation pinned to a real value (chunk duration, playhead). |
| D7 | Single-process FastAPI + embedded frontend (Approach A) | Ships fastest, one command, self-hostable, Tauri-wrappable later. |
| D8 | Clean module separation inside one process (Approach C) | Professional code structure without microservice ops cost. Each module testable/swappable. |
| D9 | SQLite + sqlite-vec for persistence + vectors | One file, no server, ships clean. |
| D10 | SSE streaming, not WebSockets | Boring stack, sufficient for token streaming + progress events. |

## 4. Architecture — One Process, Clean Modules

Deployment shape (A) + code structure (C): one FastAPI process serves a static
frontend, talks to SQLite, and runs ingestion as in-process async tasks.

```
┌─────────────────────────── one process: python main.py ───────────────────────────┐
│                                                                                    │
│   frontend/ (static, served by FastAPI)        backend/                            │
│   ┌──────────────────────┐                     ┌─────────────────────────────┐      │
│   │ index.html            │  ─── HTTP/SSE ───▶ │ api.py  (FastAPI routes)    │      │
│   │ app.js  (chat/seek)   │                     │ llm_client.py (OpenAI-compat)│     │
│   │ player.js (YT iframe) │                     │ ingestion/  youtube,        │      │
│   └──────────────────────┘                     │   transcribe, ingest         │      │
│                                                 │ grounding/  chunker, embedder,│     │
│                                                 │   retriever, reranker,       │      │
│                                                 │   pipeline                    │      │
│                                                 │ store.py  (SQLite + vec)     │      │
│                                                 │ config.py (~/.opennotebook)   │      │
│                                                 └─────────────────────────────┘      │
│                                                                                    │
│   external: yt-dlp (subprocess), faster-whisper (in-proc), sentence-transformers   │
│             (in-proc), optional remote embeddings/whisper/LLM over HTTP              │
└────────────────────────────────────────────────────────────────────────────────────┘
```

### Module responsibilities (each one job, one interface, unit-testable)

- **`api.py`** — FastAPI routes: notebook CRUD, source add + progress, chat (SSE), config.
  No business logic; delegates to `ingestion` and `grounding`.
- **`llm_client.py`** — thin wrapper over an OpenAI-compatible client. Reads config,
  exposes `stream(messages, **params)`. Knows the model's max context (from config or
  model metadata); exposes it to the grounding pipeline.
- **`ingestion/youtube.py`** — `download(url) -> audio_path` via yt-dlp subprocess.
- **`ingestion/transcribe.py`** — `transcribe(audio_path) -> [Segment{text,start,end}]`.
  Default: faster-whisper (bundled). Override: cloud Whisper-style endpoint.
- **`ingestion/ingest.py`** — orchestrates download → transcribe → chunk → embed → store.
  Updates `sources.status` for the progress stream. Isolates errors per-source.
- **`grounding/chunker.py`** — groups Whisper segments into timestamp-preserving
  semantic chunks (never splits sentences; cuts on long pauses / topic shifts;
  token ceiling). Emits `Chunk{text, start_sec, end_sec, source_id}`.
- **`grounding/embedder.py`** — interface `embed(text) -> vector`. Default: bundled
  CPU sentence-transformers. Override: OpenAI-compatible `/v1/embeddings`.
- **`grounding/retriever.py`** — `retrieve(query_vec, notebook_id, k=20) -> [chunk_id]`
  via sqlite-vec cosine.
- **`grounding/reranker.py`** — `rerank(query, chunks) -> sorted chunks`. Default:
  bundled cross-encoder. Override: endpoint.
- **`grounding/pipeline.py`** — the orchestrator (§7). `ground(query, notebook_id,
  chat_history) -> (prompt, citation_map)` and `stream_answer(...)`.
- **`store.py`** — schema, migrations, vector ops (sqlite-vec).
- **`config.py`** — load/validate `~/.opennotebook/config.yaml`; graceful degrade.

## 5. Data Model (SQLite + sqlite-vec)

```sql
notebooks(id PK, title, created_at)

sources(
  id PK, notebook_id FK, type TEXT,        -- 'youtube' for MVP
  title, youtube_id, duration_sec,
  status,        -- queued | downloading | transcribing | chunking | ready | failed
  error,         -- nullable, last failure reason
  created_at
)

chunks(
  id PK, source_id FK,
  ord INT,       -- ordinal within source
  text,
  start_sec,     -- first segment start (nullable for non-AV source types later)
  end_sec,       -- last segment end
  token_count
)

-- vectors live in a sqlite-vec virtual table keyed by chunk rowid
CREATE VIRTUAL TABLE vec_chunks USING vec0(embedding FLOAT[N]);

messages(
  id PK, notebook_id FK, role,            -- 'user' | 'assistant'
  content,                                 -- assistant stores citation_map for re-render
  created_at
)
```

`start_sec` / `end_sec` are nullable so a future PDF chunk can carry a `page`
column instead — the schema doesn't assume every source is timestamped.

**Citation wire format vs render format.** The LLM emits `[#]` (the *wire format*
— a passage number from the prompt's `[1] (…)` passages block). The frontend maps
`#` → chunk → `(source title, start_sec)` and renders the token as `⌜title · mm:ss⌟`
(the *render format*, §8). The two formats are never confused: `[#]` is what crosses
the wire and gets parsed; `⌜…⌟` is purely a display affordance.

## 6. Ingestion Pipeline (YouTube → timestamped searchable transcript)

1. **`youtube.download(url)`** — yt-dlp pulls the audio stream to a temp `.opus`.
2. **`transcribe.transcribe(audio)`** — faster-whisper `small` (CPU, default) emits
   segments `[{text, start, end}]`.
3. **`chunker.group(segments)`** — consecutive segments accumulate into a chunk until:
   a token ceiling (~300 tokens) is hit, **or** a pause > 1.5s between segments, **or**
   a lightweight topic-shift heuristic fires (e.g. heading-like cue words). A chunk
   never splits a sentence. `chunk.start_sec` = first segment's start;
   `chunk.end_sec` = last segment's end.
4. **`embedder.embed(chunk.text)`** — vector written to `vec_chunks`, metadata to `chunks`.

**Surfaces the wait.** `sources.status` drives a live UI progress indicator:
`downloading → transcribing → chunking → ready`. A 1hr lecture on CPU `small` takes
minutes; the UI must say so, not hang silently. Cloud Whisper override is the
fast path.

**`ponytail:` ceiling** (marked in code): bundled `small` Whisper is ~500MB, downloaded
on first transcribe, CPU-slow on long files. Upgrade path: cloud Whisper override
(already wired) or `medium`/`large` for users with a GPU.

## 7. Grounding Pipeline (the crème — where we match/exceed NotebookLM)

Six stages, each debuggable independently. `pipeline.ground(query, notebook_id)`
returns the assembled prompt + a `citation_map` (passage number → chunk → source /
timestamp). `pipeline.stream_answer(...)` streams the LLM response.

### 7a. Chunking
Timestamp-preserving, semantic (§6). Non-negotiable: timestamps make seek citations
possible.

### 7b. Retrieval (top-N)
Query embedded with the **same** embedder used at ingest → sqlite-vec cosine search
returns top **~20** chunks from the notebook. Cheap first pass; deliberately
over-retrieves so the reranker has signal to work with.

### 7c. Rerank (top-K)
A cross-encoder re-scores each `(query, chunk)` pair with real attention; we keep the
top **~5–8**. This is the step that separates crème from naive RAG — it kills
"retrieved by keyword overlap but actually irrelevant" false positives and reorders the
truly-relevant chunks to the top of the context. **MVP uses the bundled cross-encoder
only.** The `openai_compatible` override shown in §10 is a deferred extension point —
the `Reranker` *interface* (`rerank(query, chunks) -> scored chunks`) is pinned now so
a remote rerank API (Cohere/Jina-style) slots in later without touching callers.

### 7d. Context assembly (context-aware)
We know the model's max context (from config / model metadata; sensible default small
for local models, read from the API for cloud). Fit as many top-K chunks as
comfortable, always reserving ~1500 tokens for the answer. **This is what makes the
pipeline work on a local 8k-context model** — it still gets the *best* chunks, just
fewer of them. The pipeline adapts to the model, not the other way around.

### 7e. Grounded prompt
Strict, NotebookLM-grade:

```
SYSTEM: You are a study assistant answering ONLY from the provided passages.
For every factual claim, cite the passage as [#] — the number in brackets before
each passage. If the cited passage is timestamped, also include [mm:ss].
If the passages do not contain the answer, say "Not covered in the sources."
Do not speculate. Do not use outside knowledge.

PASSAGES:
[1] (Source: "Lecture 3 — Calculus", 13:42–14:05) ...transcript text...
[2] (Source: "Lecture 3 — Calculus", 27:10–27:50) ...

USER: {question}
```

### 7f. Citation parse + map
The assistant streams tokens. We parse `[#]` markers (tolerant: `[#]`, `[1]`, `[^1]`,
`[1,3]`) from the streamed text; each number maps to a chunk id → `source_id` →
`(title, start_sec)`. The frontend renders `[#]` as a clickable seek token
(§8). If a model ignores the format, answers still render — just without chips
(graceful). The final SSE event carries the resolved citation map so chips can be
materialized even for citations that complete at the end of the stream.

## 8. Citation UX — Time-Coded Seek Tokens

**Not pills.** Citations are inline *timeline gateways* — compact tokens fused into
the prose like footnotes you can ride.

```
The professor defines limits using epsilon-delta here ⌜L3 · 13:42⌟, then shows
why the naive definition fails ⌜L3 · 27:10⌟ and pivots to the formal proof ⌜L3 · 31:05⌟.
```

- **`⌜…⌟` corner brackets** — read as "lift from the text," distinct from academic
  `[1]` footnotes so it feels native to video.
- **Hover** → the cited transcript snippet fades in as a peek preview below the token,
  anchored, no layout shift. Click is the commit gesture.
- **Click** → the right source panel focuses the source and the YouTube iframe calls
  `player.seekTo(start_sec, true)` simultaneously. The token gets a thin animated
  underline that acts as a tracer for the cited clip's duration (chunk `end_sec` −
  `start_sec`): it fills as the passage plays, completes when it ends. "Watch with me."
- **Visited state** → token desaturates once clicked (link-color convention reused,
  not reinvented) so you can track which citations you've already checked.
- **Active citation** → a vertical accent bar on the left edge of the *answer
  paragraph*, sliding to whichever token is currently playing in the panel. Passive
  progress: glance at the answer, see which cited moment is live.
- **Multi-cite** `⌜L3 · 13:42, 27:10⌟` collapses to one token; clicking cycles.

**Honored animation discipline:** every motion is pinned to a real value (chunk
duration, playhead position, visited booleans). No decorative animation. The tokens'
job is *transport*, not garnish. `frontend-design` skill invoked at build time to make
typography and motion earn this rather than template it.

## 9. API Surface + Streaming

- `POST /api/notebooks` · `GET /api/notebooks` · `GET /api/notebooks/{id}` — CRUD.
- `POST /api/notebooks/{id}/sources` `{url}` → queues ingestion, returns `source_id`.
- `GET /api/notebooks/{id}/sources/{sid}` → status + metadata for polling.
- `GET /api/notebooks/{id}/sources/{sid}/stream` — **SSE** ingestion progress
  (`downloading → transcribing → chunking → ready | failed`).
- `POST /api/notebooks/{id}/chat` `{message}` → **SSE** streaming assistant tokens
  (incremental `[#]` chips render as they arrive); final event carries the resolved
  citation map.
- `GET /api/config` · `PATCH /api/config` — read/update settings (persisted to
  `~/.opennotebook/config.yaml`).

SSE over plain fetch — no WebSocket dependency (D10).

## 10. Config — BYO-everything with zero-config defaults

`~/.opennotebook/config.yaml`:

```yaml
llm:
  base_url: https://integrate.api.nvidia.com/v1   # or http://localhost:1234/v1 (LM Studio)
  api_key: ${NVIDIA_API_KEY}
  model: nvidia/llama-3.1-nemotron-70b
  max_context: null                                 # null = query model metadata; else override

embeddings:
  provider: bundled                                 # bundled (CPU sentence-transformers) | openai_compatible
  base_url: null
  api_key: null
  model: null

reranker:
  provider: bundled                                 # bundled cross-encoder | openai_compatible
  base_url: null
  api_key: null
  model: null

whisper:
  provider: bundled                                 # bundled faster-whisper | openai_compatible
  base_url: null
  api_key: null
  model: small                                      # tiny | base | small | medium | large
```

A **settings page** in the UI edits this. Defaults make local-only use work with zero
config; every layer is overridable for cloud keys. `config.py` validates on load and
**degrades gracefully**: if `embeddings` is unreachable, fall back to keyword/BM25-style
retrieval + a visible warning, never a hard crash. Heavy ML deps (`faster-whisper`,
`sentence-transformers`, `torch`) are lazy-imported so a user on a cloud Whisper +
cloud embeddings config installs none of them.

## 11. Testing — one runnable check per non-trivial module (no framework zoo)

Per the lazy-test discipline: the *smallest thing that fails if the logic breaks*.

- `grounding/chunker.py` — `__main__` asserts chunks carry segment timestamps,
  respect the token ceiling, and never split a sentence.
- `grounding/pipeline.py` — `__main__` asserts that for a small known transcript, a
  semantically-related query retrieves the right chunk in top-K, and that
  context-fitting scales chunk count down for a small configured max_context.
- `llm_client.py` — mock the OpenAI endpoint; assert streaming + config wiring.
- `config.py` — assert graceful degrade (unreachable embeddings → BM25 + warning).
- citation parser — assert tolerant parsing of `[#]`, `[1]`, `[^1]`, `[1,3]`.

Each check runs as `python -m backend.<module>` and exits non-zero on regression. A
pytest suite is added only when it earns one — not MVP.

## 12. Honest Caveats (so there are no surprises at build time)

1. **`faster-whisper` + `torch` are chunky deps** (hundreds of MB). Trade for zero-config
   local transcription. Mitigated: lazy-imported + optional; a cloud-only user pulls
   none of them.
2. **Streaming citation parsing is the fiddliest piece** — depends on the LLM emitting
   `[#]` markers reliably. Prompt is strict + parsing is tolerant. Worst case: answers
   render without chips. Never a hard failure.
3. **Transcription wait is real** — a long lecture on CPU `small` takes minutes. The UI
   surfaces progress; the cloud Whisper override is the speed escape hatch.
4. **Single-process risk** — a crashy transcribe job could, in theory, take down the
   process. Acceptable for a single-user local tool; mitigated with per-source
  try/except isolation in `ingest.py`.

## 13. Roadmap (explicitly out of MVP; designed-for, not built-for)

- **v1.5** Tauri shell around the same web app → installable desktop `.exe`/`.dmg`.
  Zero rewrite; the app is already a local web app.
- **v1.5** PDF / TXT / Markdown / DOCX ingestion (chunker already timestamp-nullable;
  a `page` column slots in beside `start_sec`).
- **v1.6** PDF page rendering in the source panel (`#3)` citation tier) via PDF.js +
  chunk→page-coordinate mapping.
- **v2.0** Audio Overview / podcast generation (TTS sub-system; separate project).
- **v2.0** Auto study-guide generation from the notebook's sources.
