# Anchor — Product Context and Build Plan

This is the canonical product context for continuing work on Anchor. Read this before making product or UX changes.

> Note: this project started life as "OpenNotebook" (see the historical docs
> under `docs/superpowers/`). Code, config, and UI are renamed; only
> explicitly historical documents keep the old name.

## Product thesis

Anchor is not trying to be another general-purpose AI notebook or a NotebookLM clone. It is a study environment that turns messy class material into a serious, focused study session.

The student should be able to say: “I have a test Friday. I want to understand this, but I do not have time to organize everything myself.” The product should turn their course material into understanding, practice, and targeted review.

Positioning:

> Study deeply. Cram intelligently.

Open source, self-hosting, and bring-your-own-model support are important implementation values, but the student outcome is the primary product identity.

## Brand personality

- Focused, smart, slightly intense, modern, and student-native.
- Not corporate or generic “AI productivity platform” language.
- Not childish, gimmicky, or influencer-style marketing.
- The product should feel like it has energy and understands the student’s time pressure.
- Prefer copy such as “Your next test starts here,” “You have 47 minutes. Let’s use them well,” and “Three concepts are still shaky.”

## Core product model

```text
Course
  └── Unit
        ├── Sources (YouTube, PDFs)
        └── Study Session (workspaces)
```

The primary object is a Course, not a Notebook. A course contains units; units combine multiple source types; a study session adapts the experience to the student’s available time and desired depth.

Study session inputs (planned, not yet built — see roadmap):

- Available time: 30 minutes, 1 hour, 2 hours, 3+ hours.
- Study mode: Smart Cram ↔ Deep Study.

The differentiating question is not only “What do you want to ask about your sources?” It is “What are you trying to learn, and how much time do you have?”

## Intended study loop

```text
Learn → Practice → Diagnose → Review weak spots → Practice again
```

Shipped toward this loop:

- A structured study guide (one grounded section per learning objective), not a wall of AI Markdown.
- Practice questions with explanations, server-graded so the key never leaks.
- Weakness detection, per objective, worst first (e.g. “Transformations — 42%”).
- Direct links back to the exact source timestamp or PDF page for review.

Still ahead: time-boxed/smart-cram session setup, core-concept ranking, and source-linked review loops with progress views.

## Long-term architecture

The grounded RAG foundation is built and hardened:

```text
YouTube/PDF source
  → ingestion (transcribe / extract → chunk → batch-embed → single-commit store)
  → timestamp/page-preserving chunks
  → embeddings (sqlite-vec, per-dimension tables)
  → retrieval (workspace-scoped candidate pool, ready-only)
  → reranking
  → shared passage+history context budget
  → grounded prompt
  → LLM streaming
  → citation map
  → clickable source location
```

The study-intelligence layer on top is partially built:

```text
Source → RAG → Study Intelligence layer
                    ├── Study Guide (shipped, persisted, regenerable)
                    ├── Cram Sheet (not started)
                    ├── Practice Questions (shipped, server-graded)
                    ├── Weakness Detection (shipped, worst-first panel)
                    └── Study Plan (not started)
```

Do not throw away or bypass the existing retrieval/reranking/citation architecture. It is the foundation for trustworthy study outputs.

## Current implementation

Backend (`backend/`):

- Entrypoint + static mount + CSRF/DNS-rebind middleware: `main.py`.
- API routes + security middleware: `backend/api.py`.
- Config with secret redaction and rename migration: `backend/config.py`.
- SQLite store + dim-namespaced vectors + citations column: `backend/store.py`.
- OpenAI-compatible LLM client (cloud + local): `backend/llm_client.py`.
- Grounding: `backend/grounding/pipeline.py` (single-ground chat with shared
  token budget), `retriever.py`, `reranker.py`, `citations.py`,
  `visual.py` (PDF vision Q&A with annotation overlays).
- Ingestion: `backend/ingestion/youtube.py`, `transcribe.py` (cached Whisper),
  `ingest.py` (batched), `document.py` (PDF), `pdfrender.py` (page images).
- Objectives: `backend/objectives/ced.py` + `ced_data/*.json` (38 AP courses),
  `extract.py` (AI extraction for everything else).
- Study: `backend/study/guide.py` (persisted per-objective guides).
- Practice: `backend/practice/generator.py` (server-graded quizzes, weak spots).

Frontend (`frontend/`, plain HTML/CSS/JS, no build step):

