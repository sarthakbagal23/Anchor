const $ = (id) => document.getElementById(id);
const api = (path, opts) => fetch(path, opts).then(r => r.json());

let workspaces = [], currentWS = null, sources = [], citationMap = {};
let fastMode = false;

let courses = [];

async function loadSidebar() {
  workspaces = await api("/api/workspaces");
  courses = await Promise.all((await api("/api/courses")).map(async c => {
    const full = await api(`/api/courses/${c.id}`).catch(() => ({ ...c, units: [] }));
    return { ...full, units: full.units || [] };
  }));
  renderTree();
}

function wsItem(ws) {
  return `<li class="ws-item${currentWS === ws.id ? ' active' : ''}">
     <a href="#" data-id="${ws.id}"><span class="ws-icon">📓</span><span class="ws-name">${escapeHtml(ws.title)}</span></a>
   </li>`;
}

function renderTree() {
  const list = $("workspace-list");
  const byUnit = {};
  workspaces.forEach(n => { (byUnit[n.unit_id] = byUnit[n.unit_id] || []).push(n); });
  const orphaned = (byUnit[null] || byUnit[undefined] || []).slice();

  const unitsHtml = (c) => (c.units || []).map(u => {
    const kids = (byUnit[u.id] || []);
    return `<div class="unit">
      <div class="unit-head">
        <span class="unit-bullet">▸</span><span class="unit-title">${escapeHtml(u.title)}</span>
        <span class="node-count">${kids.length}</span>
        <button class="tree-act add-ws" data-unit="${u.id}" data-course="${c.id}" title="Add workspace">+</button>
        <button class="tree-act del-unit" data-id="${u.id}" title="Delete unit">✕</button>
      </div>
      <ul class="ws-children">${kids.map(wsItem).join("") || '<li class="empty-hint">No workspaces</li>'}</ul>
    </div>`;
  }).join("");

  const courseHtml = courses.map(c => `<div class="course">
    <div class="course-head">
      <span class="course-caret">▾</span><span class="course-title">${escapeHtml(c.title)}</span>
      <span class="node-count">${(c.units || []).length}</span>
      <button class="tree-act add-unit" data-id="${c.id}" title="Add unit">+</button>
      <button class="tree-act del-course" data-id="${c.id}" title="Delete course">✕</button>
    </div>
    <div class="units">${unitsHtml(c)}</div>
  </div>`).join("");

  const orphanHtml = orphaned.length ? `<div class="course">
    <div class="course-head muted-head"><span class="course-caret">▾</span><span class="course-title">Unorganized</span><span class="node-count">${orphaned.length}</span></div>
    <ul class="ws-children">${orphaned.map(wsItem).join("")}</ul>
  </div>` : "";

  list.innerHTML = courseHtml + orphanHtml;

  list.querySelectorAll(".ws-item a").forEach(a => { a.onclick = e => { e.preventDefault(); openWS(+a.dataset.id); }; });
  list.querySelectorAll(".add-ws").forEach(b => b.onclick = e => { e.stopPropagation(); openModal(+b.dataset.course, +b.dataset.unit); });
  list.querySelectorAll(".add-unit").forEach(b => b.onclick = async e => { e.stopPropagation(); const title = prompt("Unit title?", "New unit"); if (!title || !b.dataset.id) return; await api(`/api/courses/${b.dataset.id}/units`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({title}) }); loadSidebar(); });
  list.querySelectorAll(".del-course").forEach(b => b.onclick = async e => { e.stopPropagation(); if (!confirm("Delete this course and its units?\nWorkspaces remain and move to Unorganized.")) return; await api(`/api/courses/${b.dataset.id}`, { method:"DELETE" }).catch(()=>{}); loadSidebar(); });
  list.querySelectorAll(".del-unit").forEach(b => b.onclick = async e => { e.stopPropagation(); if (!confirm("Delete this unit?\nIts sources and workspaces lose the unit link.")) return; await api(`/api/units/${b.dataset.id}`, { method:"DELETE" }).catch(()=>{}); loadSidebar(); });
}

