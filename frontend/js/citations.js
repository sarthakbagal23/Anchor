/* citations.js — CORE, hard-won. This resolves the model's inline [n] wire
 * citations into clickable tokens. Two subtleties that are easy to silently
 * break if you touch this file:
 *
 * 1. STABLE SOURCE NUMBERING. citationMap[n].source_id tells you WHICH
 *    source a passage came from, but retrieval order changes per question —
 *    passage [1] in one answer and passage [1] in the next answer can be
 *    different sources. If we labeled tokens "Source 1" using retrieval rank,
 *    the same physical source would show a different number in every answer.
 *    Instead we number sources by their fixed position in the workspace's
 *    source list, so "Source 2" always means the same lecture/PDF for the
 *    life of the workspace.
 *
 * 2. INPUT IS ALREADY-ESCAPED HTML. `render()` runs on text that has already
 *    been through Dom.escapeHtml, and only recognizes literal "[digits]"
 *    runs — it must never be handed raw, unescaped model output (that's how
 *    citation rendering turns into an HTML injection point).
 */
(function () {
  function fmtTime(sec) {
    if (sec == null) return "";
    sec = Math.floor(sec);
    return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
  }

  function sourceOrdinal(sourceId, sources, citationMap) {
    const ids = [];
    for (const s of sources) {
      const id = String(s.id);
      if (!ids.includes(id)) ids.push(id);
    }
    let ordinal = ids.indexOf(String(sourceId));
    if (ordinal < 0) {
      // A citation can reference a source not (yet) reflected in `sources`
      // (e.g. the sources list is stale). Fall back to first-seen order
      // across the citation map itself rather than showing no label at all.
      for (const item of Object.values(citationMap)) {
        const id = String(item.source_id ?? "");
        if (id && !ids.includes(id)) ids.push(id);
      }
      ordinal = ids.indexOf(String(sourceId));
    }
    return ordinal >= 0 ? ordinal + 1 : null;
  }

  function labelFor(citation, ordinal) {
    const mmss = fmtTime(citation.start_sec);
    const page = citation.page_num ? `p.${citation.page_num}` : "";
    const src = ordinal != null ? `Source ${ordinal}` : "source";
    if (mmss) return `${mmss} · ${src}`;
    return page ? `${page} · ${src}` : src;
  }

  function attr(s) {
    return String(s || "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
  }

  /** escapedText must already be HTML-escaped (see file header). */
  function render(escapedText, citationMap, sources) {
    return escapedText.replace(/\[(\d+(?:\s*,\s*\d+)*)\]([.,;:!?])?/g, (_, nums, punct) => {
      const chips = nums.split(",").map((raw) => {
        const n = raw.trim();
        const citation = citationMap[Number(n)];
        if (!citation) return `<span class="cite-raw">[${n}]</span>`;
        const ordinal = sourceOrdinal(citation.source_id, sources, citationMap);
        const label = labelFor(citation, ordinal);
        return `<span class="cite" data-chunk-id="${citation.chunk_id}" data-sec="${citation.start_sec ?? ""}" `
          + `data-source-id="${citation.source_id ?? ""}" tabindex="0" role="button" `
          + `aria-label="Jump to ${attr(label)}" title="${attr(citation.text || "")}">${attr(label)}</span>`;
      }).join(" ");
      return `<span class="cite-group">${chips}</span>${punct || ""}`;
    });
  }

  window.Citations = { fmtTime, sourceOrdinal, labelFor, render };
})();
