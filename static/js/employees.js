(function () {
  "use strict";

  const VA = window.VistAdmin;
  const PAGE_SIZE = 15;
  const esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };

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
  const startTab = (location.hash || "").replace("#", "");
  if (startTab && document.querySelector('.portal-tab[data-tab="' + startTab + '"]')) activateTab(startTab);

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

  const sheet = document.getElementById("employee-sheet");
  const modalError = document.getElementById("modal-error");
  const modalSuccess = document.getElementById("modal-success");
  const editEmployeeForm = document.getElementById("edit-employee-form");
  const editEmployeeSubmit = document.getElementById("edit-employee-submit");
  const editEmployeeSuccess = document.getElementById("edit-employee-success");
  const newStatusSelect = document.getElementById("new-status");
  const saveStatusBtn = document.getElementById("save-status");

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

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    if (body && Array.isArray(body.detail)) {
      return body.detail.map((e) => e.loc[e.loc.length - 1] + ": " + e.msg).join("; ");
    }
    return fallback;
  }

  // ---------------------------------------------------------------------
  // The record dialog: header, tabs, photo (click the photo to upload; small download / remove icons)
  // ---------------------------------------------------------------------
  const STATUS = { ACTIVE: "Active", ON_LEAVE: "On leave", TERMINATED: "Left" };
  const TYPE = { PERMANENT: "Permanent", CONTRACT: "Contract", VISITING: "Visiting", PART_TIME: "Part time" };
  const PALETTE = ["#2563eb", "#7c3aed", "#db2777", "#dc2626", "#d97706", "#059669", "#0891b2", "#4f46e5"];
  const ICON_EYE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';
  function initials(n) { const w = String(n || "").trim().split(/\s+/).filter(Boolean); return ((w[0] || "?")[0] + (w.length > 1 ? w[w.length - 1][0] : "")).toUpperCase(); }
  function tone(n) { let h = 0; const t = String(n || ""); for (let i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) >>> 0; return h % PALETTE.length; }
  function pill(st) { return '<span class="status-pill ' + esc(String(st || "").toLowerCase()) + '">' + esc(STATUS[st] || st || "") + "</span>"; }
  function money(v) { return VA.formatMoney(v || 0); }
  function fmtDate(d) { return d ? new Date(String(d).slice(0, 10) + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : ""; }
  function dl(rows) {
    return rows.filter(function (r) { return r[1] !== null && r[1] !== undefined && r[1] !== ""; })
      .map(function (r) { return "<div><dt>" + esc(r[0]) + "</dt><dd>" + (r[2] ? r[1] : esc(r[1])) + "</dd></div>"; }).join("") || '<p class="ss-empty">Nothing on record.</p>';
  }
  function tab(name) {
    sheet.querySelectorAll("[data-ss-tab]").forEach(function (b) { const on = b.getAttribute("data-ss-tab") === name; b.classList.toggle("active", on); b.setAttribute("aria-selected", on ? "true" : "false"); });
    sheet.querySelectorAll("[data-ss-panel]").forEach(function (x) { x.hidden = x.getAttribute("data-ss-panel") !== name; });
    sheet.querySelector(".ss-body").scrollTop = 0;
  }
  sheet.querySelector(".ss-tabs").addEventListener("click", function (e) { const b = e.target.closest("[data-ss-tab]"); if (b) tab(b.getAttribute("data-ss-tab")); });
  $("es-close").addEventListener("click", function () { sheet.close(); });
  sheet.addEventListener("click", function (e) { if (e.target === sheet) sheet.close(); });

  let activeEmployeeName = "";
  let activeEmployee = null;
  document.getElementById("records-btn").addEventListener("click", function () {
    if (activeEmployeeId) window.RecordsPanel.open({ type: "employees", id: activeEmployeeId, name: activeEmployeeName });
  });

  function photoMsg(text, bad) { const m = $("es-photo-msg"); m.textContent = text || ""; m.hidden = !text; m.classList.toggle("bad", !!bad); }
  function updatePhotoDisplay(employeeId, hasPhoto, originalName) {
    const img = $("es-photo"), ini = $("es-initials");
    $("es-avatar").style.setProperty("--av", PALETTE[tone(activeEmployeeName)]);
    ini.textContent = initials(activeEmployeeName);
    if (hasPhoto) {
      img.src = "/api/v1/employees/" + employeeId + "/photo?t=" + Date.now(); img.hidden = false; ini.hidden = true;
      $("es-photo-dl").href = "/api/v1/employees/" + employeeId + "/photo";
      const ext = ((originalName || "").split(".").pop() || "jpg").toLowerCase();
      $("es-photo-dl").setAttribute("data-download", ((activeEmployee && activeEmployee.employee_code) || "employee") + "-photo." + ext);
    } else { img.hidden = true; img.removeAttribute("src"); ini.hidden = false; }
    $("es-photo-dl").hidden = !hasPhoto;
    $("es-photo-rm").hidden = !hasPhoto;
  }
  $("es-avatar").addEventListener("click", function () { if (activeEmployeeId) $("es-photo-input").click(); });
  $("es-photo-input").addEventListener("change", async function () {
    const input = $("es-photo-input");
    if (!activeEmployeeId || !input.files.length) return;
    const file = input.files[0];
    if (file.size > 5 * 1024 * 1024) { photoMsg("That image is larger than 5 MB. Choose a smaller one.", true); input.value = ""; return; }
    const fd = new FormData(); fd.append("file", file);
    $("es-avatar").classList.add("is-busy"); photoMsg("Uploading the photo…");
    const result = await VA.fetchJson("/api/v1/employees/" + activeEmployeeId + "/photo", { method: "POST", body: fd });
    $("es-avatar").classList.remove("is-busy"); input.value = "";
    if (!result.ok) { photoMsg(extractErrorMessage(result.body, "Could not upload the photo."), true); return; }
    photoMsg(""); updatePhotoDisplay(activeEmployeeId, true, file.name); VA.toast("Photo saved."); loadEmployees();
  });
  $("es-photo-rm").addEventListener("click", async function () {
    if (!activeEmployeeId || !(await VA.confirm("Remove " + activeEmployeeName + "'s photo?", "Remove"))) return;
    const result = await VA.fetchJson("/api/v1/employees/" + activeEmployeeId + "/photo", { method: "DELETE" });
    if (!result.ok && result.status !== 204) { photoMsg(extractErrorMessage(result.body, "Could not remove the photo."), true); return; }
    updatePhotoDisplay(activeEmployeeId, false); VA.toast("Photo removed."); loadEmployees();
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
        "<td><strong>" + VA.escapeHtml(jr.title) + "</strong></td>" +
        '<td class="sd-session">' + VA.escapeHtml(jr.code) + "</td>" +
        "<td>" + VA.escapeHtml(ROLE_NAMES[jr.default_role_code] || jr.default_role_code) + "</td>" +
        "<td>" + statusHtml + "</td>" +
        '<td class="num jr-actions">' +
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

    const jf = $("jobrole-filter"), keep = jf.value;
    jf.innerHTML = '<option value="">All job titles</option>' + jobRolesCache.map(function (jr) { return '<option value="' + esc(jr.id) + '">' + esc(jr.title) + "</option>"; }).join("");
    jf.value = keep;
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
    if ($("jobrole-filter").value) params.set("job_role_id", $("jobrole-filter").value);

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
      tbody.innerHTML = data.items.map(rowHtml).join("");
      if (window.ProfileCompleteness) window.ProfileCompleteness.fill(tbody, "employees");
    }

    const from = data.total ? (currentPage - 1) * data.page_size + 1 : 0;
    pageInfo.textContent = from + "–" + Math.min(data.total, currentPage * data.page_size) + " of " + data.total + " employees";
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= currentTotalPages;
  }

  function rowHtml(e) {
    const name = e.first_name + " " + e.last_name;
    return '<tr data-open="' + esc(e.id) + '" tabindex="0" aria-label="Open ' + esc(name) + '">' +
      '<td class="sd-who"><div><span class="sd-avatar" data-tone="' + tone(name) + '">' +
        (e.has_photo ? '<img src="/api/v1/employees/' + esc(e.id) + '/photo" alt="" loading="lazy" />' : esc(initials(name))) + "</span>" +
        '<span class="sd-name"><strong>' + esc(name) + "</strong><small>" + esc(e.employee_code) + (e.gender ? " · " + esc(e.gender) : "") + "</small></span></div></td>" +
      '<td class="sd-prog"><div><strong>' + esc(e.job_title || "—") + "</strong><small>" + esc([e.department, TYPE[e.employment_type]].filter(Boolean).join(" · ") || "—") + "</small></div></td>" +
      '<td class="sd-contact">' + (e.contact_no ? '<a href="tel:' + esc(e.contact_no) + '">' + esc(e.contact_no) + "</a>" : '<span class="sd-dim">—</span>') + "</td>" +
      '<td class="sd-session">' + esc(fmtDate(e.hire_date) || "—") + "</td>" +
      '<td class="sd-status">' + pill(e.status) + "</td>" +
      '<td class="sd-pc" data-pc="' + esc(e.id) + '"><span class="sd-dim">…</span></td>' +
      '<td class="sd-act"><button type="button" class="sd-view" data-open="' + esc(e.id) + '">' + ICON_EYE + "<span>View</span></button></td></tr>";
  }
  tbody.addEventListener("click", function (ev) { const r = ev.target.closest("[data-open]"); if (r) openEmployeeModal(r.getAttribute("data-open")); });
  tbody.addEventListener("keydown", function (ev) {
    const r = ev.target.closest("tr[data-open]");
    if (r && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); openEmployeeModal(r.getAttribute("data-open")); }
  });

  // ---- extra access roles (teacher + exam controller, second administrator, ...)
  const ROLE_NAMES = { ADMIN: "Administrator", PRINCIPAL: "Principal / Director (view only)", HOD: "Head of Department / Coordinator", TEACHER: "Teacher", EXAM_CONTROLLER: "Exam Controller", ACCOUNTS_OFFICER: "Accounts Officer",
    LIBRARIAN: "Librarian", SECURITY_GUARD: "Security Guard", HOSTEL_WARDEN: "Hostel Warden", STAFF: "General Staff" };
  async function loadAccessRoles(employeeId) {
    const box = document.getElementById("access-roles-box");
    const msgEl = document.getElementById("access-roles-msg");
    box.hidden = true;
    msgEl.hidden = true;
    const r = await VA.fetchJson("/api/v1/employees/" + employeeId + "/access-roles");
    if (!r.ok) return;                        // no login yet, or not allowed to manage roles
    const list = document.getElementById("access-roles-list");
    list.innerHTML = r.body.grantable.map(function (code) {
      return '<label class="ss-check"><input type="checkbox" value="' + code + '"' + (r.body.extra_roles.indexOf(code) !== -1 ? " checked" : "") + " /> " +
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

  async function openEmployeeModal(employeeId, startTab) {
    activeEmployeeId = employeeId;
    modalError.hidden = true;
    modalSuccess.hidden = true;
    newEmployeePassword.value = "";
    resetEmployeePasswordError.hidden = true;
    resetEmployeePasswordConfirm.hidden = true;
    photoMsg("");
    $("es-name").textContent = "Loading…"; $("es-sub").textContent = ""; $("es-chips").innerHTML = "";
    ["es-kpis", "es-employment", "es-personal", "es-contact", "es-quals", "salary-history-tbody", "bonuses-tbody"].forEach(function (k) { $(k).innerHTML = ""; });
    tab(startTab || "overview");
    if (!sheet.open) sheet.showModal();

    const both = await Promise.all([VA.fetchJson("/api/v1/employees/" + employeeId), VA.fetchJson("/api/v1/employees/" + employeeId + "/full-profile")]);
    const result = both[0], full = both[1];
    if (!result.ok) {
      $("es-name").textContent = "Could not open this record";
      modalError.textContent = extractErrorMessage(result.body, "Could not load employee detail.");
      modalError.hidden = false;
      return;
    }

    const e = result.body, p = full.ok ? full.body : null;
    activeEmployee = e;
    activeEmployeeName = e.first_name + " " + e.last_name;
    updatePhotoDisplay(employeeId, e.has_photo, e.photo_original_name);
    const jr = jobRolesCache.find(function (j) { return j.id === e.job_role_id; });
    $("es-name").textContent = activeEmployeeName;
    $("es-sub").textContent = [e.employee_code, (p && p.job_role_title) || (jr && jr.title), e.department].filter(Boolean).join(" · ");
    $("es-chips").innerHTML = pill(e.status) + [TYPE[e.employment_type], e.gender, e.hire_date ? "Joined " + fmtDate(e.hire_date) : ""]
      .filter(Boolean).map(function (c) { return '<span class="ss-chip">' + esc(c) + "</span>"; }).join("");
    if (p) {
      const yr = new Date().getFullYear();
      const bonusYear = (p.bonuses || []).filter(function (b) { return String(b.awarded_at).slice(0, 4) === String(yr); }).reduce(function (n, b) { return n + Number(b.amount || 0); }, 0);
      const t = p.teaching;
      $("es-kpis").innerHTML = [
        ["Basic salary", p.current_salary ? money(p.current_salary) : "Not set yet", !p.current_salary, (p.salary_history || []).length > 1 ? (p.salary_history.length - 1) + " change" + (p.salary_history.length > 2 ? "s" : "") : "No changes yet"],
        ["Bonuses " + yr, money(bonusYear), false, (p.bonuses || []).length + " in total"],
        t ? ["Pass rate", t.pass_rate === null ? "—" : Math.round(t.pass_rate * 100) + "%", false, t.offerings_count + " courses · " + t.total_students_taught + " students"]
          : ["Library books", String(p.library.active_loans || 0), p.library.overdue_loans > 0, p.library.overdue_loans ? p.library.overdue_loans + " overdue" : "None overdue"],
        ["Complaints", String(p.complaints.filed_against_them || 0), p.complaints.filed_against_them > 0, "against · " + (p.complaints.filed_by_them || 0) + " filed by them"],
      ].map(function (k) { return '<div class="ss-kpi' + (k[2] ? " bad" : "") + '"><span>' + esc(k[0]) + "</span><strong>" + esc(k[1]) + "</strong><small>" + esc(k[3]) + "</small></div>"; }).join("");
      $("es-qual-count").textContent = (p.qualifications || []).length ? String(p.qualifications.length) : "";
      $("es-quals").innerHTML = (p.qualifications || []).map(function (q) {
        return '<article class="ss-card"><span class="sd-avatar sm" aria-hidden="true">' + esc(initials(q.degree_title)) + "</span><div><strong>" + esc(q.degree_title) + "</strong><small>" +
          esc(q.institution_name) + "</small><small>" + esc([q.year_completed, q.marks_or_grade].filter(Boolean).join(" · ")) + "</small></div></article>";
      }).join("") || '<p class="ss-empty">No qualifications on record. The employee can add them from their own My Profile.</p>';
      $("es-teaching-box").hidden = !t;
      if (t) $("es-teaching").innerHTML = dl([["Courses taught", t.offerings_count], ["Students taught", t.total_students_taught], ["Passed", t.passed_count], ["Failed", t.failed_count],
        ["Pass rate", t.pass_rate === null ? "—" : Math.round(t.pass_rate * 100) + "%"]]);
    }
    $("es-employment").innerHTML = dl([["Employee code", e.employee_code], ["Job title", (p && p.job_role_title) || (jr && jr.title)], ["Department", e.department],
      ["Employment type", TYPE[e.employment_type] || e.employment_type], ["Joined", fmtDate(e.hire_date)],
      ["Salary account", e.bank_iban ? [e.bank_name, e.bank_iban].filter(Boolean).join(" · ") : "Not on record (cash or cheque)"]]);
    $("es-personal").innerHTML = dl([["CNIC", e.cnic], ["Date of birth", fmtDate(e.date_of_birth)], ["Gender", e.gender], ["Marital status", e.marital_status],
      ["Religion", e.religion], ["Blood group", e.blood_group]]);
    $("es-contact").innerHTML = dl([
      ["Mobile", e.contact_no ? '<a href="tel:' + esc(e.contact_no) + '">' + esc(e.contact_no) + "</a>" : "", true],
      ["Email", e.email ? '<a href="mailto:' + esc(e.email) + '">' + esc(e.email) + "</a>" : "", true],
      ["Emergency contact", [e.emergency_contact_name, e.emergency_contact_no].filter(Boolean).join(" · ")],
      ["Address", e.address_line], ["City", [e.city, e.district, e.province].filter(Boolean).join(", ")]]);
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
    const KIND = { INITIAL: "Starting salary", FIXED_AMOUNT: "Increase", PERCENTAGE: "Increase (%)", MANUAL: "Changed", SET: "Set", CORRECTED: "Corrected" };
    salaryHistoryTbody.innerHTML = result.body.slice().reverse().map(function (row) {
      return '<li class="' + (row.effective_to ? "" : "is-now") + '"><b>' + esc(money(row.base_salary)) + "</b><span>" + esc(KIND[row.change_type] || row.change_type || "") +
        (row.reason ? " · " + esc(row.reason) : "") + "</span><small>" + esc(fmtDate(row.effective_from)) + (!row.effective_to ? " · now" : row.effective_to < row.effective_from ? " · replaced by a correction" : " – " + esc(fmtDate(row.effective_to))) + "</small></li>";
    }).join("") || '<li class="ss-empty">No salary on record.</li>';
  }

  async function loadBonuses(employeeId) {
    const result = await VA.fetchJson("/api/v1/employees/" + employeeId + "/bonuses");
    bonusesTbody.innerHTML = "";
    if (!result.ok || !result.body) return;
    bonusesTbody.innerHTML = result.body.slice().sort(function (a, b) { return String(b.awarded_at).localeCompare(String(a.awarded_at)); }).map(function (row) {
      return "<li><b>" + esc(money(row.amount)) + "</b><span>" + esc(row.reason) + "</span><small>" + esc(fmtDate(row.awarded_at)) + "</small></li>";
    }).join("") || '<li class="ss-empty">No bonuses yet.</li>';
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

    loadEmployees();
    await openEmployeeModal(activeEmployeeId, "edit");
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

    VA.toast("Status updated.");
    loadEmployees();
    openEmployeeModal(activeEmployeeId, "account");
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
    VA.toast("Job title changed.");
    loadEmployees();
    openEmployeeModal(activeEmployeeId, "account");
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
    if ($("jobrole-filter").value) params.set("job_role_id", $("jobrole-filter").value);
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

  const incrementFrom = document.getElementById("increment-from");
  function incrementMode() {
    const set = incrementType.value === "SET";
    incrementValue.placeholder = set ? "New basic salary (Rs.)" : incrementType.value === "PERCENTAGE" ? "Increase (%)" : "Increase (Rs.)";
    incrementReason.placeholder = set ? "Reason (required), e.g. Salary agreed at interview" : "Reason (optional)";
    if (!incrementFrom.value) incrementFrom.value = new Date().toISOString().slice(0, 10);
  }
  incrementType.addEventListener("change", incrementMode);
  incrementMode();

  applyIncrementBtn.addEventListener("click", async function () {
    if (!activeEmployeeId) return;
    const value = parseFloat(incrementValue.value);
    const set = incrementType.value === "SET";
    modalError.hidden = true;
    modalSuccess.hidden = true;
    if (!value || value <= 0) {
      modalError.textContent = set ? "Enter the new basic salary." : "Enter a positive increment value.";
      modalError.hidden = false;
      return;
    }
    if (set && incrementReason.value.trim().length < 5) {
      modalError.textContent = "Give the reason (kept on record), e.g. \u201cSalary agreed at interview\u201d or \u201cWrong amount entered\u201d.";
      modalError.hidden = false;
      return;
    }
    applyIncrementBtn.disabled = true;

    const result = set
      ? await VA.fetchJson("/api/v1/employees/" + activeEmployeeId + "/salary", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ amount: value, effective_from: incrementFrom.value || null, reason: incrementReason.value.trim() }) })
      : await VA.fetchJson("/api/v1/employees/" + activeEmployeeId + "/salary-increment", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ change_type: incrementType.value, value: value, reason: incrementReason.value.trim() || null, effective_from: incrementFrom.value || null }) });

    applyIncrementBtn.disabled = false;

    if (!result.ok) {
      modalError.textContent = extractErrorMessage(result.body, set ? "Could not set the salary." : "Could not apply increment.");
      modalError.hidden = false;
      return;
    }

    modalSuccess.textContent = "Basic salary is now " + money(result.body.base_salary) + " from " + fmtDate(result.body.effective_from) + ".";
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
      base_salary: parseFloat(val("ae-base-salary")) || 0,      // empty = not decided yet (set it later on the Pay tab)
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
  $("jobrole-filter").addEventListener("change", function () { currentPage = 1; loadEmployees(); syncEmployeeExportLink(); });
  const moreMenu = document.querySelector(".sd-menu");
  moreMenu.addEventListener("click", function (ev) { if (ev.target.closest("[role=menuitem]")) moreMenu.open = false; });
  document.addEventListener("click", function (ev) { if (moreMenu.open && !moreMenu.contains(ev.target)) moreMenu.open = false; });

  document.addEventListener("vistadmin:ready", function () {
    loadJobRoles();
    loadEmployees();
  });
})();