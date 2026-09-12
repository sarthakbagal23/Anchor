/* richText.js — CORE (small, but order-of-operations matters). Combines
 * escaping, citation rendering, and bold/italic/code markdown into one pass
 * for a chunk of already-block-parsed text (formatter.js hands us each
 * paragraph/list-item's raw text via its `inline` callback).
 *
 * Code spans are pulled out FIRST and replaced with placeholders before the
 * bold/italic regexes run, then restored at the end. Without this, something
 * like `a**b` inside backticks would have its asterisks treated as markdown
 * instead of literal code — a real bug if skipped, since study answers
 * frequently contain code/notation.
 */
(function () {
  function inline(rawText, citationMap, sources) {
    const codeSpans = [];
    let safe = Dom.escapeHtml(rawText).replace(/`([^`]+)`/g, (_, code) => {
      codeSpans.push(code);
      return `\u0000${codeSpans.length - 1}\u0000`;
    });
    safe = Citations.render(safe, citationMap, sources)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
    return safe.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codeSpans[Number(i)]}</code>`);
  }

  window.RichText = { inline };
})();
