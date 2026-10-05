(function () {
  "use strict";

  const VA = window.VistAdmin;

  const statusFilter = document.getElementById("status-filter");
  const tbody = document.getElementById("requests-tbody");
  const emptyState = document.getElementById("requests-empty");

  const decisionModal = document.getElementById("decision-modal");
  const decisionModalTitle = document.getElementById("decision-modal-title");
  const decisionModalBook = document.getElementById("decision-modal-book");
  const decisionForm = document.getElementById("decision-form");
  const decisionNote = document.getElementById("decision-note");
  const decisionSubmit = document.getElementById("decision-submit");
  const decisionModalError = document.getElementById("decision-modal-error");
  const closeDecisionModalBtn = document.getElementById("close-decision-modal");

  let pendingAction = null; // { requestId, kind: "approve" | "reject" }

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  function statusPillHtml(status) {
    return '<span class="status-pill ' + (status || "").toLowerCase() + '">' + VA.escapeHtml(status) + "</span>";
  }

  async function loadRequests() {
    const params = new URLSearchParams();
    if (statusFilter.value) params.set("status", statusFilter.value);

    const result = await VA.fetchJson("/api/v1/library/requests?" + params.toString());
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load requests."));
      return;
    }
    VA.clearError();

    const items = result.body;
    tbody.innerHTML = "";
    if (items.length === 0) {
      emptyState.hidden = false;
      return;
    }
    emptyState.hidden = true;

    items.forEach(function (r) {
      const tr = document.createElement("tr");
      const isPending = r.status === "PENDING";
      const titleLabel = r.book_title + (r.is_journal ? " (Journal)" : "");

      let classInfo;
      if (r.borrower_type === "STUDENT") {
        classInfo = (r.borrower_program || "No program assigned") + (r.borrower_semester ? " \u00b7 Sem " + r.borrower_semester : "");
      } else {
        classInfo = (r.borrower_department || "Staff") + " (Staff)";
      }

      tr.innerHTML =
        "<td>" + VA.escapeHtml(titleLabel) + "</td>" +
        "<td>" + VA.escapeHtml(r.borrower_name) + (r.borrower_identifier ? ' <span class="panel-hint" style="display:inline">(' + VA.escapeHtml(r.borrower_identifier) + ")</span>" : "") + "</td>" +
        "<td>" + VA.escapeHtml(classInfo) + "</td>" +
        "<td>" + VA.escapeHtml(r.note || "\u2014") + "</td>" +
        "<td>" + VA.formatDateTime(r.created_at) + "</td>" +
        "<td>" + statusPillHtml(r.status) + "</td>" +
        '<td class="row-actions"></td>';

      if (isPending) {
        const actionsCell = tr.querySelector(".row-actions");
        const approveBtn = document.createElement("button");
        approveBtn.className = "role-btn small-btn";
        approveBtn.type = "button";
        approveBtn.textContent = "Approve";
        approveBtn.addEventListener("click", function () {
          openDecisionModal(r.id, "approve", titleLabel);
        });

        const rejectBtn = document.createElement("button");
        rejectBtn.className = "danger-btn small-btn";
        rejectBtn.type = "button";
        rejectBtn.textContent = "Reject";
        rejectBtn.addEventListener("click", function () {
          openDecisionModal(r.id, "reject", titleLabel);
        });

        actionsCell.appendChild(approveBtn);
        actionsCell.appendChild(rejectBtn);
      }

      tbody.appendChild(tr);
    });
  }

  function openDecisionModal(requestId, kind, titleLabel) {
    pendingAction = { requestId: requestId, kind: kind };
    decisionModalTitle.textContent = kind === "approve" ? "Approve Request" : "Reject Request";
    decisionModalBook.textContent = titleLabel;
    decisionNote.value = "";
    decisionModalError.hidden = true;
    decisionSubmit.textContent = kind === "approve" ? "Approve & Issue" : "Reject";
    decisionModal.showModal();
  }

  closeDecisionModalBtn.addEventListener("click", function () {
    decisionModal.close();
    pendingAction = null;
  });

  decisionForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!pendingAction) return;
    decisionSubmit.disabled = true;
    decisionModalError.hidden = true;

    const path = "/api/v1/library/requests/" + pendingAction.requestId + "/" + pendingAction.kind;
    const result = await VA.fetchJson(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: decisionNote.value.trim() || null }),
    });

    decisionSubmit.disabled = false;

    if (!result.ok) {
      decisionModalError.textContent = extractErrorMessage(result.body, "Could not save decision.");
      decisionModalError.hidden = false;
      return;
    }

    decisionModal.close();
    pendingAction = null;
    loadRequests();
  });

  statusFilter.addEventListener("change", loadRequests);

  document.addEventListener("vistadmin:ready", function () {
    loadRequests();
  });
})();