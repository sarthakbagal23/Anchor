/* sse.js — CORE, hard-won. Do not "simplify" this without re-reading the
 * comments; every line here exists because of a real streaming bug.
 *
 * The backend's SSE endpoints come in two shapes:
 *   - GET endpoints (source ingest progress, study-guide/stream, practice-quiz
 *     stream) -> use the browser's native EventSource. It already parses
 *     "event:"/"data:" framing correctly; don't reinvent it for those.
 *   - POST endpoints (chat) -> EventSource cannot send a request body, so chat
 *     streams over a plain fetch() and we must parse the SSE wire format by
 *     hand. This file is that parser.
 *
 * Wire format (server side, see backend/api.py):
 *   "event: citations\ndata: {...}\n\n"   (named event)
 *   "data: {...}\n\n"                     (defaults to event "message")
 *
 * The tricky part: fetch() delivers the body as arbitrary byte chunks, which
 * do NOT line up with "one chunk = one SSE frame". A frame's data: line can
 * be split across two chunks, or one chunk can contain several frames. The
 * only safe approach is to buffer by newline and only consume a line once a
 * full "\n" has arrived, feeding any leftover partial line back into the next
 * chunk's buffer.
 */
(function () {
  /**
   * @param {Response} response - a fetch() response with a readable body
   * @param {{onEvent: (name: string, data: string) => void}} handlers
   */
  async function readEventStream(response, handlers) {
    if (!response.body) {
      throw new Error("This browser/response doesn't support streaming reads.");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let eventName = "message";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      // The last entry is either "" (buffer ended exactly on a newline) or an
      // incomplete line — either way it must NOT be processed yet.
      buffer = lines.pop();
      for (const line of lines) {
        if (line === "") {
          eventName = "message"; // blank line = end of one SSE frame
          continue;
        }
        if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
          continue;
        }
        if (line.startsWith("data:")) {
          handlers.onEvent(eventName, line.slice(5).trim());
        }
        // other SSE fields (id:, retry:, ": comment") are intentionally ignored
      }
    }
  }

  /** Parse an SSE data payload as JSON, or return `fallback` if it's empty/malformed. */
  function readJSON(payload, fallback) {
    if (!payload) return fallback;
    try {
      return JSON.parse(payload);
    } catch {
      return fallback;
    }
  }

  window.SSE = { readEventStream, readJSON };
})();