function courseIsNew() { return $("ws-course").value === "__new__"; }
function selectedCourseId() { return Number($("ws-course").value); }
function unitIsNew() { return $("ws-unit").value === "__new__"; }
function selectedUnitId() { return Number($("ws-unit").value); }
function onCourseChange() {
  const isNew = courseIsNew();
  $("ws-course-name-field").classList.toggle("hidden", !isNew);
  const unitSel = $("ws-unit");
  if (isNew) { unitSel.disabled = true; unitSel.innerHTML = '<option value="__new__">New unit…</option>'; $("ws-unit-name-field").classList.remove("hidden"); return; }
  unitSel.disabled = false;
  const c = courses.find(x => x.id === selectedCourseId());
  unitSel.innerHTML = '<option value="__new__">New unit…</option>' + (c?.units || []).map(u => `<option value="${u.id}">${escapeHtml(u.title)}</option>`).join("");
  unitSel.onchange = () => $("ws-unit-name-field").classList.toggle("hidden", !unitIsNew());
  unitSel.onchange();
}
function openModal(presetCourseId = null, presetUnitId = null) {
  const courseSel = $("ws-course");
  courseSel.innerHTML = '<option value="__new__">New course…</option>' + courses.map(c => `<option value="${c.id}">${escapeHtml(c.title)}</option>`).join("");
  courseSel.onchange = onCourseChange;
  $("ws-name").value = "";
  $("ws-course-name").value = "";
  $("ws-unit-name").value = "";
  if (presetCourseId != null && courses.some(c => c.id === presetCourseId)) courseSel.value = String(presetCourseId);
  else courseSel.value = "__new__";
  onCourseChange();
  if (presetUnitId != null && !courseIsNew()) {
    $("ws-unit").value = String(presetUnitId); $("ws-unit-name-field").classList.add("hidden");
  }
  $("ws-modal").classList.remove("hidden");
  setTimeout(() => $("ws-name").focus(), 30);
}
function closeModal() { $("ws-modal").classList.add("hidden"); }

$("new-workspace").onclick = () => openModal();
$("ws-cancel").onclick = closeModal;
$("ws-modal").addEventListener("click", e => { if (e.target === $("ws-modal")) closeModal(); });

$("ws-create").onclick = async () => {
  const name = $("ws-name").value.trim();
  if (!name) return alert("Workspace name is required.");
  let courseId;
  if (courseIsNew()) {
    const cname = $("ws-course-name").value.trim();
    if (!cname) return alert("Enter a name for the new course.");
    courseId = (await api("/api/courses", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({title:cname}) })).id;
  } else {
    courseId = selectedCourseId();
  }
  let unitId;
  if (unitIsNew()) {
    const uname = $("ws-unit-name").value.trim() || "Unit 1";
    unitId = (await api(`/api/courses/${courseId}/units`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({title:uname}) })).id;
  } else {
    unitId = selectedUnitId();
  }
  const ws = await api(`/api/units/${unitId}/workspaces`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({title:name}) });
  if (!ws.id) return alert("Failed to create workspace.");
  closeModal();
  await loadSidebar(); openWS(ws.id);
}

async function loadFastMode() {
  const cfg = await api("/api/config");
  fastMode = cfg.llm?.active === "fast";
  updateFastToggle(cfg);
}

function updateFastToggle(cfg) {
  $("speed-standard").classList.toggle("active", !fastMode);
  $("speed-fast").classList.toggle("active", fastMode);
  const hint = $("speed-hint");
  if (cfg) {
    const name = fastMode ? (cfg.llm?.fast_model || "fast model") : (cfg.llm?.model || "standard model");
    hint.textContent = fastMode ? `Fast · ${name}` : `Deep · ${name}`;
    hint.title = fastMode ? "Using the fast model for quicker answers." : "Using the default model for deeper answers.";
  } else {
    hint.textContent = fastMode ? "Fast mode on" : "Deep mode";
  }
}

async function toggleFastMode(on) {
  const cfg = await api("/api/config");
  if (on && !cfg.llm?.fast_model) return alert("No fast model configured in settings.");
  if (!on && !cfg.llm?.model) return alert("No standard model configured.");
  fastMode = on;
  await api("/api/config", {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({llm: {active: fastMode ? "fast" : "standard"}})
  });
  updateFastToggle(await api("/api/config"));
}

$("speed-standard").onclick = () => { if (fastMode) toggleFastMode(false); };
$("speed-fast").onclick = () => { if (!fastMode) toggleFastMode(true); };

loadFastMode();

async function openWS(id) {
  currentWS = id;
  window.__currentWS__ = id;
  const ws = await api(`/api/workspaces/${id}`);
  $("ws-title").textContent = ws.title;
  sources = ws.sources || [];
  renderSources(); renderMessages(ws.messages || []);
  await loadSidebar(); $("chat-input").focus();
  if (window.PDFViewer) window.PDFViewer.showPlayer();
}

