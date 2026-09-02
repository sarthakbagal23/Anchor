/* Source Preview PDF viewer: renders the original PDF (PDF.js) so users can browse the
 * actual document, jumps to a chunk's page on citation click, and overlays AI annotation
 * boxes (as fractions of a rendered page image) to explain concepts. */
(function () {
  const viewer = { pdfDoc: null, currentSourceId: null, pageCount: 0, canvases: [] };

  const scroll = () => document.getElementById("pdf-scroll");
  const annotWrap = () => document.getElementById("pdf-annot-wrap");
  const pageInd = () => document.getElementById("pdf-page-ind");
  const pdfViewer = () => document.getElementById("pdf-viewer");

  const pdfjsReady = () => typeof window.pdfjsLib !== "undefined";

  function configureWorker() {
    if (pdfjsReady() && !pdfjsLib.GlobalWorkerOptions.workerSrc) {
      pdfjsLib.GlobalWorkerOptions.workerSrc =
        "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
    }
  }

  function showPanel() {
    pdfViewer().classList.remove("hidden");
    document.getElementById("player-wrap").classList.add("hidden");
  }
  function showPlayer() {
    pdfViewer().classList.add("hidden");
    document.getElementById("player-wrap").classList.remove("hidden");
  }

  function reset(showAnnotated) {
    annotWrap().classList.toggle("hidden", !showAnnotated);
    scroll().classList.toggle("hidden", showAnnotated);
    document.getElementById("pdf-back").classList.toggle("hidden", !showAnnotated);
  }

  async function loadStream(sourceId, highlight) {
    if (!pdfjsReady()) {
      scroll().innerHTML = `<div class="player-placeholder">PDF.js failed to load (offline?). Check your connection and refresh.</div>`;
      return;
    }
    configureWorker();
    showPanel(); reset(false);
    scroll().innerHTML = `<div class="player-placeholder">Loading PDF…</div>`;
    try {
      const ws = viewer.currentWS || (window.__currentWS__);
      const res = await fetch(`/api/workspaces/${ws}/sources/${sourceId}/file`);
      if (!res.ok) throw new Error("http " + res.status);
      const buf = await res.arrayBuffer();
      const doc = await pdfjsLib.getDocument({ data: buf }).promise;
      viewer.pdfDoc = doc; viewer.currentSourceId = sourceId; viewer.pageCount = doc.numPages;
      pageInd().textContent = "1 of " + doc.numPages;
      await renderAll();
      if (highlight && highlight.page) {
        jumpToPage(highlight.page);
        if (highlight.yTop != null) highlightAt(sourceId, highlight.page, highlight.yTop);
      }
    } catch (e) {
      scroll().innerHTML = `<div class="player-placeholder">Could not load PDF (${escapeHtml(String(e))})</div>`;
    }
  }

  async function renderAll() {
    const container = scroll();
    container.innerHTML = "";
    viewer.canvases = [];
    for (let n = 1; n <= viewer.pageCount; n++) {
      const page = await viewer.pdfDoc.getPage(n);
      const fitScale = (container.clientWidth - 4) / page.getViewport({ scale: 1 }).width;
      // Always render at least 2.5x for crisp text; allow up to 3.5x if panel is wide enough
      const scale = Math.max(2.5, Math.min(fitScale, 3.5));
      const vp = page.getViewport({ scale });
      const canvas = document.createElement("canvas");
      canvas.width = vp.width; canvas.height = vp.height;
      canvas.dataset.page = n;
      canvas.style.width = (container.clientWidth - 4) + "px";
      const ctx = canvas.getContext("2d");
      await page.render({ canvasContext: ctx, viewport: vp }).promise;
      container.appendChild(canvas);
      viewer.canvases[n] = canvas;
    }
  }

  function jumpToPage(n) {
    if (!viewer.pdfDoc) return;
    reset(false);
    const c = viewer.canvases[n] || viewer.canvases[Math.min(n, viewer.pageCount)];
    if (!c) return;
    pageInd().textContent = `${n} of ${viewer.pageCount}`;
    const top = c.offsetTop - scroll().getBoundingClientRect().top;
    scroll().scrollTo({ top, behavior: "smooth" });
    c.classList.remove("page-flash"); void c.offsetWidth; c.classList.add("page-flash");
  }

  function showAnnotatedPage(sourceId, pageNum, annotations) {
    if (!sourceId) return;
    const ws = window.__currentWS__;
    const wrap = annotWrap();
    const base = `api/workspaces/${ws}/sources/${sourceId}/pages/${pageNum}/image`;
    // Use the rendered image so annotation fractions map 1:1 onto the shown pixels.
    wrap.innerHTML = `<div class="annot-page"><img class="annot-img" src="/${base}" alt="PDF page ${pageNum}"><div class="annot-layer"></div><div class="annot-title">Page ${pageNum} · annotated by AI</div></div>`;
    const layer = wrap.querySelector(".annot-layer");
    const title = wrap.querySelector(".annot-title");
    (annotations || []).forEach(a => {
      const box = document.createElement("div");
      box.className = "annot-box";
      box.style.left = (a.x * 100) + "%";
      box.style.top = (a.y * 100) + "%";
      box.style.width = (a.w * 100) + "%";
      box.style.height = (a.h * 100) + "%";
      if (a.label) box.title = a.label;
      const lbl = document.createElement("span");
      lbl.className = "annot-label";
      lbl.textContent = a.label || "";
      box.appendChild(lbl);
      layer.appendChild(box);
    });
    showPanel(); reset(true);
    pageInd().textContent = `${pageNum} of ${viewer.pageCount || pageNum}`;
    wrap.scrollIntoView({ behavior: "smooth", block: "start" });
    void title.offsetWidth; title.classList.add("show");
  }

  viewer.loadStream = loadStream;
  viewer.jumpToPage = jumpToPage;
  viewer.showAnnotatedPage = showAnnotatedPage;
  viewer.showPlayer = showPlayer;
  viewer.highlightAt = highlightAt;

  document.getElementById("pdf-back").onclick = () => {
    reset(false);
    if (viewer.canvases.length) {
      const first = viewer.canvases.find(Boolean);
      if (first) first.scrollIntoView({ behavior: "smooth" });
    } else if (viewer.currentSourceId) {
      loadStream(viewer.currentSourceId);
    }
  };

  window.PDFViewer = viewer;

  // Draw a highlight box on the page canvas at y_top (0..1 fraction from top)
  function highlightAt(sourceId, pageNum, yTop) {
    if (!viewer.pdfDoc || viewer.currentSourceId !== sourceId) return;
    const canvas = viewer.canvases[pageNum];
    if (!canvas) return;
    // Remove any existing highlight on this canvas
    const existing = canvas.parentElement.querySelector(".pdf-highlight");
    if (existing) existing.remove();
    // Create highlight overlay
    const rect = canvas.getBoundingClientRect();
    const highlight = document.createElement("div");
    highlight.className = "pdf-highlight";
    const boxHeight = Math.max(28, rect.height * 0.07); // ~7% of page height
    const top = rect.height * Math.max(0, Math.min(1, yTop));
    highlight.style.cssText = `
      position: absolute;
      left: 2%; right: 2%;
      top: ${top}px;
      height: ${boxHeight}px;
      background: rgba(88,166,255,0.25);
      border: 2px solid var(--accent);
      border-radius: 4px;
      pointer-events: none;
      z-index: 10;
      animation: highlightFade 1.5s ease forwards;
    `;
    canvas.parentElement.style.position = "relative";
    canvas.parentElement.appendChild(highlight);
    // Scroll to it
    highlight.scrollIntoView({ behavior: "smooth", block: "center" });
    // Auto-remove after 4s
    setTimeout(() => { highlight.remove(); }, 4000);
  }
})();
