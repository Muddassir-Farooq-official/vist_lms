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

  let rows = [];
  const money = function (v) { return "Rs. " + Number(v || 0).toLocaleString("en-PK", { maximumFractionDigits: 0 }); };
  const fmtDate = function (iso) { return new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }); };

  async function loadOverdue() {
    const result = await VA.fetchJson("/api/v1/library/issues/overdue/detailed");
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load overdue loans."));
      return;
    }
    VA.clearError();
    const d = result.body;
    rows = d.items;
    document.getElementById("overdue-summary").textContent = rows.length
      ? rows.length + " book" + (rows.length === 1 ? "" : "s") + " overdue with " + d.borrowers + " borrower" + (d.borrowers === 1 ? "" : "s") +
        " · late fees so far " + money(d.total_late_fees) + " (" + money(d.late_fee_per_day) + " a day" + (d.late_fee_max > 0 ? ", at most " + money(d.late_fee_max) : "") + ")"
      : "";
    document.getElementById("overdue-remind-all").hidden = !rows.length;
    render();
  }

  function render() {
    const q = (document.getElementById("overdue-q").value || "").trim().toLowerCase();
    const shown = rows.filter(function (r) { return !q || [r.book_title, r.accession_code, r.borrower_name, r.borrower_code].join(" ").toLowerCase().indexOf(q) !== -1; });
    tbody.innerHTML = "";
    emptyState.hidden = shown.length > 0;
    emptyState.textContent = rows.length ? "Nothing matches the search." : "Nothing overdue right now.";
    shown.forEach(function (issue) {
      const overdue = Math.max(1, issue.days_overdue || daysOverdue(issue.due_date));
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td><strong>" + VA.escapeHtml(issue.book_title) + "</strong><br><small>Copy " + VA.escapeHtml(issue.accession_code) + "</small></td>" +
        "<td>" + VA.escapeHtml(issue.borrower_name) + "<br><small>" + VA.escapeHtml([issue.borrower_type === "STUDENT" ? "Student" : "Staff", issue.borrower_code,
          issue.borrower_phone].filter(Boolean).join(" · ")) + (issue.borrower_phone ? ' · <a href="tel:' + VA.escapeHtml(issue.borrower_phone) + '">call</a>' : "") + "</small></td>" +
        "<td>" + VA.escapeHtml(fmtDate(issue.due_date)) + '<br><span class="status-pill overdue">' + overdue + " day" + (overdue === 1 ? "" : "s") + " late</span></td>" +
        '<td class="num">' + money(issue.late_fee) + "</td>" +
        '<td class="row-actions"></td>';
      const acts = tr.querySelector(".row-actions");
      if (issue.has_login) {
        const rb = document.createElement("button");
        rb.className = "role-btn small-btn secondary-btn"; rb.type = "button"; rb.textContent = "Remind";
        rb.addEventListener("click", function () { remind([issue.id], rb); });
        acts.appendChild(rb);
      }
      const ret = document.createElement("a");
      ret.className = "role-btn small-btn secondary-btn"; ret.href = "/dashboard/library/issue-return#return=" + encodeURIComponent(issue.accession_code); ret.textContent = "Return…";
      acts.appendChild(ret);
      if (canIssueFine) {
        const btn = document.createElement("button");
        btn.className = "danger-btn small-btn";
        btn.type = "button";
        btn.textContent = "Issue Fine";
        btn.addEventListener("click", function () {
          openFineModal(issue);
        });
        acts.appendChild(btn);
      }
      tbody.appendChild(tr);
    });
  }

  async function remind(ids, btn) {
    if (btn) btn.disabled = true;
    const r = await VA.fetchJson("/api/v1/library/issues/overdue/remind", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ issue_ids: ids }) });
    if (btn) btn.disabled = false;
    if (!r.ok) return VA.toast(extractErrorMessage(r.body, "Could not send the reminders."), "error");
    const x = r.body;
    VA.toast(x.sent ? x.sent + " reminder" + (x.sent === 1 ? "" : "s") + " sent (in the app and by email)." + (x.skipped_recent ? " " + x.skipped_recent + " already reminded today." : "")
      : x.skipped_recent ? "Already reminded today." : "Nobody to remind (no login).");
  }
  document.getElementById("overdue-q").addEventListener("input", render);
  document.getElementById("overdue-remind-all").addEventListener("click", async function () {
    if (!(await VA.confirm("Remind every borrower with an overdue book? Each gets a notification and an email. Anyone reminded about the same book today is skipped.", "Remind all"))) return;
    remind([], this);
  });

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