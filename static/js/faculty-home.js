(function () {
  "use strict";

  // Teacher home. Read-only: the teacher's own schedule, courses, assignments, result sheets and requests.
  const D = window.Dash, VA = window.VistAdmin, esc = D.esc;
  const $ = function (id) { return document.getElementById(id); };
  const today = D.iso(new Date());
  const t = function (s) { return VA.formatTimeShort(s); };
  const SHEET = { DRAFT: ["Marks open", "grey"], SUBMITTED: ["Waiting for approval", "blue"], RETURNED: ["Sent back", "red"], APPROVED: ["Approved", "green"], PUBLISHED: ["Published", "green"] };

  function nowHM() { const d = new Date(); return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); }
  function dayName(iso) {
    if (iso === today) return "Today";
    if (iso === D.iso(D.addDays(1))) return "Tomorrow";
    return new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric" });
  }

  function kpis(d, todays) {
    const ov = d.overview || [];
    const held = ov.reduce(function (n, c) { return n + c.classes_held; }, 0);
    const now = nowHM();
    const left = todays.filter(function (e) { return e.end_time.slice(0, 5) > now; });
    D.set("k-today", D.num(todays.length));
    D.set("k-today-sub", !todays.length ? "No classes today" : left.length ? "Next at " + t(left[0].start_time) + " · " + left[0].course_code : "All done for today");
    D.set("k-students", D.num(ov.reduce(function (n, c) { return n + c.enrolled_count; }, 0)));
    D.set("k-students-sub", "in " + ov.length + " course" + (ov.length === 1 ? "" : "s"));
    const rec = ov.filter(function (c) { return c.average_attendance != null && c.classes_held; });
    const avg = rec.length ? rec.reduce(function (n, c) { return n + c.average_attendance * c.classes_held; }, 0) / rec.reduce(function (n, c) { return n + c.classes_held; }, 0) : null;
    D.set("k-att", avg == null ? "—" : Math.round(avg) + "%");
    D.set("k-att-sub", avg == null ? "No classes marked yet" : "across " + D.num(held) + " classes held");
    const covered = ov.reduce(function (n, c) { return n + c.topics_covered; }, 0);
    D.set("k-topics", held ? Math.round(100 * covered / held) + "%" : "—");
    D.set("k-topics-sub", D.num(covered) + " of " + D.num(held) + " classes held");
    D.bad("k-topics-sub", held && covered / held < 0.8);
    const rev = (d.assignments || []).reduce(function (n, a) { return n + (a.awaiting_review || 0); }, 0);
    D.set("k-review", D.num(rev));
    D.set("k-review-sub", rev ? "hand-ins waiting for you" : "Nothing to check");
    const um = ov.reduce(function (n, c) { return n + c.unmarked_past_classes; }, 0);
    D.set("k-unmarked", D.num(um));
    D.set("k-unmarked-sub", um ? "ask the office to correct them" : "All classes marked");
    D.bad("k-unmarked-sub", um);
    const terms = Array.from(new Set(ov.map(function (c) { return c.term_label; })));
    D.chip("fh-term", terms.length ? "Term: " + terms.join(", ") : "", false);
  }

  function classItem(e, opts) {
    const started = e.session_date < today || (e.session_date === today && e.start_time.slice(0, 5) <= nowHM());
    const live = e.session_date === today && e.start_time.slice(0, 5) <= nowHM() && e.end_time.slice(0, 5) > nowHM();
    const done = e.session_date === today && e.end_time.slice(0, 5) <= nowHM();
    const href = started ? "/dashboard/faculty/attendance?offering=" + e.course_offering_id + "&session=" + e.id : "/dashboard/faculty/courses?offering=" + e.course_offering_id;
    const btn = opts.compact ? "" : '<a class="role-btn small-btn' + (started ? "" : " secondary-btn") + '" href="' + href + '">' + (started ? "Take attendance" : e.title ? "Lesson plan" : "Add topic") + "</a>";
    return '<li class="' + (live ? "live" : done ? "done" : "") + '"><span class="ag-time"><b>' + esc(opts.compact ? dayName(e.session_date) : t(e.start_time)) + "</b>" +
      esc(opts.compact ? t(e.start_time) : t(e.end_time)) + '</span><span class="ag-body"><strong>' + esc(e.course_code) + " · " + esc(e.course_title) +
      (live ? ' <span class="ev-kind kind-holiday">Now</span>' : "") + "</strong><small>Room " + esc(e.room) +
      (e.section_label && e.section_label !== "A" ? " · Section " + esc(e.section_label) : "") + " · " +
      (e.title ? esc(e.title) : '<span class="ag-missing">No topic yet</span>') + "</small></span>" + btn + "</li>";
  }

  function schedule(rows) {
    if (!rows) { D.hide("today-list"); return []; }
    const sorted = rows.slice().sort(function (a, b) { return (a.session_date + a.start_time).localeCompare(b.session_date + b.start_time); });
    const todays = sorted.filter(function (e) { return e.session_date === today; });
    const next = sorted.filter(function (e) { return e.session_date > today; }).slice(0, 4);
    $("today-empty").hidden = todays.length > 0;
    $("today-list").innerHTML = todays.map(function (e) { return classItem(e, {}); }).join("");
    $("next-empty").hidden = next.length > 0;
    $("next-list").innerHTML = next.map(function (e) { return classItem(e, { compact: true }); }).join("");
    // The next seven days: how many classes on each.
    const days = [];
    for (let i = 0; i < 7; i++) days.push(D.iso(D.addDays(i)));
    const max = Math.max.apply(null, days.map(function (x) { return sorted.filter(function (e) { return e.session_date === x; }).length; }).concat([1]));
    $("week-strip").innerHTML = days.map(function (x, i) {
      const n = sorted.filter(function (e) { return e.session_date === x; }).length;
      const dt = new Date(x + "T00:00:00");
      return '<div class="wk-day' + (i === 0 ? " is-today" : "") + '"><span class="wk-name">' + dt.toLocaleDateString("en-GB", { weekday: "short" }) + '</span><span class="wk-bar"><span style="height:' +
        (n ? Math.max(18, Math.round(100 * n / max)) : 0) + '%"></span></span><b>' + n + "</b></div>";
    }).join("");
    return todays;
  }

  function attention(d, rows) {
    const items = [];
    (d.overview || []).forEach(function (c) {
      if (c.unmarked_past_classes) items.push({ count: c.unmarked_past_classes, text: c.course_code + ": classes never marked (ask the office for a correction)", href: "/dashboard/faculty/attendance#requests", tone: "red" });
      if (c.average_attendance != null && c.average_attendance < 75) items.push({ count: Math.round(c.average_attendance) + "%", text: c.course_code + ": class attendance is low", href: "/dashboard/faculty/attendance" });
    });
    const soon = D.iso(D.addDays(7));
    const noTopic = (rows || []).filter(function (e) { return e.session_date >= today && e.session_date <= soon && !(e.title || "").trim(); }).length;
    items.push({ count: noTopic, text: "classes in the next 7 days have no topic yet", href: "/dashboard/faculty/courses", tone: "blue" });
    const rev = (d.assignments || []).reduce(function (n, a) { return n + (a.awaiting_review || 0); }, 0);
    items.push({ count: rev, text: "assignment hand-ins waiting for you to check", href: "/dashboard/faculty/assignments" });
    (d.sheets || []).forEach(function (s) {
      if (!s) return;
      if (s.status === "RETURNED") items.push({ count: "!", text: s.course_code + ": marks were sent back" + (s.return_reason ? " — " + s.return_reason : ""), href: "/dashboard/faculty/grading", tone: "red" });
      else if (s.can_submit) items.push({ count: "!", text: s.course_code + ": marks are complete and can be submitted", href: "/dashboard/faculty/grading", tone: "blue" });
    });
    if (d.tasks) {
      items.push({ count: d.tasks.needs_response, text: "tasks waiting for your answer", href: "/dashboard/faculty/tasks" });
      items.push({ count: d.tasks.overdue, text: "of your tasks are overdue", href: "/dashboard/faculty/tasks", tone: "red" });
    }
    (d.applications || []).filter(function (a) { return a.awaiting === "APPLICANT"; }).forEach(function (a) {
      items.push({ count: "!", text: "Your application needs a reply: " + a.subject, href: "/dashboard/faculty/applications?open=" + a.id });
    });
    if (d.fines && d.fines.total_pending > 0) items.push({ count: D.num(d.fines.pending_count), text: "unpaid fines (" + D.money(d.fines.total_pending) + ")", href: "/dashboard/faculty/fines" });
    const overdueBooks = ((d.loans && d.loans.active) || []).filter(function (l) { return l.due_date && l.due_date < today; }).length;
    items.push({ count: overdueBooks, text: "library books are overdue", href: "/dashboard/faculty/library", tone: "red" });
    D.attention("attn-list", "attn-clear", items);
  }

  function courses(d) {
    const ov = d.overview || [];
    $("courses-empty").hidden = ov.length > 0;
    const sheetBy = {};
    (d.sheets || []).forEach(function (s) { if (s) sheetBy[s.course_offering_id] = s; });
    $("course-cards").innerHTML = ov.map(function (c) {
      const pct = c.classes_total ? Math.round(100 * c.classes_held / c.classes_total) : 0;
      const cov = c.classes_held ? Math.round(100 * c.topics_covered / c.classes_held) : 0;
      const s = sheetBy[c.offering_id], st = SHEET[s && s.status] || ["Marks open", "grey"];
      const att = c.average_attendance == null ? "—" : Math.round(c.average_attendance) + "%";
      return '<a class="course-card" href="/dashboard/faculty/courses?offering=' + esc(c.offering_id) + '"><div class="cc-head"><span class="cc-code">' + esc(c.course_code) +
        (c.section_label && c.section_label !== "A" ? " · " + esc(c.section_label) : "") + '</span><span class="cc-sheet tone-' + st[1] + '">' + st[0] + "</span></div>" +
        "<strong>" + esc(c.course_title) + "</strong>" +
        '<div class="prog-track" title="Classes held"><span class="seg seg-blue" style="width:' + pct + '%"></span></div>' +
        '<small class="cc-meta">' + D.num(c.classes_held) + " of " + D.num(c.classes_total) + " classes held" + (c.next_class_date ? " · next " + esc(dayName(c.next_class_date)) : "") + "</small>" +
        '<ul class="cc-stats"><li><b>' + D.num(c.enrolled_count) + "</b><span>students</span></li><li" + (c.average_attendance != null && c.average_attendance < 75 ? ' class="bad"' : "") + "><b>" + att +
        "</b><span>attendance</span></li><li" + (c.classes_held && cov < 80 ? ' class="bad"' : "") + "><b>" + (c.classes_held ? cov + "%" : "—") + "</b><span>topics covered</span></li><li" +
        (c.unmarked_past_classes ? ' class="bad"' : "") + "><b>" + D.num(c.unmarked_past_classes) + "</b><span>not marked</span></li></ul></a>";
    }).join("");
  }

  function assignments(rows) {
    if (!rows) { D.hide("asg-list"); return; }
    const list = rows.slice().sort(function (a, b) { return (b.awaiting_review || 0) - (a.awaiting_review || 0) || String(b.deadline).localeCompare(String(a.deadline)); }).slice(0, 5);
    $("asg-empty").hidden = list.length > 0;
    D.people("asg-list", list.map(function (a) {
      return { title: a.title, sub: a.course_code + " · " + D.num(a.submitted) + " of " + D.num(a.enrolled) + " handed in" + (a.deadline ? " · due " + VA.formatDateTime(a.deadline) : ""),
        value: a.awaiting_review ? a.awaiting_review + " to check" : (a.is_open ? "Open" : "Closed"), bad: a.awaiting_review > 0, href: "/dashboard/faculty/assignments" };
    }));
  }

  async function load(me) {
    D.greet("fh-greet", "fh-date", me);
    const d = await D.getAll({
      overview: "/api/v1/academics/offerings/me/overview",
      schedule: "/api/v1/academics/offerings/me/schedule",
      assignments: "/api/v1/assignments/teaching",
      tasks: "/api/v1/tasks/summary",
      applications: "/api/v1/applications/mine",
      fines: "/api/v1/fines/me/balance",
      loans: "/api/v1/library/me/loans",
      calendar: D.upcoming(),
      announcements: "/api/v1/announcements?limit=3",
    });
    d.sheets = await Promise.all((d.overview || []).map(function (c) { return D.get("/api/v1/results/offerings/" + c.offering_id + "/sheet"); }));
    let todays = [];
    D.run(function () { todays = schedule(d.schedule); });
    D.run(function () { kpis(d, todays); });
    D.run(function () { attention(d, d.schedule); });
    D.run(function () { courses(d); });
    D.run(function () { assignments(d.assignments); });
    D.run(function () { D.calendar("cal-list", "cal-empty", d.calendar, 4); });
    D.run(function () { D.announcements("ann-list", "ann-empty", d.announcements, 3, "/dashboard/faculty/announcements"); });
  }

  document.addEventListener("vistadmin:ready", function (ev) { load(ev.detail && ev.detail.me); });
})();
