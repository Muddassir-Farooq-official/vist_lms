(function () {
  "use strict";

  // Parent portal: GET /api/v1/parents/me/children and /me/children/{id}, plus each child's datesheets. Read-only.
  const D = window.Dash, VA = window.VistAdmin, esc = D.esc;
  const $ = function (id) { return document.getElementById(id); };
  const picker = $("child-picker");
  const today = D.iso(new Date());
  let kids = [], current = null;

  function fmt(v) { return v === null || v === undefined ? "—" : Number(v).toFixed(2); }
  function shortDate(iso) { return iso ? new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—"; }
  function initials(n) { const w = String(n || "").split(/\s+/).filter(Boolean); return ((w[0] || "?")[0] + (w.length > 1 ? w[w.length - 1][0] : "")).toUpperCase(); }
  const STATUS = { PAID: ["Paid", "active"], PARTIAL: ["Part paid", "pending"], OVERDUE: ["Overdue", "rejected"], PENDING: ["Not due yet", "draft"], DUE: ["Due", "pending"], WAIVED: ["Waived", "draft"] };

  function tabs() {
    const box = $("child-tabs");
    box.hidden = kids.length < 2;
    box.innerHTML = kids.map(function (k) {
      return '<button type="button" role="tab" class="child-tab' + (k.id === current ? " active" : "") + '" aria-selected="' + (k.id === current) + '" data-id="' + esc(k.id) + '">' +
        '<span class="child-tab-av" aria-hidden="true">' + esc(initials(k.name)) + "</span><span>" + esc(k.name.split(" ")[0]) + "</span></button>";
    }).join("");
  }

  function header(st) {
    D.set("ch-avatar", initials(st.name));
    D.set("ch-name", st.name);
    D.set("ch-meta", "Roll " + (st.roll_number || "—") + " · " + (st.program || "—") + (st.semester ? " · Semester " + st.semester : ""));
    const s = $("ch-status");
    s.textContent = String(st.status || "").charAt(0) + String(st.status || "").slice(1).toLowerCase();
    s.className = "status-pill " + String(st.status || "").toLowerCase();
  }

  function kpis(d, papers) {
    const att = d.attendance || [];
    const held = att.reduce(function (n, a) { return n + a.total_sessions_taken; }, 0);
    const pres = att.reduce(function (n, a) { return n + a.present_count; }, 0);
    D.set("k-att", held ? Math.round(100 * pres / held) + "%" : "—");
    D.set("k-att-sub", held ? D.num(pres) + " of " + D.num(held) + " classes attended" : "No classes held yet");
    const low = att.filter(function (a) { return a.below_minimum; });
    D.set("k-low", D.num(low.length));
    D.set("k-low-sub", low.length ? "below " + d.attendance_minimum + "%: " + low.map(function (a) { return a.course_code; }).join(", ") : "All above " + d.attendance_minimum + "%");
    D.bad("k-low-sub", low.length);
    const r = d.results.summary;
    D.set("k-cgpa", fmt(r.cgpa));
    D.set("k-cgpa-sub", r.terms ? D.num(r.credits_earned) + " credit hours earned" : "No results published yet");
    const f = d.fees.summary;
    D.set("k-fees", D.money(f.total_to_pay || 0));
    D.set("k-fees-sub", (f.overdue > 0 ? D.money(f.overdue) + " overdue" : (f.total_to_pay ? "nothing overdue" : "All paid")) +
      (f.fines_unpaid > 0 ? " · incl. " + D.money(f.fines_unpaid) + " fines" : ""));
    D.bad("k-fees-sub", f.overdue > 0);
    const next = nextInstallment(d);
    D.set("k-next", next ? shortDate(next.due_date).replace(/ \d{4}$/, "") : "—");
    D.set("k-next-sub", next ? D.money(next.remaining) + " · " + (next.fee_name || "Fee") : "No upcoming instalment");
    const p = papers[0];
    D.set("k-exam", p ? shortDate(p.exam_date).replace(/ \d{4}$/, "") : "—");
    D.set("k-exam-sub", p ? p.course_code + (p.start_time ? " · " + VA.formatTimeShort(p.start_time) : "") : "No exams scheduled");
  }

  function installments(d) {
    const out = [];
    (d.fees.assignments || []).forEach(function (a) {
      (a.installments || []).forEach(function (i) { out.push(Object.assign({ fee_name: a.fee_name }, i)); });
    });
    return out.sort(function (a, b) { return String(a.due_date).localeCompare(String(b.due_date)); });
  }
  function nextInstallment(d) {
    return installments(d).filter(function (i) { return i.remaining > 0 && i.due_date >= today; })[0] || null;
  }

  function attendance(d) {
    const min = d.attendance_minimum;
    $("att-list").innerHTML = (d.attendance || []).map(function (a) {
      const pct = Math.round(a.percentage || 0);
      const tone = !a.total_sessions_taken ? "grey" : a.below_minimum ? "red" : pct < min + 5 ? "amber" : "green";
      return '<li><div class="prog-head"><strong>' + esc(a.course_code) + " · " + esc(a.course_title) + "</strong><b class=\"" + (tone === "red" ? "bad" : "") + '">' +
        (a.total_sessions_taken ? pct + "%" : "—") + '</b></div><div class="att-track"><span class="seg seg-' + tone + '" style="width:' + pct + '%"></span><i style="left:' + min + '%"></i></div>' +
        "<small>" + D.num(a.present_count) + " present · " + D.num(a.absent_count) + " absent" + (a.leave_count ? " · " + D.num(a.leave_count) + " on leave" : "") + " · of " + D.num(a.total_sessions_taken) + " classes</small></li>";
    }).join("") || '<li class="dash-empty">No current courses.</li>';
    D.set("att-note", "The line on each bar is the " + min + "% attendance needed to sit the final exam.");
  }

  function things(d, papers, name) {
    const first = name.split(" ")[0];
    const items = [];
    (d.attendance || []).filter(function (a) { return a.below_minimum; }).forEach(function (a) {
      items.push({ count: Math.round(a.percentage) + "%", text: first + "'s attendance in " + a.course_code + " is below " + d.attendance_minimum + "%. Please talk to the teacher.", href: "#pr-att-h", tone: "red" });
    });
    const f = d.fees.summary;
    if (f.overdue > 0) items.push({ count: "!", text: D.money(f.overdue) + " of fees is overdue" + (f.late_fee_accrued ? " (late fee so far " + D.money(f.late_fee_accrued) + ")" : "") + ". Pay with the challan at any bank.", href: "#pr-fee-h", tone: "red" });
    if (f.fines_unpaid > 0) items.push({ count: D.num(f.fines_count), text: (f.fines_count === 1 ? "unpaid fine: " : "unpaid fines: ") + D.money(f.fines_unpaid), href: "#pr-fee-h" });
    const next = nextInstallment(d);
    if (next && (new Date(next.due_date + "T00:00:00") - new Date(today + "T00:00:00")) / 864e5 <= 7) items.push({ count: "!", text: "Next instalment of " + D.money(next.remaining) + " is due on " + shortDate(next.due_date) + ".", href: "#pr-fee-h", tone: "blue" });
    if (papers.length) items.push({ count: papers.length, text: "exam papers coming up. Download the admit card below.", href: "#pr-exam-h", tone: "blue" });
    if (f.credit_balance > 0) items.push({ count: "!", text: D.money(f.credit_balance) + " paid in advance is held as credit for the next fee.", href: "#pr-fee-h", tone: "blue" });
    D.attention("attn-list", "attn-clear", items);
  }

  function fees(d) {
    const f = d.fees.summary;
    const payable = (f.charged || 0) - (f.concession || 0);
    const pct = payable > 0 ? Math.round(100 * (f.paid || 0) / payable) : null;
    $("fee-ring").style.setProperty("--p", pct == null ? 0 : pct);
    D.set("fee-ring-val", pct == null ? "—" : pct + "%");
    D.set("fee-ring-text", pct == null ? "No fees have been charged." : "paid: " + D.money(f.paid) + " of " + D.money(payable) + (f.concession ? " (after " + D.money(f.concession) + " scholarship)" : "") + ".");
    const list = installments(d);
    $("fee-empty").hidden = list.length > 0;
    $("fee-list").innerHTML = list.map(function (i) {
      const s = STATUS[i.status] || [i.status, String(i.status).toLowerCase()];
      const ch = i.challan && i.remaining > 0 ? '<a class="role-btn small-btn secondary-btn" href="/api/v1/fees/challans/' + encodeURIComponent(i.challan.id) + '/pdf" target="_blank" rel="noopener">Challan</a>' : "";
      return '<li><span class="inst-body"><strong>' + esc(i.fee_name || "Fee") + " · instalment " + esc(i.installment_number || "") + "</strong><small>Due " + esc(shortDate(i.due_date)) +
        (i.remaining > 0 ? " · " + esc(D.money(i.remaining)) + " to pay" : " · " + esc(D.money(i.paid)) + " paid") + (i.late_fee ? " · late fee " + esc(D.money(i.late_fee)) : "") +
        '</small></span><span class="status-pill ' + esc(s[1]) + '">' + esc(s[0]) + "</span>" + ch + "</li>";
    }).join("");
  }

  function exams(id, sheets) {
    const box = $("exam-body");
    $("exam-empty").hidden = !!(sheets && sheets.length);
    box.innerHTML = (sheets || []).map(function (s) {
      return '<div class="exam-sheet"><div class="exam-sheet-head"><strong>' + esc(s.name) + "</strong><small>" + esc(s.term_label) + "</small>" +
        '<a class="role-btn small-btn" target="_blank" rel="noopener" href="/api/v1/exams/children/' + encodeURIComponent(id) + "/admit-card/" + esc(s.id) + '/pdf">Admit card</a></div><ul class="event-list">' +
        (s.papers || []).map(function (p) {
          const dt = new Date(p.exam_date + "T00:00:00");
          return '<li class="' + (p.exam_date < today ? "past" : "") + '"><span class="ev-date"><b>' + dt.getDate() + "</b>" + D.MONTHS[dt.getMonth()] + '</span><span class="ev-body"><strong>' + esc(p.course_code) + " · " + esc(p.course_title) +
            "</strong><small>" + esc(VA.formatTimeShort(p.start_time)) + "–" + esc(VA.formatTimeShort(p.end_time)) + (p.room ? " · Room " + esc(p.room) : "") + "</small></span></li>";
        }).join("") + "</ul></div>";
    }).join("");
  }

  function results(d) {
    const terms = d.results.terms || [];
    $("res-body").innerHTML = terms.length ? terms.map(function (t) {
      return '<div class="res-term"><div class="res-term-head"><strong>' + esc(t.label) + '</strong><span class="dash-chip">GPA ' + fmt(t.gpa) + '</span></div><div class="table-scroll"><table class="dash-table"><thead><tr><th>Course</th><th class="num">Credits</th><th>Grade</th><th>Result</th></tr></thead><tbody>' +
        t.courses.map(function (c) {
          return "<tr><td>" + esc(c.course_code + " — " + (c.course_title || "")) + '</td><td class="num">' + esc(String(c.credit_hours)) + "</td><td><b>" +
            esc((c.letter_grade || "—") + (c.supplementary ? " (S)" : "")) + "</b></td><td>" + esc(c.outcome || "") + (c.replaced ? " (retaken)" : "") + "</td></tr>";
        }).join("") + "</tbody></table></div></div>";
    }).join("") + '<p class="panel-hint">CGPA ' + fmt(d.results.summary.cgpa) + " · credit hours earned " + esc(String(d.results.summary.credits_earned || 0)) + "</p>"
      : '<p class="dash-empty">No results have been published yet.</p>';
  }

  async function showChild(id) {
    current = id; tabs(); picker.value = id;
    const all = await D.getAll({ detail: "/api/v1/parents/me/children/" + encodeURIComponent(id), sheets: "/api/v1/exams/children/" + encodeURIComponent(id) + "/datesheets" });
    const d = all.detail;
    if (!d) { const e = $("pr-error"); e.textContent = "Could not load this child's details. Please refresh the page."; e.hidden = false; return; }
    $("pr-error").hidden = true;
    const papers = [];
    (all.sheets || []).forEach(function (s) { (s.papers || []).forEach(function (p) { if (p.exam_date >= today) papers.push(p); }); });
    papers.sort(function (a, b) { return (a.exam_date + a.start_time).localeCompare(b.exam_date + b.start_time); });
    $("child-view").hidden = false;
    D.run(function () { header(d.student || {}); });
    D.run(function () { kpis(d, papers); });
    D.run(function () { attendance(d); });
    D.run(function () { things(d, papers, (d.student && d.student.name) || ""); });
    D.run(function () { fees(d); });
    D.run(function () { exams(id, all.sheets); });
    D.run(function () { results(d); });
  }

  async function load(ev) {
    D.greet("pr-greet", "pr-date", ev && ev.detail && ev.detail.me);
    const all = await D.getAll({ kids: "/api/v1/parents/me/children", calendar: D.upcoming() });
    D.run(function () { D.calendar("cal-list", "cal-empty", all.calendar, 4); });
    kids = all.kids || [];
    if (!kids.length) { $("no-children").hidden = false; return; }
    picker.innerHTML = kids.map(function (k) { return '<option value="' + esc(k.id) + '">' + esc(k.name + " (" + (k.roll_number || "") + ")") + "</option>"; }).join("");
    picker.addEventListener("change", function () { showChild(picker.value); });
    $("child-tabs").addEventListener("click", function (e) { const b = e.target.closest("[data-id]"); if (b && b.getAttribute("data-id") !== current) showChild(b.getAttribute("data-id")); });
    showChild(kids[0].id);
  }

  document.addEventListener("vistadmin:ready", load);
})();