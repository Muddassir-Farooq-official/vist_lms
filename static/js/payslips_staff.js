(function () {
  "use strict";

  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/payroll";
  const $ = function (id) { return document.getElementById(id); };
  const money = function (v) { return "Rs. " + Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }); };
  const day = function (iso) { return iso ? new Date(String(iso).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "\u2014"; };
  const METHOD = { CASH: "cash", BANK_TRANSFER: "bank transfer", CHEQUE: "cheque", ONLINE: "online" };
  const rows = function (list, sign) { return list.length ? list.map(function (l) { return '<div class="auth-row"><span class="grow">' + esc(l.label) + '</span><span class="sched"><b>' + sign + money(l.amount) + "</b></span></div>"; }).join("") : '<p class="cc-meta">None</p>'; };

  async function open(id) {
    const r = await VA.fetchJson(API + "/payslips/" + id);
    if (!r.ok) return VA.toast("Could not open that payslip.", "error");
    const p = r.body;
    $("pv-title").textContent = "Salary slip: " + p.month_label;
    $("pv-meta").textContent = p.employee_name + " \u00b7 " + p.employee_code + (p.job_title ? " \u00b7 " + p.job_title : "") + (p.paid_date ? " \u00b7 paid " + day(p.paid_date) + (p.payment_method ? " by " + (METHOD[p.payment_method] || "") : "") : "") + (p.days_payable < p.days_in_month ? " \u00b7 " + p.days_payable + " of " + p.days_in_month + " days" : "");
    $("pv-earn").innerHTML = rows(p.earnings, "");
    $("pv-ded").innerHTML = rows(p.deduction_lines, "\u2212");
    $("pv-net").innerHTML = "<span>Net pay</span><strong>" + money(p.net) + "</strong>";
    $("pv-pdf").setAttribute("href", API + "/payslips/" + id + "/pdf"); $("pv-pdf").setAttribute("data-download", "payslip-" + p.employee_code + "-" + String(p.month).slice(0, 7) + ".pdf");
    $("pv-modal").showModal();
  }
  $("pv-close").addEventListener("click", function () { $("pv-modal").close(); });

  document.addEventListener("vistadmin:ready", async function () {
    const r = await VA.fetchJson(API + "/me");
    if (!r.ok) return;
    const box = $("ps-list"); box.innerHTML = "";
    r.body.forEach(function (s) {
      const row = document.createElement("div"); row.className = "auth-row";
      row.innerHTML = '<span class="grow"><strong>' + esc(s.month_label) + "</strong><small>Paid " + esc(day(s.paid_date)) + " \u00b7 earned " + money(s.gross) + " \u00b7 deductions " + money(s.deductions) + '</small></span><span class="sched"><b>' + money(s.net) + "</b></span>";
      const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn"; b.textContent = "View"; b.addEventListener("click", function () { open(s.id); });
      row.appendChild(b); box.appendChild(row);
    });
    $("ps-empty").hidden = r.body.length > 0;
  });
})();