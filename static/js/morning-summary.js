(function () {
  "use strict";
  // Morning summary page (GET /api/v1/morning-summary): the same figures the 8 AM notice sends.
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const money = function (v) { return VA.formatMoney(v); };
  const kpi = function (label, value, href) {
    const inner = '<div class="kpi-label">' + esc(label) + '</div><div class="kpi-value">' + esc(value) + "</div>";
    return href ? '<a class="kpi" href="' + href + '">' + inner + "</a>" : '<div class="kpi">' + inner + "</div>";
  };
  const LINKS = { approvals: ["approval", "approvals"], profile_changes: ["profile change", "profile-changes"],
    certificates: ["certificate request", "certificates"], result_sheets: ["result sheet to approve", "result-sheets"], applications: ["application", "applications"],
    money_review: ["money change to review", "activity#money"] };

  VA.fetchJson("/api/v1/morning-summary").then(function (r) {
    if (!r.ok) { $("ms-when").textContent = (r.body && r.body.error && r.body.error.message) || "Could not load the summary."; return; }
    const d = r.body;
    $("ms-when").textContent = "Looking back on " + d.label + "." + (d.last_sent ? " Last sent: " + d.last_sent.split(":")[0] + "." : "");
    const m = d.money;
    $("ms-money").innerHTML = m ? kpi("Received", money(m.received), "finance-reports") + kpi("Payments", m.payments) +
      kpi("Overdue now", money(m.overdue), "fee-defaulters") + kpi("Students overdue", m.defaulters, "fee-defaulters") +
      kpi("Promises due today", m.promises_today, "fee-defaulters") : '<p class="panel-hint">Not available.</p>';
    const a = d.attendance;
    $("ms-att").innerHTML = a ? kpi("Classes held", a.classes, "attendance") + kpi("Average attendance", a.average == null ? "—" : a.average + "%", "attendance") +
      kpi("Classes not marked", a.not_marked, "attendance") + kpi("Staff absent", a.staff.absent, "staff-attendance") +
      kpi("Staff late", a.staff.late, "staff-attendance") + kpi("Staff on leave", a.staff.leave, "staff-attendance") : '<p class="panel-hint">Not available.</p>';
    const w = d.waiting || {};
    const items = Object.keys(LINKS).filter(function (k) { return w[k]; }).map(function (k) {
      return '<li><a href="' + LINKS[k][1] + '"><b>' + w[k] + "</b> " + esc(LINKS[k][0] + (w[k] === 1 ? "" : "s")) + "</a></li>";
    });
    $("ms-wait").innerHTML = items.join("") || '<li class="sd-dim">Nothing waiting.</li>';
    $("ms-prob").innerHTML = (d.problems || []).map(function (p) { return '<li class="ms-bad">' + esc(p) + "</li>"; }).join("") || '<li class="sd-dim">None.</li>';
  });
})();
