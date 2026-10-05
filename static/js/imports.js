(function () {
  "use strict";

  const VA = window.VistAdmin;

  const uploadForm = document.getElementById("upload-form");
  const fileInput = document.getElementById("csv-file-input");
  const uploadBtn = document.getElementById("upload-btn");
  const uploadError = document.getElementById("upload-error");
  const uploadStatus = document.getElementById("upload-status");

  const jobsTbody = document.getElementById("jobs-tbody");
  const jobsEmpty = document.getElementById("jobs-empty");

  const errorsModal = document.getElementById("errors-modal");
  const errorsTbody = document.getElementById("errors-tbody");
  const closeErrorsModalBtn = document.getElementById("close-errors-modal");

  function statusPillHtml(status) {
    return '<span class="status-pill ' + (status || "").toLowerCase() + '">' + VA.escapeHtml(status) + "</span>";
  }

  // NOTE: there is no "list all import jobs" endpoint in the API — the
  // import history shown here reuses the same recent-imports data that
  // powers the main dashboard (last 5 jobs), rather than inventing a new
  // backend endpoint just for this page.
  async function loadJobHistory() {
    const result = await VA.fetchJson("/api/v1/reports/admin-dashboard");
    if (!result.ok) {
      VA.showError("Could not load import history.");
      return;
    }
    VA.clearError();

    const jobs = (result.body && result.body.recent_imports) || [];
    jobsTbody.innerHTML = "";

    if (jobs.length === 0) {
      jobsEmpty.hidden = false;
      return;
    }
    jobsEmpty.hidden = true;

    jobs.forEach(function (job) {
      const tr = document.createElement("tr");
      const viewErrorsHtml =
        job.status === "FAILED"
          ? '<button class="row-action view-errors-btn" type="button" data-job-id="' + job.id + '">View Errors</button>'
          : "";
      tr.innerHTML =
        "<td>" + VA.escapeHtml(job.filename) + "</td>" +
        "<td>" + statusPillHtml(job.status) + "</td>" +
        '<td class="num">' + VA.escapeHtml(job.imported_rows) + " / " + VA.escapeHtml(job.total_rows) + "</td>" +
        "<td>" + VA.escapeHtml(VA.formatDateTime(job.created_at)) + "</td>" +
        "<td>" + viewErrorsHtml + "</td>";
      jobsTbody.appendChild(tr);
    });

    jobsTbody.querySelectorAll(".view-errors-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openErrorsModal(btn.getAttribute("data-job-id"));
      });
    });
  }

  async function openErrorsModal(jobId) {
    errorsTbody.innerHTML = '<tr><td colspan="3">Loading&hellip;</td></tr>';
    errorsModal.showModal();

    const result = await VA.fetchJson("/api/v1/imports/students/" + jobId + "/errors?page_size=100");
    if (!result.ok) {
      errorsTbody.innerHTML = '<tr><td colspan="3">Could not load errors.</td></tr>';
      return;
    }

    const rows = (result.body && result.body.items) || [];
    errorsTbody.innerHTML = "";
    if (rows.length === 0) {
      errorsTbody.innerHTML = '<tr><td colspan="3">No row-level errors recorded.</td></tr>';
      return;
    }
    rows.forEach(function (err) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(err.row_number) + "</td>" +
        "<td>" + VA.escapeHtml(err.field) + "</td>" +
        "<td>" + VA.escapeHtml(err.message) + "</td>";
      errorsTbody.appendChild(tr);
    });
  }

  closeErrorsModalBtn.addEventListener("click", function () {
    errorsModal.close();
  });

  async function pollJobStatus(jobId, attemptsLeft) {
    if (attemptsLeft <= 0) {
      uploadStatus.textContent = "Still processing in the background — check Import History shortly.";
      return;
    }

    const result = await VA.fetchJson("/api/v1/imports/students/" + jobId);
    if (!result.ok) {
      uploadStatus.textContent = "Could not check import status.";
      return;
    }

    const job = result.body;
    if (job.status === "COMPLETED") {
      uploadStatus.textContent =
        "Completed — " + job.imported_rows + " of " + job.total_rows + " row(s) imported.";
      loadJobHistory();
      return;
    }
    if (job.status === "FAILED") {
      uploadStatus.textContent =
        "Failed — " + (job.failure_reason || "see row errors below") + ". Nothing was imported.";
      loadJobHistory();
      return;
    }

    uploadStatus.textContent = "Status: " + job.status + "\u2026";
    setTimeout(function () {
      pollJobStatus(jobId, attemptsLeft - 1);
    }, 1200);
  }

  uploadForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    uploadError.hidden = true;
    uploadStatus.hidden = false;
    uploadStatus.textContent = "Uploading\u2026";
    uploadBtn.disabled = true;

    const file = fileInput.files[0];
    if (!file) {
      uploadBtn.disabled = false;
      return;
    }

    const formData = new FormData();
    formData.append("file", file);

    const result = await VA.fetchJson("/api/v1/imports/students", { method: "POST", body: formData });
    uploadBtn.disabled = false;

    if (!result.ok) {
      uploadStatus.hidden = true;
      const message = (result.body && result.body.error && result.body.error.message) || "Upload failed.";
      uploadError.textContent = message;
      uploadError.hidden = false;
      return;
    }

    uploadForm.reset();
    uploadStatus.textContent = "Accepted \u2014 processing\u2026";
    pollJobStatus(result.body.import_job_id, 20);
  });

  document.addEventListener("vistadmin:ready", function () {
    loadJobHistory();
  });
})();