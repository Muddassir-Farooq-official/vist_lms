(function () {
  "use strict";

  // Exam department dashboard. Read-only: result sheets by step, outstanding courses, datesheets and supplementary exams.
  const D = window.Dash, VA = window.VistAdmin, esc = D.esc;
  const $ = function (id) { return document.getElementById(id); };
  const today = D.iso(new Date());
  const STEPS = [["DRAFT", "Draft"], ["SUBMITTED", "Waiting"], ["RETURNED", "Sent back"], ["APPROVED", "Approved"], ["PUBLISHED", "Published"]];

  function count(sheets, st) { return (sheets || []).filter(function (s) { return s.status === st; }).length; }
  function ago(iso) {
    if (!iso) return "";
    const h = Math.floor((Date.now() - new Date(iso).getTime()) / 36e5);
    return h < 1 ? "just now" : h < 24 ? h + " h ago" : Math.floor(h / 24) + " days ago";
  }

  function kpis(d, papers) {
    const s = d.sheets || [], out = d.outstanding || [];
    const sub = count(s, "SUBMITTED"), appr = count(s, "APPROVED"), pub = count(s, "PUBLISHED");
    D.set("k-sub", D.num(sub));
    const oldest = s.filter(function (x) { return x.status === "SUBMITTED"; }).map(function (x) { return x.submitted_at; }).sort()[0];
    D.set("k-sub-sub", sub ? "oldest sent " + ago(oldest) : "Nothing waiting");
    D.set("k-appr", D.num(appr));
    D.set("k-appr-sub", appr ? "students cannot see them yet" : "Nothing to publish");
    D.set("k-pub", D.num(pub));
    D.set("k-pub-sub", "result sheets students can see");
    D.set("k-out", D.num(out.length));
    const returned = out.filter(function (o) { return o.status === "RETURNED"; }).length;
    D.set("k-out-sub", out.length ? (returned ? returned + " sent back · " : "") + "marks still with teachers" : "Every course submitted");
    D.bad("k-out-sub", out.length);
    const p = papers[0];
    D.set("k-next", p ? new Date(p.exam_date + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "—");
    D.set("k-next-sub", p ? p.course_code + " · " + p.start_time + " · " + (p.room || "") : "No papers scheduled");
    const el = d.eligible || [], at = d.attempts || [];
    D.set("k-supp", D.num(at.length));
    D.set("k-supp-sub", el.length ? D.num(el.length) + " students may sit one" : "registered this term");
  }

  function waiting(d) {
    const s = d.sheets || [];
    const all = {}; s.forEach(function (x) { all[x.status] = (all[x.status] || 0) + 1; });
    all.DRAFT = (all.DRAFT || 0) + (d.outstanding || []).filter(function (o) { return o.status === "DRAFT" || !o.status; }).length;
    $("res-pipeline").innerHTML = STEPS.map(function (st) {
      const n = all[st[0]] || 0;
      return '<li class="' + (n ? "has" : "") + (st[0] === "RETURNED" && n ? " warn" : "") + '"><b>' + D.num(n) + "</b><span>" + st[1] + "</span></li>";
    }).join("");
    const list = s.filter(function (x) { return x.status === "SUBMITTED" || x.status === "APPROVED"; })
      .sort(function (a, b) { return (a.status === "SUBMITTED" ? 0 : 1) - (b.status === "SUBMITTED" ? 0 : 1) || String(a.submitted_at).localeCompare(String(b.submitted_at)); }).slice(0, 6);
    $("wait-empty").hidden = list.length > 0;
    D.people("wait-list", list.map(function (x) {
      return { title: x.course_code + " · " + x.course_title, sub: (x.teacher_name || "No teacher") + " · " + x.students + " students · " + x.term_label + (x.resubmission_count ? " · resubmitted" : ""),
        value: x.status === "SUBMITTED" ? "Approve" : "Publish", bad: x.status === "SUBMITTED", href: "/dashboard/exam#" + x.status };
    }));
  }

  function attention(d, papers) {
    const s = d.sheets || [], out = d.outstanding || [], items = [];
    items.push({ count: count(s, "SUBMITTED"), text: "result sheets waiting for your approval", href: "/dashboard/exam#SUBMITTED", tone: "red" });
    items.push({ count: count(s, "APPROVED"), text: "approved result sheets not published yet", href: "/dashboard/exam#APPROVED", tone: "blue" });
    items.push({ count: out.filter(function (o) { return o.status === "RETURNED"; }).length, text: "sheets sent back and not resubmitted", href: "/dashboard/exam#RETURNED" });
    items.push({ count: out.filter(function (o) { return !o.assessments; }).length, text: "courses have no assessments set up (no marks can be entered)", href: "/dashboard/exam#OUTSTANDING" });
    items.push({ count: out.filter(function (o) { return o.missing_students; }).length, text: "courses have students with missing marks", href: "/dashboard/exam#OUTSTANDING", tone: "blue" });
    const drafts = (d.schedules || []).filter(function (x) { return x.status !== "PUBLISHED"; }).length;
    items.push({ count: drafts, text: "datesheets are drafts: students cannot see them yet", href: "/dashboard/exam/datesheets", tone: "blue" });
    const soon = papers.filter(function (p) { return p.exam_date <= D.iso(D.addDays(2)); }).length;
    items.push({ count: soon, text: "exam papers in the next 2 days: admit cards ready?", href: "/dashboard/exam/datesheets" });
    items.push({ count: (d.eligible || []).length, text: "students failed on marks and may sit a supplementary", href: "/dashboard/exam/supplementary", tone: "blue" });
    if (d.tasks) {
      items.push({ count: d.tasks.needs_response, text: "tasks waiting for your answer", href: "/dashboard/exam/tasks" });
      items.push({ count: d.tasks.overdue, text: "of your tasks are overdue", href: "/dashboard/exam/tasks", tone: "red" });
    }
    D.attention("attn-list", "attn-clear", items);
  }

  function chase(out) {
    if (!out) { D.hide("chase-list"); return; }
    const by = {};
    out.forEach(function (o) { const k = o.teacher_name || "No teacher assigned"; (by[k] = by[k] || []).push(o); });
    const rows = Object.keys(by).map(function (k) { return { name: k, list: by[k] }; }).sort(function (a, b) { return b.list.length - a.list.length; });
    $("chase-empty").hidden = rows.length > 0;
    D.people("chase-list", rows.slice(0, 6).map(function (r) {
      const ret = r.list.filter(function (o) { return o.status === "RETURNED"; }).length;
      return { title: r.name, sub: r.list.map(function (o) { return o.course_code + (o.status === "RETURNED" ? " (sent back)" : ""); }).join(", "),
        value: r.list.length + " course" + (r.list.length === 1 ? "" : "s"), bad: ret > 0, href: "/dashboard/exam#OUTSTANDING" };
    }));
  }

  function paperList(papers) {
    $("paper-empty").hidden = papers.length > 0;
    $("paper-list").innerHTML = papers.slice(0, 6).map(function (p) {
      const dt = new Date(p.exam_date + "T00:00:00");
      return '<li><span class="ev-date"><b>' + dt.getDate() + "</b>" + D.MONTHS[dt.getMonth()] + '</span><span class="ev-body"><strong>' + esc(p.course_code) + " · " + esc(p.course_title) +
        '</strong><small><span class="ev-kind kind-exam">' + esc(p.sheet) + "</span> " + esc(p.start_time) + "–" + esc(p.end_time) + (p.room ? " · " + esc(p.room) : "") +
        (p.section && p.section !== "A" ? " · Section " + esc(p.section) : "") + "</small></span></li>";
    }).join("");
  }

  function sheetsList(rows) {
    if (!rows) { D.hide("ds-list"); return; }
    $("ds-empty").hidden = rows.length > 0;
    D.people("ds-list", rows.slice(0, 4).map(function (s) {
      return { title: s.name, sub: s.term_label + (s.published_at ? " · published " + VA.formatDateTime(s.published_at) : " · draft"),
        value: s.status === "PUBLISHED" ? "Published" : "Draft", bad: s.status !== "PUBLISHED", href: "/dashboard/exam/datesheets" };
    }));
  }

  async function load(me) {
    D.greet("ex-greet", "ex-date", me);
    const terms = await D.get("/api/v1/academics/terms");
    const term = (terms || [])[0];
    D.chip("ex-term", term ? "Term: " + term : "", false);
    const q = term ? "?term_label=" + encodeURIComponent(term) : "";
    const want = {
      sheets: "/api/v1/results/sheets",
      outstanding: "/api/v1/results/sheets-outstanding",
      schedules: "/api/v1/exams/schedules",
      tasks: "/api/v1/tasks/summary",
      calendar: D.upcoming(),
    };
    if (term) { want.eligible = "/api/v1/supplementary/eligible" + q; want.attempts = "/api/v1/supplementary/attempts" + q; }
    const d = await D.getAll(want);
    if (!d.sheets && !d.outstanding) { const e = $("ex-error"); e.textContent = "Could not load the results. Please refresh the page."; e.hidden = false; }
    // Papers of every datesheet (published or not), from today on.
    const details = await Promise.all((d.schedules || []).slice(0, 6).map(function (s) { return D.get("/api/v1/exams/schedules/" + s.id); }));
    const papers = [];
    details.forEach(function (s) { if (s) (s.papers || []).forEach(function (p) { if (p.exam_date >= today) papers.push(Object.assign({ sheet: s.name }, p)); }); });
    papers.sort(function (a, b) { return (a.exam_date + a.start_time).localeCompare(b.exam_date + b.start_time); });
    D.run(function () { kpis(d, papers); });
    D.run(function () { waiting(d); });
    D.run(function () { attention(d, papers); });
    D.run(function () { chase(d.outstanding); });
    D.run(function () { paperList(papers); });
    D.run(function () { sheetsList(d.schedules); });
    D.run(function () { D.calendar("cal-list", "cal-empty", d.calendar, 3); });
  }

  document.addEventListener("vistadmin:ready", function (ev) { load(ev.detail && ev.detail.me); });
})();