- Shell + study-desk theme: `index.html`, `style.css`, `logo.png`.
- Namespaced modules in `frontend/js/`: `main.js` (bootstrap +
  `App.openWorkspace`), `state.js` (one shared `AppState`), `api.js` (every
  backend call + CSRF header), `sse.js` (hand-rolled POST-stream SSE parser),
  `chat.js` (single in-flight send, route/page/citation events),
  `citations.js` (stable Source-N numbering, click-to-seek),
  `sidebar.js` (course/unit/workspace tree + modals), `sources.js` (YouTube
  add + PDF upload with progress), `studyGuide.js`, `practice.js` (key never
  touches the client), `pdfViewer.js` (multi-page render, jump, highlight and
  AI-annotation overlays), `player.js` (YouTube IFrame wrapper),
  `formatter.js`, `richText.js`, `dom.js`.
- See `frontend/DEV_NOTES.md` for the load-bearing details and handoff tasks.

Chat routing (current architecture — read before touching):

- `POST /api/workspaces/{id}/chat` grounds ONCE, then answers over one SSE
  stream: a `route` event (`route:text` / `route:vision`), an optional `page`
  event (image + annotations), streamed tokens, then `citations`.
- The vision path triggers only when passage [1] of the grounded evidence
  carries a `page_num` (`evidence_anchors_pdf` in `visual.py`) — never merely
  because the workspace contains a PDF. Positions 2+ are interleave order,
  not relevance order, so they must not drive routing.
- `POST .../chat/visual` still exists for compatibility (blocking JSON).

Current supported source state:

- YouTube ingestion (download → transcribe → chunk → embed) working, with
  progress streaming and duplicate-URL detection.
- PDF upload with progress, per-page extraction, page-image rendering,
  citation jump + highlight, and vision Q&A — all working end to end.
- Video understanding is transcript-only; PDF understanding can additionally
  see rendered pages when a vision model is configured. Never claim otherwise.

## AI behavior requirements

The system prompt should make the model understand its job: help a student understand class material, prepare for assessment, and identify what to review.

Required behavior:

- Treat supplied source evidence as the primary authority.
- Refer to “the lecture,” “the notes,” or the “source material,” never “passages.”
- Cite supported claims with the provided citation numbers.
- Never invent citation numbers or timestamps.
- If evidence is partial, answer the supported part first and clearly state what is not established.
- For useful general knowledge outside the sources, answer concisely under “Beyond this source:” instead of refusing with only “Not covered in the sources.”
- Be honest that video ingestion reads transcripts but does not inspect video visuals (PDF pages can additionally be seen by the vision model when configured).
- Use clean study formatting: short headings, short paragraphs, bullets/numbered steps, bold key terms, and backticks for code.
- Keep citations inline with the sentence they support.

## Build philosophy

- Build one specialized capability at a time.
- Test each capability before starting the next.
- Preserve the working YouTube iframe, timestamp seeking, citation jump, and transcript behavior.
- Prefer the best product outcome over a lazy or unnecessarily minimal shortcut.
- Do not add speculative features before the current milestone is tested.

## Recommended roadmap (only genuinely-ahead work)

1. Real beta users: 2–3 classmates cramming one real test; write down what breaks.
2. Packaging: Dockerfile / lock file for the heavy `[local]` deps.
3. Settings UI for the existing `/api/config` route (keys stay redacted server-side).
4. Mobile nav: sidebar and preview panel are `display:none` below 900px.
5. Toast notifications replacing the remaining `alert()`/`confirm()` calls.
6. Keyboard shortcuts (`/` focuses chat, `Esc` closes modals).
7. Cram Sheet + Study Plan layers (time-boxed/smart-cram session setup, concept ranking, review loops).

## Current test/runtime details

- Local URL: `http://127.0.0.1:8765/` (run via `run-server.cmd` watchdog or `python main.py`; `--host/--port/--no-browser` flags exist).
- Health endpoint: `GET /api/health`.
- Contract tests: `python -m pytest tests/ -q` (hermetic — tmp DBs, stub LLM, no network).
- Lint: `ruff check backend tests main.py` (bug rules; runs in CI on push/PR).
- Frontend checks: `node --check frontend/js/*.js`.
- Per-module assert self-checks: `python backend/store.py`, etc.
- Server should be restarted to pick up backend changes (frontend is served from disk; hard-refresh for JS/CSS).
- Commit on green; `main` tracks `origin/main`.

## Immediate next task

Pick the top item off the roadmap above (beta users first). Whatever it is: verify against the running server, keep `tests/` + suites green, and commit on `main`.
