(function () {
  "use strict";

  // Student home. Read-only: only the student's own classes, attendance, assignments, results, fees and loans.
  const D = window.Dash, VA = window.VistAdmin, esc = D.esc;
  const $ = function (id) { return document.getElementById(id); };
  const today = D.iso(new Date());
  const t = function (s) { return VA.formatTimeShort(s); };

  function nowHM() { const d = new Date(); return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); }
  function dayName(iso) {
    if (iso === today) return "Today";
    if (iso === D.iso(D.addDays(1))) return "Tomorrow";
    return new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric" });
  }
  function daysTo(iso) { return Math.round((new Date(iso + "T00:00:00") - new Date(today + "T00:00:00")) / 864e5); }
  function upcomingClasses(rows) {
    const now = nowHM();
    return (rows || []).filter(function (e) { return e.session_date > today || (e.session_date === today && e.end_time.slice(0, 5) > now); })
      .sort(function (a, b) { return (a.session_date + a.start_time).localeCompare(b.session_date + b.start_time); });
  }
  function overall(ov) {
    const held = ov.reduce(function (n, c) { return n + (c.classes_held || 0); }, 0);
    const att = ov.reduce(function (n, c) { return n + (c.classes_attended || 0); }, 0);
    return held ? { pct: 100 * att / held, held: held, att: att } : null;
  }

  function hero(d) {
    const me = d.profile;
    if (me) {
      const tr = d.transcript && d.transcript.student;
      D.chip("st-program", (tr && tr.program_code ? tr.program_code + " · " : "") + "Semester " + (me.current_semester || "—") + " · Roll " + me.roll_number, false);
    }
  }

  function kpis(d) {
    const ov = d.overview || [], thr = (d.cfg && d.cfg.eligibility_threshold) || 75;
    const o = overall(ov);
    D.set("k-att", o ? Math.round(o.pct) + "%" : "—");
    const low = ov.filter(function (c) { return c.attendance_percentage != null && c.classes_held && c.attendance_percentage < thr; }).length;
    D.set("k-att-sub", !o ? "No classes held yet" : low ? low + " course" + (low === 1 ? "" : "s") + " below " + thr + "%" : D.num(o.att) + " of " + D.num(o.held) + " classes attended");
    D.bad("k-att-sub", low);
    const next = upcomingClasses(d.schedule)[0];
    D.set("k-next", next ? (next.session_date === today ? t(next.start_time) : dayName(next.session_date)) : "—");
    D.set("k-next-sub", next ? next.course_code + " · Room " + next.room + (next.session_date !== today ? " · " + t(next.start_time) : "") : "No classes scheduled");
    const todo = (d.assignments || []).filter(function (a) { return a.is_open && (a.state === "NOT_SUBMITTED" || a.state === "REJECTED"); });
    D.set("k-asg", D.num(todo.length));
    const soonest = todo.slice().sort(function (a, b) { return String(a.deadline).localeCompare(String(b.deadline)); })[0];
    D.set("k-asg-sub", soonest ? "next due " + VA.formatDateTime(soonest.deadline) : "Nothing to hand in");
    const s = d.transcript && d.transcript.summary;
    D.set("k-cgpa", s && s.cgpa != null ? Number(s.cgpa).toFixed(2) : "—");
    D.set("k-cgpa-sub", s && s.terms ? D.num(s.credits_earned) + " credit hours earned" : "No results published yet");
    const fees = (d.fees || []).filter(function (f) { return f.status === "ACTIVE"; });
    const due = fees.reduce(function (n, f) { return n + (f.total_due_now || 0); }, 0);
    const overdue = fees.some(function (f) { return f.overdue_principal > 0; });
    D.set("k-fees", D.money(due));
    D.set("k-fees-sub", due ? (overdue ? "some of it is overdue" : "pay by the due date") : "Nothing due right now");
    D.bad("k-fees-sub", overdue);
    const loans = (d.loans && d.loans.active) || [];
    const late = loans.filter(function (l) { return l.is_overdue || l.due_date < today; }).length;
    D.set("k-books", D.num(loans.length));
    D.set("k-books-sub", late ? late + " overdue: return it" : loans.length ? "all within their due date" : "No books borrowed");
    D.bad("k-books-sub", late);
  }

  function classes(rows) {
    if (!rows) { D.hide("class-list"); return; }
    const list = upcomingClasses(rows).slice(0, window.innerWidth < 700 ? 3 : 5), now = nowHM();
    $("class-empty").hidden = list.length > 0;
    $("class-list").innerHTML = list.map(function (e) {
      const live = e.session_date === today && e.start_time.slice(0, 5) <= now && e.end_time.slice(0, 5) > now;
      return '<li class="' + (live ? "live" : "") + '"><span class="ag-time"><b>' + esc(dayName(e.session_date)) + "</b>" + esc(t(e.start_time)).replace(/\s?[AP]M$/i, "") +
        '</span><span class="ag-body"><strong>' + esc(e.course_code) + " · " + esc(e.course_title) + (live ? ' <span class="ev-kind kind-holiday">Now</span>' : "") +
        "</strong><small>" + esc(t(e.start_time)) + "–" + esc(t(e.end_time)) + " · Room " + esc(e.room) + (e.teacher_name ? " · " + esc(e.teacher_name) : "") + (e.title ? " · " + esc(e.title) : "") + "</small></span>" +
        '<button type="button" class="icon-btn ag-ics" data-ics="' + esc(e.id) + '" data-name="' + esc(e.course_code + "-" + e.session_date) + '" aria-label="Add to calendar" title="Add to calendar">' +
        '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18M12 14v4M10 16h4"/></svg></button></li>';
    }).join("");
    $("class-list").querySelectorAll("[data-ics]").forEach(function (b) {
      b.addEventListener("click", function () { VA.downloadFile("/api/v1/academics/class-sessions/" + b.getAttribute("data-ics") + "/calendar.ics", b.getAttribute("data-name") + ".ics", "calendar file"); });
    });
    const days = [];
    for (let i = 0; i < 7; i++) days.push(D.iso(D.addDays(i)));
    const count = function (x) { return rows.filter(function (e) { return e.session_date === x; }).length; };
    const max = Math.max.apply(null, days.map(count).concat([1]));
    $("week-strip").innerHTML = days.map(function (x, i) {
      const n = count(x);
      return '<div class="wk-day' + (i === 0 ? " is-today" : "") + '"><span class="wk-name">' + new Date(x + "T00:00:00").toLocaleDateString("en-GB", { weekday: "short" }) +
        '</span><span class="wk-bar"><span style="height:' + (n ? Math.max(18, Math.round(100 * n / max)) : 0) + '%"></span></span><b>' + n + "</b></div>";
    }).join("");
  }

  function alerts(d) {
    const items = [], thr = (d.cfg && d.cfg.eligibility_threshold) || 75;
    if (d.feedback && d.feedback.open && d.feedback.open.length) items.push({ count: d.feedback.open.length, text: "course" + (d.feedback.open.length > 1 ? "s" : "") + " waiting for your feedback (anonymous, 2 minutes each)", href: "/dashboard/student/feedback", tone: "blue" });
    (d.overview || []).forEach(function (c) {
      if (!c.classes_held || c.attendance_percentage == null) return;
      if (c.attendance_percentage < thr) items.push({ count: Math.round(c.attendance_percentage) + "%", text: c.course_code + ": attendance is below the " + thr + "% needed to pass. Speak to your teacher.", href: "/dashboard/student/attendance", tone: "red" });
      else if (c.attendance_percentage < thr + 5) items.push({ count: Math.round(c.attendance_percentage) + "%", text: c.course_code + ": close to the " + thr + "% limit. Try not to miss classes.", href: "/dashboard/student/attendance" });
    });
    (d.assignments || []).forEach(function (a) {
      const ms = new Date(a.deadline).getTime() - Date.now();
      if (a.is_open && a.state === "NOT_SUBMITTED" && ms < 3 * 864e5) {
        const when = ms < 36e5 ? "within the hour" : ms < 864e5 ? "in " + Math.ceil(ms / 36e5) + " h" : "in " + Math.ceil(ms / 864e5) + " days";
        items.push({ count: "!", text: "Assignment due " + when + ": " + a.title + " (" + a.course_code + ")", href: "/dashboard/student/assignments?open=" + a.id, tone: ms < 864e5 ? "red" : "amber" });
      } else if (a.is_open && a.state === "REJECTED") {
        items.push({ count: "!", text: "Sent back: " + a.title + ". Fix it and hand it in again.", href: "/dashboard/student/assignments?open=" + a.id, tone: "red" });
      }
    });
    const fees = (d.fees || []).filter(function (f) { return f.status === "ACTIVE" && f.total_due_now > 0; });
    if (fees.length) {
      const due = fees.reduce(function (n, f) { return n + f.total_due_now; }, 0), od = fees.some(function (f) { return f.overdue_principal > 0; });
      items.push({ count: "!", text: "Fee payment due: " + D.money(due) + (od ? ". Some of it is overdue." : "."), href: "/dashboard/student/fees", tone: od ? "red" : "amber" });
    }
    ((d.loans && d.loans.active) || []).forEach(function (l) {
      const n = daysTo(l.due_date);
      if (n < 0) items.push({ count: "!", text: "Overdue book: " + l.book_title + " (" + Math.abs(n) + " day" + (n === -1 ? "" : "s") + " late)", href: "/dashboard/student/library", tone: "red" });
      else if (n <= 3) items.push({ count: "!", text: "Book due " + (n === 0 ? "today" : "in " + n + " day" + (n === 1 ? "" : "s")) + ": " + l.book_title, href: "/dashboard/student/library", tone: "blue" });
    });
    if (d.fines && d.fines.total_pending > 0) items.push({ count: D.num(d.fines.pending_count), text: "unpaid fines (" + D.money(d.fines.total_pending) + "). They also stop you borrowing books.", href: "/dashboard/student/fines" });
    (d.applications || []).filter(function (a) { return a.awaiting === "APPLICANT"; }).forEach(function (a) {
      items.push({ count: "!", text: "Your application needs a reply: " + a.subject, href: "/dashboard/student/applications?open=" + a.id });
    });
    D.attention("attn-list", "attn-clear", items);
  }

  function courses(d) {
    const ov = d.overview || [], thr = (d.cfg && d.cfg.eligibility_threshold) || 75;
    if (!d.overview) { D.hide("course-cards"); return; }
    $("courses-empty").hidden = ov.length > 0;
    $("course-cards").innerHTML = ov.map(function (c) {
      const pct = c.attendance_percentage, has = c.classes_held && pct != null;
      const tone = !has ? "grey" : pct < thr ? "red" : pct < thr + 5 ? "amber" : "green";
      const label = !has ? "No classes yet" : pct < thr ? "Below " + thr + "%" : pct < thr + 5 ? "Close to the limit" : "Good standing";
      const cov = c.topics_total ? Math.round(100 * c.topics_covered / c.topics_total) : 0;
      return '<a class="course-card" href="/dashboard/student/courses"><div class="cc-head"><span class="cc-code">' + esc(c.course_code) + '</span><span class="cc-sheet tone-' +
        (tone === "amber" ? "blue" : tone) + '">' + label + "</span></div><strong>" + esc(c.course_title) + "</strong>" +
        '<small class="cc-meta">' + esc(c.teacher_name || "Teacher not assigned yet") + "</small>" +
        '<div class="att-track" title="Attendance"><span class="seg seg-' + (tone === "grey" ? "grey" : tone) + '" style="width:' + (has ? Math.round(pct) : 0) + '%"></span><i style="left:' + thr + '%"></i></div>' +
        '<ul class="cc-stats cc-stats--3"><li' + (tone === "red" ? ' class="bad"' : "") + "><b>" + (has ? Math.round(pct) + "%" : "—") + "</b><span>attendance</span></li><li><b>" + D.num(c.classes_attended) + "/" + D.num(c.classes_held) +
        "</b><span>classes attended</span></li><li><b>" + (c.topics_total ? cov + "%" : "—") + "</b><span>course covered</span></li></ul></a>";
    }).join("");
  }

  function assignments(rows) {
    if (!rows) { D.hide("asg-list"); return; }
    const STATE = { NOT_SUBMITTED: "To do", SUBMITTED: "Handed in", ACCEPTED: "Accepted", REJECTED: "Sent back" };
    const list = rows.filter(function (a) { return a.is_open || a.state === "REJECTED"; })
      .sort(function (a, b) { return String(a.deadline).localeCompare(String(b.deadline)); }).slice(0, 5);
    $("asg-empty").hidden = list.length > 0;
    D.people("asg-list", list.map(function (a) {
      return { title: a.title, sub: a.course_code + " · due " + VA.formatDateTime(a.deadline), value: STATE[a.state] || a.state,
        bad: a.state === "NOT_SUBMITTED" || a.state === "REJECTED", href: "/dashboard/student/assignments?open=" + a.id };
    }));
  }

  function exams(sheets) {
    if (!sheets) { D.hide("exam-list"); return; }
    const papers = [];
    sheets.forEach(function (s) { (s.papers || []).forEach(function (p) { if (p.exam_date >= today) papers.push(Object.assign({ sheet: s.name }, p)); }); });
    papers.sort(function (a, b) { return (a.exam_date + (a.start_time || "")).localeCompare(b.exam_date + (b.start_time || "")); });
    $("exam-empty").hidden = papers.length > 0;
    if (!papers.length && sheets.length) $("exam-empty").textContent = "No exams coming up.";
    $("exam-list").innerHTML = papers.slice(0, 5).map(function (p) {
      const dt = new Date(p.exam_date + "T00:00:00");
      return '<li><span class="ev-date"><b>' + dt.getDate() + "</b>" + D.MONTHS[dt.getMonth()] + '</span><span class="ev-body"><strong>' + esc(p.course_code) + " · " + esc(p.course_title) +
        '</strong><small><span class="ev-kind kind-exam">' + esc(p.sheet) + "</span> " + esc(p.start_time ? t(p.start_time) : "") + (p.room ? " · Room " + esc(p.room) : "") + "</small></span></li>";
    }).join("");
  }

  function fees(rows) {
    if (!rows) { D.hide("fee-list"); return; }
    const act = rows.filter(function (f) { return f.status === "ACTIVE" || f.status === "COMPLETED"; });
    const payable = act.reduce(function (n, f) { return n + (f.total_payable || 0); }, 0);
    const paid = act.reduce(function (n, f) { return n + (f.total_paid || 0); }, 0);
    const pct = payable ? Math.round(100 * paid / payable) : null;
    $("fee-ring").style.setProperty("--p", pct == null ? 0 : pct);
    D.set("fee-ring-val", pct == null ? "—" : pct + "%");
    D.set("fee-ring-text", pct == null ? "No fees have been charged to you yet." : "of your fees are paid (" + D.money(paid) + " of " + D.money(payable) + ").");
    const remaining = act.reduce(function (n, f) { return n + (f.total_remaining || 0); }, 0);
    const due = act.reduce(function (n, f) { return n + (f.total_due_now || 0); }, 0);
    const disc = act.reduce(function (n, f) { return n + (f.total_concession || 0); }, 0);
    const fine = act.reduce(function (n, f) { return n + (f.accrued_fine || 0); }, 0);
    D.facts("fee-list", [["Due now", D.money(due), due ? "bad" : ""], ["Still to pay in total", D.money(remaining)], ["Scholarship / discount", D.money(disc)], ["Late fee so far", D.money(fine), fine ? "bad" : ""]]);
  }

  function loans(l) {
    if (!l) { D.hide("loan-list"); return; }
    const list = (l.active || []).slice().sort(function (a, b) { return a.due_date.localeCompare(b.due_date); });
    $("loan-empty").hidden = list.length > 0;
    D.people("loan-list", list.map(function (x) {
      const n = daysTo(x.due_date);
      return { title: x.book_title, sub: x.accession_code + " · due " + new Date(x.due_date + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" }),
        value: n < 0 ? Math.abs(n) + " days late" : n === 0 ? "Due today" : "in " + n + " days", bad: n < 0, href: "/dashboard/student/library" };
    }));
  }

  // The latest announcement only (unread marked); the rest are under View all. Opening it goes to Notices.
  function newsStrip(rows) {
    const list = document.getElementById("news-cards"), empty = document.getElementById("news-empty");
    if (!list) return;
    if (!rows) { list.innerHTML = ""; empty.hidden = false; empty.textContent = "Announcements could not be loaded."; return; }
    const latest = rows.slice().sort(function (a, b) { return String(b.published_at || "").localeCompare(String(a.published_at || "")); }).slice(0, 1);
    empty.hidden = latest.length > 0;
    const esc = window.VistAdmin.escapeHtml;
    const TAG = { URGENT: "Urgent", IMPORTANT: "Important" };
    list.innerHTML = latest.map(function (a) {
      const when = a.published_at ? new Date(a.published_at).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "";
      return '<li class="news-card' + (a.is_read ? "" : " unread") + (a.priority !== "NORMAL" ? " p-" + a.priority.toLowerCase() : "") + '">' +
        '<a href="/dashboard/student/announcements?open=' + encodeURIComponent(a.id) + '">' +
        '<span class="news-top">' + (a.is_read ? "" : '<span class="news-dot" aria-label="New"></span>') +
        (TAG[a.priority] ? '<span class="news-tag">' + TAG[a.priority] + "</span>" : "") + '<span class="news-date">' + esc(when) + "</span></span>" +
        "<strong>" + esc(a.title) + "</strong>" +
        '<small>' + esc(a.issuer_name || "") + (a.issuer_designation ? ", " + esc(a.issuer_designation) : "") + "</small>" +
        '<span class="news-body">' + esc((a.body || "").replace(/\s+/g, " ").slice(0, 260)) + "</span></a></li>";
    }).join("");
  }

  function todayPanel(d) {
    const now = nowHM(), ur = document.documentElement.lang === "ur";
    $("today-date").textContent = new Date().toLocaleDateString(ur ? "ur-PK" : "en-GB", { weekday: "long", day: "numeric", month: "long" });
    const li = function (main, sub, cls, href) {
      const tag = href ? 'a href="' + esc(href) + '"' : "div";
      return '<li class="' + (cls || "") + '"><' + tag + ' class="today-item"><strong>' + main + "</strong>" + (sub ? "<small>" + sub + "</small>" : "") + "</" + (href ? "a" : "div") + "></li>";
    };
    // classes today
    const todays = (d.schedule || []).filter(function (e) { return e.session_date === today; }).sort(function (a, b) { return a.start_time.localeCompare(b.start_time); });
    let html = todays.map(function (e) {
      const live = e.start_time.slice(0, 5) <= now && e.end_time.slice(0, 5) > now, past = e.end_time.slice(0, 5) <= now;
      return li(esc(t(e.start_time)) + " · " + esc(e.course_code) + (live ? ' <span class="ev-kind kind-holiday">Now</span>' : ""), "Room " + esc(e.room) + (e.teacher_name ? " · " + esc(e.teacher_name) : ""), live ? "live" : past ? "past" : "", "/dashboard/student/timetable");
    }).join("");
    if (!html) {
      const nxt = upcomingClasses(d.schedule)[0];
      html = li("No classes today.", nxt ? "Next: " + esc(dayName(nxt.session_date)) + " " + esc(t(nxt.start_time)) + " · " + esc(nxt.course_code) : "", "empty");
    }
    $("today-classes").innerHTML = html;
    // due in the next 7 days: assignments, fee instalments, exam papers
    const due = [];
    (d.assignments || []).forEach(function (a) {
      if (!a.is_open || !(a.state === "NOT_SUBMITTED" || a.state === "REJECTED")) return;
      const day = String(a.deadline).slice(0, 10), n = daysTo(day);
      if (n >= 0 && n <= 7) due.push({ n: n, html: li(esc(a.title), esc(a.course_code || "") + " · " + (n === 0 ? "due today" : n === 1 ? "due tomorrow" : "due in " + n + " days"), n <= 1 ? "urgent" : "", "/dashboard/student/assignments") });
    });
    ((d.account && d.account.assignments) || []).forEach(function (f) {
      (f.installments || []).forEach(function (i) {
        if (!(i.remaining > 0) || !i.due_date) return;
        const n = daysTo(i.due_date);
        if (n >= 0 && n <= 7) due.push({ n: n, html: li("Fee " + D.money(i.remaining), (n === 0 ? "due today" : "due in " + n + " days") + (i.challan && i.challan.consumer_number ? " · consumer no. " + esc(i.challan.consumer_number) : ""), n <= 1 ? "urgent" : "", "/dashboard/student/fees") });
      });
    });
    (d.exams || []).forEach(function (sh) {
      (sh.papers || []).forEach(function (p) {
        const n = daysTo(p.exam_date);
        if (n >= 0 && n <= 7) due.push({ n: n, html: li("Exam: " + esc(p.course_code), esc(dayName(p.exam_date)) + " " + esc(t(p.start_time)) + (p.room ? " · Room " + esc(p.room) : ""), n <= 1 ? "urgent" : "", "/dashboard/student/exams") });
      });
    });
    due.sort(function (a, b) { return a.n - b.n; });
    $("today-due").innerHTML = due.length ? due.slice(0, 5).map(function (x) { return x.html; }).join("") : li("Nothing due this week.", "", "empty");
    // latest marks
    const marks = d.marks || [];
    $("today-marks").innerHTML = marks.length ? marks.slice(0, 4).map(function (m) {
      const low = m.percentage != null && m.percentage < 50;
      return li(esc(m.course_code) + " · " + esc(m.assessment), '<span class="mk' + (low ? " low" : "") + '" dir="ltr">' + esc(String(m.obtained)) + " / " + esc(String(m.max)) + "</span>", "", "/dashboard/student/results");
    }).join("") : li("No marks yet.", "They appear here as teachers enter them.", "empty");
  }

  async function load(me) {
    D.greet("st-greet", "st-date", me);
    const d = await D.getAll({
      profile: "/api/v1/students/me",
      overview: "/api/v1/academics/enrollments/me/overview",
      schedule: "/api/v1/academics/enrollments/me/schedule",
      cfg: "/api/v1/attendance/config",
      feedback: "/api/v1/feedback/me",
      assignments: "/api/v1/assignments/mine",
      transcript: "/api/v1/results/me/transcript",
      fees: "/api/v1/fees/assignments/me",
      loans: "/api/v1/library/me/loans",
      fines: "/api/v1/fines/me/balance",
      applications: "/api/v1/applications/mine",
      exams: "/api/v1/exams/me/datesheets",
      calendar: D.upcoming(),
      announcements: "/api/v1/announcements?limit=12",
      marks: "/api/v1/academics/enrollments/me/recent-marks?limit=4",
      account: "/api/v1/fees/accounts/me",
    });
    D.run(function () { todayPanel(d); });
    D.run(function () { hero(d); });
    D.run(function () { kpis(d); });
    D.run(function () { classes(d.schedule); });
    D.run(function () { alerts(d); });
    D.run(function () { courses(d); });
    D.run(function () { assignments(d.assignments); });
    D.run(function () { exams(d.exams); });
    D.run(function () { fees(d.fees); });
    D.run(function () { loans(d.loans); });
    D.run(function () { D.calendar("cal-list", "cal-empty", d.calendar, 4); });
    D.run(function () { newsStrip(d.announcements); });
  }

  document.addEventListener("vistadmin:ready", function (ev) { load(ev.detail && ev.detail.me); });
})();
