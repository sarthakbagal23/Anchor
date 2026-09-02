// Small, dependency-free formatter for grounded study answers.
(function () {
  function escapeHtml(value) {
    return String(value || "").replace(/[&<>\"]/g, ch => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;"}[ch]));
  }

  function formatStudyText(text, inline) {
    inline = inline || escapeHtml;
    const blocks = [];
    let paragraph = [];
    let list = null;
    const flushParagraph = () => { if (paragraph.length) { blocks.push(`<p>${inline(paragraph.join(" "))}</p>`); paragraph = []; } };
    const closeList = () => { if (list) { blocks.push(`</${list}>`); list = null; } };
    for (const raw of String(text || "").split(/\r?\n/)) {
      const line = raw.trim();
      if (!line) { flushParagraph(); closeList(); continue; }
      const heading = line.match(/^#{1,4}\s+(.+)/);
      if (heading) { flushParagraph(); closeList(); blocks.push(`<h3>${inline(heading[1])}</h3>`); continue; }
      const bullet = line.match(/^[-*]\s+(.+)/);
      if (bullet) { flushParagraph(); if (!list) { list = "ul"; blocks.push("<ul>"); } blocks.push(`<li>${inline(bullet[1])}</li>`); continue; }
      const numbered = line.match(/^\d+[.)]\s+(.+)/);
      if (numbered) { flushParagraph(); if (list !== "ol") { closeList(); list = "ol"; blocks.push("<ol>"); } blocks.push(`<li>${inline(numbered[1])}</li>`); continue; }
      closeList(); paragraph.push(line);
    }
    flushParagraph(); closeList();
    return blocks.join("");
  }

  if (typeof window !== "undefined") window.formatStudyText = formatStudyText;
  if (typeof module !== "undefined") module.exports = { formatStudyText, escapeHtml };
})();

if (typeof require !== "undefined" && require.main === module) {
  const assert = require("assert");
  const html = module.exports.formatStudyText("## Core idea\n\n- **Mean** is sensitive to outliers\n- Median is not", s => s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>"));
  assert(html.includes("<h3>Core idea</h3>"));
  assert(html.includes("<ul>") && html.includes("<strong>Mean</strong>"));
  assert(html.includes("<li>Median is not</li>"));
  console.log("formatter OK");
}
