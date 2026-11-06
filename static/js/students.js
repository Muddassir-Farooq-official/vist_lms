(function () {
  "use strict";
  window.PROFILE_COMPLETION_COLUMN = true;      // the admin list shows the Profile column (student-sheet.js rowHtml)

  const VA = window.VistAdmin;
  const PAGE_SIZE = 15;
  const Sheet = window.StudentSheet;

  let currentPage = 1;
  let currentTotalPages = 1;
  let searchDebounceTimer = null;

  const searchInput = document.getElementById("search-input");
  const statusFilter = document.getElementById("status-filter");
  const programFilter = document.getElementById("program-filter");
  const tbody = document.getElementById("students-tbody");
  const emptyState = document.getElementById("students-empty");
  const prevBtn = document.getElementById("prev-page");
  const nextBtn = document.getElementById("next-page");
  const pageInfo = document.getElementById("page-info");

  const modalError = document.getElementById("modal-error");
  const newStatusSelect = document.getElementById("new-status");
  const saveStatusBtn = document.getElementById("save-status");
  const resetPasswordBtn = document.getElementById("reset-password-btn");
  const resetPasswordConfirm = document.getElementById("reset-password-confirm");

  const openAddStudentBtn = document.getElementById("open-add-student");
  const addStudentModal = document.getElementById("add-student-modal");
  const addStudentForm = document.getElementById("add-student-form");
  const addStudentSubmit = document.getElementById("add-student-submit");
  const addStudentError = document.getElementById("add-student-error");
  const addStudentSuccess = document.getElementById("add-student-success");
  const closeAddStudentModalBtn = document.getElementById("close-add-student-modal");
  const programSelect = document.getElementById("as-program");

  function syncExportLink() {
    const link = document.getElementById("export-students-link");
    if (!link) return;
    const params = new URLSearchParams();
    const q = document.getElementById("search-input").value.trim();
    const st = document.getElementById("status-filter").value;
    const pr = document.getElementById("program-filter").value;
    const sm = document.getElementById("semester-filter").value;
    const sc = document.getElementById("section-filter").value.trim();
    if (q) params.set("q", q);
    if (st) params.set("status", st);
    if (pr) params.set("program_id", pr);
    if (sm) params.set("semester", sm);
    if (sc) params.set("section", sc);
    link.href = "/api/v1/students/export" + (params.toString() ? "?" + params.toString() : "");
  }
  document.getElementById("search-input").addEventListener("input", syncExportLink);
  document.getElementById("status-filter").addEventListener("change", syncExportLink);
  document.getElementById("program-filter").addEventListener("change", syncExportLink);
  document.getElementById("semester-filter").addEventListener("change", syncExportLink);
  document.getElementById("section-filter").addEventListener("input", syncExportLink);

  const openAdvanceSemesterBtn = document.getElementById("open-advance-semester");
  const advanceSemesterModal = document.getElementById("advance-semester-modal");
  const advanceProgramSelect = document.getElementById("advance-program-select");
  const confirmAdvanceSemesterBtn = document.getElementById("confirm-advance-semester-btn");
  const advanceSemesterError = document.getElementById("advance-semester-error");
  const advanceSemesterSuccess = document.getElementById("advance-semester-success");
  const closeAdvanceSemesterModalBtn = document.getElementById("close-advance-semester-modal");

  const editStudentForm = document.getElementById("edit-student-form");
  const editStudentSubmit = document.getElementById("edit-student-submit");
  const editStudentSuccess = document.getElementById("edit-student-success");
  const editProgramSelect = document.getElementById("es-program");

  let activeStudentId = null;

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  let activeStudentName = "";
  let pageStudentIds = [];
  document.getElementById("print-cards").addEventListener("click", async function () {
    if (!pageStudentIds.length) return VA.toast("No active students on this page.", "error");
    if (!(await VA.confirm("Print ID cards for the " + pageStudentIds.length + " active student(s) on this page? Each card is registered with a check code."))) return;
    VA.downloadFile("/api/v1/records/id-cards", "id-cards.pdf", "ID cards",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ student_ids: pageStudentIds }) });
  });

  async function loadStudents() {
    const params = new URLSearchParams();
    params.set("page", String(currentPage));
    params.set("page_size", String(PAGE_SIZE));
    if (searchInput.value.trim()) params.set("q", searchInput.value.trim());
    if (statusFilter.value) params.set("status", statusFilter.value);
    if (programFilter.value) params.set("program_id", programFilter.value);
    const semFilter = document.getElementById("semester-filter"), secFilter = document.getElementById("section-filter");
    if (semFilter.value) params.set("semester", semFilter.value);
    if (secFilter.value.trim()) params.set("section", secFilter.value.trim());

    const result = await VA.fetchJson("/api/v1/students?" + params.toString());

    if (!result.ok) {
      const message =
        (result.body && result.body.error && result.body.error.message) || "Could not load students.";
      VA.showError(message);
      return;
    }
    VA.clearError();

    const data = result.body;
    currentTotalPages = Math.max(1, Math.ceil(data.total / data.page_size));
    tbody.innerHTML = "";

    if (data.items.length === 0) {
      emptyState.hidden = false;
    } else {
      emptyState.hidden = true;
      pageStudentIds = data.items.filter(function (s) { return s.status === "ACTIVE"; }).map(function (s) { return s.id; });
      tbody.innerHTML = data.items.map(Sheet.rowHtml).join("");
      if (window.ProfileCompleteness) window.ProfileCompleteness.fill(tbody, "students");
    }

    const what = [programFilter.value ? programFilter.options[programFilter.selectedIndex].text : "",
      document.getElementById("semester-filter").value ? "semester " + document.getElementById("semester-filter").value : "",
      document.getElementById("section-filter").value.trim() ? "section " + document.getElementById("section-filter").value.trim().toUpperCase() : "",
      statusFilter.value ? statusFilter.options[statusFilter.selectedIndex].text.toLowerCase() : ""].filter(Boolean).join(", ");
    document.getElementById("students-count").innerHTML = "<b>" + Number(data.total).toLocaleString() + "</b> student" + (data.total === 1 ? "" : "s") +
      (what ? " \u2014 " + VA.escapeHtml(what) : " in all") + (searchInput.value.trim() ? " matching \u201c" + VA.escapeHtml(searchInput.value.trim()) + "\u201d" : "") + ".";
    const from = data.total ? (currentPage - 1) * data.page_size + 1 : 0;
    pageInfo.textContent = from + "–" + Math.min(data.total, currentPage * data.page_size) + " of " + data.total + " students";
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= currentTotalPages;
  }

  // ---- correct a mistyped CNIC or the guardian's details (reason required, audited)
  let lastGuardian = null;
  let lastStudent = null;
  const idModal = document.getElementById("identity-modal");
  document.getElementById("identity-btn").addEventListener("click", function () {
    document.getElementById("id-cnic").value = (lastStudent && lastStudent.cnic) || "";
    document.getElementById("id-gname").value = (lastGuardian && lastGuardian.guardian_name) || "";
    document.getElementById("id-gcnic").value = (lastGuardian && lastGuardian.guardian_cnic) || "";
    document.getElementById("id-grel").value = (lastGuardian && lastGuardian.relation) || "";
    document.getElementById("id-gphone").value = "";
    document.getElementById("id-reason").value = "";
    document.getElementById("identity-error").hidden = true;
    idModal.showModal();
  });
  document.getElementById("identity-close").addEventListener("click", function () { idModal.close(); });
  ["records-btn", "records-btn-2"].forEach(function (id) {
    document.getElementById(id).addEventListener("click", function () {
      if (activeStudentId) window.RecordsPanel.open({ type: "students", id: activeStudentId, name: activeStudentName });
    });
  });
  document.getElementById("identity-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const err = document.getElementById("identity-error");
    const body = { reason: document.getElementById("id-reason").value.trim() };
    function put(key, id, old) { const v = document.getElementById(id).value.trim(); if (v && v !== (old || "")) body[key] = v; }
    put("student_cnic", "id-cnic", lastStudent && lastStudent.cnic);
    put("guardian_name", "id-gname", lastGuardian && lastGuardian.guardian_name);
    put("guardian_cnic", "id-gcnic", lastGuardian && lastGuardian.guardian_cnic);
    put("guardian_relation", "id-grel", lastGuardian && lastGuardian.relation);
    put("guardian_contact_no", "id-gphone", "");
    if (body.reason.length < 10) { err.textContent = "Write the reason (at least 10 letters)."; err.hidden = false; return; }
    const r = await VA.fetchJson("/api/v1/students/" + activeStudentId + "/identity", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!r.ok) { err.textContent = (r.body && r.body.error && r.body.error.message) || "Could not save the correction."; err.hidden = false; return; }
    idModal.close();
    VA.toast("Correction saved and recorded.");
    Sheet.open(activeStudentId, "account");
  });

  // ---- parent portal access (guardian login), from the student's record
  async function loadParentAccess(studentId) {
    const text = document.getElementById("parent-access-text");
    const give = document.getElementById("parent-access-btn");
    const reset = document.getElementById("parent-reset-btn");
    const revoke = document.getElementById("parent-revoke-btn");
    document.getElementById("parent-access-result").hidden = true;
    [give, reset, revoke].forEach(function (b) { b.hidden = true; });
    const r = await VA.fetchJson("/api/v1/parents/students/" + studentId);
    if (!r.ok) { text.textContent = "Parent portal: " + ((r.body && r.body.error && r.body.error.message) || "not available."); return; }
    const a = r.body;
    lastGuardian = a;
    text.textContent = a.guardian_name + " (" + a.relation + ", CNIC " + a.guardian_cnic + "): " +
      (a.linked ? "can sign in as " + a.username + "." : a.has_login ? "has a login; this student is not linked yet." : "no login yet.");
    give.hidden = a.linked;
    reset.hidden = !a.has_login;
    revoke.hidden = !a.linked;
    const box = document.getElementById("parent-siblings");
    const open = (a.siblings || []).filter(function (s) { return !s.linked; });
    box.hidden = !open.length;
    box.innerHTML = open.length ? '<p class="panel-hint">Other students with the same guardian CNIC. Tick only real brothers and sisters, then press "Link ticked":</p>' +
      open.map(function (s) {
        return '<label class="check-row"><input type="checkbox" value="' + VA.escapeHtml(s.id) + '"> ' + VA.escapeHtml(s.name) + " (" + VA.escapeHtml(s.roll_number || "-") + ")</label>";
      }).join("") + '<button type="button" class="role-btn secondary-btn" id="parent-siblings-btn">Link ticked</button>' : "";
    if (open.length) {
      document.getElementById("parent-siblings-btn").addEventListener("click", function () {
        const ids = Array.from(box.querySelectorAll("input:checked")).map(function (i) { return i.value; });
        if (!ids.length) { modalError.textContent = "Tick at least one brother or sister first."; modalError.hidden = false; return; }
        parentAction("POST", "", null, ids);
      });
    }
  }
  async function parentAction(method, query, confirmText, siblingIds) {
    if (confirmText && !(await window.VistAdmin.confirm(confirmText))) return;
    const opts = { method: method };
    if (siblingIds) { opts.body = JSON.stringify({ sibling_ids: siblingIds }); opts.headers = { "Content-Type": "application/json" }; }
    const r = await VA.fetchJson("/api/v1/parents/students/" + activeStudentId + (query || ""), opts);
    const out = document.getElementById("parent-access-result");
    if (!r.ok) { modalError.textContent = (r.body && r.body.error && r.body.error.message) || "That did not work."; modalError.hidden = false; return; }
    if (r.body && r.body.temporary_password) {
      out.textContent = "Sign-in for the guardian — username: " + r.body.username + "  one-time password: " + r.body.temporary_password +
        "  (shown only now; they must change it at first sign-in). Children linked: " + r.body.children_linked + ".";
    } else {
      out.textContent = r.body && r.body.message ? r.body.message : "Done.";
    }
    out.hidden = false;
    const keep = out.textContent;
    await loadParentAccess(activeStudentId);
    out.textContent = keep;
    out.hidden = false;
  }
  document.getElementById("parent-access-btn").addEventListener("click", function () { parentAction("POST", "", null); });
  document.getElementById("parent-reset-btn").addEventListener("click", function () { parentAction("POST", "?reset_password=true", "Issue a new one-time password for the guardian? The old one stops working."); });
  document.getElementById("parent-revoke-btn").addEventListener("click", function () { parentAction("DELETE", "", "Stop the guardian from seeing this student?"); });

  // The record dialog (student-sheet.js) shows the record; this page fills its "Edit details" and "Status & access" tabs.
  tbody.addEventListener("click", function (e) {
    const row = e.target.closest("[data-open]");
    if (row) Sheet.open(row.getAttribute("data-open"));
  });
  tbody.addEventListener("keydown", function (e) {
    const row = e.target.closest("tr[data-open]");
    if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); Sheet.open(row.getAttribute("data-open")); }
  });
  Sheet.element.addEventListener("studentsheet:changed", function () { loadStudents(); });

  Sheet.element.addEventListener("studentsheet:loaded", async function (ev) {
    const s = ev.detail;
    activeStudentId = s.id;
    lastStudent = s;
    activeStudentName = s.first_name + " " + s.last_name;
    document.getElementById("print-slip-link").href = "/api/v1/students/" + s.id + "/admission-slip";
    resetPasswordConfirm.hidden = true;
    editStudentSuccess.hidden = true;
    document.getElementById("status-reason").value = "";
    document.getElementById("keep-fees-row").hidden = true;
    newStatusSelect.value = s.status;
    loadParentAccess(s.id);

    await ensureProgramsLoaded();
    await ensureReferenceDataLoaded();
    document.getElementById("es-contact").value = s.contact_no || "";
    document.getElementById("es-marital").value = s.marital_status || "Single";
    document.getElementById("es-religion").value = s.religion || "";
    document.getElementById("es-nationality").value = s.nationality || "";
    document.getElementById("es-roll").value = s.roll_number || "";
    document.getElementById("es-semester").value = s.current_semester || "";
    editProgramSelect.value = s.program_id || "";
    document.getElementById("es-address").value = s.present_address || "";
    esProvinceSelect.value = s.province || "";
    populateDistrictOptions(esDistrictSelect, s.province || "", s.district || null);
    document.getElementById("es-city").value = s.city || "";
    document.getElementById("es-section").value = s.section_label || "";
    document.getElementById("es-first-name").value = s.first_name || "";
    document.getElementById("es-last-name").value = s.last_name || "";
    document.getElementById("es-dob").value = s.date_of_birth || "";
    document.getElementById("es-gender").value = s.gender || "Male";
    document.getElementById("es-admission-session").value = s.admission_session || "";
    document.getElementById("es-admission-category").value = s.admission_category || "";
    document.getElementById("es-email").value = s.email || "";
    document.getElementById("es-blood-group").value = s.blood_group || "";
  });

  editStudentForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!activeStudentId) return;
    editStudentSubmit.disabled = true;
    modalError.hidden = true;
    editStudentSuccess.hidden = true;

    const semesterValue = document.getElementById("es-semester").value.trim();
    const payload = {
      contact_no: document.getElementById("es-contact").value.trim() || null,
      marital_status: document.getElementById("es-marital").value || null,
      religion: document.getElementById("es-religion").value.trim() || null,
      nationality: document.getElementById("es-nationality").value.trim() || null,
      program_id: editProgramSelect.value || null,
      current_semester: semesterValue ? parseInt(semesterValue, 10) : null,
      present_address: document.getElementById("es-address").value.trim() || null,
      province: esProvinceSelect.value || null,
      district: esDistrictSelect.value || null,
      city: document.getElementById("es-city").value || null,
      section_label: document.getElementById("es-section").value.trim() || null,
      first_name: document.getElementById("es-first-name").value.trim() || null,
      last_name: document.getElementById("es-last-name").value.trim() || null,
      date_of_birth: document.getElementById("es-dob").value || null,
      gender: document.getElementById("es-gender").value || null,
      admission_session: document.getElementById("es-admission-session").value.trim() || null,
      admission_category: document.getElementById("es-admission-category").value.trim() || null,
      email: document.getElementById("es-email").value.trim() || null,
      blood_group: document.getElementById("es-blood-group").value || null,
    };

    const result = await VA.fetchJson("/api/v1/students/" + activeStudentId, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    editStudentSubmit.disabled = false;

    if (!result.ok) {
      let message = "Could not update student.";
      if (result.body && result.body.error && result.body.error.message) {
        message = result.body.error.message;
      } else if (result.body && Array.isArray(result.body.detail)) {
        message = result.body.detail.map((e) => e.loc[e.loc.length - 1] + ": " + e.msg).join("; ");
      }
      modalError.textContent = message;
      modalError.hidden = false;
      return;
    }

    loadStudents();
    await Sheet.open(activeStudentId, "edit");
    editStudentSuccess.textContent = "Saved.";
    editStudentSuccess.hidden = false;
  });

  // Leaving the institute cancels the fee installments not yet due (owner decision); the office can keep them for one student.
  newStatusSelect.addEventListener("change", function () {
    document.getElementById("keep-fees-row").hidden = newStatusSelect.value !== "DROPPED";
  });

  saveStatusBtn.addEventListener("click", async function () {
    if (!activeStudentId) return;
    saveStatusBtn.disabled = true;
    modalError.hidden = true;

    const result = await VA.fetchJson("/api/v1/students/" + activeStudentId + "/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_status: newStatusSelect.value, reason: document.getElementById("status-reason").value.trim() || null,
        keep_future_fees: newStatusSelect.value === "DROPPED" && document.getElementById("keep-future-fees").checked }),
    });

    saveStatusBtn.disabled = false;

    if (!result.ok) {
      modalError.textContent =
        (result.body && result.body.error && result.body.error.message) || "Could not update status.";
      modalError.hidden = false;
      return;
    }

    VA.toast("Status updated.");
    loadStudents();
    Sheet.open(activeStudentId, "account");
  });

  resetPasswordBtn.addEventListener("click", async function () {
    if (!activeStudentId) return;
    if (!(await window.VistAdmin.confirm("Reset this student's password to the institution default? They will be required to change it on next login."))) {
      return;
    }
    resetPasswordBtn.disabled = true;
    modalError.hidden = true;
    resetPasswordConfirm.hidden = true;

    const result = await VA.fetchJson("/api/v1/students/" + activeStudentId + "/reset-password", {
      method: "POST",
    });

    resetPasswordBtn.disabled = false;

    if (!result.ok) {
      modalError.textContent =
        (result.body && result.body.error && result.body.error.message) || "Could not reset password.";
      modalError.hidden = false;
      return;
    }

    resetPasswordConfirm.textContent = "New one-time password: " + ((result.body && result.body.temporary_password) || "see the admission slip") +
      ". Give it to the student; it must be changed at first sign-in.";
    resetPasswordConfirm.hidden = false;
  });

  // ---------------------------------------------------------------------
  // Programs (shared cache — used by both the Add Student form and the
  // Edit Details form inside the detail modal)
  // ---------------------------------------------------------------------
  let programsCache = null;

  async function ensureProgramsLoaded() {
    if (programsCache) return programsCache;
    const result = await VA.fetchJson("/api/v1/programs?active_only=true");
    programsCache = result.ok && result.body ? result.body : [];

    [programSelect, editProgramSelect, advanceProgramSelect, programFilter].forEach(function (select) {
      if (!select) return;
      // Keep the first "None"/"All programs" option, replace the rest.
      while (select.options.length > 1) select.remove(1);
      programsCache.forEach(function (p) {
        const opt = document.createElement("option");
        opt.value = p.id;
        opt.textContent = p.code + " \u2014 " + p.name;
        select.appendChild(opt);
      });
    });

    return programsCache;
  }

  // ---------------------------------------------------------------------
  // Reference data (provinces/districts/cities/religions) — one fetch,
  // shared across the Add and Edit forms. District options are
  // re-populated whenever the matching Province select changes.
  // ---------------------------------------------------------------------
  let referenceData = null;

  async function ensureReferenceDataLoaded() {
    if (referenceData) return referenceData;
    const result = await VA.fetchJson("/api/v1/reference-data");
    referenceData = result.ok && result.body ? result.body : { provinces: [], districts_by_province: {}, cities: [], religions: [] };

    function fillStaticOptions(select, values, placeholder) {
      if (!select) return;
      select.innerHTML = "";
      if (placeholder) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = placeholder;
        select.appendChild(opt);
      }
      values.forEach(function (v) {
        const opt = document.createElement("option");
        opt.value = v;
        opt.textContent = v;
        select.appendChild(opt);
      });
    }

    fillStaticOptions(document.getElementById("as-province"), referenceData.provinces, "Select\u2026");
    fillStaticOptions(document.getElementById("es-province"), referenceData.provinces, "Select\u2026");
    fillStaticOptions(document.getElementById("as-city"), referenceData.cities, "Select\u2026");
    fillStaticOptions(document.getElementById("es-city"), referenceData.cities, "Select\u2026");
    fillStaticOptions(document.getElementById("as-religion"), referenceData.religions, "Select\u2026");
    fillStaticOptions(document.getElementById("es-religion"), referenceData.religions, "Select\u2026");

    return referenceData;
  }

  function populateDistrictOptions(districtSelect, province, keepValue) {
    if (!districtSelect) return;
    const districts = (referenceData && referenceData.districts_by_province[province]) || [];
    districtSelect.innerHTML = "";
    const placeholderOpt = document.createElement("option");
    placeholderOpt.value = "";
    placeholderOpt.textContent = province ? "Select\u2026" : "Choose a province first\u2026";
    districtSelect.appendChild(placeholderOpt);
    districts.forEach(function (d) {
      const opt = document.createElement("option");
      opt.value = d;
      opt.textContent = d;
      districtSelect.appendChild(opt);
    });
    if (keepValue && districts.indexOf(keepValue) !== -1) districtSelect.value = keepValue;
  }

  const asProvinceSelect = document.getElementById("as-province");
  const asDistrictSelect = document.getElementById("as-district");
  if (asProvinceSelect) {
    asProvinceSelect.addEventListener("change", function () {
      populateDistrictOptions(asDistrictSelect, asProvinceSelect.value, null);
    });
  }

  const esProvinceSelect = document.getElementById("es-province");
  const esDistrictSelect = document.getElementById("es-district");
  if (esProvinceSelect) {
    esProvinceSelect.addEventListener("change", function () {
      populateDistrictOptions(esDistrictSelect, esProvinceSelect.value, null);
    });
  }

  openAddStudentBtn.addEventListener("click", async function () {
    addStudentError.hidden = true;
    addStudentSuccess.hidden = true;
    await ensureProgramsLoaded();
    await ensureReferenceDataLoaded();
    populateDistrictOptions(asDistrictSelect, "", null);
    addStudentModal.showModal();
  });

  openAdvanceSemesterBtn.addEventListener("click", async function () {
    advanceSemesterError.hidden = true;
    advanceSemesterSuccess.hidden = true;
    await ensureProgramsLoaded();
    advanceSemesterModal.showModal();
  });

  closeAdvanceSemesterModalBtn.addEventListener("click", function () {
    advanceSemesterModal.close();
  });

  const previewAdvanceBtn = document.getElementById("preview-advance-semester-btn");
  const advancePreviewBox = document.getElementById("advance-preview");

  function advanceRequestBody(dryRun) {
    return JSON.stringify({
      program_id: advanceProgramSelect.value || null,
      dry_run: dryRun,
      skip_open_enrollments: document.getElementById("advance-skip-open").checked,
      skip_overdue_fees: document.getElementById("advance-skip-fees").checked,
      skip_failed_courses: document.getElementById("advance-skip-failed").checked,
      skip_low_cgpa: document.getElementById("advance-skip-cgpa").checked,
      skip_unpaid_fines: document.getElementById("advance-skip-fines").checked,
    });
  }

  function summarizeAdvance(r, verbPast) {
    let text =
      verbPast + " " + r.advanced_count + " student(s). Held back: " +
      r.skipped_open_enrollments + " with courses in progress, " + r.skipped_overdue_fees + " with overdue fees, " +
      (r.skipped_failed_courses || 0) + " with more than " + r.max_courses_to_clear + " failed courses to clear, " +
      (r.skipped_low_cgpa || 0) + " with CGPA below " + r.min_cgpa + ", " + (r.skipped_unpaid_fines || 0) + " with unpaid fines, " +
      r.skipped_already_at_max + " at final semester, " + r.skipped_no_semester_set + " with no semester, " +
      r.skipped_no_program + " with no program.";
    return text;
  }

  function renderSkipped(r) {
    if (!r.skipped_students || !r.skipped_students.length) return "";
    return (
      "<ul>" +
      r.skipped_students
        .map(function (x) {
          return "<li>" + VA.escapeHtml((x.roll_number || "?") + " " + x.name + " \u2014 " + x.reason) + "</li>";
        })
        .join("") +
      "</ul>"
    );
  }

  previewAdvanceBtn.addEventListener("click", async function () {
    advanceSemesterError.hidden = true;
    advanceSemesterSuccess.hidden = true;
    advancePreviewBox.hidden = true;
    previewAdvanceBtn.disabled = true;
    const result = await VA.fetchJson("/api/v1/students/advance-semester", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: advanceRequestBody(true),
    });
    previewAdvanceBtn.disabled = false;
    if (!result.ok) {
      advanceSemesterError.textContent = (result.body && result.body.error && result.body.error.message) || "Could not preview.";
      advanceSemesterError.hidden = false;
      return;
    }
    advancePreviewBox.innerHTML = "<strong>" + VA.escapeHtml(summarizeAdvance(result.body, "Would advance")) + "</strong>" + renderSkipped(result.body);
    advancePreviewBox.hidden = false;
  });

  confirmAdvanceSemesterBtn.addEventListener("click", async function () {
    const programId = advanceProgramSelect.value;
    const programLabel = programId
      ? advanceProgramSelect.options[advanceProgramSelect.selectedIndex].textContent
      : "all programs";

    if (!(await window.VistAdmin.confirm("Advance current_semester by 1 for every eligible active student in " + programLabel + "? This cannot be undone in bulk."))) {
      return;
    }

    confirmAdvanceSemesterBtn.disabled = true;
    advanceSemesterError.hidden = true;
    advanceSemesterSuccess.hidden = true;

    const result = await VA.fetchJson("/api/v1/students/advance-semester", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: advanceRequestBody(false),
    });

    confirmAdvanceSemesterBtn.disabled = false;

    if (!result.ok) {
      const message = (result.body && result.body.error && result.body.error.message) || "Could not advance semesters.";
      advanceSemesterError.textContent = message;
      advanceSemesterError.hidden = false;
      return;
    }

    advanceSemesterSuccess.textContent = summarizeAdvance(result.body, "Advanced");
    advanceSemesterSuccess.hidden = false;
    advancePreviewBox.innerHTML = renderSkipped(result.body);
    advancePreviewBox.hidden = !advancePreviewBox.innerHTML;
    loadStudents();
  });

  closeAddStudentModalBtn.addEventListener("click", function () {
    addStudentModal.close();
  });

  addStudentForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    addStudentSubmit.disabled = true;
    addStudentError.hidden = true;
    addStudentSuccess.hidden = true;

    const val = function (id) {
      return document.getElementById(id).value.trim();
    };

    const payload = {
      first_name: val("as-first-name"),
      last_name: val("as-last-name"),
      date_of_birth: val("as-dob"),
      gender: val("as-gender"),
      cnic: val("as-cnic"),
      contact_no: val("as-contact"),
      marital_status: val("as-marital"),
      religion: val("as-religion"),
      nationality: val("as-nationality"),
      guardian_name: val("as-guardian-name"),
      guardian_cnic: val("as-guardian-cnic"),
      guardian_contact_no: val("as-guardian-contact") || null,
      relation_with_guardian: val("as-relation"),
      emergency_contact_no: val("as-emergency"),
      present_address: val("as-address"),
      province: val("as-province"),
      district: val("as-district"),
      city: val("as-city"),
      program_id: programSelect.value || null,
      section_label: val("as-section") || null,
      current_semester: val("as-semester") ? parseInt(val("as-semester"), 10) : null,
      enrollment_date: val("as-enrollment-date") || null,
      admission_session: val("as-admission-session") || null,
      admission_category: val("as-admission-category") || null,
      email: val("as-email") || null,
      blood_group: val("as-blood-group") || null,
    };

    const result = await VA.fetchJson("/api/v1/students", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    addStudentSubmit.disabled = false;

    if (!result.ok) {
      let message = "Could not create student.";
      if (result.body && result.body.error && result.body.error.message) {
        message = result.body.error.message;
      } else if (result.body && Array.isArray(result.body.detail)) {
        // Pydantic validation error shape: [{loc, msg}, ...]
        message = result.body.detail
          .map(function (e) {
            return e.loc[e.loc.length - 1] + ": " + e.msg;
          })
          .join("; ");
      }
      addStudentError.textContent = message;
      addStudentError.hidden = false;
      return;
    }

    addStudentSuccess.textContent =
      "Student created. Roll number / login username: " + result.body.username +
      " \u2014 one-time password: " + (result.body.temporary_password || "see the admission slip") + " (must be changed at first sign-in).";
    addStudentSuccess.hidden = false;
    addStudentForm.reset();
    loadStudents();
  });

  prevBtn.addEventListener("click", function () {
    if (currentPage > 1) {
      currentPage -= 1;
      loadStudents();
    }
  });

  nextBtn.addEventListener("click", function () {
    if (currentPage < currentTotalPages) {
      currentPage += 1;
      loadStudents();
    }
  });

  searchInput.addEventListener("input", function () {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(function () {
      currentPage = 1;
      loadStudents();
    }, 350);
  });

  statusFilter.addEventListener("change", function () {
    currentPage = 1;
    loadStudents();
  });
  programFilter.addEventListener("change", function () {
    currentPage = 1;
    loadStudents();
  });
  document.getElementById("semester-filter").addEventListener("change", function () { currentPage = 1; loadStudents(); });
  let secTimer = null;
  document.getElementById("section-filter").addEventListener("input", function () {
    clearTimeout(secTimer);
    secTimer = setTimeout(function () { currentPage = 1; loadStudents(); }, 350);
  });
  // The "More" menu closes after a choice or a click elsewhere.
  const moreMenu = document.querySelector(".sd-menu");
  moreMenu.addEventListener("click", function (e) { if (e.target.closest("[role=menuitem]")) moreMenu.open = false; });
  document.addEventListener("click", function (e) { if (moreMenu.open && !moreMenu.contains(e.target)) moreMenu.open = false; });

  document.addEventListener("vistadmin:ready", async function () {
    await ensureProgramsLoaded();
    // Opened from Programs ("Students" on a program card): filter to that program.
    const qs = new URLSearchParams(location.search), wanted = qs.get("program");
    if (wanted && programsCache.some(function (p) { return p.id === wanted; })) programFilter.value = wanted;
    if (qs.get("semester")) document.getElementById("semester-filter").value = qs.get("semester");
    if (qs.get("status")) statusFilter.value = qs.get("status");
    syncExportLink();
    loadStudents();
  });
})();