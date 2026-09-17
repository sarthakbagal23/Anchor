/* pdfViewer.js — CORE, the fiddliest file in the app. Three non-obvious
 * things are load-bearing here; if a page jump or highlight silently stops
 * working after an edit, check these first:
 *
 * 1. SCROLL CONTAINER. #pdf-viewer (not #pdf-scroll) is the element with
 *    `overflow: auto` — #pdf-scroll is just a plain flex container. Scrolling
 *    #pdf-scroll is a silent no-op. jumpToPage() computes the target page's
 *    offset relative to #pdf-viewer's own scroll position, so it's correct
 *    no matter how far the panel is already scrolled.
 * 2. PER-PAGE WRAPPER. Each rendered page gets its own positioned wrapper
 *    element, and highlight boxes are appended to THAT wrapper, not to the
 *    shared scroll container. Appending to the shared container instead
 *    anchors the box near page one, and scrollIntoView() on it then yanks
 *    the view back to page one — undoing the very jump that just happened.
 * 3. NULL y_top MEANS "FLASH THE WHOLE PAGE", NOT "SHOW NOTHING". Citations
 *    written before position-tracking existed carry y_top = null. A click
 *    must always visibly respond, so the fallback is a full-page flash
 *    rather than a silently-doing-nothing highlight.
 * 4. RENDER GENERATION + WINDOWING. Pages render lazily in a small window
 *    around the viewport/jump target — never the whole document up front
 *    (a 200-page PDF at 3x scale will take down the tab). Every loadStream()
 *    and showPlayer() bumps viewer.generation; every await point re-checks
 *    it, so a superseded render dies instead of appending stale canvases
 *    into the new document. jumpToPage/highlightAt render their target page
 *    on demand, so they work on not-yet-rendered pages too.
 */
