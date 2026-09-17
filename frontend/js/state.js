/* state.js — CORE (small, but everything below reads/writes this). One shared
 * object instead of scattered globals (the old code had window.__currentWS__,
 * window.__currentPDFSourceId__, a module-level citationMap, etc. living in
 * different files). If you add a feature that needs to remember something
 * across modules, put it here rather than inventing a new window.* global. */
(function () {
  window.AppState = {
    workspaceId: null,       // currently open workspace, or null
    workspace: null,         // full workspace payload from GET /api/workspaces/:id
    sources: [],             // this workspace's sources (for citation -> source lookup)
    citationMap: {},         // CHAT context map: passage number -> {chunk_id, source_id, start_sec, page_num, y_top, text, ...}
    guideCitations: {},      // STUDY-GUIDE context map (same shape). Separate object on
                             // purpose: guide sections number passages from 1 just like chat
                             // answers, so sharing one map lets a guide silently clobber
                             // chat citations (see contextFor() in citations.js).
    pdfCurrentSourceId: null,// which PDF is currently loaded in the preview panel
    fastMode: false,         // mirrors the server's llm.active config
  };
})();