function renderSources() {
  const statusColors = {queued:"status-queued",downloading:"status-downloading",transcribing:"status-transcribing",extracting:"status-extracting",chunking:"status-chunking",ready:"status-ready",failed:"status-failed"};
  $("source-list").innerHTML = sources.map(s =>
    `<li class="source-item"><span class="source-title" title="${escapeHtml(s.error || '')}">${escapeHtml(s.title || s.youtube_id || 'Processing…')}</span><div style="display:flex;gap:8px;align-items:center"><span class="source-status ${statusColors[s.status] || ''}">${s.status}</span><button class="delete-source" data-id="${s.id}" style="background:none;border:none;color:var(--text-muted);cursor:pointer;padding:0 4px" title="Delete source">✕</button></div></li>`).join("");
  $("ws-sources-count").textContent = sources.length ? `${sources.length} source${sources.length > 1 ? 's' : ''}` : '';
  document.querySelectorAll('.delete-source').forEach(btn => btn.onclick = async e => { e.stopPropagation(); if (!confirm("Delete this source?")) return; btn.disabled=true; try { await api(`/api/workspaces/${currentWS}/sources/${btn.dataset.id}`,{method:'DELETE'}); openWS(currentWS); } catch { alert("Failed to delete source"); btn.disabled=false; } });
}

function renderMessages(messages) {
  const container = $("messages");
  if (!messages.length) { container.innerHTML = `<div id="empty-state"><div class="empty-icon">💬</div><h2>Start a conversation</h2><p>Add a YouTube lecture source, then ask questions grounded in the transcript.</p></div>`; return; }
  container.innerHTML = messages.map(renderStored).join(""); container.scrollTop = container.scrollHeight;
}

$("add-source").onclick = async () => {
  if (!currentWS) return alert("Create or open a workspace first.");
  const url = $("source-url").value.trim(); if (!url) return;
  const button = $("add-source"); button.disabled=true; button.textContent="Adding…";
  try { const {source_id} = await api(`/api/workspaces/${currentWS}/sources`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url})}); $("source-url").value=""; const es=new EventSource(`/api/workspaces/${currentWS}/sources/${source_id}/stream`); es.onmessage=e=>{const d=JSON.parse(e.data);openWS(currentWS);if(d.status==='ready'||d.status==='failed')es.close()};es.onerror=()=>es.close(); } finally { button.disabled=false; button.textContent="Add source"; }
};

const pdfNameEl = () => $("pdf-filename");
$("pick-pdf").onclick = () => $("pdf-file").click();
$("pdf-file").addEventListener("change", async () => {
  const file = $("pdf-file").files[0];
  if (!file) return;
  if (!currentWS) { setPdfStatus("Open a workspace first.", true); return; }
  if (!/\.pdf$/i.test(file.name)) { setPdfStatus("Only PDF files are supported.", true); return; }
  if (file.size > 25 * 1024 * 1024) { setPdfStatus("PDF must be 25 MB or smaller.", true); return; }
  pdfNameEl().textContent = file.name;
  setPdfStatus("Uploading…", false);
  const body = new FormData();
  body.append("file", file, file.name);
  try {
    const res = await fetch(`/api/workspaces/${currentWS}/uploads`, { method:"POST", body });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { setPdfStatus(errorMsg(res.status, data), true); pdfNameEl().textContent=""; return; }
    const sourceId = data.source_id;
    const es = new EventSource(`/api/workspaces/${currentWS}/sources/${sourceId}/stream`);
    es.onmessage = e => {
      const d = JSON.parse(e.data);
      setPdfStatus(statusLabel(d.status, d.error), d.status === "failed");
      openWS(currentWS);
      if (d.status === "ready" || d.status === "failed") { es.close(); if (d.status === "ready") setTimeout(() => { setPdfStatus(""); pdfNameEl().textContent=""; }, 2500); }
    };
    es.onerror = () => es.close();
  } catch (err) {
    setPdfStatus("Upload failed. Is the server running?", true);
    pdfNameEl().textContent = "";
  }
});
function setPdfStatus(text, isError) {
  const el = $("pdf-status");
  el.textContent = text;
  el.style.color = isError ? "var(--danger)" : (text ? "var(--accent)" : "");
}
function statusLabel(status, error) {
  if (status === "failed") return `Failed — ${(error || "unknown error")}`;
  if (status === "ready") return "Done ✓";
  return `Status: ${status}`;
}
function errorMsg(status, data) {
  if (status === 415) return "Unsupported file type — PDF only.";
  if (status === 413) return "File too large — 25 MB max.";
  if (status === 404) return "Workspace not found.";
  return (data && data.detail) || `Upload failed (HTTP ${status}).`;
}

