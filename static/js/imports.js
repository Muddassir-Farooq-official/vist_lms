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

  // Plain words for each state. An upload still "UPLOADED" after a couple of minutes was never picked up.
  const STUCK_MS = 2 * 60 * 1000;
  function stateOf(job) {
    if (job.status === "UPLOADED" && Date.now() - new Date(job.created_at).getTime() > STUCK_MS) return ["dropped", "Not started"];
    return ({ UPLOADED: ["pending", "Waiting"], VALIDATING: ["pending", "Checking"], VALIDATED: ["pending", "Checked"], IMPORTING: ["pending", "Importing"],
              COMPLETED: ["active", "Imported"], FAILED: ["dropped", "Failed"] })[job.status] || ["", job.status];
  }

  async function loadJobHistory() {
    const result = await VA.fetchJson("/api/v1/imports/students?limit=20");
    if (!result.ok) {
      VA.showError("Could not load import history.");
      return;
    }
    VA.clearError();

    const jobs = result.body || [];
    jobsTbody.innerHTML = "";

    if (jobs.length === 0) {
      jobsEmpty.hidden = false;
      return;
    }
    jobsEmpty.hidden = true;

    let waiting = false;
    jobs.forEach(function (job) {
      const tr = document.createElement("tr");
      const st = stateOf(job);
      if (["UPLOADED", "VALIDATING", "VALIDATED", "IMPORTING"].indexOf(job.status) !== -1 && st[1] !== "Not started") waiting = true;
      let note = "";
      if (st[1] === "Not started") note = "Was not picked up. Upload the same file again to retry.";
      else if (job.status === "FAILED") note = (job.failure_reason || "") + " Fix the file (or the cause) and upload it again.";
      else if (job.status === "COMPLETED") note = job.imported_rows + " student(s) added.";
      const viewErrorsHtml =
        job.status === "FAILED" && job.invalid_rows > 0
          ? '<button class="row-action view-errors-btn" type="button" data-job-id="' + job.id + '">View errors</button>'
          : "";
      tr.innerHTML =
        "<td>" + VA.escapeHtml(job.filename) + (note ? "<br><small>" + VA.escapeHtml(note) + "</small>" : "") + "</td>" +
        '<td><span class="status-pill ' + st[0] + '">' + VA.escapeHtml(st[1]) + "</span></td>" +
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
    if (waiting) setTimeout(loadJobHistory, 3000);           // keep the list fresh while something is running
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
      uploadStatus.textContent = "Still waiting to start. Check Import history in a minute; if it says \u201cNot started\u201d, upload the same file again.";
      loadJobHistory();
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

    uploadStatus.textContent = stateOf(job)[1] + "\u2026";
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
    pollJobStatus(result.body.import_job_id, 40);
    loadJobHistory();
  });

  document.addEventListener("vistadmin:ready", function () {
    loadJobHistory();
  });
})();