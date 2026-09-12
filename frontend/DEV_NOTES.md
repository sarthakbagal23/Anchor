# Frontend dev notes

This replaces the old `frontend/` folder wholesale. No build step, same as
before — FastAPI serves this directory as-is (`main.py`'s `StaticFiles` mount
doesn't need to change). Same feature set as the previous frontend, rebuilt
with a modular file layout and a new visual design (details at the bottom).

## Installing it

Copy everything in this folder over your existing `frontend/` directory,
keeping the `js/` subfolder. Run the app the same way you already do
(`python main.py` / `run-server.cmd`). No new dependencies, no `npm install` —
it's still plain HTML/CSS/JS loaded as classic `<script>` tags (deliberately
**not** ES modules — Windows' registry sometimes maps `.js` to the wrong MIME
type, which silently breaks `<script type="module">` but not classic scripts).

## How it's organized

Every file attaches one namespace to `window` (`window.Api`, `window.Chat`,
etc.) and is loaded in dependency order from `index.html` — see the comment
above the `<script>` tags there before reordering anything.

```
js/dom.js         $ and escapeHtml — used everywhere
js/state.js       one shared AppState object instead of scattered globals
js/sse.js         hand-rolled SSE line parser, only needed for the chat POST stream
js/api.js         every backend fetch() call, one place
js/formatter.js   markdown-lite block parser (headings/lists/paragraphs)
js/citations.js   [n] -> clickable citation token, stable "Source N" numbering
js/richText.js    bold/italic/code + citations, for one block of text
js/player.js      YouTube IFrame API wrapper
js/pdfViewer.js   PDF.js rendering, page-jump, highlight overlay, AI-annotation overlay
js/sidebar.js     course/unit/workspace tree + the "new workspace" modal
js/chat.js        message send, streaming render, citation click -> seek/jump
js/studyGuide.js  study guide modal + streamed generation
js/practice.js    practice quiz modal + streamed generation + grading
js/sources.js     add-YouTube-URL and PDF-upload flows
js/main.js        bootstrap + openWorkspace() (the thing every module calls
                   when the active workspace changes)
```

## What's load-bearing (read the comment block before editing)

I did the parts that are genuinely easy to get subtly wrong, and left a
comment at the top of each file explaining the trap. In order of "will bite
you fastest if changed carelessly":

1. **`sse.js`** — the chat stream's SSE line-framing. fetch() delivers bytes
   in arbitrary chunks that don't line up with SSE frame boundaries; the
   buffering here handles a `data:` line split across two chunks.
2. **`citations.js`** — "Source N" numbering is stable by the workspace's
   fixed source order, not by retrieval rank. Numbering by rank instead would
   make the same lecture show a different number in every answer.
3. **`pdfViewer.js`** — `#pdf-viewer` (not `#pdf-scroll`) is the actual
   scrollable element; three separate bugs are avoided here (see the file's
   header comment) around scroll math, highlight anchoring, and the
   null-`y_top` fallback.
4. **`chat.js`**'s `streamAnswer()` — partial repaints during streaming only
   resolve citations, not full markdown (an unclosed `**` mid-stream would
   otherwise misrender); repaints are throttled to avoid an O(n²) freeze on
   long answers.
5. **`practice.js`** — never let the correct answer reach the client before
   an attempt is submitted. The backend already enforces this server-side;
   don't add client-side logic that guesses or caches it early.
6. **`studyGuide.js` / `practice.js`**'s SSE `error` listeners — a real
   `event: error` frame carries a JSON reason; a dropped connection fires the
   same browser event with no data. Don't collapse this distinction into one
   generic "something went wrong."

## Good tasks for a lighter model to pick up

These don't touch any of the above — they're additive UI work with an
existing pattern to copy:

- **Rename course/unit/workspace.** There's create + delete for all three but
  no rename. Follow the `del-unit`/`del-course` button pattern in
  `sidebar.js`: add a button, wire an `onclick`, `prompt()` for the new name,
  call a new `Api.*` function (needs a small `PATCH` route added on the
  backend too — there isn't one yet).
- **A settings screen for `/api/config`** (`Api.getConfig` /
  `Api.patchConfig` already exist) — base_url/model/api_key fields for llm,
  embeddings, reranker, whisper.
- **Toast notifications** instead of `alert()`/`confirm()` for the handful of
  guard messages (open a workspace first, couldn't remove source, etc.) and
  destructive-action confirmations.
- **Keyboard shortcuts** — e.g. `/` to focus the chat input, `Esc` to close
  whichever modal is open.
- **Mobile nav** — right now `#sidebar` and `#panel` just hide below 900px
  (see the `@media` query at the bottom of `style.css`). A slide-over toggle
  for both would make the phone experience actually usable instead of
  "chat-only."
- **Empty/loading states with more personality** — the brand doc
  (`docs/PROJECT_CONTEXT.md`) has good copy direction ("You have 47 minutes.
  Let's use them well.") for moments like an empty workspace list.
- **Visual polish**: hover/active micro-states, a proper drag-to-resize for
  the preview panel, syntax highlighting inside `<code>` blocks.

None of these need to understand SSE parsing, PDF.js, or the YouTube API —
they're DOM + `Api.*` + CSS.

## Design direction (why it looks the way it does)

Old design was a dark-glass/neon-glow kit (cyan/violet, backdrop-blur,
gradient borders everywhere) — visually generic and disconnected from the
"focused, intense, student-native" brand in `docs/PROJECT_CONTEXT.md`. This
version is grounded in the actual subject instead: a study desk at 11pm
before a test.

- **Palette**: warm near-black (`--ink`), not blue-black. One accent per
  *meaning*, not one accent for everything — highlighter gold (`--mark`) for
  citations/primary actions, rust (`--flag`) for gaps/weak-spots/wrong
  answers, moss (`--good`) for correct/ready. No blur, no glow, no gradients.
- **Type**: Fraunces (serif, has some texture) for headings/titles, IBM Plex
  Sans for body/UI, IBM Plex Mono reserved for genuinely code-like data —
  timestamps, page numbers, skill codes (`2.A`) — never used as a decorative
  label font.
- **Citations look like an actual highlighter stroke** (a background
  gradient behind the text) rather than a bordered pill/badge — literally
  what a student does to a passage worth remembering.

All CSS custom properties are declared once at the top of `style.css`, so a
full re-theme is a ten-line edit, not a find-and-replace across the file.