function hasReadyPdf() { return sources.some(s => s.type === "pdf" && s.status === "ready"); }

$("chat-form").onsubmit = async e => {
  e.preventDefault(); if (!currentWS) return alert("Open a workspace first.");
  const q=$("chat-input").value.trim(); if(!q)return; $("chat-input").value="";
  const container=$("messages"), empty=$("empty-state"); if(empty)empty.remove();
  const userDiv=document.createElement("div"); userDiv.className="msg user"; userDiv.innerHTML=`<div class="msg-content"><p>${escapeHtml(q)}</p></div>`;
  const bubble=document.createElement("div"); bubble.className="msg assistant"; bubble.innerHTML='<div class="msg-content"><div class="streaming"><div class="thinking"><span class="spinner"></span><span class="thinking-txt">Thinking</span><span class="thinking-timer"></span></div><span class="cursor"></span></div></div>';
  container.append(userDiv,bubble); container.scrollTop=container.scrollHeight; const p=bubble.querySelector(".streaming"), timerEl=bubble.querySelector(".thinking-timer");
  const t0=Date.now(); let tick=null; const clock=()=>{timerEl.textContent=`${Math.round((Date.now()-t0)/1000)}s`}; tick=setInterval(clock,1000); clock();
  const stopTimer=()=>{if(tick){clearInterval(tick);tick=null}};
  try {
    if (hasReadyPdf()) {
      // PDF-aware: AI can see the page images and optionally annotate.
      const thinkingTxt = bubble.querySelector(".thinking-txt");
      thinkingTxt.textContent = "Seeing the PDF";
      const res = await fetch(`/api/workspaces/${currentWS}/chat/visual`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:q})});
      if (!res.ok) throw new Error("visual http "+res.status);
      const data = await res.json();
      stopTimer();
      citationMap = data.citations || {};
      p.classList.remove("streaming");
      p.innerHTML = formatStudyText(data.answer || "", inlineFormat);
      if (data.annotations && data.annotations.length && data.page && window.PDFViewer) {
        window.__currentPDFSourceId__ = data.page.source_id;
        PDFViewer.showAnnotatedPage(data.page.source_id, data.page.page_num, data.annotations);
      } else if (data.page && window.PDFViewer) {
        PDFViewer.loadStream(data.page.source_id).then(() => PDFViewer.jumpToPage(data.page.page_num));
      }
    } else {
      await streamAnswer(q, p, stopTimer, container);
    }
  } catch (err) {
    stopTimer(); p.classList.remove("streaming");
    // The PDF visual path failed (e.g. vision model error/unavailable). Degrade gracefully
    // to a normal grounded text answer instead of leaving the user with a raw error.
    try { await streamAnswer(q, p, stopTimer, container); }
    catch (e2) { p.innerHTML = `<span style="color:var(--danger)">⚠ ${escapeHtml(String(err))}</span>`; }
  }
  container.scrollTop = container.scrollHeight;
};

// Stream a normal grounded text answer (SSE) into bubble `p`, updating citations as they arrive.
async function streamAnswer(q, p, stopTimer, container) {
  const resp = await fetch(`/api/workspaces/${currentWS}/chat`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:q})});
  if (!resp.ok) { const d = await resp.json().catch(()=>({})); throw new Error(d.detail || ("http "+resp.status)); }
  const reader=resp.body.getReader(),dec=new TextDecoder();
  let buf="",full="",curEvent="message";
  // Large answers: rebuilding the full answer's HTML on EVERY token is O(n^2) and freezes
  // the browser for long responses ("stops after the first word"). Throttle repaints to a
  // few per second so token accumulation stays cheap; the final paint uses full formatting.
  let lastPaint = 0;
  while(true){const {done,value}=await reader.read();if(done)break;buf+=dec.decode(value,{stream:true});const lines=buf.split("\n");buf=lines.pop();for(const line of lines){if(line.startsWith("event:")){curEvent=line.slice(6).trim();continue}if(!line.startsWith("data:"))continue;const payload=line.slice(5).trim();if(!payload)continue;if(curEvent==="citations"){if(stopTimer)stopTimer();citationMap=JSON.parse(payload);p.classList.remove("streaming");p.innerHTML=renderTokens(escapeHtml(full))}else if(curEvent==="error"){if(stopTimer)stopTimer();const {error}=JSON.parse(payload);p.classList.remove("streaming");p.innerHTML=`<span style="color:var(--danger)">⚠ ${escapeHtml(error)}</span>`}else{const {token}=JSON.parse(payload);if(token){if(stopTimer)stopTimer();full+=token;const now=Date.now();if(now-lastPaint>=100){lastPaint=now;p.innerHTML=renderTokens(escapeHtml(full))+'<span class="cursor"></span>'}}}}container.scrollTop=container.scrollHeight}
  if(stopTimer)stopTimer(); p.classList.remove("streaming"); p.innerHTML=formatStudyText(full, inlineFormat); container.scrollTop=container.scrollHeight;
}

