/* chat.js — CORE. Two subtleties worth preserving if you touch streamAnswer():
 *
 * 1. TWO DIFFERENT RENDER PASSES. While tokens are still arriving, each
 *    repaint only escapes + resolves citations (Citations.render) — it does
 *    NOT run full markdown formatting. A "**bold" that hasn't been closed
 *    yet mid-stream would otherwise render as a stray asterisk or an
 *    unintentionally-bolded rest-of-answer. Only once the stream ends do we
 *    run the real block/inline formatter (formatStudyText + RichText) on the
 *    complete text.
 * 2. THROTTLED REPAINT. Rebuilding the whole answer's innerHTML on every
 *    single token is O(n²) over a long answer and visibly freezes the tab.
 *    Repaints are capped at ~10/sec while streaming; the final paint always
 *    runs unthrottled so nothing is left stale.
 *
 * The citation click handler at the bottom is the other load-bearing part:
 * dataset.sec is the STRING "0" for a citation at the very start of a video,
 * which is a real, valid seek target — only an empty/missing attribute means
 * "this citation has no timestamp". Checking `if (sec)` instead of checking
 * for emptiness was a real bug (silently swallowed every 0:00 citation).
 */
(function () {
  const { $, escapeHtml } = Dom;

  // Only one question may be in flight at a time. A new send supersedes the
  // previous one: the old request is aborted and its bubble is settled
  // silently, so hung requests can never pile up server-side (each one holds
  // a thread + SQLite time) and the UI never shows two spinners at once.
  let activeSend = null;

  function bubble(role, innerHtml) {
    const div = document.createElement("div");
    div.className = `msg msg--${role}`;
    div.innerHTML = `<div class="msg-content">${innerHtml}</div>`;
    return div;
  }

  function formatFinal(text) {
    return formatStudyText(text, (t) => RichText.inline(t, AppState.citationMap, AppState.sources));
  }

  function renderStored(m) {
    // Citations arrive in their own field now; the delimiter split is only
    // for pre-migration rows the backend didn't normalize.
    if (m.role === "assistant" && m.citations && Object.keys(m.citations).length) {
      Object.assign(AppState.citationMap, m.citations);
      return bubble("assistant", formatFinal(m.content));
    }
    if (m.role === "assistant" && m.content.includes("|||CITATIONS|||")) {
      const [text, mapJson] = m.content.split("|||CITATIONS|||");
      try { Object.assign(AppState.citationMap, JSON.parse(mapJson)); } catch { /* malformed stored map — show text without citations */ }
      return bubble("assistant", formatFinal(text));
    }
    return bubble(m.role, `<p>${escapeHtml(m.content)}</p>`);
  }

  function renderHistory(messages) {
    const container = $("messages");
    if (!messages.length) {
      container.innerHTML = `<div id="empty-state" class="empty-state">
        <p class="empty-kicker">No sources yet</p>
        <h2>Add a lecture or a PDF to start</h2>
        <p>Every answer here will cite the exact moment or page it came from.</p>
      </div>`;
      return;
    }
    container.innerHTML = "";
    messages.forEach((m) => container.append(renderStored(m)));
    container.scrollTop = container.scrollHeight;
  }

  async function streamAnswer(question, body, stopTimer, container, send) {
    const res = await fetch(Api.chatStreamUrl(AppState.workspaceId), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...Api.authHeaders() },
      body: JSON.stringify({ message: question }),
      signal: send.controller.signal,
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `http ${res.status}`);
    }
    let full = "";
    let lastPaint = 0;
    const paintPartial = () => {
      const now = Date.now();
      if (now - lastPaint < 100) return;
      lastPaint = now;
      body.innerHTML = Citations.render(escapeHtml(full), AppState.citationMap, AppState.sources) + '<span class="cursor"></span>';
      container.scrollTop = container.scrollHeight;
    };
    const showPage = (payload) => {
      // Same display logic the old per-path visual call used: annotated overlay
      // when the model pointed at regions, plain jump otherwise.
      const data = payload || {};
      if (data.annotations && data.annotations.length && data.page) {
        AppState.pdfCurrentSourceId = data.page.source_id;
        PDFViewer.showAnnotatedPage(AppState.workspaceId, data.page.source_id, data.page.page_num, data.annotations);
      } else if (data.page) {
        PDFViewer.loadStream(AppState.workspaceId, data.page.source_id).then(() => PDFViewer.jumpToPage(data.page.page_num));
      }
    };

    await SSE.readEventStream(res, {
      onEvent(name, payload) {
        send.markProgress(); // any frame proves the model is alive; resets the first-token deadline
        if (name === "route") {
          // The server grounded first and is telling us which path it took.
          // Vision answers are one slow non-streaming model call, so they get
          // a longer first-token deadline than the text stream's 120s.
          const mode = (SSE.readJSON(payload, {}) || {}).mode;
          if (mode === "vision") {
            const label = body.querySelector(".thinking-txt");
            if (label) label.textContent = "Reading the PDF";
            send.armDeadline(300000);
          }
        } else if (name === "page") {
          showPage(SSE.readJSON(payload, {}));
        } else if (name === "citations") {
          stopTimer();
          AppState.citationMap = SSE.readJSON(payload, {});
          body.classList.remove("streaming");
          body.innerHTML = Citations.render(escapeHtml(full), AppState.citationMap, AppState.sources);
        } else if (name === "error") {
          stopTimer();
          const { error } = SSE.readJSON(payload, { error: "Something went wrong." });
          body.classList.remove("streaming");
          body.innerHTML = `<p class="error-text">${escapeHtml(error)}</p>`;
        } else {
          const { token } = SSE.readJSON(payload, {});
          if (token) { stopTimer(); full += token; paintPartial(); }
        }
      },
    });

    stopTimer();
    body.classList.remove("streaming");
    body.innerHTML = formatFinal(full);
    container.scrollTop = container.scrollHeight;
  }

  function attachSend() {
    $("chat-form").onsubmit = async (e) => {
      e.preventDefault();
      if (!AppState.workspaceId) return alert("Open a workspace first.");
      const input = $("chat-input");
      const question = input.value.trim();
      if (!question) return;
      input.value = "";

      // Supersede any still-running send: abort its request and settle its
      // bubble silently, so the new question is the only thing in flight.
      if (activeSend) {
        const prev = activeSend;
        activeSend = null;
        prev.superseded = true;
        try { prev.controller.abort(); } catch {}
        try { prev.settleOld && prev.settleOld(); } catch {}
      }
      const send = {
        controller: new AbortController(),
        superseded: false, timedOut: false, settled: false,
        firstTokenTimer: null, settleOld: null,
        markProgress() { clearTimeout(send.firstTokenTimer); send.firstTokenTimer = null; },
        // First-token deadline, re-armable: 120s default; the server's route
        // event extends it when the slower vision path is taken.
        armDeadline(ms) {
          clearTimeout(send.firstTokenTimer);
          send.firstTokenTimer = setTimeout(() => {
            if (send.settled || send.superseded) return;
            send.timedOut = true;
            try { send.controller.abort(); } catch {}
          }, ms);
        },
      };
      activeSend = send;
      send.armDeadline(120000);

      const container = $("messages");
      const empty = $("empty-state");
      if (empty) empty.remove();
      container.append(bubble("user", `<p>${escapeHtml(question)}</p>`));

      const assistant = bubble("assistant",
        '<div class="streaming"><span class="thinking"><span class="spinner"></span>'
        + '<span class="thinking-txt">Thinking</span><span class="thinking-timer">0s</span></span>'
        + '<span class="cursor"></span></div>');
      container.append(assistant);
      container.scrollTop = container.scrollHeight;

      const body = assistant.querySelector(".streaming");
      const timerEl = assistant.querySelector(".thinking-timer");
      const t0 = Date.now();
      const tick = setInterval(() => { timerEl.textContent = `${Math.round((Date.now() - t0) / 1000)}s`; }, 1000);
      let stopped = false;
      const stopTimer = () => { if (!stopped) { stopped = true; clearInterval(tick); } };
      send.settleOld = () => {
        stopTimer();
        body.classList.remove("streaming");
        const label = assistant.querySelector(".thinking-txt");
        if (label) label.textContent = "Stopped — answering your newer question below.";
        const cursor = assistant.querySelector(".cursor");
        if (cursor) cursor.remove();
      };

      // Routing lives server-side now: /chat grounds once and answers over
      // this single stream (route event first), so there is no workspace-level
      // hasReadyPdf branch anymore — a video-only question in a mixed
      // workspace streams text immediately instead of hanging on the vision
      // model, and PDF-anchored questions still get the page event below.
      try {
        await streamAnswer(question, body, stopTimer, container, send);
      } catch (err) {
        // Superseded sends stay silent — the newer question owns the UI now.
        // Anything else (network error, both paths stalled) becomes an honest
        // error bubble instead of a permanent spinner.
        if (send.superseded) return;
        stopTimer();
        body.classList.remove("streaming");
        const msg = send.timedOut
          ? "The model didn't respond within 2 minutes. Try again, or switch to the fast model."
          : String((err && err.message) || err);
        body.innerHTML = `<p class="error-text">${escapeHtml(msg)}</p>`;
      } finally {
        send.settled = true;
        clearTimeout(send.firstTokenTimer);
        if (activeSend === send) activeSend = null;
      }
      container.scrollTop = container.scrollHeight;
    };
  }

  document.addEventListener("click", (e) => {
    const target = e.target.closest(".cite");
    if (!target) return;

    const secAttr = target.dataset.sec;
    const hasSec = secAttr !== undefined && secAttr !== "";
    const sec = hasSec ? Number(secAttr) : NaN;

    document.querySelectorAll(".cite.playing").forEach((c) => c.classList.remove("playing"));
    target.classList.add("playing", "visited");

    const citation = Object.values(AppState.citationMap).find((c) => String(c.chunk_id) === target.dataset.chunkId);
    if (!citation) return;
    const source = AppState.sources.find((s) => s.id === citation.source_id);

    const location = source?.type === "pdf" && citation.page_num
      ? `p.${citation.page_num}`
      : (Citations.fmtTime(citation.start_sec) || "source");
    $("passage").innerHTML = `<div class="passage-card">`
      + `<p class="passage-meta">${escapeHtml(citation.source_title)} · ${escapeHtml(location)}</p>`
      + `<p class="passage-text">${escapeHtml(citation.text)}</p></div>`;

    if (source?.youtube_id && hasSec) {
      PDFViewer.showPlayer(); // the PDF panel would otherwise cover the player
      Player.load(source.youtube_id, sec);
    }
    if (source?.type === "pdf") {
      Player.pause(); // leaving video context — never leave audio playing behind the PDF panel
      PDFViewer.showPanel();
      const highlight = citation.page_num ? { page: citation.page_num, yTop: citation.y_top ?? null } : null;
      if (AppState.pdfCurrentSourceId !== source.id) {
        PDFViewer.loadStream(AppState.workspaceId, source.id, highlight).then(() => { AppState.pdfCurrentSourceId = source.id; });
      } else if (citation.page_num) {
        PDFViewer.jumpToPage(citation.page_num);
        PDFViewer.highlightAt(source.id, citation.page_num, citation.y_top ?? null);
      }
    }
  });

  window.Chat = { attachSend, renderHistory };
})();
