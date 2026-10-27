(function () {
  "use strict";

  const VA = window.VistAdmin;
  let canManage = false;
  let canWaive = false;
  let searchDebounce = null;
  let selectedPayerLabel = "";

  const filterPayerType = document.getElementById("filter-payer-type");
  const filterStatus = document.getElementById("filter-status");
  const filterSource = document.getElementById("filter-source");
  const tbody = document.getElementById("fines-tbody");
  const emptyState = document.getElementById("fines-empty");

  const openIssueFineBtn = document.getElementById("open-issue-fine");
  const issueFineModal = document.getElementById("issue-fine-modal");
  const issueFineForm = document.getElementById("issue-fine-form");
  const finePayerType = document.getElementById("fine-payer-type");
  const finePayerSearch = document.getElementById("fine-payer-search");
  const finePayerResults = document.getElementById("fine-payer-results");
  const finePayerId = document.getElementById("fine-payer-id");
  const fineAmount = document.getElementById("fine-amount");
  const fineSource = document.getElementById("fine-source");
  const fineReason = document.getElementById("fine-reason");
  const issueFineSubmit = document.getElementById("issue-fine-submit");
  const issueFineError = document.getElementById("issue-fine-error");
  const issueFineSuccess = document.getElementById("issue-fine-success");
  const closeIssueFineModalBtn = document.getElementById("close-issue-fine-modal");

  const resolveFineModal = document.getElementById("resolve-fine-modal");
  const resolveFineTitle = document.getElementById("resolve-fine-title");
  const resolveFineSummary = document.getElementById("resolve-fine-summary");
  const resolveFineForm = document.getElementById("resolve-fine-form");
  const resolveFineNote = document.getElementById("resolve-fine-note");
  const resolveFineSubmit = document.getElementById("resolve-fine-submit");
  const resolveFineError = document.getElementById("resolve-fine-error");
  const closeResolveFineModalBtn = document.getElementById("close-resolve-fine-modal");

  let pendingResolve = null; // { fineId, kind: "mark-paid" | "waive" }

  function money(n) {
    return VA.formatMoney(n);
  }
  const SOURCE = { LIBRARY: "Library", DISCIPLINARY: "Disciplinary", FEE_LATE_PAYMENT: "Late fee", ATTENDANCE: "Attendance",
                   GATE: "Gate (late)", INVENTORY: "Store", HOSTEL: "Hostel", OTHER: "Other" };
  const STATUS = { PENDING: "Unpaid", PAID: "Paid", WAIVED: "Waived" };
  let allFines = [];

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  function statusPillHtml(status) {
    return '<span class="status-pill ' + (status || "").toLowerCase() + '">' + VA.escapeHtml(STATUS[status] || status) + "</span>";
  }

  async function loadFines() {
    const params = new URLSearchParams();
    if (filterPayerType.value) params.set("payer_type", filterPayerType.value);
    if (filterStatus.value) params.set("status", filterStatus.value);
    if (filterSource.value) params.set("source", filterSource.value);

    const result = await VA.fetchJson("/api/v1/fines?" + params.toString());
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load fines."));
      return;
    }
    VA.clearError();

    allFines = result.body;
    renderFines();
  }

  // Search works on the loaded list (the filters above it ask the server).
  function renderFines() {
    const q = (document.getElementById("fines-q").value || "").trim().toLowerCase();
    const items = allFines.filter(function (f) {
      return !q || [f.payer_name, f.payer_identifier, f.reason, f.receipt_number].join(" ").toLowerCase().indexOf(q) !== -1;
    });
    const total = items.reduce(function (n, f) { return n + Number(f.amount || 0); }, 0);
    const people = new Set(items.map(function (f) { return f.payer_type + ":" + (f.payer_identifier || f.payer_name); })).size;
    document.getElementById("fines-summary").innerHTML = items.length
      ? '<span class="crs-chip"><b>' + items.length + "</b> fine" + (items.length === 1 ? "" : "s") + "</span>" +
        '<span class="crs-chip"><b>' + VA.escapeHtml(money(total)) + "</b> in total</span>" +
        '<span class="crs-chip"><b>' + people + "</b> " + (people === 1 ? "person" : "people") + "</span>" : "";
    tbody.innerHTML = "";
    document.getElementById("fines-table").hidden = items.length === 0;
    if (items.length === 0) {
      emptyState.hidden = false;
      return;
    }
    emptyState.hidden = true;

    items.forEach(function (f) {
      const tr = document.createElement("tr");
      const payerLabel = f.payer_name + (f.payer_identifier ? " (" + f.payer_identifier + ")" : "");
      tr.innerHTML =
        '<td><span class="vl-c"><strong>' + VA.escapeHtml(f.payer_name) + '</strong><small class="fn-sub">' + VA.escapeHtml([f.payer_identifier, f.payer_type === "STUDENT" ? "Student" : "Staff"].filter(Boolean).join(" \u00b7 ")) + "</small></span></td>" +
        '<td class="num"><strong>' + VA.escapeHtml(money(f.amount)) + "</strong></td>" +
        '<td class="fn-reason">' + VA.escapeHtml(f.reason) + "</td>" +
        '<td><span class="crs-chip">' + VA.escapeHtml(SOURCE[f.source] || f.source) + "</span></td>" +
        "<td>" + VA.formatDateTime(f.issued_at) + "</td>" +
        "<td>" + statusPillHtml(f.status) +
          (f.receipt_number ? '<small class="fn-sub"><a href="/api/v1/fines/' + encodeURIComponent(f.id) + '/receipt.pdf" target="_blank" rel="noopener">Receipt ' + VA.escapeHtml(f.receipt_number) + "</a></small>" : "") + "</td>" +
        '<td class="row-actions crs-actions"></td>';

      if (f.status === "PENDING" && (canManage || canWaive)) {
        const actionsCell = tr.querySelector(".row-actions");

        if (canManage) {
          const payBtn = document.createElement("button");
          payBtn.className = "role-btn small-btn";
          payBtn.type = "button";
          payBtn.textContent = "Mark paid";
          payBtn.addEventListener("click", function () {
            openResolveModal(f.id, "mark-paid", payerLabel + " \u2014 " + money(f.amount) + " (" + f.reason + ")");
          });
          actionsCell.appendChild(payBtn);
        }

        if (canWaive) {
          const waiveBtn = document.createElement("button");
          waiveBtn.className = "sd-view tt-del";
          waiveBtn.type = "button";
          waiveBtn.textContent = "Waive";
          waiveBtn.addEventListener("click", function () {
            openResolveModal(f.id, "waive", payerLabel + " \u2014 " + money(f.amount) + " (" + f.reason + ")");
          });
          actionsCell.appendChild(waiveBtn);
        }
      }

      tbody.appendChild(tr);
    });
  }

  function openResolveModal(fineId, kind, summary) {
    pendingResolve = { fineId: fineId, kind: kind };
    resolveFineTitle.textContent = kind === "mark-paid" ? "Mark Fine as Paid" : "Waive Fine";
    resolveFineSummary.textContent = summary;
    resolveFineNote.value = "";
    resolveFineError.hidden = true;
    document.querySelectorAll("#resolve-fine-form [data-paid-only]").forEach(function (el) { el.hidden = kind !== "mark-paid"; });
    document.getElementById("resolve-fine-method").value = "CASH";
    document.getElementById("resolve-fine-reference").value = "";
    document.getElementById("resolve-fine-paid-on").value = new Date().toISOString().slice(0, 10);
    resolveFineSubmit.textContent = kind === "mark-paid" ? "Confirm Paid" : "Confirm Waive";
    resolveFineModal.showModal();
  }

  closeResolveFineModalBtn.addEventListener("click", function () {
    resolveFineModal.close();
    pendingResolve = null;
  });

  resolveFineForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!pendingResolve) return;
    resolveFineSubmit.disabled = true;
    resolveFineError.hidden = true;

    const result = await VA.fetchJson("/api/v1/fines/" + pendingResolve.fineId + "/" + pendingResolve.kind, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(pendingResolve.kind === "mark-paid" ? {
        note: resolveFineNote.value.trim() || null,
        method: document.getElementById("resolve-fine-method").value,
        reference: document.getElementById("resolve-fine-reference").value.trim() || null,
        paid_on: document.getElementById("resolve-fine-paid-on").value || null,
      } : { note: resolveFineNote.value.trim() || null }),
    });

    resolveFineSubmit.disabled = false;

    if (!result.ok) {
      resolveFineError.textContent = extractErrorMessage(result.body, "Could not save.");
      resolveFineError.hidden = false;
      return;
    }

    resolveFineModal.close();
    pendingResolve = null;
    if (result.body && result.body.pending_approval) VA.toast(result.body.message);   // another administrator approves the waiver
    loadFines();
  });

  // ---- Issue Fine modal ----
  openIssueFineBtn.addEventListener("click", function () {
    issueFineForm.reset();
    finePayerId.value = "";
    finePayerResults.hidden = true;
    selectedPayerLabel = "";
    issueFineError.hidden = true;
    issueFineSuccess.hidden = true;
    issueFineModal.showModal();
  });

  closeIssueFineModalBtn.addEventListener("click", function () {
    issueFineModal.close();
  });

  finePayerType.addEventListener("change", function () {
    finePayerSearch.value = "";
    finePayerId.value = "";
    finePayerResults.hidden = true;
  });

  finePayerSearch.addEventListener("input", function () {
    finePayerId.value = "";
    clearTimeout(searchDebounce);
    const q = finePayerSearch.value.trim();
    if (q.length < 2) {
      finePayerResults.hidden = true;
      return;
    }
    searchDebounce = setTimeout(async function () {
      const params = new URLSearchParams({ payer_type: finePayerType.value, q: q });
      const result = await VA.fetchJson("/api/v1/fines/search-payers?" + params.toString());
      if (!result.ok) return;

      finePayerResults.innerHTML = "";
      if (result.body.length === 0) {
        finePayerResults.innerHTML = '<div class="search-result-item search-result-empty">No matches.</div>';
        finePayerResults.hidden = false;
        return;
      }
      result.body.forEach(function (person) {
        const item = document.createElement("div");
        item.className = "search-result-item";
        item.textContent = person.name + (person.identifier ? " (" + person.identifier + ")" : "");
        item.addEventListener("click", function () {
          finePayerId.value = person.id;
          finePayerSearch.value = item.textContent;
          finePayerResults.hidden = true;
        });
        finePayerResults.appendChild(item);
      });
      finePayerResults.hidden = false;
    }, 300);
  });

  issueFineForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    issueFineError.hidden = true;
    issueFineSuccess.hidden = true;

    if (!finePayerId.value) {
      issueFineError.textContent = "Search and select a person from the dropdown first.";
      issueFineError.hidden = false;
      return;
    }

    issueFineSubmit.disabled = true;

    const result = await VA.fetchJson("/api/v1/fines", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        payer_type: finePayerType.value,
        payer_id: finePayerId.value,
        amount: parseFloat(fineAmount.value),
        reason: fineReason.value.trim(),
        source: fineSource.value,
      }),
    });

    issueFineSubmit.disabled = false;

    if (!result.ok) {
      issueFineError.textContent = extractErrorMessage(result.body, "Could not issue fine.");
      issueFineError.hidden = false;
      return;
    }

    issueFineSuccess.textContent = "Fine issued against " + result.body.payer_name + ".";
    issueFineSuccess.hidden = false;
    issueFineForm.reset();
    finePayerId.value = "";
    loadFines();
  });

  filterPayerType.addEventListener("change", loadFines);
  filterStatus.addEventListener("change", loadFines);
  filterSource.addEventListener("change", loadFines);
  document.getElementById("fines-q").addEventListener("input", renderFines);

  document.addEventListener("vistadmin:ready", function (event) {
    const me = event.detail && event.detail.me;
    canManage = !!(me && Array.isArray(me.permissions) && me.permissions.indexOf("fine.manage") !== -1);
    canWaive = !!(me && Array.isArray(me.permissions) && me.permissions.indexOf("fine.waive") !== -1);
    loadFines();
  });
})();