/* Principal / admin -> Monthly report (/api/v1/reports/monthly). */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/reports/monthly";

  function money(v) { return "Rs. " + Math.round(v || 0).toLocaleString("en-PK"); }
  function pct(v) { return v == null ? "—" : v + "%"; }
  function kpis(id, items) {
    $(id).innerHTML = items.map(function (x) { return '<div class="kpi"><div class="kpi-label">' + esc(x[0]) + '</div><div class="kpi-value">' + esc(String(x[1])) + "</div></div>"; }).join("");
  }
  function rows(id, list, cells, empty, firstText) {
    const tb = $(id).tBodies[0];
    const n = $(id).tHead.rows[0].cells.length;
    tb.innerHTML = list.length ? list.map(function (r) {
      return "<tr>" + cells(r).map(function (v, i) { return "<td" + (i >= (firstText || 1) ? ' class="num"' : "") + ">" + esc(String(v)) + "</td>"; }).join("") + "</tr>";
    }).join("") : '<tr><td colspan="' + n + '">' + esc(empty) + "</td></tr>";
  }
  function lastMonth() {
    const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1);
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0");
  }

  async function load() {
    const m = $("mr-month").value || lastMonth();
    $("mr-pdf").href = API + "/pdf?month=" + encodeURIComponent(m);
    const r = await VA.fetchJson(API + "?month=" + encodeURIComponent(m));
    if (!r.ok) { $("mr-error").textContent = (r.body && r.body.error && r.body.error.message) || "Could not load the report."; $("mr-error").hidden = false; return; }
    $("mr-error").hidden = true;
    const d = r.body;
    $("mr-partial").hidden = d.complete;
    $("mr-partial").textContent = d.complete ? "" : "This month is not over yet: the figures are up to today.";
    const f = d.fees;
    kpis("mr-fees-kpis", [["Received this month", money(f.received)], ["Payments", f.payments], ["Still owed today", money(f.outstanding)],
      ["Overdue today", money(f.overdue)], ["Students overdue", f.defaulters]]);
    $("mr-fees-note").textContent = f.taken_back ? "Received " + money(f.received) + ", less " + money(f.taken_back) + " paid back or reversed: net " + money(f.net) + "." : "";
    rows("mr-heads", f.by_head, function (h) { return [h.head, h.students, money(h.payable), money(h.paid), money(h.outstanding), money(h.overdue), pct(h.collected_pct)]; }, "No fees yet.");
    rows("mr-defaulters", f.top_defaulters, function (x) { return [x.name, x.roll_number, money(x.overdue), x.days_overdue]; }, "Nobody has an overdue fee.", 2);
    const c = d.classes;
    kpis("mr-classes-kpis", [["Classes held", c.held], ["Average attendance", pct(c.average)], ["Students below 75% in a class", c.low]]);
    rows("mr-teachers", c.teachers, function (t) { return [t.teacher, t.classes, t.held, pct(t.average), t.low]; }, "No attendance was taken this month.");
    rows("mr-classes", c.classes, function (x) { return [x.course, x.section, x.teacher, x.held, pct(x.average), x.low]; }, "No attendance was taken this month.", 3);
    const res = d.results;
    kpis("mr-results-kpis", [["Results published", res.results], ["Passed", res.passed], ["Pass rate", pct(res.pass_rate)]]);
    rows("mr-results", res.courses, function (x) { return [x.course, x.section, x.results, x.passed, x.failed, pct(x.pass_rate), pct(x.average)]; }, "No results were published this month.", 2);
    const s = d.staff;
    kpis("mr-staff-kpis", [["Staff", s.staff], ["Present", pct(s.present_rate)], ["Days absent", s.absent_days], ["Late arrivals", s.late], ["Leave days", s.leave_days]]);
    $("mr-leave").textContent = (s.leave_by_kind.length ? "Leave approved: " + s.leave_by_kind.map(function (k) { return k.kind + ": " + k.requests + " request(s), " + k.days + " day(s)"; }).join("; ") + "." : "No leave approved this month.")
      + (s.leave_pending ? " Waiting for a decision: " + s.leave_pending + "." : "");
    rows("mr-staff", s.people, function (p) { return [p.name, p.job, p.working_days, p.present, p.leave, p.absent, p.late, p.early]; }, "No staff records.", 2);
  }

  document.addEventListener("vistadmin:ready", function () {
    const now = new Date();
    $("mr-month").max = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0");
    $("mr-month").value = new URLSearchParams(location.search).get("month") || lastMonth();
    $("mr-month").addEventListener("change", load);
    load();
  });
})();
