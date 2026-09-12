/* sources.js — EASY/MEDIUM. Ingestion progress uses native EventSource (a
 * plain GET stream — unlike chat, no manual SSE parsing needed here). The
 * only thing worth preserving is the upload guard order (workspace open? ->
 * .pdf extension? -> size limit?) so the user sees the most useful error
 * first, and the status->message mapping for the 413/415/404 cases the
 * backend can return for a bad upload.
 */
(function () {
  const { $ } = Dom;

  function watchSourceProgress(sourceId, onUpdate) {
    const es = new EventSource(Api.sourceStreamUrl(AppState.workspaceId, sourceId));
    es.onmessage = (e) => {
      const data = JSON.parse(e.data);
      onUpdate(data);
      if (data.status === "ready" || data.status === "failed") es.close();
    };
    es.onerror = () => es.close();
    return es;
  }

  function attachYoutube() {
    $("add-source").onclick = async () => {
      if (!AppState.workspaceId) return alert("Open a workspace first.");
      const input = $("source-url");
      const url = input.value.trim();
      if (!url) return;
      const btn = $("add-source");
      btn.disabled = true;
      btn.textContent = "Adding…";
      try {
        const { source_id } = await Api.addYoutubeSource(AppState.workspaceId, url);
        input.value = "";
        watchSourceProgress(source_id, () => window.App.openWorkspace(AppState.workspaceId));
      } finally {
        btn.disabled = false;
        btn.textContent = "Add";
      }
    };
  }

  function setStatus(text, isError) {
    const el = $("pdf-status");
    el.textContent = text;
    el.classList.toggle("error", !!isError);
  }

  function uploadErrorMessage(err) {
    if (err.status === 415) return "Only PDF files are supported.";
    if (err.status === 413) return "That file is over the 25 MB limit.";
    if (err.status === 404) return "Workspace not found.";
    return err.message || "Upload failed.";
  }

  function attachPdf() {
    $("pick-pdf").onclick = () => $("pdf-file").click();
    $("pdf-file").addEventListener("change", async () => {
      const file = $("pdf-file").files[0];
      if (!file) return;
      if (!AppState.workspaceId) return setStatus("Open a workspace first.", true);
      if (!/\.pdf$/i.test(file.name)) return setStatus("Only PDF files are supported.", true);
      if (file.size > 25 * 1024 * 1024) return setStatus("PDF must be 25 MB or smaller.", true);
      $("pdf-filename").textContent = file.name;
      setStatus("Uploading…", false);
      const form = new FormData();
      form.append("file", file, file.name);
      try {
        const data = await Api.uploadPdf(AppState.workspaceId, form);
        watchSourceProgress(data.source_id, (d) => {
          const label = d.status === "failed" ? `Failed — ${d.error || "unknown error"}` : d.status === "ready" ? "Done" : `Status: ${d.status}`;
          setStatus(label, d.status === "failed");
          window.App.openWorkspace(AppState.workspaceId);
          if (d.status === "ready") setTimeout(() => { setStatus(""); $("pdf-filename").textContent = ""; }, 2500);
        });
      } catch (err) {
        setStatus(uploadErrorMessage(err), true);
        $("pdf-filename").textContent = "";
      }
    });
  }

  function attach() {
    attachYoutube();
    attachPdf();
  }

  window.Sources = { attach };
})();
