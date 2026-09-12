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
 *    rather than a silently-do-nothing highlight.
 */
(function () {
  const el = (id) => document.getElementById(id);
  const viewer = { pdfDoc: null, currentSourceId: null, currentWsId: null, pageCount: 0, canvases: [] };

  const pdfjsReady = () => typeof window.pdfjsLib !== "undefined";

  function configureWorker() {
    if (pdfjsReady() && !pdfjsLib.GlobalWorkerOptions.workerSrc) {
      pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
    }
  }

  function showPanel() { el("pdf-viewer").classList.remove("hidden"); el("player-wrap").classList.add("hidden"); }
  function showPlayer() { el("pdf-viewer").classList.add("hidden"); el("player-wrap").classList.remove("hidden"); }

  function setAnnotatedMode(showAnnotated) {
    el("pdf-annot-wrap").classList.toggle("hidden", !showAnnotated);
    el("pdf-scroll").classList.toggle("hidden", showAnnotated);
    el("pdf-back").classList.toggle("hidden", !showAnnotated);
  }

  async function loadStream(wsId, sourceId, highlight) {
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
      if (!res.ok) throw new Error(`http ${res.status}`);
      const buffer = await res.arrayBuffer();
      const doc = await pdfjsLib.getDocument({ data: buffer }).promise;
      viewer.pdfDoc = doc;
      viewer.currentSourceId = sourceId;
      viewer.pageCount = doc.numPages;
      el("pdf-page-ind").textContent = `1 of ${doc.numPages}`;
      await renderAllPages();
      if (highlight && highlight.page) {
        jumpToPage(highlight.page);
        if (highlight.yTop !== undefined) highlightAt(sourceId, highlight.page, highlight.yTop);
      }
    } catch (e) {
      el("pdf-scroll").innerHTML = `<p class="panel-empty">Couldn't load this PDF (${Dom.escapeHtml(String(e))}).</p>`;
    }
  }

  async function renderAllPages() {
    const container = el("pdf-scroll");
    container.innerHTML = "";
    viewer.canvases = [];
    for (let n = 1; n <= viewer.pageCount; n++) {
      const page = await viewer.pdfDoc.getPage(n);
      const fitScale = (container.clientWidth - 4) / page.getViewport({ scale: 1 }).width;
      const scale = Math.max(2.5, Math.min(fitScale, 3.5)); // floor for crisp text, ceiling for very wide panels
      const viewport = page.getViewport({ scale });
      const pageWrap = document.createElement("div");
      pageWrap.className = "pdf-page";
      pageWrap.dataset.page = String(n);
      const canvas = document.createElement("canvas");
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = `${container.clientWidth - 4}px`;
      await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
      pageWrap.appendChild(canvas);
      container.appendChild(pageWrap);
      viewer.canvases[n] = canvas;
    }
  }

  function jumpToPage(n) {
    if (!viewer.pdfDoc) return;
    setAnnotatedMode(false);
    const canvas = viewer.canvases[n] || viewer.canvases[Math.min(n, viewer.pageCount)];
    if (!canvas) return;
    el("pdf-page-ind").textContent = `${n} of ${viewer.pageCount}`;
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

  function highlightAt(sourceId, pageNum, yTop) {
    if (!viewer.pdfDoc || viewer.currentSourceId !== sourceId) return;
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
  }

  document.addEventListener("DOMContentLoaded", () => {
    el("pdf-back").onclick = () => {
      setAnnotatedMode(false);
      const first = viewer.canvases.find(Boolean);
      if (first) first.scrollIntoView({ behavior: "smooth" });
      else if (viewer.currentSourceId && viewer.currentWsId) loadStream(viewer.currentWsId, viewer.currentSourceId);
    };
  });

  window.PDFViewer = { loadStream, jumpToPage, showAnnotatedPage, showPanel, showPlayer, highlightAt };
})();
