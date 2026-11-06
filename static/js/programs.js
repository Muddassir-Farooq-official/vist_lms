(function () {
  "use strict";

  const VA = window.VistAdmin;

  const tbody = document.getElementById("programs-list-tbody");
  const emptyState = document.getElementById("programs-list-empty");
  const form = document.getElementById("create-program-form");
  const createBtn = document.getElementById("create-program-btn");
  const createError = document.getElementById("create-error");
  const createSuccess = document.getElementById("create-success");

  const editModal = document.getElementById("edit-program-modal");
  const editCodeHint = document.getElementById("edit-program-code-hint");
  const editForm = document.getElementById("edit-program-form");
  const editSubmit = document.getElementById("edit-program-submit");
  const editError = document.getElementById("edit-program-error");
  const editSuccess = document.getElementById("edit-program-success");
  const closeEditModalBtn = document.getElementById("close-edit-program-modal");

  let activeProgramId = null;

  function statusPillHtml(isActive) {
    const label = isActive ? "Active" : "Inactive";
    const cls = isActive ? "active" : "dropped";
    return '<span class="status-pill ' + cls + '">' + label + "</span>";
  }

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    if (body && Array.isArray(body.detail)) {
      return body.detail.map((e) => e.loc[e.loc.length - 1] + ": " + e.msg).join("; ");
    }
    return fallback;
  }

  const esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  let programs = [];

  function n(v) { return Number(v || 0).toLocaleString("en-PK"); }
  function card(p) {
    const st = p.students || {}, c = p.curriculum || {};
    const active = st.ACTIVE || 0, others = (st.GRADUATED || 0) + (st.SUSPENDED || 0) + (st.DROPPED || 0);
    const planned = c.semesters_planned || 0, pct = p.total_semesters ? Math.min(100, Math.round(100 * planned / p.total_semesters)) : 0;
    return '<article class="pg-card' + (p.is_active ? "" : " is-off") + '">' +
      '<header><span class="crs-code">' + esc(p.code) + "</span>" + statusPillHtml(p.is_active) + "</header>" +
      "<h3>" + esc(p.name) + "</h3>" +
      '<p class="pg-meta">' + esc(p.duration_years) + " year" + (p.duration_years === 1 ? "" : "s") + " · " + esc(p.total_semesters) + " semesters · up to " + esc(p.max_credit_hours_per_semester) + " credit hours a semester</p>" +
      '<div class="pg-stats"><div><b>' + n(active) + "</b><span>active students</span></div><div><b>" + n(c.courses) + "</b><span>courses</span></div><div><b>" + n(c.credit_hours) +
      "</b><span>credit hours</span></div></div>" +
      ((p.active_by_semester || []).length ? '<div class="pg-sems" aria-label="Active students by semester">' + p.active_by_semester.map(function (x) {
        return '<a class="pg-sem" href="/dashboard/admin/students?program=' + esc(p.id) + (x.semester ? "&semester=" + x.semester : "") + '&status=ACTIVE" title="Open this list">' +
          (x.semester ? "Sem " + x.semester : "No semester") + " <b>" + n(x.students) + "</b></a>";
      }).join("") + "</div>" : "") +
      '<div class="pg-plan"><span>Curriculum planned for ' + planned + " of " + esc(p.total_semesters) + ' semesters</span><span class="crs-meter"><i style="width:' + pct + '%"></i></span></div>' +
      (others ? '<p class="pg-meta">' + [st.GRADUATED ? n(st.GRADUATED) + " graduated" : "", st.SUSPENDED ? n(st.SUSPENDED) + " suspended" : "", st.DROPPED ? n(st.DROPPED) + " left" : ""].filter(Boolean).join(" · ") + "</p>" : "") +
      '<footer><a class="sd-view" href="/dashboard/admin/courses?program=' + esc(p.id) + '#curriculum">Curriculum</a><a class="sd-view" href="/dashboard/admin/students?program=' + esc(p.id) + '">Students</a>' +
      '<button type="button" class="role-btn small-btn secondary-btn" data-edit="' + esc(p.id) + '">Edit</button></footer></article>';
  }
  function render() {
    const q = ($("prog-q").value || "").trim().toLowerCase(), st = $("prog-status").value;
    const rows = programs.filter(function (p) { return (!q || (p.code + " " + p.name).toLowerCase().indexOf(q) !== -1) && (!st || (st === "active") === !!p.is_active); });
    $("programs-grid").innerHTML = rows.map(card).join("");
    emptyState.hidden = rows.length > 0;
    if (programs.length && !rows.length) emptyState.textContent = "No program matches this search.";
    const students = programs.reduce(function (t, p) { return t + ((p.students || {}).ACTIVE || 0); }, 0);
    $("prog-summary").innerHTML = programs.length ? ['<span class="crs-chip"><b>' + programs.length + "</b> programs</span>",
      '<span class="crs-chip"><b>' + programs.filter(function (p) { return p.is_active; }).length + "</b> active</span>", '<span class="crs-chip"><b>' + n(students) + "</b> active students</span>"].join("") : "";
  }
  $("prog-q").addEventListener("input", render);
  $("prog-status").addEventListener("change", render);
  $("programs-grid").addEventListener("click", function (e) {
    const b = e.target.closest("[data-edit]"); if (!b) return;
    const p = programs.find(function (x) { return x.id === b.getAttribute("data-edit"); });
    if (p) openEditModal(p);
  });

  async function loadPrograms() {
    const result = await VA.fetchJson("/api/v1/programs/overview");
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load programs."));
      return;
    }
    VA.clearError();
    programs = result.body || [];
    tbody.innerHTML = "";
    render();
  }

  function openEditModal(program) {
    activeProgramId = program.id;
    editError.hidden = true;
    editSuccess.hidden = true;
    editCodeHint.textContent = "Code: " + program.code + " (not editable)";
    document.getElementById("ep-name").value = program.name;
    document.getElementById("ep-years").value = program.duration_years;
    document.getElementById("ep-semesters").value = program.total_semesters;
    document.getElementById("ep-active").value = String(program.is_active);
    document.getElementById("ep-maxch").value = program.max_credit_hours_per_semester || 18;
    editModal.showModal();
  }

  closeEditModalBtn.addEventListener("click", function () {
    editModal.close();
  });

  editForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!activeProgramId) return;
    editSubmit.disabled = true;
    editError.hidden = true;
    editSuccess.hidden = true;

    const payload = {
      name: document.getElementById("ep-name").value.trim(),
      duration_years: parseInt(document.getElementById("ep-years").value, 10),
      total_semesters: parseInt(document.getElementById("ep-semesters").value, 10),
      is_active: document.getElementById("ep-active").value === "true",
      max_credit_hours_per_semester: parseInt(document.getElementById("ep-maxch").value, 10) || undefined,
    };

    const result = await VA.fetchJson("/api/v1/programs/" + activeProgramId, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    editSubmit.disabled = false;

    if (!result.ok) {
      editError.textContent = extractErrorMessage(result.body, "Could not update program.");
      editError.hidden = false;
      return;
    }

    editSuccess.textContent = "Saved.";
    editSuccess.hidden = false;
    loadPrograms();
  });

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    createBtn.disabled = true;
    createError.hidden = true;
    createSuccess.hidden = true;

    const payload = {
      code: document.getElementById("p-code").value.trim(),
      name: document.getElementById("p-name").value.trim(),
      duration_years: parseInt(document.getElementById("p-years").value, 10),
      total_semesters: parseInt(document.getElementById("p-semesters").value, 10),
      max_credit_hours_per_semester: parseInt(document.getElementById("p-maxch").value, 10) || 18,
    };

    const result = await VA.fetchJson("/api/v1/programs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    createBtn.disabled = false;

    if (!result.ok) {
      createError.textContent = extractErrorMessage(result.body, "Could not create program.");
      createError.hidden = false;
      return;
    }

    form.reset();
    createSuccess.textContent = 'Program "' + result.body.name + '" created.';
    createSuccess.hidden = false;
    loadPrograms();
  });

  document.addEventListener("vistadmin:ready", function () {
    loadPrograms();
  });
})();