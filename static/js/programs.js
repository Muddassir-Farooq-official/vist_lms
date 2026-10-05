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

  async function loadPrograms() {
    const result = await VA.fetchJson("/api/v1/programs");

    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load programs."));
      return;
    }
    VA.clearError();

    const programs = result.body || [];
    tbody.innerHTML = "";

    if (programs.length === 0) {
      emptyState.hidden = false;
      return;
    }
    emptyState.hidden = true;

    programs.forEach(function (p) {
      const tr = document.createElement("tr");
      tr.className = "clickable-row";
      tr.innerHTML =
        "<td>" + VA.escapeHtml(p.code) + "</td>" +
        "<td>" + VA.escapeHtml(p.name) + "</td>" +
        '<td class="num">' + VA.escapeHtml(p.duration_years) + " yrs</td>" +
        '<td class="num">' + VA.escapeHtml(p.total_semesters) + "</td>" +
        "<td>" + statusPillHtml(p.is_active) + "</td>" +
        '<td class="num"><span class="row-action">Edit &rarr;</span></td>';
      tr.addEventListener("click", function () {
        openEditModal(p);
      });
      tbody.appendChild(tr);
    });
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