// Compact citation presentation: timestamp + source number, with transcript on hover.
function sourceNumberForCitationIndex(index){
  const ids=[];
  for(const item of Object.values(citationMap)){const id=String(item.source_id ?? "");if(!ids.includes(id))ids.push(id);}
  const sourceId=citationMap[Number(index)]?.source_id;
  const ordinal=ids.indexOf(String(sourceId));
  return ordinal>=0?ordinal+1:index;
}
function renderTokens(text){return text.replace(/\[(\d+(?:\s*,\s*\d+)*)\]([.,;:!?])?/g,(_,nums,punct)=>`<span class="cite-wrap">${nums.split(",").map(n=>{const c=citationMap[+n.trim()];if(!c)return `<span class="cite-raw">[${n.trim()}]</span>`;const mmss=fmtTime(c.start_sec);const page=c.page_num?`p.${c.page_num}`:"";const label=mmss?`${mmss} · Source ${sourceNumberForCitationIndex(n)}`:`${page? page+" · ":""}Source ${sourceNumberForCitationIndex(n)}`;return `<span class="cite" data-id="${c.chunk_id}" data-sec="${c.start_sec||0}" data-source-id="${c.source_id||''}" aria-label="${escapeHtml(`Jump to ${label}`)}" title="${escapeHtml(c.text||'')}">${escapeHtml(label)}<span class="under"></span></span>`}).join(" ")}${punct||""}</span>`) }
function inlineFormat(text){
  const code=[];
  let safe=escapeHtml(text).replace(/`([^`]+)`/g,(_,value)=>{code.push(value);return `\u0000${code.length-1}\u0000`;});
  safe=renderTokens(safe).replace(/\*\*(.+?)\*\*/g,"<strong>$1</strong>").replace(/\*([^*\n]+)\*/g,"<em>$1</em>");
  return safe.replace(/\u0000(\d+)\u0000/g,(_,index)=>`<code>${code[+index]}</code>`);
}
function fmtTime(sec){if(sec==null)return "";sec=Math.floor(sec);return `${Math.floor(sec/60)}:${String(sec%60).padStart(2,"0")}`}
document.addEventListener("click", e => {
  const t = e.target.closest(".cite");
  if (!t) return;
  const sec = +t.dataset.sec;
  t.classList.add("visited");
  document.querySelectorAll(".cite.playing").forEach(c => c.classList.remove("playing"));
  t.classList.add("playing");
  const c = Object.values(citationMap).find(x => String(x.chunk_id) === t.dataset.id);
  if (!c) return;
  const src = sources.find(s => s.id === c.source_id);
  if (src?.youtube_id && sec && window.Player) { window.Player.seek(sec); window.Player.load(src.youtube_id, sec); }
  if (src?.type === "pdf" && window.PDFViewer) {
    if (window.__currentPDFSourceId__ !== src.id) {
      PDFViewer.loadStream(src.id, (c.page_num && c.y_top != null) ? { page: c.page_num, yTop: c.y_top } : null)
        .then(() => { window.__currentPDFSourceId__ = src.id; if (c.page_num) PDFViewer.jumpToPage(c.page_num); });
    } else {
      if (c.page_num) PDFViewer.jumpToPage(c.page_num);
      if (c.page_num && c.y_top != null) PDFViewer.highlightAt(src.id, c.page_num, c.y_top);
    }
    const loc = c.page_num ? `p.${c.page_num}` : fmtTime(c.start_sec);
    $("passage").innerHTML = `<div class="passage-card"><div class="passage-meta">${escapeHtml(c.source_title)} · ${loc}</div><div class="passage-text">${escapeHtml(c.text)}</div></div>`;
  }
});
function escapeHtml(s){return(s||"").replace(/[&<>\"]/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'\"':"&quot;"}[m]))}

// ---------- Study Guide (persisted, objective-driven) ----------
const studyModal = $("study-modal"), studyBody = $("study-body"), studyMeta = $("study-meta");

function openStudyGuide() {
  studyModal.classList.remove("hidden");
  loadStudyGuide();
}
function closeStudyGuide() { studyModal.classList.add("hidden"); }
$("study-close").onclick = closeStudyGuide;
studyModal.addEventListener("click", e => { if (e.target === studyModal) closeStudyGuide(); });
$("study-guide-btn").onclick = () => { if (!currentWS) return alert("Open a workspace first."); openStudyGuide(); };

function uncoveredBanner(uncovered, data) {
  const total = (data && data.guide && data.guide.objective_count) || 0;
  if (!uncovered || !total) return "";
  return `<div class="study-gap">📌 <strong>${uncovered}</strong> of <strong>${total}</strong> topics aren't in your uploaded sources yet — add material covering them and hit <strong>Generate / Refresh</strong>. (Gaps show "Not covered in the sources" below.)</div>`;
}

