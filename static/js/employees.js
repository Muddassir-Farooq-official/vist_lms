(function () {
  "use strict";

  const VA = window.VistAdmin;
  const PAGE_SIZE = 10;

  let currentPage = 1;
  let currentTotalPages = 1;
  let searchDebounceTimer = null;
  let activeEmployeeId = null;
  let jobRolesCache = [];

  // =========================================================================
  // Tabs — same pattern as static/js/courses.js and static/js/fees.js
  // =========================================================================
  const tabButtons = document.querySelectorAll(".portal-tab");
  const tabPanels = document.querySelectorAll(".portal-tab-panel");

  function activateTab(tabName) {
    tabButtons.forEach(function (btn) {
      btn.classList.toggle("active", btn.dataset.tab === tabName);
    });
    tabPanels.forEach(function (panel) {
      panel.hidden = panel.dataset.tabPanel !== tabName;
    });
  }

  tabButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      activateTab(btn.dataset.tab);
    });
  });

  const searchInput = document.getElementById("search-input");
  const statusFilter = document.getElementById("status-filter");
  const tbody = document.getElementById("employees-tbody");
  const emptyState = document.getElementById("employees-empty");
  const prevBtn = document.getElementById("prev-page");
  const nextBtn = document.getElementById("next-page");
  const pageInfo = document.getElementById("page-info");

  const jobRolesTbody = document.getElementById("jobroles-tbody");
  const jobRoleForm = document.getElementById("create-jobrole-form");
  const jobRoleBtn = document.getElementById("create-jobrole-btn");
  const jobRoleError = document.getElementById("jobrole-error");

  const editJobRoleModal = document.getElementById("edit-jobrole-modal");
  const editJobRoleCodeHint = document.getElementById("edit-jobrole-code-hint");
  const editJobRoleForm = document.getElementById("edit-jobrole-form");
  const editJobRoleSubmit = document.getElementById("edit-jobrole-submit");
  const editJobRoleError = document.getElementById("edit-jobrole-error");
  const editJobRoleSuccess = document.getElementById("edit-jobrole-success");
  const closeEditJobRoleModalBtn = document.getElementById("close-edit-jobrole-modal");
  let activeJobRoleId = null;

  const modal = document.getElementById("employee-modal");
  const modalName = document.getElementById("modal-name");
  const modalDetails = document.getElementById("modal-details");
  const modalError = document.getElementById("modal-error");
  const modalSuccess = document.getElementById("modal-success");
  const editEmployeeForm = document.getElementById("edit-employee-form");
  const editEmployeeSubmit = document.getElementById("edit-employee-submit");
  const editEmployeeSuccess = document.getElementById("edit-employee-success");
  const newStatusSelect = document.getElementById("new-status");
  const saveStatusBtn = document.getElementById("save-status");
  const closeModalBtn = document.getElementById("close-modal");

  const newEmployeePassword = document.getElementById("new-employee-password");
  const resetEmployeePasswordBtn = document.getElementById("reset-employee-password-btn");
  const resetEmployeePasswordConfirm = document.getElementById("reset-employee-password-confirm");
  const resetEmployeePasswordError = document.getElementById("reset-employee-password-error");

  const salaryHistoryTbody = document.getElementById("salary-history-tbody");
  const incrementType = document.getElementById("increment-type");
  const incrementValue = document.getElementById("increment-value");
  const incrementReason = document.getElementById("increment-reason");
  const applyIncrementBtn = document.getElementById("apply-increment-btn");

  const bonusesTbody = document.getElementById("bonuses-tbody");
  const bonusAmount = document.getElementById("bonus-amount");
  const bonusReason = document.getElementById("bonus-reason");
  const awardBonusBtn = document.getElementById("award-bonus-btn");

  const openAddEmployeeBtn = document.getElementById("open-add-employee");
  const addEmployeeModal = document.getElementById("add-employee-modal");
  const addEmployeeForm = document.getElementById("add-employee-form");
  const addEmployeeSubmit = document.getElementById("add-employee-submit");
  const addEmployeeError = document.getElementById("add-employee-error");
  const addEmployeeSuccess = document.getElementById("add-employee-success");
  const closeAddEmployeeModalBtn = document.getElementById("close-add-employee-modal");
  const jobRoleSelect = document.getElementById("ae-job-role");

  function statusPillHtml(status) {
    return '<span class="status-pill ' + (status || "").toLowerCase() + '">' + VA.escapeHtml(status) + "</span>";
  }

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    if (body && Array.isArray(body.detail)) {
      return body.detail.map((e) => e.loc[e.loc.length - 1] + ": " + e.msg).join("; ");
    }
    return fallback;
  }

  // ---------------------------------------------------------------------
  // Profile photo — display, upload, download, remove. Same pattern as
  // static/js/students.js.
  // ---------------------------------------------------------------------
  const photoFrame = document.getElementById("modal-photo-frame");
  const photoInput = document.getElementById("modal-photo-input");
  const photoUploadBtn = document.getElementById("modal-photo-upload-btn");
  const photoDownloadLink = document.getElementById("modal-photo-download-link");
  const photoRemoveBtn = document.getElementById("modal-photo-remove-btn");
  const photoError = document.getElementById("modal-photo-error");

  function photoUrlFor(employeeId) {
    return "/api/v1/employees/" + employeeId + "/photo";
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

  let activeEmployeeName = "";
  document.getElementById("records-btn").addEventListener("click", function () {
    if (activeEmployeeId) window.RecordsPanel.open({ type: "employees", id: activeEmployeeId, name: activeEmployeeName });
  });

  function updatePhotoDisplay(employeeId, hasPhoto) {
    photoError.hidden = true;
    if (hasPhoto) {
      photoFrame.innerHTML = '<img src="' + photoUrlFor(employeeId) + "?t=" + Date.now() + '" alt="" />';
      photoDownloadLink.href = photoUrlFor(employeeId);
      photoDownloadLink.setAttribute("download", "employee-photo.jpg");
      photoDownloadLink.hidden = false;
      photoRemoveBtn.hidden = false;
    } else {
      photoFrame.innerHTML = initialsAvatarHtml(activeEmployeeName);
      photoDownloadLink.hidden = true;
      photoRemoveBtn.hidden = true;
    }
  }

  photoUploadBtn.addEventListener("click", async function () {
    if (!activeEmployeeId) return;
    photoError.hidden = true;
    if (!photoInput.files.length) {
      photoError.textContent = "Choose an image file first.";
      photoError.hidden = false;
      return;
    }
    photoUploadBtn.disabled = true;
    const formData = new FormData();
    formData.append("file", photoInput.files[0]);
    const result = await VA.fetchJson("/api/v1/employees/" + activeEmployeeId + "/photo", {
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
    updatePhotoDisplay(activeEmployeeId, true);
  });

  photoRemoveBtn.addEventListener("click", async function () {
    if (!activeEmployeeId) return;
    photoError.hidden = true;
    photoRemoveBtn.disabled = true;
    const result = await VA.fetchJson("/api/v1/employees/" + activeEmployeeId + "/photo", { method: "DELETE" });
    photoRemoveBtn.disabled = false;

    if (!result.ok && result.status !== 204) {
      photoError.textContent = extractErrorMessage(result.body, "Could not remove photo.");
      photoError.hidden = false;
      return;
    }
    updatePhotoDisplay(activeEmployeeId, false);
  });

  // ---------------------------------------------------------------------
  // Job roles
  // ---------------------------------------------------------------------
  async function loadJobRoles() {
    // Fetch ALL job roles (not just active) so deactivated ones remain
    // visible here with a "Reactivate" option — only the Add Employee
    // dropdown filters to active-only, since that's the one place an
    // inactive role genuinely shouldn't be selectable.
    const result = await VA.fetchJson("/api/v1/job-roles");
    if (!result.ok) return;
    jobRolesCache = result.body || [];

    jobRolesTbody.innerHTML = "";
    jobRolesCache.forEach(function (jr) {
      const tr = document.createElement("tr");
      const statusHtml = jr.is_active
        ? '<span class="status-pill active">Active</span>'
        : '<span class="status-pill dropped">Inactive</span>';
      const actionLabel = jr.is_active ? "Deactivate" : "Reactivate";
      tr.innerHTML =
        "<td>" + VA.escapeHtml(jr.code) + "</td>" +
        "<td>" + VA.escapeHtml(jr.title) + "</td>" +
        "<td>" + VA.escapeHtml(jr.default_role_code) + "</td>" +
        "<td>" + statusHtml + "</td>" +
        '<td class="num">' +
        '<button type="button" class="row-action jobrole-edit-btn" data-id="' + jr.id + '">Edit</button>' +
        " &middot; " +
        '<button type="button" class="row-action jobrole-toggle-btn" data-id="' + jr.id + '" data-active="' + jr.is_active + '">' + actionLabel + "</button>" +
        " &middot; " +
        '<button type="button" class="row-action jobrole-delete-btn" data-id="' + jr.id + '" data-code="' + VA.escapeHtml(jr.code) + '">Delete</button>' +
        "</td>";
      jobRolesTbody.appendChild(tr);
    });

    jobRolesTbody.querySelectorAll(".jobrole-edit-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const jobRoleId = btn.getAttribute("data-id");
        const jr = jobRolesCache.find(function (r) {
          return r.id === jobRoleId;
        });
        if (jr) openEditJobRoleModal(jr);
      });
    });

    jobRolesTbody.querySelectorAll(".jobrole-delete-btn").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        const jobRoleId = btn.getAttribute("data-id");
        const code = btn.getAttribute("data-code");
        if (!(await window.VistAdmin.confirm('Permanently delete job role "' + code + '"? This only works if no employees are assigned to it.'))) {
          return;
        }
        btn.disabled = true;
        jobRoleError.hidden = true;

        const result = await VA.fetchJson("/api/v1/job-roles/" + jobRoleId, { method: "DELETE" });

        btn.disabled = false;

        if (!result.ok) {
          jobRoleError.textContent = extractErrorMessage(result.body, "Could not delete job role.");
          jobRoleError.hidden = false;
          return;
        }

        loadJobRoles();
      });
    });

    jobRolesTbody.querySelectorAll(".jobrole-toggle-btn").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        const jobRoleId = btn.getAttribute("data-id");
        const currentlyActive = btn.getAttribute("data-active") === "true";
        btn.disabled = true;
        jobRoleError.hidden = true;

        const result = await VA.fetchJson("/api/v1/job-roles/" + jobRoleId, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ is_active: !currentlyActive }),
        });

        btn.disabled = false;

        if (!result.ok) {
          jobRoleError.textContent = extractErrorMessage(result.body, "Could not update job role.");
          jobRoleError.hidden = false;
          return;
        }

        loadJobRoles();
      });
    });

    jobRoleSelect.innerHTML = '<option value="">Select&hellip;</option>';
    jobRolesCache
      .filter(function (jr) {
        return jr.is_active;
      })
      .forEach(function (jr) {
        const opt = document.createElement("option");
        opt.value = jr.id;
        opt.textContent = jr.code + " \u2014 " + jr.title;
        jobRoleSelect.appendChild(opt);
      });
  }

  // ---------------------------------------------------------------------
  // Reference data (provinces/districts/cities/religions) — same
  // single-source-of-truth endpoint and pattern already used in
  // static/js/students.js.
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

    fillStaticOptions(document.getElementById("ae-province"), referenceData.provinces, "Select\u2026");
    fillStaticOptions(document.getElementById("ee-province"), referenceData.provinces, "Select\u2026");
    fillStaticOptions(document.getElementById("ae-city"), referenceData.cities, "Select\u2026");
    fillStaticOptions(document.getElementById("ee-city"), referenceData.cities, "Select\u2026");
    fillStaticOptions(document.getElementById("ae-religion"), referenceData.religions, "Select\u2026");
    fillStaticOptions(document.getElementById("ee-religion"), referenceData.religions, "Select\u2026");

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

  const aeProvinceSelect = document.getElementById("ae-province");
  const aeDistrictSelect = document.getElementById("ae-district");
  if (aeProvinceSelect) {
    aeProvinceSelect.addEventListener("change", function () {
      populateDistrictOptions(aeDistrictSelect, aeProvinceSelect.value, null);
    });
  }

  const eeProvinceSelect = document.getElementById("ee-province");
  const eeDistrictSelect = document.getElementById("ee-district");
  if (eeProvinceSelect) {
    eeProvinceSelect.addEventListener("change", function () {
      populateDistrictOptions(eeDistrictSelect, eeProvinceSelect.value, null);
    });
  }

  function openEditJobRoleModal(jr) {
    activeJobRoleId = jr.id;
    editJobRoleError.hidden = true;
    editJobRoleSuccess.hidden = true;
    editJobRoleCodeHint.textContent = "Code: " + jr.code + " (not editable)";
    document.getElementById("ejr-title").value = jr.title;
    document.getElementById("ejr-default-role").value = jr.default_role_code;
    document.getElementById("ejr-active").value = String(jr.is_active);
    editJobRoleModal.showModal();
  }

  closeEditJobRoleModalBtn.addEventListener("click", function () {
    editJobRoleModal.close();
  });

  editJobRoleForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!activeJobRoleId) return;
    editJobRoleSubmit.disabled = true;
    editJobRoleError.hidden = true;
    editJobRoleSuccess.hidden = true;

    const payload = {
      title: document.getElementById("ejr-title").value.trim(),
      default_role_code: document.getElementById("ejr-default-role").value,
      is_active: document.getElementById("ejr-active").value === "true",
    };

    const result = await VA.fetchJson("/api/v1/job-roles/" + activeJobRoleId, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    editJobRoleSubmit.disabled = false;

    if (!result.ok) {
      editJobRoleError.textContent = extractErrorMessage(result.body, "Could not update job role.");
      editJobRoleError.hidden = false;
      return;
    }

    editJobRoleSuccess.textContent = "Saved.";
    editJobRoleSuccess.hidden = false;
    loadJobRoles();
  });

  jobRoleForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    jobRoleBtn.disabled = true;
    jobRoleError.hidden = true;

    const payload = {
      code: document.getElementById("jr-code").value.trim(),
      title: document.getElementById("jr-title").value.trim(),
      default_role_code: document.getElementById("jr-default-role").value,
    };

    const result = await VA.fetchJson("/api/v1/job-roles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    jobRoleBtn.disabled = false;

    if (!result.ok) {
      jobRoleError.textContent = extractErrorMessage(result.body, "Could not create job role.");
      jobRoleError.hidden = false;
      return;
    }

    jobRoleForm.reset();
    loadJobRoles();
  });

  // ---------------------------------------------------------------------
  // Employee list
  // ---------------------------------------------------------------------
  async function loadEmployees() {
    const params = new URLSearchParams();
    params.set("page", String(currentPage));
    params.set("page_size", String(PAGE_SIZE));
    if (searchInput.value.trim()) params.set("q", searchInput.value.trim());
    if (statusFilter.value) params.set("status", statusFilter.value);

    const result = await VA.fetchJson("/api/v1/employees?" + params.toString());
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load employees."));
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
      data.items.forEach(function (emp) {
        const tr = document.createElement("tr");
        tr.className = "clickable-row";
        tr.innerHTML =
          "<td>" + VA.escapeHtml(emp.employee_code) + "</td>" +
          "<td>" + VA.escapeHtml(emp.first_name) + " " + VA.escapeHtml(emp.last_name) + "</td>" +
          "<td>" + statusPillHtml(emp.status) + "</td>" +
          '<td class="num"><span class="row-action">View &rarr;</span></td>';
        tr.addEventListener("click", function () {
          openEmployeeModal(emp.id);
        });
        tbody.appendChild(tr);
      });
    }

    pageInfo.textContent = "Page " + currentPage + " of " + currentTotalPages + " (" + data.total + " total)";
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= currentTotalPages;
  }

  // ---- extra access roles (teacher + exam controller, second administrator, ...)
  const ROLE_NAMES = { ADMIN: "Administrator", PRINCIPAL: "Principal / Director (view only)", HOD: "Head of Department / Coordinator", TEACHER: "Teacher", EXAM_CONTROLLER: "Exam Controller", ACCOUNTS_OFFICER: "Accounts Officer",
    LIBRARIAN: "Librarian", SECURITY_GUARD: "Security Guard", HOSTEL_WARDEN: "Hostel Warden" };
  async function loadAccessRoles(employeeId) {
    const box = document.getElementById("access-roles-box");
    const msgEl = document.getElementById("access-roles-msg");
    box.hidden = true;
    msgEl.hidden = true;
    const r = await VA.fetchJson("/api/v1/employees/" + employeeId + "/access-roles");
    if (!r.ok) return;                        // no login yet, or not allowed to manage roles
    const list = document.getElementById("access-roles-list");
    list.innerHTML = r.body.grantable.map(function (code) {
      return '<label><input type="checkbox" value="' + code + '"' + (r.body.extra_roles.indexOf(code) !== -1 ? " checked" : "") + " /> " +
        VA.escapeHtml(ROLE_NAMES[code] || code) + "</label>";
    }).join(" ");
    box.dataset.employeeId = employeeId;
    box.hidden = false;
  }
  document.getElementById("access-roles-save").addEventListener("click", async function () {
    const box = document.getElementById("access-roles-box");
    const msgEl = document.getElementById("access-roles-msg");
    const extra = Array.prototype.map.call(box.querySelectorAll("#access-roles-list input:checked"), function (i) { return i.value; });
    if (!(await window.VistAdmin.confirm("Save this access? The person will be signed out and must sign in again."))) return;
    const r = await VA.fetchJson("/api/v1/employees/" + box.dataset.employeeId + "/access-roles", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ extra_roles: extra }),
    });
    msgEl.textContent = r.ok ? "Access saved." : ((r.body && r.body.error && r.body.error.message) || "Could not save.");
    msgEl.hidden = false;
    if (r.ok) loadAccessRoles(box.dataset.employeeId);
  });

  async function openEmployeeModal(employeeId) {
    activeEmployeeId = employeeId;
    modalError.hidden = true;
    modalSuccess.hidden = true;
    newEmployeePassword.value = "";
    resetEmployeePasswordError.hidden = true;
    resetEmployeePasswordConfirm.hidden = true;
    modalDetails.innerHTML = "<dt>Loading&hellip;</dt>";
    modal.showModal();

    const result = await VA.fetchJson("/api/v1/employees/" + employeeId);
    if (!result.ok) {
      modalDetails.innerHTML = "";
      modalError.textContent = extractErrorMessage(result.body, "Could not load employee detail.");
      modalError.hidden = false;
      return;
    }

    const e = result.body;
    activeEmployeeName = e.first_name + " " + e.last_name;
    updatePhotoDisplay(employeeId, e.has_photo);
    modalName.textContent = e.first_name + " " + e.last_name;
    modalDetails.innerHTML =
      "<dt>Employee Code / Username</dt><dd><strong>" + VA.escapeHtml(e.employee_code) + "</strong></dd>" +
      "<dt>CNIC</dt><dd>" + VA.escapeHtml(e.cnic) + "</dd>" +
      "<dt>Contact</dt><dd>" + VA.escapeHtml(e.contact_no) + "</dd>" +
      "<dt>Hire Date</dt><dd>" + VA.escapeHtml(e.hire_date) + "</dd>" +
      "<dt>Department</dt><dd>" + VA.escapeHtml(e.department || "\u2014") + "</dd>" +
      "<dt>Salary account</dt><dd>" + VA.escapeHtml(e.bank_iban ? (e.bank_name || "") + " " + e.bank_iban : "Not on record (paid by cash or cheque)") + "</dd>" +
      "<dt>Employment Type</dt><dd>" + VA.escapeHtml(e.employment_type || "\u2014") + "</dd>" +
      "<dt>Email</dt><dd>" + VA.escapeHtml(e.email || "\u2014") + "</dd>" +
      "<dt>Blood Group</dt><dd>" + VA.escapeHtml(e.blood_group || "\u2014") + "</dd>" +
      "<dt>Emergency Contact</dt><dd>" + VA.escapeHtml((e.emergency_contact_name || "") + " " + (e.emergency_contact_no || "") || "\u2014") + "</dd>" +
      "<dt>Status</dt><dd>" + statusPillHtml(e.status) + "</dd>";
    newStatusSelect.value = e.status;

    await ensureReferenceDataLoaded();
    document.getElementById("ee-contact").value = e.contact_no || "";
    document.getElementById("ee-department").value = e.department || "";
    document.getElementById("ee-religion").value = e.religion || "";
    eeProvinceSelect.value = e.province || "";
    populateDistrictOptions(eeDistrictSelect, e.province || "", e.district || null);
    document.getElementById("ee-city").value = e.city || "";
    document.getElementById("ee-address").value = e.address_line || "";
    document.getElementById("ee-first-name").value = e.first_name || "";
    document.getElementById("ee-last-name").value = e.last_name || "";
    document.getElementById("ee-dob").value = e.date_of_birth || "";
    document.getElementById("ee-gender").value = e.gender || "Male";
    document.getElementById("ee-hire-date").value = e.hire_date || "";
    document.getElementById("ee-employment-type").value = e.employment_type || "";
    document.getElementById("ee-email").value = e.email || "";
    document.getElementById("ee-blood-group").value = e.blood_group || "";
    document.getElementById("ee-marital").value = e.marital_status || "";
    document.getElementById("ee-emergency-name").value = e.emergency_contact_name || "";
    document.getElementById("ee-emergency-no").value = e.emergency_contact_no || "";
    document.getElementById("ee-bank").value = e.bank_name || "";
    document.getElementById("ee-bank-title").value = e.bank_account_title || "";
    document.getElementById("ee-iban").value = e.bank_iban || "";
    editEmployeeSuccess.hidden = true;
    clearanceResult.hidden = true;
    jobRoleChangeSelect.innerHTML = "";
    jobRolesCache.filter(function (jr) { return jr.is_active || jr.id === e.job_role_id; }).forEach(function (jr) {
      const opt = document.createElement("option");
      opt.value = jr.id;
      opt.textContent = jr.title + " (" + jr.code + ")";
      jobRoleChangeSelect.appendChild(opt);
    });
    jobRoleChangeSelect.value = e.job_role_id;
    loadAccessRoles(employeeId);

    await loadSalaryHistory(employeeId);
    await loadBonuses(employeeId);
  }

  async function loadSalaryHistory(employeeId) {
    const result = await VA.fetchJson("/api/v1/employees/" + employeeId + "/salary-history");
    salaryHistoryTbody.innerHTML = "";
    if (!result.ok || !result.body) return;
    result.body.forEach(function (row) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(row.base_salary) + "</td>" +
        "<td>" + VA.escapeHtml(row.effective_from) + "</td>" +
        "<td>" + VA.escapeHtml(row.effective_to || "current") + "</td>" +
        "<td>" + VA.escapeHtml(row.change_type) + "</td>";
      salaryHistoryTbody.appendChild(tr);
    });
  }

  async function loadBonuses(employeeId) {
    const result = await VA.fetchJson("/api/v1/employees/" + employeeId + "/bonuses");
    bonusesTbody.innerHTML = "";
    if (!result.ok || !result.body) return;
    result.body.forEach(function (row) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(row.amount) + "</td>" +
        "<td>" + VA.escapeHtml(row.reason) + "</td>" +
        "<td>" + VA.escapeHtml(row.awarded_at) + "</td>";
      bonusesTbody.appendChild(tr);
    });
  }

  editEmployeeForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!activeEmployeeId) return;
    editEmployeeSubmit.disabled = true;
    modalError.hidden = true;
    editEmployeeSuccess.hidden = true;

    const payload = {
      contact_no: document.getElementById("ee-contact").value.trim() || null,
      department: document.getElementById("ee-department").value.trim() || null,
      religion: document.getElementById("ee-religion").value || null,
      province: eeProvinceSelect.value || null,
      district: eeDistrictSelect.value || null,
      city: document.getElementById("ee-city").value || null,
      address_line: document.getElementById("ee-address").value.trim() || null,
      first_name: document.getElementById("ee-first-name").value.trim() || null,
      last_name: document.getElementById("ee-last-name").value.trim() || null,
      date_of_birth: document.getElementById("ee-dob").value || null,
      gender: document.getElementById("ee-gender").value || null,
      hire_date: document.getElementById("ee-hire-date").value || null,
      employment_type: document.getElementById("ee-employment-type").value || null,
      email: document.getElementById("ee-email").value.trim() || null,
      blood_group: document.getElementById("ee-blood-group").value || null,
      marital_status: document.getElementById("ee-marital").value || null,
      emergency_contact_name: document.getElementById("ee-emergency-name").value.trim() || null,
      emergency_contact_no: document.getElementById("ee-emergency-no").value.trim() || null,
      bank_name: document.getElementById("ee-bank").value.trim(),
      bank_account_title: document.getElementById("ee-bank-title").value.trim(),
      bank_iban: document.getElementById("ee-iban").value.trim(),
    };

    const result = await VA.fetchJson("/api/v1/employees/" + activeEmployeeId, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    editEmployeeSubmit.disabled = false;

    if (!result.ok) {
      modalError.textContent = extractErrorMessage(result.body, "Could not save details.");
      modalError.hidden = false;
      return;
    }

    editEmployeeSuccess.textContent = "Saved.";
    editEmployeeSuccess.hidden = false;
  });

  saveStatusBtn.addEventListener("click", async function () {
    if (!activeEmployeeId) return;
    if (newStatusSelect.value === "TERMINATED") {
      const c = await VA.fetchJson("/api/v1/employees/" + activeEmployeeId + "/clearance");
      if (c.ok) {
        const b = c.body;
        const lines = [
          "Terminating this employee will lock their login.",
          "Running courses that will be un-assigned: " + b.active_teaching_offerings,
          "Library books still out: " + b.open_library_loans,
          "Unpaid fines: " + b.unpaid_fines,
          "Outstanding fee balance: Rs. " + b.outstanding_fee_balance,
          "",
          "Continue?",
        ];
        if (!(await window.VistAdmin.confirm(lines.join("\n")))) return;
      }
    }
    saveStatusBtn.disabled = true;
    modalError.hidden = true;

    const result = await VA.fetchJson("/api/v1/employees/" + activeEmployeeId + "/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_status: newStatusSelect.value }),
    });

    saveStatusBtn.disabled = false;

    if (!result.ok) {
      modalError.textContent = extractErrorMessage(result.body, "Could not update status.");
      modalError.hidden = false;
      return;
    }

    modal.close();
    loadEmployees();
  });

  // ---- Job role change + clearance -------------------------------------
  const jobRoleChangeSelect = document.getElementById("change-job-role");
  const clearanceResult = document.getElementById("clearance-result");

  document.getElementById("change-job-role-btn").addEventListener("click", async function () {
    if (!activeEmployeeId) return;
    if (!(await window.VistAdmin.confirm("Change this employee's job role? Their login permissions change too, and they must sign in again."))) return;
    modalError.hidden = true;
    modalSuccess.hidden = true;
    const result = await VA.fetchJson("/api/v1/employees/" + activeEmployeeId + "/job-role", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        new_job_role_id: jobRoleChangeSelect.value,
        reason: document.getElementById("change-job-role-reason").value.trim() || null,
      }),
    });
    if (!result.ok) {
      modalError.textContent = extractErrorMessage(result.body, "Could not change job role.");
      modalError.hidden = false;
      return;
    }
    modalSuccess.textContent = "Job role changed.";
    modalSuccess.hidden = false;
    loadEmployees();
  });

  document.getElementById("clearance-btn").addEventListener("click", async function () {
    if (!activeEmployeeId) return;
    const result = await VA.fetchJson("/api/v1/employees/" + activeEmployeeId + "/clearance");
    if (!result.ok) {
      modalError.textContent = extractErrorMessage(result.body, "Could not load clearance.");
      modalError.hidden = false;
      return;
    }
    const b = result.body;
    clearanceResult.innerHTML =
      "<strong>" + (b.can_leave_cleanly ? "Clear \u2014 nothing outstanding." : "Items outstanding:") + "</strong>" +
      "<ul><li>Running courses assigned: " + b.active_teaching_offerings + "</li>" +
      "<li>Library books not returned: " + b.open_library_loans + "</li>" +
      "<li>Unpaid fines: " + b.unpaid_fines + "</li>" +
      "<li>Outstanding fee balance: Rs. " + b.outstanding_fee_balance + "</li></ul>";
    clearanceResult.hidden = false;
  });

  // ---- Export link follows the search/filter -----------------------------
  function syncEmployeeExportLink() {
    const link = document.getElementById("export-employees-link");
    const params = new URLSearchParams();
    const q = document.getElementById("search-input").value.trim();
    const st = document.getElementById("status-filter").value;
    if (q) params.set("q", q);
    if (st) params.set("status", st);
    link.href = "/api/v1/employees/export" + (params.toString() ? "?" + params.toString() : "");
  }
  document.getElementById("search-input").addEventListener("input", syncEmployeeExportLink);
  document.getElementById("status-filter").addEventListener("change", syncEmployeeExportLink);

  // ---- Bulk import ---------------------------------------------------------
  const importModal = document.getElementById("import-employees-modal");
  document.getElementById("open-import-employees").addEventListener("click", function () {
    ["import-emp-error", "import-emp-errors", "import-emp-success"].forEach(function (id) {
      document.getElementById(id).hidden = true;
    });
    importModal.showModal();
  });
  document.getElementById("close-import-employees-modal").addEventListener("click", function () {
    importModal.close();
  });
  document.getElementById("import-emp-submit").addEventListener("click", async function () {
    const fileInput = document.getElementById("import-emp-file");
    const errBox = document.getElementById("import-emp-error");
    const listBox = document.getElementById("import-emp-errors");
    const okBox = document.getElementById("import-emp-success");
    errBox.hidden = true;
    listBox.hidden = true;
    okBox.hidden = true;
    if (!fileInput.files.length) {
      errBox.textContent = "Choose a CSV file first.";
      errBox.hidden = false;
      return;
    }
    const fd = new FormData();
    fd.append("file", fileInput.files[0]);
    fd.append("initial_password", document.getElementById("import-emp-password").value);
    const btn = document.getElementById("import-emp-submit");
    btn.disabled = true;
    const result = await VA.fetchJson("/api/v1/employees/import", { method: "POST", body: fd });
    btn.disabled = false;
    if (!result.ok) {
      errBox.textContent = extractErrorMessage(result.body, "Import failed.");
      errBox.hidden = false;
      const rows = result.body && result.body.error && result.body.error.details && result.body.error.details.rows;
      if (rows && rows.length) {
        listBox.innerHTML =
          "<ul>" +
          rows.map(function (r) { return "<li>Row " + r.row + ": " + VA.escapeHtml(r.errors.join("; ")) + "</li>"; }).join("") +
          "</ul>";
        listBox.hidden = false;
      }
      return;
    }
    okBox.textContent =
      "Imported " + result.body.imported + " employee(s): " +
      result.body.employees.map(function (e) { return e.employee_code; }).join(", ");
    okBox.hidden = false;
    fileInput.value = "";
    loadEmployees();
  });

  resetEmployeePasswordBtn.addEventListener("click", async function () {
    if (!activeEmployeeId) return;
    const newPassword = newEmployeePassword.value;
    if (!newPassword || newPassword.length < 10) {
      resetEmployeePasswordError.textContent = "Enter a new password of at least 10 characters.";
      resetEmployeePasswordError.hidden = false;
      resetEmployeePasswordConfirm.hidden = true;
      return;
    }
    if (!(await window.VistAdmin.confirm("Set a new password for this employee? They will be required to change it on next login."))) {
      return;
    }
    resetEmployeePasswordBtn.disabled = true;
    resetEmployeePasswordError.hidden = true;
    resetEmployeePasswordConfirm.hidden = true;

    const result = await VA.fetchJson("/api/v1/employees/" + activeEmployeeId + "/reset-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_password: newPassword }),
    });

    resetEmployeePasswordBtn.disabled = false;

    if (!result.ok) {
      resetEmployeePasswordError.textContent = extractErrorMessage(result.body, "Could not set new password.");
      resetEmployeePasswordError.hidden = false;
      return;
    }

    newEmployeePassword.value = "";
    resetEmployeePasswordConfirm.hidden = false;
  });

  applyIncrementBtn.addEventListener("click", async function () {
    if (!activeEmployeeId) return;
    const value = parseFloat(incrementValue.value);
    if (!value || value <= 0) {
      modalError.textContent = "Enter a positive increment value.";
      modalError.hidden = false;
      return;
    }
    applyIncrementBtn.disabled = true;
    modalError.hidden = true;
    modalSuccess.hidden = true;

    const result = await VA.fetchJson("/api/v1/employees/" + activeEmployeeId + "/salary-increment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        change_type: incrementType.value,
        value: value,
        reason: incrementReason.value.trim() || null,
      }),
    });

    applyIncrementBtn.disabled = false;

    if (!result.ok) {
      modalError.textContent = extractErrorMessage(result.body, "Could not apply increment.");
      modalError.hidden = false;
      return;
    }

    modalSuccess.textContent = "New salary: " + result.body.base_salary;
    modalSuccess.hidden = false;
    incrementValue.value = "";
    incrementReason.value = "";
    await loadSalaryHistory(activeEmployeeId);
  });

  awardBonusBtn.addEventListener("click", async function () {
    if (!activeEmployeeId) return;
    const amount = parseFloat(bonusAmount.value);
    const reason = bonusReason.value.trim();
    if (!amount || amount <= 0 || !reason) {
      modalError.textContent = "Enter a positive amount and a reason.";
      modalError.hidden = false;
      return;
    }
    awardBonusBtn.disabled = true;
    modalError.hidden = true;
    modalSuccess.hidden = true;

    const result = await VA.fetchJson("/api/v1/employees/" + activeEmployeeId + "/bonuses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount: amount, reason: reason }),
    });

    awardBonusBtn.disabled = false;

    if (!result.ok) {
      modalError.textContent = extractErrorMessage(result.body, "Could not award bonus.");
      modalError.hidden = false;
      return;
    }

    modalSuccess.textContent = "Bonus awarded.";
    modalSuccess.hidden = false;
    bonusAmount.value = "";
    bonusReason.value = "";
    await loadBonuses(activeEmployeeId);
  });

  closeModalBtn.addEventListener("click", function () {
    modal.close();
  });

  // ---------------------------------------------------------------------
  // Add Employee
  // ---------------------------------------------------------------------
  openAddEmployeeBtn.addEventListener("click", async function () {
    addEmployeeError.hidden = true;
    addEmployeeSuccess.hidden = true;
    await loadJobRoles();
    await ensureReferenceDataLoaded();
    populateDistrictOptions(aeDistrictSelect, "", null);
    addEmployeeModal.showModal();
  });

  closeAddEmployeeModalBtn.addEventListener("click", function () {
    addEmployeeModal.close();
  });

  addEmployeeForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    addEmployeeSubmit.disabled = true;
    addEmployeeError.hidden = true;
    addEmployeeSuccess.hidden = true;

    const val = (id) => document.getElementById(id).value.trim();

    const payload = {
      first_name: val("ae-first-name"),
      last_name: val("ae-last-name"),
      date_of_birth: val("ae-dob"),
      gender: val("ae-gender"),
      cnic: val("ae-cnic"),
      contact_no: val("ae-contact"),
      hire_date: val("ae-hire-date"),
      department: val("ae-department") || null,
      job_role_id: jobRoleSelect.value,
      base_salary: parseFloat(val("ae-base-salary")),
      religion: val("ae-religion") || null,
      province: val("ae-province") || null,
      district: val("ae-district") || null,
      city: val("ae-city") || null,
      address_line: val("ae-address") || null,
      employment_type: val("ae-employment-type") || null,
      email: val("ae-email") || null,
      blood_group: val("ae-blood-group") || null,
      marital_status: val("ae-marital") || null,
      emergency_contact_name: val("ae-emergency-name") || null,
      emergency_contact_no: val("ae-emergency-no") || null,
      password: document.getElementById("ae-password").value,
    };

    const result = await VA.fetchJson("/api/v1/employees", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    addEmployeeSubmit.disabled = false;

    if (!result.ok) {
      addEmployeeError.textContent = extractErrorMessage(result.body, "Could not create employee.");
      addEmployeeError.hidden = false;
      return;
    }

    addEmployeeSuccess.textContent = "Employee created. Employee code / login username: " + result.body.username;
    addEmployeeSuccess.hidden = false;
    addEmployeeForm.reset();
    loadEmployees();
  });

  // ---------------------------------------------------------------------
  // Pagination / search / filter
  // ---------------------------------------------------------------------
  prevBtn.addEventListener("click", function () {
    if (currentPage > 1) {
      currentPage -= 1;
      loadEmployees();
    }
  });

  nextBtn.addEventListener("click", function () {
    if (currentPage < currentTotalPages) {
      currentPage += 1;
      loadEmployees();
    }
  });

  searchInput.addEventListener("input", function () {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(function () {
      currentPage = 1;
      loadEmployees();
    }, 350);
  });

  statusFilter.addEventListener("change", function () {
    currentPage = 1;
    loadEmployees();
  });

  // ---------------------------------------------------------------------
  // Full Profile — "everything about this employee" aggregated view
  // ---------------------------------------------------------------------
  const fullProfileModal = document.getElementById("full-profile-modal");
  const fpName = document.getElementById("fp-name");
  const fpError = document.getElementById("fp-error");
  const fpCore = document.getElementById("fp-core");
  const fpQualificationsTbody = document.getElementById("fp-qualifications-tbody");
  const fpQualificationsEmpty = document.getElementById("fp-qualifications-empty");
  const fpSalaryTbody = document.getElementById("fp-salary-tbody");
  const fpBonusesTbody = document.getElementById("fp-bonuses-tbody");
  const fpBonusesEmpty = document.getElementById("fp-bonuses-empty");
  const fpTeachingSection = document.getElementById("fp-teaching-section");
  const fpTeaching = document.getElementById("fp-teaching");

  function fpRow(label, value) {
    if (value === null || value === undefined || value === "") return "";
    return "<dt>" + label + "</dt><dd>" + VA.escapeHtml(value) + "</dd>";
  }

  document.getElementById("open-full-profile-btn").addEventListener("click", async function () {
    if (!activeEmployeeId) return;
    fpError.hidden = true;
    fpName.textContent = "Loading\u2026";
    fullProfileModal.showModal();

    const result = await VA.fetchJson("/api/v1/employees/" + activeEmployeeId + "/full-profile");
    if (!result.ok) {
      fpName.textContent = "";
      fpError.textContent = extractErrorMessage(result.body, "Could not load full profile.");
      fpError.hidden = false;
      return;
    }

    const p = result.body;
    fpName.textContent = p.first_name + " " + p.last_name;

    fpCore.innerHTML = [
      fpRow("Employee Code", p.employee_code),
      fpRow("CNIC", p.cnic),
      fpRow("Contact No.", p.contact_no),
      fpRow("Gender", p.gender),
      fpRow("Hire Date", p.hire_date),
      fpRow("Employment Type", p.employment_type),
      fpRow("Email", p.email),
      fpRow("Blood Group", p.blood_group),
      fpRow("Marital Status", p.marital_status),
      fpRow("Emergency Contact", ((p.emergency_contact_name || "") + " " + (p.emergency_contact_no || "")).trim() || null),
      fpRow("Department", p.department),
      fpRow("Job Role", p.job_role_title),
      fpRow("Status", p.status),
      fpRow("Religion", p.religion),
      fpRow("Address", p.present_address),
      fpRow("Province", p.province),
      fpRow("District", p.district),
      fpRow("City", p.city),
      fpRow("Current Salary", p.current_salary !== null ? Number(p.current_salary).toFixed(2) : null),
    ].join("");

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

    fpSalaryTbody.innerHTML = p.salary_history.map(function (s) {
      return "<tr><td>" + Number(s.base_salary).toFixed(2) + "</td><td>" + VA.escapeHtml(s.effective_from) + "</td><td>" +
        VA.escapeHtml(s.effective_to || "\u2014 (current)") + "</td><td>" + VA.escapeHtml(s.reason || "\u2014") + "</td></tr>";
    }).join("");

    fpBonusesTbody.innerHTML = "";
    if (p.bonuses.length === 0) {
      fpBonusesEmpty.hidden = false;
    } else {
      fpBonusesEmpty.hidden = true;
      fpBonusesTbody.innerHTML = p.bonuses.map(function (b) {
        return "<tr><td>" + Number(b.amount).toFixed(2) + "</td><td>" + VA.escapeHtml(b.reason) + "</td><td>" +
          VA.escapeHtml(b.awarded_at) + "</td></tr>";
      }).join("");
    }

    if (p.teaching) {
      fpTeachingSection.hidden = false;
      const passRateText = p.teaching.pass_rate === null ? "\u2014" : Math.round(p.teaching.pass_rate * 100) + "%";
      fpTeaching.innerHTML = [
        fpRow("Courses Taught", p.teaching.offerings_count),
        fpRow("Students Taught", p.teaching.total_students_taught),
        fpRow("Passed", p.teaching.passed_count),
        fpRow("Failed", p.teaching.failed_count),
        fpRow("Pass Rate", passRateText),
      ].join("");
    } else {
      fpTeachingSection.hidden = true;
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
    loadJobRoles();
    loadEmployees();
  });
})();