# Architecture audit — 2026-09-16

Read-only pass over the Anchor codebase at `main`. No code was changed for
this audit. Findings are ordered by severity; each ends with a one-line
recommended next step. File references are to the audited revision.

## Bug (wrong behavior or data risk)

### 1. Source interleave distorts relevance order — routing already worked around it
`backend/grounding/pipeline.py` (`_stage_passages` → `_interleave_by_source`):
round-robin reorders retrieved chunks so a barely-related chunk can sit at
passage [2] above far better matches (verified live: a 0.99-distance chunk
outranked 0.83 matches). Positions 2+ therefore do not mean "2nd most
relevant," but every consumer reads them that way. The vision router already
defends itself by inspecting only passage [1] (`evidence_anchors_pdf`,
`backend/grounding/visual.py`) — which is always the true relevance winner —
but the model still *sees* inflated passages ordered as if relevant.
Next step: decide whether interleave should apply only beyond the top-K and
document the invariant the router relies on.

### 2. Study-guide citations poison the chat citation map
`frontend/js/studyGuide.js` (`mergeCitations`) merges each guide section's map
into the global `AppState.citationMap`, whose passage numbers collide with
chat's numbering. Afterwards, clicking an older chat citation resolves against
guide chunk ids, finds nothing, and silently does nothing (`chat.js` click
handler returns on miss) until the next answer rebuilds the map.
Next step: scope citation maps per rendered context instead of one global.

### 3. PDF viewer renders every page with no cancellation
`frontend/js/pdfViewer.js` (`renderAllPages`): all N pages render sequentially
at 2.5–3.5× scale, and switching sources mid-render doesn't stop the old loop
(`viewer.pdfDoc` is reassigned but the in-flight loop keeps appending to the
shared container, interleaving two documents). This is a tab-freeze/OOM vector
on long PDFs and a plausible contributor to past "tab died" reports.
Next step: add a generation counter checked per page, and render a window
around the target page first.

### 4. Failed migrations poison their own one-time guard
`backend/config.py` (`migrate_file` dir case, `migrate_db`): a copy that dies
midway leaves a partial `target/` (or partial target DB file), which now
`.exists()` — so every future boot uses the half-migrated result and never
retries, while the intact original sits ignored next to it.
Next step: copy to a temp sibling and `os.replace` into place.

### 5. Dead chat protocol left running: `stream_answer` + `__CITATIONS__`
`backend/grounding/pipeline.py` (`stream_answer`, ~line 187) has exactly one
reference in the repo: its own definition. The unified `/chat` endpoint no
longer calls it, so the `__CITATIONS__` marker protocol is unproduced and
unconsumed anywhere. Dead protocol code next to a live replacement invites
resurrection bugs.
Next step: delete the method (keep the marker out of any new design).

### 6. Dead client surface over a live route: `Api.chatVisual`
`frontend/js/api.js:81` exports `chatVisual` with zero callers since routing
moved server-side, while `POST /api/workspaces/{id}/chat/visual` still serves.
Next step: either delete the export or the route — don't keep both halves.

## Tech debt

### 7. Passage-block formatting is triplicated and already drifting
`pipeline._build_prompt`, `study/guide._ground_block`, and
`practice/generator._ground_block` each independently build the
`[i] (Source: "title"[, ts])` header plus near-identical passage-metadata
dicts (all three carry `y_top`). Drift has started: question-first prompt
ordering exists only in the pipeline copy.
Next step: extract one `build_evidence_block()` helper in
`backend/grounding/`, keeping per-caller system/user wrappers.

### 8. `api.py`'s ~450-line `__main__` suite vs `tests/`
The embedded suite and `tests/` overlap (chat SSE contract, practice
key-disclosure) without either covering everything (only `__main__` asserts
SSE framing and attempt-404s; only `tests/` covers security, retrieval,
migration). `__main__` also pays a reranker weight load on every run.
Next step: migrate route-contract asserts into `tests/test_api_contract.py`
over time; leave a 10-line smoke in `__main__`.

### 9. Frontend coverage stops at `formatter.js`
Only `formatter.js` has a runnable self-check; the load-bearing logic in
`chat.js`, `citations.js`, `sources.js`, `studyGuide.js`, and `practice.js`
has none — yet several of those are pure functions (`sourceOrdinal`,
`labelFor`, SSE line buffering in `sse.js`) testable under plain
`node --test` with zero dependencies.
Next step: extract-and-test the pure functions first; leave DOM code alone.

### 10. No SECURITY.md
The threat model (loopback-only, CSRF/Host/port checks, redacted keys, and
the explicit non-goal of shared-machine attackers) lives only in code
comments and `tests/test_security.py`.
Next step: write a 30-line SECURITY.md so the model is discoverable.

### 11. Ingest error handling is contract-consistent but message-poor
`ingest_url`/`ingest_pdf` catch-all → `failed` + chunk cleanup is uniform and
correct; the leaves just raise. Two warts: a `yt-dlp` timeout surfaces raw
subprocess text as the user-facing error, and `_remote_transcribe` buffers
the whole audio file in RAM (the same class of bug fixed for uploads).
Next step: map timeout errors to a friendly message; stream the Whisper upload.

### 12. `player.js` rough edges
Single-slot `pending` request (rapid pre-ready clicks keep only the last),
`seek`/`currentTime` exported but uncalled.
Next step: queue pending seeks or drop the slot; remove dead exports.

### 13. Citation tokens are keyboard-focusable but not keyboard-activatable
`citations.js` renders `tabindex="0" role="button"` with a click handler only.
Next step: add Enter/Space activation.

### 14. `jumpToPage` clamps only one end
`pdfViewer.js`: `canvases[n] || canvases[min(n, pageCount)]` mishandles `n < 1`
by showing the last page.
Next step: clamp both ends.

## Checked and clear (leads that did not pan out)

- **Guide/practice do not share the interleave blind spot.** Only
  `Pipeline._stage_passages` interleaves; `guide`/`practice` ground via raw
  `search_unit` order, and the CED coverage pass matches pre-interleave too.
  No change needed — but see finding 1 for the paths disagreeing on what
  "top passages" means.
- **Migration one-time-ness and backup safety hold** (modulo finding 4):
  guards are existence-based so success never re-runs, originals are never
  deleted, failures warn loudly, and the DB path uses the online backup API
  (safe against a live source and WAL sidecars).
- **`_validate` is real** (rejects unknown providers, allows unset), the
  `embeddings` duplicate field is gone, and messages are `ORDER BY id`.
- **`chatVisual` backend route still works** (untouched by the routing change;
  covered by the vision-path tests) — only its client caller is gone.

## Stylistic (noted, not recommended)

- `docs/superpowers/sdd/task-2-brief.md` still describes the old world; like
  the design spec it reads as planning history — leave it unless the team
  wants the superseded banner treatment too.
- `run-server.cmd` hardcodes the developer's checkout path by nature of being
  a double-click helper; fine as-is, do not "fix" into something clever.