function renderStudySections(sections, guide, data) {
  if (!sections || !sections.length) {
    studyBody.innerHTML = `<p class="study-hint">No study guide yet. Click <strong>Generate</strong> to build it from this workspace's learning objectives.</p>`;
    return;
  }
  const uncovered = (data && data.uncovered) || 0;
  studyMeta.textContent = guide ? `Generated · ${guide.objective_count} objectives` : "";
  // Merge every section's citation map into the global map so each section's [n]
  // links resolve via the shared click handler (lookup is by chunk_id).
  citationMap = {};
  sections.forEach(s => Object.assign(citationMap, s.citations || {}));
  const items = sections.map((s, i) => {
    const html = formatStudyText(s.body || "", inlineFormat);
    const gapClass = (!s.covered) ? " study-gap-section" : "";
    return `
    <div class="study-section${gapClass}">
      <div class="study-section-head">
        <span class="study-num">${i + 1}</span>
        <span class="study-skill">${escapeHtml(s.skill_code || "obj")}</span>
        <span class="study-title">${escapeHtml(s.title)}</span>
        ${s.covered ? "" : '<span class="study-tag study-tag-gap">not in sources</span>'}
      </div>
      <div class="study-body-text msg-content">${html}</div>
    </div>`;
  }).join("");
  studyBody.innerHTML = uncoveredBanner(uncovered, data) + items;
}

async function loadStudyGuide() {
  if (!currentWS) { studyBody.innerHTML = `<p class="study-hint">Open a workspace first.</p>`; studyMeta.textContent = ""; return; }
  studyBody.innerHTML = `<div class="thinking"><span class="spinner"></span><span class="thinking-txt">Loading…</span></div>`;
  try {
    const data = await api(`/api/workspaces/${currentWS}/study-guide`);
    renderStudySections(data.sections, data.guide, data);
    if (!data.guide && !studyMeta.textContent) studyMeta.textContent = "not generated yet";
  } catch (e) {
    studyBody.innerHTML = `<p class="study-hint">Failed to load study guide.</p>`;
  }
}

