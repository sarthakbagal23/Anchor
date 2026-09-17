/* main.js — CORE glue, EASY to extend. window.App.openWorkspace is the one
 * function every other module calls when the active workspace changes, so
 * it's the right place to reset anything that must never leak across
 * workspaces (it already resets AppState.citationMap and stops the player —
 * add new per-workspace resets here, not in whichever module happens to
 * trigger the switch).
 *
 * Adding a new toolbar button, a new source-chip action, or a new modal
 * follows the same shape as everything already here: grab elements with
 * Dom.$, wire an onclick, call Api.*, then re-render from AppState.
 */
(function () {
  const { $, escapeHtml } = Dom;

  const STATUS_LABELS = {
    queued: "queued", downloading: "downloading", transcribing: "transcribing",
    extracting: "extracting", chunking: "indexing", ready: "ready", failed: "failed",
  };

  async function openWorkspace(id) {
    AppState.workspaceId = id;
    const ws = await Api.getWorkspace(id);
    AppState.workspace = ws;
    AppState.sources = ws.sources || [];
    AppState.citationMap = {};
    AppState.guideCitations = {};
    AppState.pdfCurrentSourceId = null;

    $("ws-title").textContent = ws.title;
    renderSources();
    Chat.renderHistory(ws.messages || []);
    await Sidebar.refresh();
    $("chat-input").focus();

    // Switching workspaces must never leave the previous one's video playing
    // or its PDF sitting open behind a panel the user can no longer see.
    Player.pause();
    PDFViewer.showPlayer();
  }

  function renderSources() {
    const list = $("source-list");
    list.innerHTML = AppState.sources.map((s) => `
      <li class="source-chip" data-status="${s.status}" title="${escapeHtml(s.error || "")}">
        <span class="source-title">${escapeHtml(s.title || s.youtube_id || "Processing…")}</span>
        <span class="source-status">${STATUS_LABELS[s.status] || s.status}</span>
        <button class="chip-remove" data-id="${s.id}" title="Remove source" aria-label="Remove source">×</button>
      </li>`).join("");
    $("ws-sources-count").textContent = AppState.sources.length
      ? `${AppState.sources.length} source${AppState.sources.length > 1 ? "s" : ""}`
      : "";
    list.querySelectorAll(".chip-remove").forEach((btn) => {
      btn.onclick = async (e) => {
        e.stopPropagation();
        if (!confirm("Remove this source?")) return;
        btn.disabled = true;
        try {
          await Api.deleteSource(AppState.workspaceId, Number(btn.dataset.id));
          openWorkspace(AppState.workspaceId);
        } catch {
          alert("Couldn't remove the source.");
          btn.disabled = false;
        }
      };
    });
  }

  function paintModelToggle(cfg) {
    $("speed-standard").classList.toggle("active", !AppState.fastMode);
    $("speed-fast").classList.toggle("active", AppState.fastMode);
    const name = AppState.fastMode ? (cfg?.llm?.fast_model || "fast model") : (cfg?.llm?.model || "standard model");
    $("speed-hint").textContent = `${AppState.fastMode ? "Fast" : "Deep"} · ${name}`;
  }

  async function loadModelToggle() {
    const cfg = await Api.getConfig().catch(() => null);
    if (!cfg) return;
    AppState.fastMode = cfg.llm?.active === "fast";
    paintModelToggle(cfg);
  }

  async function setFastMode(on) {
    const cfg = await Api.getConfig();
    if (on && !cfg.llm?.fast_model) return alert("No fast model is configured on the server yet.");
    if (!on && !cfg.llm?.model) return alert("No standard model is configured on the server yet.");
    AppState.fastMode = on;
    await Api.patchConfig({ llm: { active: on ? "fast" : "standard" } });
    paintModelToggle(await Api.getConfig());
  }

  function init() {
    Sidebar.attach();
    Chat.attachSend();
    StudyGuide.attach();
    Practice.attach();
    Sources.attach();
    $("speed-standard").onclick = () => { if (AppState.fastMode) setFastMode(false); };
    $("speed-fast").onclick = () => { if (!AppState.fastMode) setFastMode(true); };
    loadModelToggle();
    Sidebar.refresh();
  }

  window.App = { openWorkspace, renderSources };
  document.addEventListener("DOMContentLoaded", init);
})();
