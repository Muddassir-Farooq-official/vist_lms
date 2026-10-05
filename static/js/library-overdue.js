(function () {
  "use strict";

  const VA = window.VistAdmin;
  let canIssueFine = false;

  const tbody = document.getElementById("overdue-tbody");
  const emptyState = document.getElementById("overdue-empty");

  const fineModal = document.getElementById("overdue-fine-modal");
  const fineSummary = document.getElementById("overdue-fine-summary");
  const fineForm = document.getElementById("overdue-fine-form");
  const fineAmount = document.getElementById("overdue-fine-amount");
  const fineReason = document.getElementById("overdue-fine-reason");
  const fineSubmit = document.getElementById("overdue-fine-submit");
  const fineError = document.getElementById("overdue-fine-error");
  const closeFineModalBtn = document.getElementById("close-overdue-fine-modal");

  let pendingFineTarget = null; // { borrowerType, borrowerId, relatedIssueId }

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  function daysOverdue(dueDate) {
    const due = new Date(dueDate);
    const today = new Date();
    const diffMs = today.setHours(0, 0, 0, 0) - due.setHours(0, 0, 0, 0);
    return Math.max(1, Math.round(diffMs / 86400000));
  }

  async function loadOverdue() {
    const result = await VA.fetchJson("/api/v1/library/issues/overdue");
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load overdue loans."));
      return;
    }
    VA.clearError();

    tbody.innerHTML = "";
    if (result.body.length === 0) {
      emptyState.hidden = false;
      return;
    }
    emptyState.hidden = true;

    result.body.forEach(function (issue) {
      const overdue = daysOverdue(issue.due_date);
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(issue.book_title) + "</td>" +
        "<td>" + VA.escapeHtml(issue.accession_code) + "</td>" +
        "<td>" + VA.escapeHtml(issue.borrower_name) + "</td>" +
        "<td>" + VA.escapeHtml(issue.due_date) + "</td>" +
        '<td><span class="status-pill overdue">' + overdue + " day" + (overdue === 1 ? "" : "s") + "</span></td>" +
        '<td class="row-actions"></td>';

      if (canIssueFine) {
        const btn = document.createElement("button");
        btn.className = "danger-btn small-btn";
        btn.type = "button";
        btn.textContent = "Issue Fine";
        btn.addEventListener("click", function () {
          openFineModal(issue);
        });
        tr.querySelector(".row-actions").appendChild(btn);
      }

      tbody.appendChild(tr);
    });
  }

  function openFineModal(issue) {
    pendingFineTarget = { borrowerType: issue.borrower_type, borrowerId: issue.borrower_id, issueId: issue.id };
    fineSummary.textContent = issue.borrower_name + " \u2014 \"" + issue.book_title + "\" (due " + issue.due_date + ")";
    fineAmount.value = "";
    fineReason.value = "Book returned late: \"" + issue.book_title + "\"";
    fineError.hidden = true;
    fineModal.showModal();
  }

  closeFineModalBtn.addEventListener("click", function () {
    fineModal.close();
    pendingFineTarget = null;
  });

  fineForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!pendingFineTarget) return;
    fineSubmit.disabled = true;
    fineError.hidden = true;

    const result = await VA.fetchJson("/api/v1/fines", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        payer_type: pendingFineTarget.borrowerType,
        payer_id: pendingFineTarget.borrowerId,
        amount: parseFloat(fineAmount.value),
        reason: fineReason.value.trim(),
        source: "LIBRARY",
      }),
    });

    fineSubmit.disabled = false;

    if (!result.ok) {
      fineError.textContent = extractErrorMessage(result.body, "Could not issue fine.");
      fineError.hidden = false;
      return;
    }

    fineModal.close();
    pendingFineTarget = null;
  });

  document.addEventListener("vistadmin:ready", function (event) {
    const me = event.detail && event.detail.me;
    canIssueFine = !!(me && Array.isArray(me.permissions) && me.permissions.indexOf("fine.issue") !== -1);
    loadOverdue();
  });
})();