$("study-generate").onclick = async () => {
  if (!currentWS) return alert("Open a workspace first.");
  const btn = $("study-generate");
  btn.disabled = true; btn.textContent = "Generating…";
  studyBody.innerHTML = `<div class="thinking"><span class="spinner"></span><span class="thinking-txt">Writing your study guide…</span></div>`;
  studyMeta.textContent = "";
  let total = 0, done = 0, stream = null;

  const headerEl = () => {
    const h = document.createElement("div");
    h.className = "study-progress";
    h.innerHTML = `Generating <strong>${done}</strong> of <strong>${total}</strong> topics…`;
    return h;
  };

  const ensureStream = () => {
    if (stream) return;
    stream = document.createElement("div");
    stream.className = "study-stream";
    stream.appendChild(headerEl());
    studyBody.innerHTML = "";
    studyBody.appendChild(stream);
  };

  const renderSection = (idx, totalN, sec) => {
    total = totalN; done = idx;
    ensureStream();
    const gap = (!sec.covered) ? " study-gap-section" : "";
    const tag = sec.covered ? "" : '<span class="study-tag study-tag-gap">not in sources</span>';
    const el = document.createElement("div");
    el.className = "study-section" + gap;
    el.innerHTML = `
      <div class="study-section-head">
        <span class="study-num">${idx}</span>
        <span class="study-skill">${escapeHtml(sec.skill_code || "obj")}</span>
        <span class="study-title">${escapeHtml(sec.title)}</span>
        ${tag}
      </div>
      <div class="study-body-text msg-content">${formatStudyText(sec.body || "", inlineFormat)}</div>`;
    // update the header counter in place
    const ph = stream.querySelector(".study-progress");
    stream.insertBefore(el, ph ? ph.nextSibling : stream.firstChild);
    if (ph) ph.innerHTML = headerEl().innerHTML;
    studyBody.scrollTop = studyBody.scrollHeight;
  };

  let es;
  try {
    es = new EventSource(`/api/workspaces/${currentWS}/study-guide/stream`);
  } catch (e) {
    btn.disabled = false; btn.textContent = "Generate / Refresh";
    studyBody.innerHTML = `<p class="study-hint">Failed to start generation.</p>`;
    return;
  }
  es.addEventListener("meta", e => {
    total = JSON.parse(e.data).objectives;
    ensureStream();
  });
  es.addEventListener("section", e => {
    const { index, total: t, section } = JSON.parse(e.data);
    renderSection(index, t, section);
  });
  es.addEventListener("done", e => {
    const data = JSON.parse(e.data);
    es.close();
    btn.disabled = false; btn.textContent = "Generate / Refresh";
    if (data.error) {
      studyMeta.textContent = "";
      studyBody.innerHTML = `<p class="study-hint">${escapeHtml(data.error)}</p>`;
      return;
    }
    studyMeta.textContent = data.guide ? `Generated · ${data.guide.objective_count} objectives` : "";
    citationMap = {};
    (data.sections || []).forEach(s => Object.assign(citationMap, s.citations || {}));
    const banner = uncoveredBanner(data.uncovered || 0, data);
    if (banner && stream) stream.prepend(document.createRange().createContextualFragment(banner));
    const ph = stream && stream.querySelector(".study-progress");
    if (ph) ph.remove();
  });
  es.addEventListener("error", e => {
    es.close();
    btn.disabled = false; btn.textContent = "Generate / Refresh";
    if (done) return;
    // A server-sent `event: error` carries the real reason in e.data; a genuine
    // connection drop has no data. Prefer the server's message so a known state
    // (e.g. missing workspace/unit) never reads as a connectivity failure.
    let msg = "Failed to generate. Is the server's LLM reachable?";
    try {
      const d = e.data ? JSON.parse(e.data) : null;
      if (d && d.error) msg = d.error;
    } catch (err) {}
    studyBody.innerHTML = `<p class="study-hint">${escapeHtml(msg)}</p>`;
  });
};

const practiceModal = $("practice-modal"), practiceBody = $("practice-body"), practiceScore = $("practice-score");
let practiceQuizId = null;
let practiceQuestions = [];
let practiceAttempts = {};   // qid -> {attempted, correct}

function openPractice() {
  practiceModal.classList.remove("hidden");
  loadPractice();
}
function closePractice() { practiceModal.classList.add("hidden"); }
$("practice-close").onclick = closePractice;
practiceModal.addEventListener("click", e => { if (e.target === practiceModal) closePractice(); });
$("practice-btn").onclick = () => { if (!currentWS) return alert("Open a workspace first."); openPractice(); };

function updatePracticeScore() {
  const qs = practiceQuestions.filter(q => practiceAttempts[String(q.id)] && practiceAttempts[String(q.id)].attempted);
  const right = qs.filter(q => practiceAttempts[String(q.id)].correct).length;
  practiceScore.textContent = qs.length ? `${right} / ${qs.length} correct` : "";
}

async function choosePracticeOption(qid, optKey, el) {
  const optEls = el.parentElement.querySelectorAll(".pq-opt");
  optEls.forEach(o => o.disabled = true);
  try {
    const res = await api(`/api/workspaces/${currentWS}/practice-quiz/${practiceQuizId}/questions/${qid}/attempt`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ selected_option: optKey }) });
    practiceAttempts[String(qid)] = { attempted: true, correct: res.correct, selected: optKey };
    const correctEl = optEls[["a","b","c","d"].indexOf(res.correct_option)];
    correctEl.classList.add("revealed-correct");
    const chosenEl = optEls[["a","b","c","d"].indexOf(optKey)];
    if (res.correct) chosenEl.classList.add("correct");
    else chosenEl.classList.add("wrong");
    const exp = el.closest(".pq").querySelector(".pq-explain");
    exp.innerHTML = `${res.correct ? "✅ Correct." : "❌ Incorrect."} <span class="pq-correct-anno">Correct answer: ${escapeHtml(res.options[res.correct_option])}.</span> ${escapeHtml(res.explanation || "")}`;
    exp.classList.add("show");
    const meta = el.closest(".pq").querySelector(".pq-meta");
    meta.textContent = res.correct ? "Answered correctly" : "Answered — review the explanation";
    updatePracticeScore();
  } catch (err) {
    optEls.forEach(o => o.disabled = false);
  }
}