(function () {
  const el = (id) => document.getElementById(id);
  const viewer = { pdfDoc: null, currentSourceId: null, currentWsId: null, pageCount: 0, canvases: [], wraps: {}, generation: 0, scrollHooked: false };
  const RENDER_RADIUS = 2; // pages each side of the viewport/jump target

  const pdfjsReady = () => typeof window.pdfjsLib !== "undefined";

  function configureWorker() {
    if (pdfjsReady() && !pdfjsLib.GlobalWorkerOptions.workerSrc) {
      pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
    }
  }

  function showPanel() { el("pdf-viewer").classList.remove("hidden"); el("player-wrap").classList.add("hidden"); }
  function showPlayer() { el("pdf-viewer").classList.add("hidden"); el("player-wrap").classList.remove("hidden"); viewer.generation++; }

  function setAnnotatedMode(showAnnotated) {
    el("pdf-annot-wrap").classList.toggle("hidden", !showAnnotated);
    el("pdf-scroll").classList.toggle("hidden", showAnnotated);
    el("pdf-back").classList.toggle("hidden", !showAnnotated);
  }

  async function loadStream(wsId, sourceId, highlight) {
    const myGen = ++viewer.generation;
    const alive = () => myGen === viewer.generation;
    if (!pdfjsReady()) {
      el("pdf-scroll").innerHTML = `<p class="panel-empty">PDF.js failed to load — check your connection and refresh.</p>`;
      return;
    }
    configureWorker();
    showPanel();
    setAnnotatedMode(false);
    el("pdf-scroll").innerHTML = `<p class="panel-empty">Loading PDF…</p>`;
    viewer.currentWsId = wsId;
    try {
      const res = await fetch(Api.pdfFileUrl(wsId, sourceId));
      if (!alive()) return; // superseded while fetching
      if (!res.ok) throw new Error(`http ${res.status}`);
      const buffer = await res.arrayBuffer();
      if (!alive()) return; // superseded while downloading
      const doc = await pdfjsLib.getDocument({ data: buffer }).promise;
      if (!alive()) return;
      viewer.pdfDoc = doc;
      viewer.currentSourceId = sourceId;
      viewer.pageCount = doc.numPages;
      const startPage = (highlight && highlight.page) || 1;
      await ensurePlaceholders(myGen);
      if (!alive()) return;
      el("pdf-page-ind").textContent = `${startPage} of ${doc.numPages}`;
      await renderWindow(startPage, myGen);
      if (!alive()) return;
      if (highlight && highlight.page) {
        await jumpToPage(highlight.page);
        if (!alive()) return;
        if (highlight.yTop !== undefined) await highlightAt(sourceId, highlight.page, highlight.yTop);
      }
    } catch (e) {
      if (!alive()) return; // an old load failing must not clobber the new document's UI
      el("pdf-scroll").innerHTML = `<p class="panel-empty">Couldn't load this PDF (${Dom.escapeHtml(String(e))}).</p>`;
    }
  }

  async function ensurePlaceholders(myGen) {
    // Cheap layout pass: one getPage per page for dimensions only (no
    // raster), so the scroll container has stable full-document height while
    // canvases render lazily. aspect-ratio keeps boxes correct on resize.
    const container = el("pdf-scroll");
    container.innerHTML = "";
    viewer.canvases = [];
    viewer.wraps = {};
    for (let n = 1; n <= viewer.pageCount; n++) {
      if (myGen !== viewer.generation) return false;
      const page = await viewer.pdfDoc.getPage(n);
      const v = page.getViewport({ scale: 1 });
      const wrap = document.createElement("div");
      wrap.className = "pdf-page";
      wrap.dataset.page = String(n);
      wrap.style.aspectRatio = `${v.width} / ${v.height}`;
      container.appendChild(wrap);
      viewer.wraps[n] = wrap;
    }
    return myGen === viewer.generation;
  }

  async function renderPage(n) {
    // Render exactly one page into its placeholder. Idempotent: already
    // rendered pages resolve immediately.
    if (viewer.canvases[n]) return viewer.canvases[n];
    const wrap = viewer.wraps[n];
    if (!wrap || !viewer.pdfDoc) return null;
    const container = el("pdf-scroll");
    const page = await viewer.pdfDoc.getPage(n);
    const fitScale = (container.clientWidth - 4) / page.getViewport({ scale: 1 }).width;
    const scale = Math.max(2.5, Math.min(fitScale, 3.5)); // floor for crisp text, ceiling for very wide panels
    const viewport = page.getViewport({ scale });
    const canvas = document.createElement("canvas");
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    canvas.style.width = `${container.clientWidth - 4}px`;
    await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
    wrap.appendChild(canvas);
    viewer.canvases[n] = canvas;
    return canvas;
  }

  async function renderWindow(center, myGen) {
    const alive = () => myGen === undefined || myGen === viewer.generation;
    const lo = Math.max(1, center - RENDER_RADIUS);
    const hi = Math.min(viewer.pageCount, center + RENDER_RADIUS);
    // Render the jump/anchor target FIRST so navigation never waits behind
    // its neighbors, then fill outward.
    const order = [center];
    for (let d = 1; d <= RENDER_RADIUS; d++) {
      if (center - d >= lo) order.push(center - d);
      if (center + d <= hi) order.push(center + d);
    }
    for (const n of order) {
      if (!alive()) return false;
      try {
        await renderPage(n);
      } catch {
        // One corrupt page must not kill the whole window; leave its box empty.
      }
      if (!alive()) return false;
    }
    return alive();
  }

  function currentFirstVisible() {
    // First placeholder whose bottom edge is below the scroll position.
    const host = el("pdf-viewer");
    const top = host.scrollTop;
    for (let n = 1; n <= viewer.pageCount; n++) {
      const wrap = viewer.wraps[n];
      if (wrap && wrap.offsetTop + wrap.offsetHeight > top) return n;
    }
    return 1;
  }

  let scrollRaf = null;
  function onPanelScroll() {
    if (scrollRaf) return;
    scrollRaf = requestAnimationFrame(() => {
      scrollRaf = null;
      if (!viewer.pdfDoc) return;
      if (!el("pdf-annot-wrap").classList.contains("hidden")) return; // annotated mode: nothing to fill in
      renderWindow(currentFirstVisible(), viewer.generation);
    });
  }

  async function jumpToPage(n) {
    if (!viewer.pdfDoc) return;
    // Clamp both ends (n < 1 used to fall through to the LAST page).
    const target = Math.min(Math.max(n, 1), viewer.pageCount);
    const myGen = viewer.generation;
    setAnnotatedMode(false);
    try {
      await renderWindow(target, myGen);
    } catch {
      return;
    }
    if (myGen !== viewer.generation) return;
    const canvas = viewer.canvases[target];
    if (!canvas) return;
    el("pdf-page-ind").textContent = `${target} of ${viewer.pageCount}`;
    // Scroll math stays on #pdf-viewer (the real overflow container), so the
    // offset is correct no matter how far the panel is already scrolled.
    const scrollHost = el("pdf-viewer");
    const top = scrollHost.scrollTop + canvas.getBoundingClientRect().top - scrollHost.getBoundingClientRect().top;
    scrollHost.scrollTo({ top, behavior: "smooth" });
    canvas.classList.remove("page-flash");
    void canvas.offsetWidth; // restart the CSS animation
    canvas.classList.add("page-flash");
  }

  function showAnnotatedPage(wsId, sourceId, pageNum, annotations) {
    if (!sourceId) return;
    const wrap = el("pdf-annot-wrap");
    const src = Api.pdfPageImageUrl(wsId, sourceId, pageNum);
    wrap.innerHTML = `<div class="annot-page"><img class="annot-img" src="${src}" alt="PDF page ${pageNum}" />`
      + `<div class="annot-layer"></div><p class="annot-title">Page ${pageNum} · annotated by the model</p></div>`;
    const layer = wrap.querySelector(".annot-layer");
    (annotations || []).forEach((a) => {
      const box = document.createElement("div");
      box.className = "annot-box";
      box.style.left = `${a.x * 100}%`;
      box.style.top = `${a.y * 100}%`;
      box.style.width = `${a.w * 100}%`;
      box.style.height = `${a.h * 100}%`;
      if (a.label) box.title = a.label;
      const label = document.createElement("span");
      label.className = "annot-label";
      label.textContent = a.label || "";
      box.appendChild(label);
      layer.appendChild(box);
    });
    showPanel();
    setAnnotatedMode(true);
    el("pdf-page-ind").textContent = `${pageNum} of ${viewer.pageCount || pageNum}`;
    wrap.scrollIntoView({ behavior: "smooth", block: "start" });
    const title = wrap.querySelector(".annot-title");
    void title.offsetWidth;
    title.classList.add("show");
  }

  async function highlightAt(sourceId, pageNum, yTop) {
    // Best-effort UI affordance: never rejects, so fire-and-forget callers
    // (citation clicks) need no error handling.
    try {
      if (!viewer.pdfDoc || viewer.currentSourceId !== sourceId) return;
      const myGen = viewer.generation;
      await renderWindow(Math.min(Math.max(pageNum, 1), viewer.pageCount), myGen);
      if (myGen !== viewer.generation) return;
      const canvas = viewer.canvases[pageNum];
      if (!canvas) return;
      const existing = canvas.parentElement.querySelector(".pdf-highlight");
      if (existing) existing.remove();
      const rect = canvas.getBoundingClientRect();
      const fullPage = yTop == null;
      const height = fullPage ? rect.height : Math.max(28, rect.height * 0.07);
      const top = fullPage ? 0 : rect.height * Math.max(0, Math.min(1, yTop));
      const box = document.createElement("div");
      box.className = "pdf-highlight";
      box.style.top = `${top}px`;
      box.style.height = `${height}px`;
      canvas.parentElement.style.position = "relative";
      canvas.parentElement.appendChild(box);
      box.scrollIntoView({ behavior: "smooth", block: "center" });
      setTimeout(() => box.remove(), 4000);
    } catch {
      return;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!viewer.scrollHooked) {
      viewer.scrollHooked = true;
      el("pdf-viewer").addEventListener("scroll", onPanelScroll, { passive: true });
    }
    el("pdf-back").onclick = () => {
      setAnnotatedMode(false);
      const first = viewer.canvases.find(Boolean);
      if (first) first.scrollIntoView({ behavior: "smooth" });
      else if (viewer.currentSourceId && viewer.currentWsId) loadStream(viewer.currentWsId, viewer.currentSourceId);
    };
  });

  window.PDFViewer = { loadStream, jumpToPage, showAnnotatedPage, showPanel, showPlayer, highlightAt };
})();
