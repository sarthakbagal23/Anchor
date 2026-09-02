# OpenNotebook — Product Context and Build Plan

This is the canonical product context for continuing work on OpenNotebook. Read this before making product or UX changes.

## Product thesis

OpenNotebook is not trying to be another general-purpose AI notebook or a NotebookLM clone. It is a study environment that turns messy class material into a serious, focused study session.

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

The long-term mental model is:

```text
Course
  └── Unit
        ├── Sources (YouTube, PDFs, slides, notes)
        └── Study Session
```

The primary object should eventually be a Course, not a Notebook. A course contains units; units combine multiple source types; a study session adapts the experience to the student’s available time and desired depth.

Study session inputs:

- Available time: 30 minutes, 1 hour, 2 hours, 3+ hours.
- Study mode: Smart Cram ↔ Deep Study.

The differentiating question is not only “What do you want to ask about your sources?” It is “What are you trying to learn, and how much time do you have?”

## Intended study loop

```text
Learn → Practice → Diagnose → Review weak spots → Practice again
```

The product should eventually generate:

- A structured study guide, not a wall of AI Markdown.
- Core concepts ranked by importance.
- “What you need to know,” “Why it matters,” “Example,” “Common trap,” and “Try it.”
- Practice questions and explanations.
- Weakness detection, such as “Transformations — 42%.”
- Direct links back to the exact source timestamp or PDF page for review.

## Long-term architecture

The current MVP is a strong grounded RAG foundation:

```text
YouTube/PDF source
  → ingestion
  → timestamp/page-preserving chunks
  → embeddings
  → retrieval
  → reranking
  → context fitting
  → grounded prompt
  → LLM streaming
  → citation map
  → clickable source location
```

The product layer should eventually sit above this:

```text
Source → RAG → Study Intelligence layer
                    ├── Study Guide
                    ├── Cram Sheet
                    ├── Practice Questions
                    ├── Weakness Detection
                    └── Study Plan
```

Do not throw away or bypass the existing retrieval/reranking/citation architecture. It is the foundation for trustworthy study outputs.

## Current implementation

Backend:

- FastAPI entrypoint: `main.py`.
- API routes: `backend/api.py`.
- Grounding pipeline: `backend/grounding/pipeline.py`.
- Retrieval/reranking: `backend/grounding/retriever.py`, `backend/grounding/reranker.py`.
- Citation mapping: `backend/grounding/citations.py`.
- YouTube ingestion: `backend/ingestion/youtube.py`, `backend/ingestion/transcribe.py`, `backend/ingestion/ingest.py`.
- PDF extraction/ingestion: `backend/ingestion/document.py`.
- SQLite store, including PDF page numbers: `backend/store.py`.
- LLM client: `backend/llm_client.py`.

Frontend:

- `frontend/index.html`, `frontend/app.js`, `frontend/style.css`, `frontend/player.js`.
- Current UI is intentionally the stable notebook/source/chat/player MVP while product work proceeds incrementally.
- YouTube ingestion and timestamp seeking are foundational and must remain working.
- `frontend/formatter.js` formats headings, paragraphs, lists, bold, italics, and inline code.
- Citations currently display compactly as `22:08 · Source 1`; hovering shows the transcript; clicking seeks the video.

Current supported source state:

- YouTube upload/ingestion is working.
- PDF backend ingestion exists and stores page citations.
- The PDF picker/upload UI still needs to be wired into the frontend as its own milestone.
- Video visual understanding is not implemented yet. Never claim to see slides, diagrams, or frames when only transcript text is available.

## AI behavior requirements

The system prompt should make the model understand its job: help a student understand class material, prepare for assessment, and identify what to review.

Required behavior:

- Treat supplied source evidence as the primary authority.
- Refer to “the lecture,” “the notes,” or “the source material,” never “passages.”
- Cite supported claims with the provided citation numbers.
- Never invent citation numbers or timestamps.
- If evidence is partial, answer the supported part first and clearly state what is not established.
- For useful general knowledge outside the sources, answer concisely under “Beyond this source:” instead of refusing with only “Not covered in the sources.”
- Be honest that current video ingestion reads transcripts but does not inspect video visuals.
- Use clean study formatting: short headings, short paragraphs, bullets/numbered steps, bold key terms, and backticks for code.
- Keep citations inline with the sentence they support.

## Build philosophy

- Build one specialized capability at a time.
- Test each capability before starting the next.
- Preserve the working YouTube iframe, timestamp seeking, citation hover, and transcript behavior.
- Prefer the best product outcome over a lazy or unnecessarily minimal shortcut.
- Avoid broad UI rewrites while foundational behavior is being stabilized.
- Do not add speculative features before the current milestone is tested.

## Recommended roadmap

1. Stabilize chat rendering, Markdown, compact citations, and timestamp interactions.
2. Add the PDF upload picker and progress/status UI using the existing backend route; test PDF page citations.
3. Add a real Course → Unit structure while preserving source/chat compatibility.
4. Add Study Session setup: available time and Cram/Deep Study mode.
5. Generate structured study guides and cram sheets from the existing grounded pipeline.
6. Add practice questions and weakness tracking.
7. Add source-linked review loops and progress views.
8. Explore visual grounding (key frames/slides/diagrams) only after designing a reliable, efficient extraction and storage strategy.

## Current test/runtime details

- Local URL: `http://127.0.0.1:8765/`.
- Health endpoint: `GET /api/health`.
- Frontend checks: `node --check frontend/app.js`, `node --check frontend/formatter.js`, `node frontend/formatter.js`.
- Current server should be verified before asking the user to test.
- Existing changes are uncommitted; preserve them and do not reset or checkout the worktree.

## Immediate next task

Wire the existing PDF upload endpoint into the stable frontend with a clear file picker, upload progress/status, error handling, and PDF page citations. Keep this isolated and testable; do not redesign the entire app at the same time.
