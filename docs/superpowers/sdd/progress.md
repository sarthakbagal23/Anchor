# Anchor — Build Status

This replaces the original 16-task MVP ledger below. All 16 MVP tasks are
done, plus substantial work that was never in the original plan. Read
`docs/PROJECT_CONTEXT.md` for the product picture; this file is the
built/not-built record.

## Original MVP ledger (all complete)

(Commit references from the original planning session did not correspond to
real commits and have been removed — the task list itself is what's preserved.)

- [x] Task 1: scaffold, venv, git, entrypoint shell
- [x] Task 2: config.py — load/validate/graceful degrade
- [x] Task 3: store.py — SQLite + sqlite-vec schema and CRUD
- [x] Task 4: grounding/chunker.py — timestamp-preserving chunking
- [x] Task 5: grounding/embedder.py — bundled default, OpenAI-compatible override, hashing fallback
- [x] Task 6: grounding/retriever.py — sqlite-vec cosine search
- [x] Task 7: grounding/reranker.py — cross-encoder rerank
- [x] Task 8: grounding/citations.py — [#] → source/timestamp/page map
- [x] Task 9: grounding/pipeline.py — retrieve → rerank → fit → grounded prompt → stream
- [x] Task 10: llm_client.py — one OpenAI-compatible socket (cloud + local)
- [x] Task 11: ingestion/youtube.py — yt-dlp download
- [x] Task 12: ingestion/transcribe.py — faster-whisper or remote endpoint
- [x] Task 13: ingestion/ingest.py — download → transcribe → chunk → embed → store
- [x] Task 14: api.py — routes + SSE
- [x] Task 15: frontend — citation seek UX
- [x] Task 16: main.py wiring + README

## Beyond the original plan (all shipped)

- **Course → Unit → Workspace hierarchy** (`backend/store.py`, `frontend/js/sidebar.js`).
  The MVP's flat notebook is now courses containing units containing workspaces.
- **Learning objectives, two pipelines** (`backend/objectives/`). AP courses
  import a built-in topic framework covering 38 courses (`ced_data/*.json`,
  casual titles like `apush` resolve too); everything else gets AI-extracted
  objectives automatically when a source finishes indexing (`extract.py`).
- **Persisted study guides** (`backend/study/guide.py`, `frontend/js/studyGuide.js`):
  one grounded, regenerable section per objective.
- **Server-graded practice quizzes + weak spots** (`backend/practice/generator.py`,
  `frontend/js/practice.js`). The answer key never reaches the client; attempts
  persist per question and aggregate into a worst-first weak-spots panel
  (`GET /api/workspaces/{id}/weak-spots`).
- **PDF ingestion + vision Q&A** (`backend/ingestion/document.py`,
  `backend/ingestion/pdfrender.py`, `backend/grounding/visual.py`,
  `frontend/js/pdfViewer.js`). PDFs extract per page; questions whose grounded
  evidence is anchored in a page go to a vision model with annotation overlays.
- **Unified chat routing.** `POST /api/workspaces/{id}/chat` grounds once,
  then answers over one SSE stream: a `route` event announces
  `route:text` (token streaming) or `route:vision` (page image via a `page`
  event, then tokens), followed by `citations`. The vision path triggers only
  when passage [1] of the grounded evidence carries a `page_num` — never
  merely because the workspace contains a PDF (see `evidence_anchors_pdf` in
  `backend/grounding/visual.py`). The old per-path `POST .../chat/visual`
  route still exists for compatibility.
- **Security model** (`backend/api.py`, `main.py`, `frontend/js/api.js`):
  API keys redacted from `/api/config`, merge-safe PATCH, per-machine CSRF
  token + loopback Host/Origin checks (port-scoped).
- **Retrieval hardening** (`backend/store.py`): workspace-scoped candidate
  pools, ready-only filters, per-dimension vector tables with legacy
  migration, chunk cleanup on failed ingest.
- **Perf/robustness**: shared passage+history token budget, batched
  embeddings + single-commit ingest, cached Whisper model, temp-dir cleanup,
  timeouts on yt-dlp/LLM/embeddings, single in-flight chat question with
  supersede + first-token deadlines.
- **Rebrand OpenNotebook → Anchor**: display strings, `ANCHOR_CONFIG_DIR`
  (with `OPENNOTEBOOK_*` fallback), one-time copy-never-move migration of
  settings (`~/.opennotebook` → `~/.anchor`) and database (repo-root
  `opennotebook.db` → data dir `anchor.db`, via the WAL-safe backup API).
- **Test suite**: `tests/` (pytest, hermetic — tmp DBs, stub LLM, no network)
  plus ruff (bug rules) in CI on every push/PR. Backend modules keep their
  assert-based `__main__` self-checks for single-file verification.

## Data model (`backend/store.py`)

workspaces, courses, units, sources, chunks (+ `vec_chunks_{dim}` virtual
tables), messages (with `citations` column), learning_objectives,
objective_chunks, study_guides, guide_sections, practice_quizzes,
practice_questions, practice_attempts.

## Genuinely still ahead (not started)

- Real beta users (classmates) on a real test — matters more than features.
- Packaging: Dockerfile / lock file for the heavy `[local]` deps.
- Settings UI for the existing `/api/config` route.
- Mobile nav (sidebar/panel are display:none below 900px).
- Toast notifications replacing the remaining `alert()`/`confirm()` calls.
- Keyboard shortcuts (`/` focuses chat, `Esc` closes modals).
