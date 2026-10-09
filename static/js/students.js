(function () {
  "use strict";

  const VA = window.VistAdmin;
  const PAGE_SIZE = 10;

  let currentPage = 1;
  let currentTotalPages = 1;
  let searchDebounceTimer = null;

  const searchInput = document.getElementById("search-input");
  const statusFilter = document.getElementById("status-filter");
  const tbody = document.getElementById("students-tbody");
  const emptyState = document.getElementById("students-empty");
  const prevBtn = document.getElementById("prev-page");
  const nextBtn = document.getElementById("next-page");
  const pageInfo = document.getElementById("page-info");

  const modal = document.getElementById("student-modal");
  const modalName = document.getElementById("modal-name");
  const modalDetails = document.getElementById("modal-details");
  const modalError = document.getElementById("modal-error");
  const newStatusSelect = document.getElementById("new-status");
  const saveStatusBtn = document.getElementById("save-status");
  const closeModalBtn = document.getElementById("close-modal");
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
    if (q) params.set("q", q);
    if (st) params.set("status", st);
    link.href = "/api/v1/students/export" + (params.toString() ? "?" + params.toString() : "");
  }
  document.getElementById("search-input").addEventListener("input", syncExportLink);
  document.getElementById("status-filter").addEventListener("change", syncExportLink);

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

  function statusPillHtml(status) {
    return '<span class="status-pill ' + (status || "").toLowerCase() + '">' + VA.escapeHtml(status) + "</span>";
  }

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  // ---------------------------------------------------------------------
  // Profile photo — display, upload, download, remove. Same pattern
  // reused identically in static/js/employees.js and the self-service
  // portal pages.
  // ---------------------------------------------------------------------
  const photoFrame = document.getElementById("modal-photo-frame");
  const photoInput = document.getElementById("modal-photo-input");
  const photoUploadBtn = document.getElementById("modal-photo-upload-btn");
  const photoDownloadLink = document.getElementById("modal-photo-download-link");
  const photoRemoveBtn = document.getElementById("modal-photo-remove-btn");
  const photoError = document.getElementById("modal-photo-error");

  function photoUrlFor(studentId) {
    return "/api/v1/students/" + studentId + "/photo";
  }

  function initialsAvatarHtml(fullName) {
    const parts = (fullName || "").trim().split(/\s+/).filter(Boolean);
    const initials = ((parts[0] ? parts[0][0] : "") + (parts[1] ? parts[1][0] : "")).toUpperCase() || "?";
    const palette = ["#2563eb", "#7c3aed", "#db2777", "#dc2626", "#d97706", "#059669", "#0891b2", "#4f46e5"];
    let hash = 0;
    for (let i = 0; i < (fullName || "").length; i++) hash = (hash * 31 + fullName.charCodeAt(i)) >>> 0;
    const color = palette[hash % palette.length];
    return '<div class="profile-photo-initials" style="background:' + color + '">' + VA.escapeHtml(initials) + "</div>";
  }

  let activeStudentName = "";

  function updatePhotoDisplay(studentId, hasPhoto) {
    photoError.hidden = true;
    if (hasPhoto) {
      // Cache-bust so a just-replaced photo doesn't show the browser's
      // cached copy of the old one at the same URL.
      photoFrame.innerHTML = '<img src="' + photoUrlFor(studentId) + "?t=" + Date.now() + '" alt="" />';
      photoDownloadLink.href = photoUrlFor(studentId);
      photoDownloadLink.setAttribute("download", "student-photo.jpg");
      photoDownloadLink.hidden = false;
      photoRemoveBtn.hidden = false;
    } else {
      photoFrame.innerHTML = initialsAvatarHtml(activeStudentName);
      photoDownloadLink.hidden = true;
      photoRemoveBtn.hidden = true;
    }
  }

  photoUploadBtn.addEventListener("click", async function () {
    if (!activeStudentId) return;
    photoError.hidden = true;
    if (!photoInput.files.length) {
      photoError.textContent = "Choose an image file first.";
      photoError.hidden = false;
      return;
    }
    photoUploadBtn.disabled = true;
    const formData = new FormData();
    formData.append("file", photoInput.files[0]);
    const result = await VA.fetchJson("/api/v1/students/" + activeStudentId + "/photo", {
      method: "POST",
      body: formData,
    });
    photoUploadBtn.disabled = false;

    if (!result.ok) {
      photoError.textContent = extractErrorMessage(result.body, "Could not upload photo.");
      photoError.hidden = false;
      return;
    }
    photoInput.value = "";
    updatePhotoDisplay(activeStudentId, true);
  });

  photoRemoveBtn.addEventListener("click", async function () {
    if (!activeStudentId) return;
    photoError.hidden = true;
    photoRemoveBtn.disabled = true;
    const result = await VA.fetchJson("/api/v1/students/" + activeStudentId + "/photo", { method: "DELETE" });
    photoRemoveBtn.disabled = false;

    if (!result.ok && result.status !== 204) {
      photoError.textContent = extractErrorMessage(result.body, "Could not remove photo.");
      photoError.hidden = false;
      return;
    }
    updatePhotoDisplay(activeStudentId, false);
  });

  async function loadStudents() {
    const params = new URLSearchParams();
    params.set("page", String(currentPage));
    params.set("page_size", String(PAGE_SIZE));
    if (searchInput.value.trim()) params.set("q", searchInput.value.trim());
    if (statusFilter.value) params.set("status", statusFilter.value);

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
      data.items.forEach(function (student) {
        const tr = document.createElement("tr");
        tr.className = "clickable-row";
        tr.innerHTML =
          "<td>" + VA.escapeHtml(student.first_name) + " " + VA.escapeHtml(student.last_name) + "</td>" +
          "<td>" + VA.escapeHtml(student.roll_number || "\u2014") + "</td>" +
          "<td>" + statusPillHtml(student.status) + "</td>" +
          '<td class="num"><span class="row-action">View &rarr;</span></td>';
        tr.addEventListener("click", function () {
          openStudentModal(student.id);
        });
        tbody.appendChild(tr);
      });
    }

    pageInfo.textContent = "Page " + currentPage + " of " + currentTotalPages + " (" + data.total + " total)";
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= currentTotalPages;
  }

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
    text.textContent = "Parent portal — " + a.guardian_name + " (" + a.relation + ", CNIC " + a.guardian_cnic + "): " +
      (a.linked ? "can sign in as " + a.username + "." : a.has_login ? "has a login; this student is not linked yet." : "no login yet.");
    give.hidden = a.linked;
    reset.hidden = !a.has_login;
    revoke.hidden = !a.linked;
  }
  async function parentAction(method, query, confirmText) {
    if (confirmText && !window.confirm(confirmText)) return;
    const r = await VA.fetchJson("/api/v1/parents/students/" + activeStudentId + (query || ""), { method: method });
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

  async function openStudentModal(studentId) {
    activeStudentId = studentId;
    loadParentAccess(studentId);
    document.getElementById("print-slip-link").href = "/api/v1/students/" + studentId + "/admission-slip";
    modalError.hidden = true;
    resetPasswordConfirm.hidden = true;
    editStudentSuccess.hidden = true;
    modalDetails.innerHTML = "<dt>Loading&hellip;</dt>";
    modal.showModal();

    await ensureProgramsLoaded();
    await ensureReferenceDataLoaded();

    const result = await VA.fetchJson("/api/v1/students/" + studentId);
    if (!result.ok) {
      modalDetails.innerHTML = "";
      modalError.textContent =
        (result.body && result.body.error && result.body.error.message) || "Could not load student detail.";
      modalError.hidden = false;
      return;
    }

    const s = result.body;
    activeStudentName = s.first_name + " " + s.last_name;
    updatePhotoDisplay(studentId, s.has_photo);
    modalName.textContent = s.first_name + " " + s.last_name;
    modalDetails.innerHTML =
      "<dt>Roll Number</dt><dd>" + VA.escapeHtml(s.roll_number || "\u2014") + "</dd>" +
      "<dt>CNIC</dt><dd>" + VA.escapeHtml(s.cnic) + "</dd>" +
      "<dt>Contact</dt><dd>" + VA.escapeHtml(s.contact_no) + "</dd>" +
      "<dt>Gender</dt><dd>" + VA.escapeHtml(s.gender) + "</dd>" +
      "<dt>Date of Birth</dt><dd>" + VA.escapeHtml(s.date_of_birth) + "</dd>" +
      "<dt>Current Semester</dt><dd>" + VA.escapeHtml(s.current_semester || "\u2014") + "</dd>" +
      "<dt>Enrollment Date</dt><dd>" + VA.escapeHtml(s.enrollment_date || "\u2014") + "</dd>" +
      "<dt>Section</dt><dd>" + VA.escapeHtml(s.section_label || "\u2014") + "</dd>" +
      "<dt>Email</dt><dd>" + VA.escapeHtml(s.email || "\u2014") + "</dd>" +
      "<dt>Blood Group</dt><dd>" + VA.escapeHtml(s.blood_group || "\u2014") + "</dd>" +
      "<dt>Admission Session</dt><dd>" + VA.escapeHtml(s.admission_session || "\u2014") + "</dd>" +
      "<dt>Admission Category</dt><dd>" + VA.escapeHtml(s.admission_category || "\u2014") + "</dd>" +
      "<dt>Status</dt><dd>" + statusPillHtml(s.status) + "</dd>";
    newStatusSelect.value = s.status;

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
  }

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

    editStudentSuccess.textContent = "Saved.";
    editStudentSuccess.hidden = false;
    loadStudents();
  });

  saveStatusBtn.addEventListener("click", async function () {
    if (!activeStudentId) return;
    saveStatusBtn.disabled = true;
    modalError.hidden = true;

    const result = await VA.fetchJson("/api/v1/students/" + activeStudentId + "/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_status: newStatusSelect.value }),
    });

    saveStatusBtn.disabled = false;

    if (!result.ok) {
      modalError.textContent =
        (result.body && result.body.error && result.body.error.message) || "Could not update status.";
      modalError.hidden = false;
      return;
    }

    modal.close();
    loadStudents();
  });

  closeModalBtn.addEventListener("click", function () {
    modal.close();
  });

  resetPasswordBtn.addEventListener("click", async function () {
    if (!activeStudentId) return;
    if (!window.confirm("Reset this student's password to the institution default? They will be required to change it on next login.")) {
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

    [programSelect, editProgramSelect, advanceProgramSelect].forEach(function (select) {
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

    if (!window.confirm("Advance current_semester by 1 for every eligible active student in " + programLabel + "? This cannot be undone in bulk.")) {
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
      " \u2014 default password: vertex123 (must change on first login).";
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

  // ---------------------------------------------------------------------
  // Full Profile — "everything about this student" aggregated view
  // ---------------------------------------------------------------------
  const fullProfileModal = document.getElementById("full-profile-modal");
  const fpName = document.getElementById("fp-name");
  const fpError = document.getElementById("fp-error");
  const fpCore = document.getElementById("fp-core");
  const fpGuardiansTbody = document.getElementById("fp-guardians-tbody");
  const fpEmergencyTbody = document.getElementById("fp-emergency-tbody");
  const fpQualificationsTbody = document.getElementById("fp-qualifications-tbody");
  const fpQualificationsEmpty = document.getElementById("fp-qualifications-empty");
  const fpEnrollmentsTbody = document.getElementById("fp-enrollments-tbody");
  const fpEnrollmentsEmpty = document.getElementById("fp-enrollments-empty");

  function fpRow(label, value) {
    if (value === null || value === undefined || value === "") return "";
    return "<dt>" + label + "</dt><dd>" + VA.escapeHtml(value) + "</dd>";
  }

  document.getElementById("open-full-profile-btn").addEventListener("click", async function () {
    if (!activeStudentId) return;
    fpError.hidden = true;
    fpName.textContent = "Loading\u2026";
    fullProfileModal.showModal();

    const result = await VA.fetchJson("/api/v1/students/" + activeStudentId + "/full-profile");
    if (!result.ok) {
      fpName.textContent = "";
      fpError.textContent = extractErrorMessage(result.body, "Could not load full profile.");
      fpError.hidden = false;
      return;
    }

    const p = result.body;
    fpName.textContent = p.first_name + " " + p.last_name;

    fpCore.innerHTML = [
      fpRow("Roll Number", p.roll_number),
      fpRow("CNIC", p.cnic),
      fpRow("Contact No.", p.contact_no),
      fpRow("Gender", p.gender),
      fpRow("Marital Status", p.marital_status),
      fpRow("Religion", p.religion),
      fpRow("Nationality", p.nationality),
      fpRow("Status", p.status),
      fpRow("Program", p.program_name),
      fpRow("Current Semester", p.current_semester),
      fpRow("Enrollment Date", p.enrollment_date),
      fpRow("Section", p.section_label),
      fpRow("Email", p.email),
      fpRow("Blood Group", p.blood_group),
      fpRow("Admission Session", p.admission_session),
      fpRow("Admission Category", p.admission_category),
      fpRow("Address", p.present_address),
      fpRow("Province", p.province),
      fpRow("District", p.district),
      fpRow("City", p.city),
    ].join("");

    fpGuardiansTbody.innerHTML = p.guardians.map(function (g) {
      return "<tr><td>" + VA.escapeHtml(g.name) + "</td><td>" + VA.escapeHtml(g.cnic) + "</td><td>" +
        VA.escapeHtml(g.contact_no || "\u2014") + "</td><td>" + VA.escapeHtml(g.relation || "\u2014") + "</td></tr>";
    }).join("");

    fpEmergencyTbody.innerHTML = p.emergency_contacts.map(function (e) {
      return "<tr><td>" + VA.escapeHtml(e.name || "\u2014") + "</td><td>" + VA.escapeHtml(e.contact_no) + "</td><td>" +
        VA.escapeHtml(e.relation || "\u2014") + "</td></tr>";
    }).join("");

    fpQualificationsTbody.innerHTML = "";
    if (p.qualifications.length === 0) {
      fpQualificationsEmpty.hidden = false;
    } else {
      fpQualificationsEmpty.hidden = true;
      fpQualificationsTbody.innerHTML = p.qualifications.map(function (q) {
        return "<tr><td>" + VA.escapeHtml(q.degree_title) + "</td><td>" + VA.escapeHtml(q.institution_name) + "</td><td>" +
          VA.escapeHtml(q.year_completed || "\u2014") + "</td><td>" + VA.escapeHtml(q.marks_or_grade || "\u2014") + "</td></tr>";
      }).join("");
    }

    fpEnrollmentsTbody.innerHTML = "";
    if (p.enrollments.length === 0) {
      fpEnrollmentsEmpty.hidden = false;
    } else {
      fpEnrollmentsEmpty.hidden = true;
      fpEnrollmentsTbody.innerHTML = p.enrollments.map(function (e) {
        return "<tr><td>" + VA.escapeHtml(e.course_code) + " \u2014 " + VA.escapeHtml(e.course_title) + "</td>" +
          '<td class="num">' + e.credit_hours + "</td><td>" + VA.escapeHtml(e.term_label) + "</td><td>" +
          '<span class="status-pill ' + e.status.toLowerCase() + '">' + VA.escapeHtml(e.status) + "</span></td><td>" +
          (e.outcome ? '<span class="status-pill ' + e.outcome.toLowerCase() + '">' + VA.escapeHtml(e.outcome) + "</span>" : "\u2014") +
          "</td><td>" + (e.is_retake ? "Yes" : "\u2014") + "</td></tr>";
      }).join("");
    }

    document.getElementById("fp-fee-outstanding").textContent = Number(p.fees.total_outstanding).toFixed(2);
    document.getElementById("fp-library-active").textContent = p.library.active_loans;
    document.getElementById("fp-library-overdue").textContent = p.library.overdue_loans;
    document.getElementById("fp-fine-pending").textContent = Number(p.fines.total_pending).toFixed(2);
    document.getElementById("fp-complaints-filed").textContent = p.complaints.filed_by_them;
    document.getElementById("fp-complaints-against").textContent = p.complaints.filed_against_them;
  });

  document.getElementById("close-full-profile-modal").addEventListener("click", function () {
    fullProfileModal.close();
  });

  document.addEventListener("vistadmin:ready", function () {
    loadStudents();
  });
})();