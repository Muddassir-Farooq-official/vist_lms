(function () {
  "use strict";

  const VA = window.VistAdmin;
  const SU = window.StudentUI;
  const esc = VA.escapeHtml;

  const SOURCE_LABEL = { LIBRARY: "Library", ATTENDANCE: "Attendance", GATE: "Late arrival at the gate", MANUAL: "Issued by the office", OTHER: "Other", FEE_LATE_PAYMENT: "Late fee payment" };

  function extractErrorMessage(body, fallback) {
    return body && body.error && body.error.message ? body.error.message : fallback;
  }

  async function loadMyFines() {
    const [balance, list] = await Promise.all([VA.fetchJson("/api/v1/fines/me/balance"), VA.fetchJson("/api/v1/fines/me")]);
    if (balance.ok) {
      document.getElementById("fine-stat-pending").textContent = SU.money(balance.body.total_pending);
      document.getElementById("fine-stat-paid").textContent = SU.money(balance.body.total_paid);
      document.getElementById("fine-stat-waived").textContent = SU.money(balance.body.total_waived);
    } else {
      VA.showError(extractErrorMessage(balance.body, "Could not load your fine balance."));
    }
    const grid = document.getElementById("my-fines-grid");
    const empty = document.getElementById("my-fines-empty");
    grid.innerHTML = "";
    if (!list.ok) {
      grid.innerHTML = '<p class="home-empty">' + esc(extractErrorMessage(list.body, "Could not load your fines.")) + "</p>";
      return;
    }
    empty.hidden = list.body.length > 0;
    list.body.forEach(function (f) {
      const card = document.createElement("article");
      const status = String(f.status || "").toLowerCase();
      card.className = "fine-card " + status;
      card.innerHTML =
        '<div class="fine-main"><strong>' + esc(f.reason) + '</strong><span class="fine-meta">' + esc(SOURCE_LABEL[f.source] || f.source) +
        (f.issued_at ? " \u00b7 Issued " + esc(VA.formatDateLong(String(f.issued_at).slice(0, 10))) : "") + "</span>" +
        (status === "pending" ? '<span class="fine-hint">Pay at the accounts office. Unpaid fines also stop you borrowing books.</span>' : "") + "</div>" +
        '<div class="fine-side"><span class="fine-amount">' + esc(SU.money(f.amount)) + "</span>" + SU.pill(f.status) + "</div>";
      grid.appendChild(card);
    });
  }

  document.addEventListener("vistadmin:ready", loadMyFines);
})();