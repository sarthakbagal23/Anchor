/* dom.js — EASY to extend. Just element lookup + escaping. Add helpers here
 * (e.g. a toast() function) rather than duplicating escaping logic elsewhere. */
(function () {
  const $ = (id) => document.getElementById(id);

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"]/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
    }[ch]));
  }

  window.Dom = { $, escapeHtml };
})();
