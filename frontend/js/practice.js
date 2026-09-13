/* practice.js — CORE. The one rule this file must never break: the correct
 * answer is never known client-side until the server's attempt response says
 * so. Don't add client-side "helpfully" pre-marking an option as correct, and
 * don't cache `result.correct_option` anywhere except to paint the one
 * question just answered — the backend explicitly withholds it from every
 * other endpoint (see backend/practice/generator.py's fetch_public/_public).
 */
(function () {
  const { $, escapeHtml } = Dom;
  const OPTION_KEYS = ["a", "b", "c", "d"];

  let quizId = null;
  let questions = [];
  let attempts = {}; // question id (string) -> { attempted, correct, selected }
  let weakSpots = null; // { overall, objectives } from getWeakSpots, or null

  function thinking(label) {
    return `<div class="thinking"><span class="spinner"></span><span>${escapeHtml(label)}</span></div>`;
  }

  function updateScore() {
    const answered = questions.filter((q) => attempts[String(q.id)] && attempts[String(q.id)].attempted);
    const right = answered.filter((q) => attempts[String(q.id)].correct).length;
    $("practice-score").textContent = answered.length ? `${right} / ${answered.length} correct` : "";
  }

  function weakSpotsHtml() {
    if (!weakSpots || !weakSpots.objectives || !weakSpots.objectives.length) return "";
    const rows = weakSpots.objectives.map((o) => {
      const pct = o.accuracy == null ? "—" : `${Math.round(o.accuracy * 100)}%`;
      const cls = o.accuracy != null && o.accuracy < 1 ? "weak-row--missed" : "weak-row--clean";
      return `<li class="weak-row ${cls}"><span class="weak-acc">${pct}</span>`
        + `<span class="weak-stmt">${escapeHtml(o.statement || "Other")}</span>`
        + `<span class="weak-count">${o.correct}/${o.attempted}</span></li>`;
    }).join("");
    return `<section class="weak-spots" id="weak-spots" aria-label="Weak spots">`
      + `<header class="question-head"><span class="q-num">Weak spots</span>`
      + `<span class="skill-code">worst first</span></header>`
      + `<ul>${rows}</ul></section>`;
  }

  async function refreshWeakSpots() {
    // Best-effort: a missing/failed weak-spots read must never break the quiz UI.
    try {
      weakSpots = await Api.getWeakSpots(AppState.workspaceId);
    } catch {
      weakSpots = null;
    }
    const body = $("practice-body");
    const old = $("weak-spots");
    const html = weakSpotsHtml();
    if (old) {
      if (html) old.outerHTML = html;
      else old.remove();
    } else if (html && questions.length) {
      body.insertAdjacentHTML("afterbegin", html);
    }
  }

  function questionHtml(q, displayIndex) {
    const att = attempts[String(q.id)];
    const options = OPTION_KEYS.map((k) => {
      let cls = "option";
      if (att && att.selected === k) cls += att.correct ? " option--correct" : " option--wrong";
      return `<button class="${cls}" data-opt="${k}" ${att ? "disabled" : ""}>`
        + `<span class="option-key">${k.toUpperCase()}</span>${escapeHtml(q.options[k])}</button>`;
    }).join("");
    return `<article class="question" data-qid="${q.id}">
      <header class="question-head"><span class="q-num">Q${displayIndex + 1}</span><span class="skill-code">${escapeHtml(q.skill_code || "obj")}</span></header>
      <p class="question-prompt">${escapeHtml(q.prompt)}</p>
      <div class="options">${options}</div>
      <div class="explanation${att ? " show" : ""}">${att ? explanationHtml(att) : ""}</div>
    </article>`;
  }

  function explanationHtml(result) {
    const verdict = result.correct ? "Correct." : "Not quite.";
    return `${verdict} ${result.explanation ? escapeHtml(result.explanation) : ""}`;
  }

  function wireQuestion(card, question) {
    card.querySelectorAll(".option").forEach((btn) => {
      btn.onclick = () => choose(question.id, btn.dataset.opt, card);
    });
  }

  function renderAll() {
    const body = $("practice-body");
    if (!questions.length) {
      body.innerHTML = `<p class="hint">No practice quiz yet. Click <strong>Generate quiz</strong> to build questions from this unit's learning objectives.</p>`;
      $("practice-score").textContent = "";
      return;
    }
    body.innerHTML = weakSpotsHtml() + questions.map((q, i) => questionHtml(q, i)).join("");
    body.querySelectorAll(".question").forEach((card) => {
      const qid = Number(card.dataset.qid);
      const question = questions.find((q) => q.id === qid);
      wireQuestion(card, question);
    });
    updateScore();
  }

  async function choose(questionId, optionKey, card) {
    const buttons = card.querySelectorAll(".option");
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const result = await Api.submitAttempt(AppState.workspaceId, quizId, questionId, optionKey);
      attempts[String(questionId)] = { attempted: true, correct: result.correct, selected: optionKey };
      const correctBtn = [...buttons].find((b) => b.dataset.opt === result.correct_option);
      const chosenBtn = [...buttons].find((b) => b.dataset.opt === optionKey);
      if (correctBtn && correctBtn !== chosenBtn) correctBtn.classList.add("option--reveal");
      if (chosenBtn) chosenBtn.classList.add(result.correct ? "option--correct" : "option--wrong");
      const exp = card.querySelector(".explanation");
      exp.classList.add("show");
      exp.innerHTML = explanationHtml(result);
      updateScore();
      refreshWeakSpots();
    } catch {
      buttons.forEach((b) => { b.disabled = false; });
    }
  }

  async function load() {
    const body = $("practice-body");
    if (!AppState.workspaceId) { body.innerHTML = `<p class="hint">Open a workspace first.</p>`; return; }
    body.innerHTML = thinking("Loading…");
    try {
      const data = await Api.getPracticeQuiz(AppState.workspaceId);
      quizId = data.quiz ? data.quiz.id : null;
      questions = data.questions || [];
      attempts = data.attempts || {};
      weakSpots = null;
      renderAll();
      if (quizId) refreshWeakSpots();
    } catch {
      body.innerHTML = `<p class="hint">Couldn't load the practice quiz.</p>`;
    }
  }

  function generate() {
    if (!AppState.workspaceId) return alert("Open a workspace first.");
    const btn = $("practice-generate");
    btn.disabled = true;
    btn.textContent = "Generating…";
    const body = $("practice-body");
    body.innerHTML = thinking("Writing practice questions…");
    $("practice-score").textContent = "";

    let done = 0;
    let streamEl = null;
    const drafted = [];

    const ensureStream = () => {
      if (streamEl) return streamEl;
      streamEl = document.createElement("div");
      streamEl.className = "practice-stream";
      body.innerHTML = "";
      body.appendChild(streamEl);
      return streamEl;
    };

    const es = new EventSource(Api.practiceStreamUrl(AppState.workspaceId));

    es.addEventListener("meta", () => ensureStream());

    es.addEventListener("q", (e) => {
      const { index, question } = JSON.parse(e.data);
      done = index;
      drafted[index - 1] = question;
      const streamRoot = ensureStream();
      streamRoot.insertAdjacentHTML("beforeend", questionHtml(question, index - 1));
      const card = streamRoot.querySelector(`.question[data-qid="${question.id}"]`);
      wireQuestion(card, question);
      body.scrollTop = body.scrollHeight;
    });

    es.addEventListener("done", (e) => {
      const data = JSON.parse(e.data);
      es.close();
      btn.disabled = false;
      btn.textContent = "Generate quiz";
      quizId = data.quiz ? data.quiz.id : null;
      questions = data.questions || drafted.filter(Boolean);
      attempts = {};
      weakSpots = null;
      if (data.skipped) $("practice-score").textContent = `${data.skipped} topic(s) skipped (not in sources)`;
    });

    es.addEventListener("error", (e) => {
      es.close();
      btn.disabled = false;
      btn.textContent = "Generate quiz";
      if (done) return; // questions already rendered — don't clobber real output with a late error
      let message = "Couldn't generate the quiz. Is the server's model reachable?";
      try {
        const parsed = e.data ? JSON.parse(e.data) : null;
        if (parsed && parsed.error) message = parsed.error;
      } catch { /* a genuine connection drop carries no JSON payload at all */ }
      body.innerHTML = `<p class="hint">${escapeHtml(message)}</p>`;
    });
  }

  function attach() {
    $("practice-btn").onclick = () => {
      if (!AppState.workspaceId) return alert("Open a workspace first.");
      $("practice-modal").classList.remove("hidden");
      load();
    };
    $("practice-close").onclick = () => $("practice-modal").classList.add("hidden");
    $("practice-modal").addEventListener("click", (e) => { if (e.target.id === "practice-modal") $("practice-modal").classList.add("hidden"); });
    $("practice-generate").onclick = generate;
  }

  window.Practice = { attach };
})();
