(function () {
  "use strict";

  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/payroll";
  const $ = function (id) { return document.getElementById(id); };
  const money = function (v) { return VA.formatMoney(v); };
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

  // ---- my own attendance (arrivals, leavings, late marks)
  async function loadAttendance() {
    const m = $("ma-month").value;
    const r = await VA.fetchJson("/api/v1/staff-attendance/me" + (m ? "?month=" + m + "-01" : ""));
    const tb = $("ma-table").tBodies[0];
    if (!r.ok) {
      if (r.status === 422) { $("my-att").hidden = true; return; }      // not a staff record (e.g. an admin-only sign-in)
      tb.innerHTML = '<tr><td colspan="6">Could not load your attendance. <button type="button" class="link-btn" id="ma-retry">Try again</button></td></tr>';
      $("ma-retry").addEventListener("click", loadAttendance);
      return;
    }
    const s = r.body.summary || {};
    $("ma-summary").innerHTML = [["Working days", s.scheduled], ["Present", s.present], ["Excused", s.excused], ["Absent", (s.absent_dates || []).length],
      ["Late", s.late], ["Left early", s.early], ["No leaving mark", s.missing_out]].map(function (x) {
      return '<div class="kpi"><div class="kpi-label">' + esc(x[0]) + '</div><div class="kpi-value">' + esc(String(x[1] == null ? 0 : x[1])) + "</div></div>";
    }).join("");
    const days = (r.body.days || []).filter(Boolean);
    $("ma-empty").hidden = days.length > 0;
    tb.innerHTML = days.map(function (d) {
      const note = d.excused ? "Excused: " + (d.excuse_reason || "") : (!d.is_working_day ? "Off day" : (d.note || ""));
      return "<tr><td>" + esc(day(d.work_date)) + "</td><td>" + esc(d.check_in || "\u2014") + "</td><td>" + esc(d.check_out || "\u2014") + "</td><td>" +
        (d.late_minutes || "") + "</td><td>" + (d.early_minutes || "") + "</td><td>" + esc(note) + "</td></tr>";
    }).join("");
  }
  (function () {
    const now = new Date();
    $("ma-month").value = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0");
    $("ma-month").addEventListener("change", loadAttendance);
  })();
  document.addEventListener("vistadmin:ready", loadAttendance);

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