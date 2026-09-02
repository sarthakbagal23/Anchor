# OpenNotebook MVP — Subagent-Driven Progress Ledger

Tasks 2-16 are dispatched to fresh implementer subagents + task-reviewer subagents.
Task 1 is controller-executed (git bootstrap).

- [x] Task 1: scaffold, venv, git, entrypoint shell (controller) — commits 224b59e..3174a0b, health 200 OK
- [x] Task 2: config.py — load/validate/graceful degrade (843a7c5, self-check green, inline-exec)
- [x] Task 3: store.py — SQLite + sqlite-vec schema and CRUD (342623e, fixed loadable_path() call, green)
- [x] Task 4: grounding/chunker.py (bda7591, green; fixed soft/hard-ceiling logic so chunks land on sentence boundaries)
- [x] Task 5: grounding/embedder.py (543783e, green; fixed lazy-OpenAI patch target + fallback dim tracking)
- [ ] Task 6: grounding/retriever.py
- [ ] Task 7: grounding/reranker.py
- [ ] Task 8: grounding/citations.py
- [ ] Task 9: grounding/pipeline.py
- [ ] Task 10: llm_client.py
- [ ] Task 11: ingestion/youtube.py
- [ ] Task 12: ingestion/transcribe.py
- [ ] Task 13: ingestion/ingest.py
- [ ] Task 14: api.py — routes + SSE
- [ ] Task 15: frontend — seek tokens UX
- [ ] Task 16: main.py wiring + README
