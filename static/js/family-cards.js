(function () {
  "use strict";
  // Home cards (GET /api/v1/family/cards): a parent sees one card per child, a student their own -- today's classes and
  // attendance, fees to pay with the challan to pay now, the next exam and the latest result.
  const box = document.getElementById("family-cards");
  if (!box) return;
  const VA = window.VistAdmin || {};
  const esc = function (v) { return (VA.escapeHtml ? VA.escapeHtml : function (x) { return String(x).replace(/[&<>"']/g, function (c) { return "&#" + c.charCodeAt(0) + ";"; }); })(v == null ? "" : String(v)); };
  const money = function (v) { return "Rs. " + Number(v || 0).toLocaleString("en-PK", { maximumFractionDigits: 0 }); };
  const day = function (v) { return v ? new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) : ""; };
  const parent = box.getAttribute("data-who") === "parent";
  const get = VA.fetchJson ? VA.fetchJson : function (u) { return fetch(u, { credentials: "same-origin" }).then(function (r) { return r.json().then(function (b) { return { ok: r.ok, body: b }; }); }); };

  function one(c) {
    const t = c.today, f = c.fees;
    const marks = { PRESENT: '<span class="fam-ok">present</span>', ABSENT: '<span class="fam-bad">absent</span>', LEAVE: "on leave" };
    const classes = t.classes.length ? "<ul class=\"fam-list\">" + t.classes.map(function (x) {
      return "<li><b>" + esc(x.start) + "</b> " + esc(x.course_code) + " <small>" + esc(x.room || "") + "</small> " + (marks[x.attendance] || '<small class="sd-dim">not marked yet</small>') + "</li>";
    }).join("") + "</ul>" : '<p class="fam-dim">No class today.</p>';
    const pay = f.pay ? '<a class="role-btn fam-pay" href="/api/v1/fees/challans/' + f.pay.challan_id + '/pdf" data-download="challan.pdf">Pay now: challan ' + money(f.pay.amount) + "</a>" +
      (f.pay.consumer_number ? '<small class="fam-dim">1Bill / bank app: ' + esc(f.pay.consumer_number) + "</small>" : "") : "";
    const fees = f.to_pay > 0.5 ? "<p><b>" + money(f.to_pay) + "</b> to pay" + (f.overdue > 0.5 ? ' · <span class="fam-bad">' + money(f.overdue) + " overdue</span>" : "") +
      (f.next_due ? "<br><small>Next: " + money(f.next_due.amount) + " by " + esc(day(f.next_due.date)) + "</small>" : "") + "</p>" + pay : '<p class="fam-ok">Nothing to pay.</p>';
    const ex = c.next_exam ? "<p><b>" + esc(c.next_exam.course_code) + "</b> " + esc(c.next_exam.exam) + "<br><small>" + esc(day(c.next_exam.date)) + " " + esc(c.next_exam.start) +
      (c.next_exam.room ? " · " + esc(c.next_exam.room) : "") + (c.next_exam.days === 0 ? " · today" : " · in " + c.next_exam.days + " day(s)") + "</small></p>" : '<p class="fam-dim">No exam coming up.</p>';
    const r = c.latest_result ? "<p><b>" + esc(c.latest_result.course_code) + "</b>: " + esc(c.latest_result.grade) + " (" + Math.round(c.latest_result.percentage) + "%)" +
      (c.latest_result.outcome === "FAILED" ? ' <span class="fam-bad">failed</span>' : "") + "</p>" : '<p class="fam-dim">No result yet.</p>';
    return '<article class="fam-card">' + (parent || box.children.length ? "<h3>" + esc(c.name) + " <small>" + esc((c.program || "") + (c.semester ? " · Sem " + c.semester : "") + " · " + (c.roll_number || "")) + "</small></h3>" : "") +
      '<div class="fam-grid"><section><h4>Today' + (t.absent ? ' <span class="fam-bad">· absent in ' + t.absent + "</span>" : "") + "</h4>" + classes + "</section>" +
      "<section><h4>Fees</h4>" + fees + "</section><section><h4>Next exam</h4>" + ex + "</section><section><h4>Latest result</h4>" + r + "</section></div></article>";
  }

  get("/api/v1/family/cards").then(function (r) {
    if (!r.ok || !r.body || !r.body.length) { box.hidden = true; return; }
    box.innerHTML = (parent ? '<h2 class="fam-h">Your children today</h2>' : "") + r.body.map(one).join("");
  });
})();