function renderPractice() {
  if (!practiceQuestions.length) {
    practiceBody.innerHTML = `<p class="practice-hint">No practice quiz yet. Click <strong>Generate quiz</strong> to build questions from this workspace's learning objectives.</p>`;
    practiceScore.textContent = "";
    return;
  }
  practiceBody.innerHTML = practiceQuestions.map((q, i) => {
    const att = practiceAttempts[String(q.id)];
    return `
    <div class="pq" data-qid="${q.id}">
      <div class="pq-head"><span class="pq-num">Q${i + 1}</span><span class="pq-skill">${escapeHtml(q.skill_code || "obj")}</span></div>
      <p class="pq-prompt">${escapeHtml(q.prompt)}</p>
      <div class="pq-opts">
        ${["a","b","c","d"].map(k => {
          let cls = "pq-opt";
          if (att && att.selected === k) cls += att.correct ? " correct" : " wrong";
          return `<button class="${cls}" data-opt="${k}" ${att ? "disabled" : ""}>${escapeHtml(k.toUpperCase())}. ${escapeHtml(q.options[k])}</button>`;
        }).join("")}
      </div>
      <div class="pq-explain${att ? " show" : ""}">${att ? (att.correct ? "✅ Correct." : "❌ Incorrect.") : ""}</div>
      <div class="pq-meta">${att ? "Answered" : "Select an answer"}</div>
    </div>`;
  }).join("");
  practiceBody.querySelectorAll(".pq").forEach(pq => {
    const qid = parseInt(pq.dataset.qid, 10);
    pq.querySelectorAll(".pq-opt").forEach(btn => {
      btn.onclick = () => choosePracticeOption(qid, btn.dataset.opt, btn);
    });
  });
  updatePracticeScore();
}

async function loadPractice() {
  if (!currentWS) { practiceBody.innerHTML = `<p class="practice-hint">Open a workspace first.</p>`; return; }
  practiceBody.innerHTML = `<div class="thinking"><span class="spinner"></span><span class="thinking-txt">Loading…</span></div>`;
  try {
    const data = await api(`/api/workspaces/${currentWS}/practice-quiz`);
    if (!data.quiz) { practiceQuestions = []; practiceAttempts = {}; practiceQuizId = null; renderPractice(); return; }
    practiceQuizId = data.quiz.id;
    practiceQuestions = data.questions || [];
    practiceAttempts = data.attempts || {};
    renderPractice();
  } catch (e) {
    practiceBody.innerHTML = `<p class="practice-hint">Failed to load the practice quiz.</p>`;
  }
}

$("practice-generate").onclick = async () => {
  if (!currentWS) return alert("Open a workspace first.");
  const btn = $("practice-generate");
  btn.disabled = true; btn.textContent = "Generating…";
  practiceBody.innerHTML = `<div class="thinking"><span class="spinner"></span><span class="thinking-txt">Writing practice questions…</span></div>`;
  practiceScore.textContent = "";
  try {
    const data = await api(`/api/workspaces/${currentWS}/practice-quiz`, { method: "POST" });
    if (!data.quiz) {
      practiceBody.innerHTML = `<p class="practice-hint">${escapeHtml(data.error || "No learning objectives for this workspace yet.")}</p>`;
    } else {
      practiceQuizId = data.quiz.id;
      practiceQuestions = data.questions || [];
      practiceAttempts = {};
      renderPractice();
      if (data.skipped) practiceScore.textContent = `${data.skipped} topic(s) skipped (not in sources)`;
    }
  } catch (e) {
    practiceBody.innerHTML = `<p class="practice-hint">Failed to generate. Is the server's LLM reachable?</p>`;
  } finally {
    btn.disabled = false; btn.textContent = "Generate quiz";
  }
};

function renderStored(m){if(m.role==="assistant"&&m.content.includes("|||CITATIONS|||")){const [text,json]=m.content.split("|||CITATIONS|||");try{citationMap=JSON.parse(json)}catch{}return `<div class="msg assistant"><div class="msg-content">${formatStudyText(text, inlineFormat)}</div></div>`}return `<div class="msg ${m.role}"><div class="msg-content"><p>${escapeHtml(m.content)}</p></div></div>`}
loadSidebar();
