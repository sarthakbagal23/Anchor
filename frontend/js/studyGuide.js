/* studyGuide.js — CORE-ish. The one detail worth preserving: the SSE "error"
 * listener distinguishes a genuine dropped connection from a server-reported
 * reason. `event: error` frames carry a real JSON payload (e.g. "this unit
 * has no learning objectives yet"); a plain network drop fires EventSource's
 * error event with no data at all. If sections have already streamed in
 * (`done > 0`), a late connection drop after the real work finished must NOT
 * overwrite them with a scary error message.
 */
(function () {
  const { $, escapeHtml } = Dom;

  function thinking(label) {
    return `<div class="thinking"><span class="spinner"></span><span>${escapeHtml(label)}</span></div>`;
  }

  function gapBanner(uncovered, total) {
    if (!uncovered || !total) return "";
    return `<div class="gap-banner"><strong>${uncovered}</strong> of <strong>${total}</strong> topics aren't in your `
      + `sources yet. Add material covering them and generate again — gaps are marked "not covered" below.</div>`;
  }

  function sectionHtml(index, section) {
    const gapClass = section.covered ? "" : " study-section--gap";
    const tag = section.covered ? "" : '<span class="tag tag--gap">not in sources</span>';
    const body = formatStudyText(section.body || "", (t) => RichText.inline(t, AppState.guideCitations, AppState.sources));
    return `<article class="study-section${gapClass}">
      <header class="study-section-head">
        <span class="section-num">${index}</span>
        <span class="skill-code">${escapeHtml(section.skill_code || "obj")}</span>
        <h3>${escapeHtml(section.title)}</h3>
        ${tag}
      </header>
      <div class="study-section-body">${body}</div>
    </article>`;
  }

  function mergeCitations(sections) {
    (sections || []).forEach((s) => Object.assign(AppState.guideCitations, s.citations || {}));
  }

  function renderGuide(data) {
    const body = $("study-body");
    if (!data.sections || !data.sections.length) {
      $("study-meta").textContent = data.guide ? "" : "not generated yet";
      body.innerHTML = `<p class="hint">No study guide yet. Click <strong>Generate</strong> to build one from this unit's learning objectives.</p>`;
      return;
    }
    $("study-meta").textContent = data.guide ? `${data.guide.objective_count} objectives` : "";
    body.innerHTML = gapBanner(data.uncovered, data.guide && data.guide.objective_count)
      + data.sections.map((s, i) => sectionHtml(i + 1, s)).join("");
  }

  async function load() {
    const body = $("study-body");
    if (!AppState.workspaceId) { body.innerHTML = `<p class="hint">Open a workspace first.</p>`; return; }
    body.innerHTML = thinking("Loading…");
    AppState.guideCitations = {}; // fresh guide, fresh numbering space
    try {
      const data = await Api.getStudyGuide(AppState.workspaceId);
      mergeCitations(data.sections);
      renderGuide(data);
    } catch {
      body.innerHTML = `<p class="hint">Couldn't load the study guide.</p>`;
    }
  }

  function generate() {
    if (!AppState.workspaceId) return alert("Open a workspace first.");
    const btn = $("study-generate");
    btn.disabled = true;
    btn.textContent = "Generating…";
    const body = $("study-body");
    body.innerHTML = thinking("Writing your study guide…");
    $("study-meta").textContent = "";

    let total = 0;
    let done = 0;
    let streamEl = null;

    const ensureStream = () => {
      if (streamEl) return streamEl;
      streamEl = document.createElement("div");
      streamEl.className = "study-stream";
      streamEl.innerHTML = `<p class="progress">Generating <strong class="p-done">0</strong> of <strong class="p-total">${total}</strong> topics…</p>`;
      body.innerHTML = "";
      body.appendChild(streamEl);
      return streamEl;
    };

    const es = new EventSource(Api.studyGuideStreamUrl(AppState.workspaceId));

    es.addEventListener("meta", (e) => { total = JSON.parse(e.data).objectives; ensureStream(); });

    es.addEventListener("section", (e) => {
      const { index, total: t, section } = JSON.parse(e.data);
      total = t; done = index;
      const streamRoot = ensureStream();
      const progress = streamRoot.querySelector(".progress");
      if (progress) {
        progress.querySelector(".p-done").textContent = String(done);
        progress.querySelector(".p-total").textContent = String(total);
      }
      streamRoot.insertAdjacentHTML("beforeend", sectionHtml(index, section));
      mergeCitations([section]);
      body.scrollTop = body.scrollHeight;
    });

    es.addEventListener("done", (e) => {
      const data = JSON.parse(e.data);
      es.close();
      btn.disabled = false;
      btn.textContent = "Generate / Refresh";
      if (data.error) {
        $("study-meta").textContent = "";
        body.innerHTML = `<p class="hint">${escapeHtml(data.error)}</p>`;
        return;
      }
      $("study-meta").textContent = data.guide ? `${data.guide.objective_count} objectives` : "";
      const banner = gapBanner(data.uncovered, data.guide && data.guide.objective_count);
      const progressEl = streamEl && streamEl.querySelector(".progress");
      if (progressEl) progressEl.remove();
      if (banner && streamEl) streamEl.insertAdjacentHTML("afterbegin", banner);
    });

    es.addEventListener("error", (e) => {
      es.close();
      btn.disabled = false;
      btn.textContent = "Generate / Refresh";
      if (done) return; // sections already rendered — don't clobber real output with a late error
      let message = "Couldn't generate the guide. Is the server's model reachable?";
      try {
        const parsed = e.data ? JSON.parse(e.data) : null;
        if (parsed && parsed.error) message = parsed.error;
      } catch { /* a genuine connection drop carries no JSON payload at all */ }
      body.innerHTML = `<p class="hint">${escapeHtml(message)}</p>`;
    });
  }

  function attach() {
    $("study-guide-btn").onclick = () => {
      if (!AppState.workspaceId) return alert("Open a workspace first.");
      $("study-modal").classList.remove("hidden");
      load();
    };
    $("study-close").onclick = () => $("study-modal").classList.add("hidden");
    $("study-modal").addEventListener("click", (e) => { if (e.target.id === "study-modal") $("study-modal").classList.add("hidden"); });
    $("study-generate").onclick = generate;
  }

  window.StudyGuide = { attach };
})();
