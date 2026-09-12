/* sidebar.js — mostly EASY, one MEDIUM part. The tree-building logic
 * (grouping workspaces by unit_id, an "Unsorted" bucket for orphaned
 * workspaces) and the new-workspace modal's new-course/new-unit branching are
 * the parts worth reading carefully before changing. Adding a new tree action
 * (e.g. rename) is a good task to hand off — follow the pattern of
 * add-unit/del-course/del-unit below: render a button with a data-id, wire it
 * in wire(), call the matching Api.* function, then refresh().
 */
(function () {
  const { $, escapeHtml } = Dom;
  let courses = [];
  let workspaces = [];

  async function refresh() {
    workspaces = await Api.listWorkspaces();
    const summaries = await Api.listCourses();
    courses = await Promise.all(summaries.map(async (c) => {
      try { return await Api.getCourse(c.id); } catch { return { ...c, units: [] }; }
    }));
    render();
  }

  function wsItemHtml(ws) {
    const active = AppState.workspaceId === ws.id ? " ws-item--active" : "";
    return `<li class="ws-item${active}"><a href="#" data-id="${ws.id}">${escapeHtml(ws.title)}</a></li>`;
  }

  function render() {
    const byUnit = {};
    workspaces.forEach((w) => { (byUnit[w.unit_id] = byUnit[w.unit_id] || []).push(w); });
    const orphaned = (byUnit[null] || byUnit[undefined] || []).slice();

    const unitsHtml = (course) => (course.units || []).map((unit) => {
      const kids = byUnit[unit.id] || [];
      return `<div class="unit">
        <div class="unit-head">
          <span class="unit-title">${escapeHtml(unit.title)}</span>
          <span class="count">${kids.length}</span>
          <button class="icon-btn add-ws" data-unit="${unit.id}" data-course="${course.id}" title="New workspace in this unit">+</button>
          <button class="icon-btn del-unit" data-id="${unit.id}" title="Delete unit">×</button>
        </div>
        <ul class="ws-list">${kids.map(wsItemHtml).join("") || '<li class="empty-hint">No workspaces yet</li>'}</ul>
      </div>`;
    }).join("");

    const courseHtml = courses.map((course) => `<div class="course">
      <div class="course-head">
        <span class="course-title">${escapeHtml(course.title)}</span>
        <span class="count">${(course.units || []).length}</span>
        <button class="icon-btn add-unit" data-id="${course.id}" title="New unit">+</button>
        <button class="icon-btn del-course" data-id="${course.id}" title="Delete course">×</button>
      </div>
      <div class="units">${unitsHtml(course)}</div>
    </div>`).join("");

    const orphanHtml = orphaned.length ? `<div class="course course--muted">
      <div class="course-head"><span class="course-title">Unsorted</span><span class="count">${orphaned.length}</span></div>
      <ul class="ws-list">${orphaned.map(wsItemHtml).join("")}</ul>
    </div>` : "";

    const list = $("workspace-list");
    list.innerHTML = courseHtml + orphanHtml;
    wire(list);
  }

  function wire(list) {
    list.querySelectorAll(".ws-item a").forEach((a) => {
      a.onclick = (e) => { e.preventDefault(); window.App.openWorkspace(Number(a.dataset.id)); };
    });
    list.querySelectorAll(".add-ws").forEach((b) => {
      b.onclick = (e) => { e.stopPropagation(); openModal(Number(b.dataset.course), Number(b.dataset.unit)); };
    });
    list.querySelectorAll(".add-unit").forEach((b) => {
      b.onclick = async (e) => {
        e.stopPropagation();
        const title = prompt("Unit name?", "New unit");
        if (!title) return;
        await Api.createUnit(Number(b.dataset.id), title);
        refresh();
      };
    });
    list.querySelectorAll(".del-course").forEach((b) => {
      b.onclick = async (e) => {
        e.stopPropagation();
        if (!confirm("Delete this course and its units? Workspaces move to Unsorted.")) return;
        await Api.deleteCourse(Number(b.dataset.id)).catch(() => {});
        refresh();
      };
    });
    list.querySelectorAll(".del-unit").forEach((b) => {
      b.onclick = async (e) => {
        e.stopPropagation();
        if (!confirm("Delete this unit? Its sources and workspaces lose the unit link.")) return;
        await Api.deleteUnit(Number(b.dataset.id)).catch(() => {});
        refresh();
      };
    });
  }

  // --- new-workspace modal ---
  function courseIsNew() { return $("ws-course").value === "__new__"; }
  function unitIsNew() { return $("ws-unit").value === "__new__"; }

  function onCourseChange() {
    const isNew = courseIsNew();
    $("ws-course-name-field").classList.toggle("hidden", !isNew);
    const unitSel = $("ws-unit");
    if (isNew) {
      unitSel.disabled = true;
      unitSel.innerHTML = '<option value="__new__">New unit…</option>';
      $("ws-unit-name-field").classList.remove("hidden");
      return;
    }
    unitSel.disabled = false;
    const course = courses.find((c) => c.id === Number($("ws-course").value));
    unitSel.innerHTML = '<option value="__new__">New unit…</option>'
      + (course?.units || []).map((u) => `<option value="${u.id}">${escapeHtml(u.title)}</option>`).join("");
    unitSel.onchange = () => $("ws-unit-name-field").classList.toggle("hidden", !unitIsNew());
    unitSel.onchange();
  }

  function openModal(presetCourseId = null, presetUnitId = null) {
    const courseSel = $("ws-course");
    courseSel.innerHTML = '<option value="__new__">New course…</option>'
      + courses.map((c) => `<option value="${c.id}">${escapeHtml(c.title)}</option>`).join("");
    courseSel.onchange = onCourseChange;
    $("ws-name").value = "";
    $("ws-course-name").value = "";
    $("ws-unit-name").value = "";
    courseSel.value = presetCourseId != null && courses.some((c) => c.id === presetCourseId) ? String(presetCourseId) : "__new__";
    onCourseChange();
    if (presetUnitId != null && !courseIsNew()) {
      $("ws-unit").value = String(presetUnitId);
      $("ws-unit-name-field").classList.add("hidden");
    }
    $("ws-modal").classList.remove("hidden");
    setTimeout(() => $("ws-name").focus(), 30);
  }
  function closeModal() { $("ws-modal").classList.add("hidden"); }

  async function createWorkspace() {
    const name = $("ws-name").value.trim();
    if (!name) return alert("Give the workspace a name.");
    let courseId;
    if (courseIsNew()) {
      const cname = $("ws-course-name").value.trim();
      if (!cname) return alert("Name the new course.");
      courseId = (await Api.createCourse(cname)).id;
    } else {
      courseId = Number($("ws-course").value);
    }
    let unitId;
    if (unitIsNew()) {
      const uname = $("ws-unit-name").value.trim() || "Unit 1";
      unitId = (await Api.createUnit(courseId, uname)).id;
    } else {
      unitId = Number($("ws-unit").value);
    }
    const ws = await Api.createWorkspace(unitId, name);
    if (!ws.id) return alert("Couldn't create the workspace.");
    closeModal();
    await refresh();
    window.App.openWorkspace(ws.id);
  }

  function attach() {
    $("new-workspace").onclick = () => openModal();
    $("ws-cancel").onclick = closeModal;
    $("ws-modal").addEventListener("click", (e) => { if (e.target.id === "ws-modal") closeModal(); });
    $("ws-create").onclick = createWorkspace;
  }

  window.Sidebar = { refresh, attach };
})();
