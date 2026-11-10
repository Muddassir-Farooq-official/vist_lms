(function () {
  "use strict";

  const VA = window.VistAdmin;
  const root = document.querySelector("[data-home]");
  if (!root) return;
  const kind = root.getAttribute("data-home"); // "student" | "faculty"

  const el = function (id) { return document.getElementById(id); };
  const esc = VA.escapeHtml;

  function greeting(name) {
    const h = new Date().getHours();
    const part = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    el("home-greeting").textContent = part + (name ? ", " + name : "");
    el("home-date").textContent = new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  }

  function alertBox(text, href, linkText, tone) {
    const div = document.createElement("div");
    div.className = "home-alert" + (tone === "warn" ? " warn" : "");
    div.innerHTML = "<span>" + esc(text) + "</span>" + (href ? ' <a href="' + href + '">' + esc(linkText) + "</a>" : "");
    el("home-alerts").appendChild(div);
  }

  function isoToday() {
    const d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function dayLabel(iso) {
    const today = isoToday();
    const t = new Date(); t.setDate(t.getDate() + 1);
    const tomorrow = t.getFullYear() + "-" + String(t.getMonth() + 1).padStart(2, "0") + "-" + String(t.getDate()).padStart(2, "0");
    if (iso === today) return "Today";
    if (iso === tomorrow) return "Tomorrow";
    // Short form ("Tue 6 Oct") so the pill never squeezes the course name.
    return new Date(iso + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  }

  // The schedule endpoint lists every non-cancelled session, past ones too.
  // "Next classes" must only ever show today and later, soonest first.
  function fromToday(entries) {
    const today = isoToday();
    return entries
      .filter(function (e) { return e.session_date >= today; })
      .sort(function (a, b) { return (a.session_date + a.start_time).localeCompare(b.session_date + b.start_time); });
  }

  function classRow(entry) {
    const row = document.createElement("div");
    row.className = "list-row";
    const label = dayLabel(entry.session_date);
    row.innerHTML =
      '<div class="list-main"><strong>' + esc(entry.course_code) + " \u2014 " + esc(entry.course_title) + "</strong>" +
      '<span class="list-sub">' + esc(VA.formatTimeShort(entry.start_time)) + "\u2013" + esc(VA.formatTimeShort(entry.end_time)) +
      " \u00b7 Room " + esc(entry.room) + "</span></div>" +
      '<span class="pill' + (label === "Today" ? " pill-now" : "") + '">' + esc(label) + "</span>";
    return row;
  }

  function fill(id, rows, emptyText) {
    const box = el(id);
    box.innerHTML = "";
    if (!rows.length) {
      box.innerHTML = '<p class="home-empty">' + esc(emptyText) + "</p>";
      return;
    }
    rows.forEach(function (r) { box.appendChild(r); });
  }

  // ------------------------------------------------------------------------
  // Student Home
  // ------------------------------------------------------------------------
  const CIRC = 2 * Math.PI * 52;

  const SU = window.StudentUI;
  const classCard = function (e, isNext) { return SU.classCard(e, isNext); };

  function attendanceRing(courses, threshold) {
    const held = courses.reduce(function (n, c) { return n + c.classes_held; }, 0);
    const attended = courses.reduce(function (n, c) { return n + c.classes_attended; }, 0);
    const box = el("sh-att");
    if (!held) {
      box.innerHTML = '<p class="home-empty">No classes have been held yet, so there is no attendance to show.</p>';
      return;
    }
    const pct = Math.round((attended / held) * 1000) / 10;
    const tone = SU.tone(pct, threshold);
    box.innerHTML =
      '<div class="sh-att-body">' +
      '<svg class="sh-ring ' + tone + '" viewBox="0 0 120 120" role="img" aria-label="Overall attendance ' + pct + ' percent">' +
      '<circle class="ring-bg" cx="60" cy="60" r="52"/><circle class="ring-fg" cx="60" cy="60" r="52" transform="rotate(-90 60 60)" ' +
      'stroke-dasharray="' + (CIRC * pct / 100).toFixed(2) + " " + CIRC.toFixed(2) + '"/>' +
      '<text x="60" y="68" text-anchor="middle" class="ring-text">' + pct + "%</text></svg>" +
      '<ul class="sh-att-stats"><li><strong>' + attended + "</strong> classes attended</li><li><strong>" + (held - attended) + "</strong> classes missed</li>" +
      "<li><strong>" + held + "</strong> classes held so far</li><li>Pass mark <strong>" + threshold + "%</strong></li></ul></div>" +
      '<a class="sh-btn" href="/dashboard/student/attendance">See each course</a>';
  }

  function coursesOverview(courses, threshold) {
    const box = el("sh-courses");
    if (!courses.length) {
      box.innerHTML = '<p class="home-empty">You are not enrolled in any course yet.</p>';
      return;
    }
    box.innerHTML = "";
    courses.slice(0, 6).forEach(function (c) {
      const pct = c.topics_total ? Math.round((c.topics_covered / c.topics_total) * 100) : 0;
      const attTone = c.attendance_percentage === null ? "" : " " + SU.tone(c.attendance_percentage, threshold);
      const row = document.createElement("div");
      row.className = "sh-course";
      row.innerHTML =
        '<div class="sh-course-top"><strong>' + esc(c.course_code) + " \u2014 " + esc(c.course_title) + "</strong><span class=\"sh-pct\">" + pct + "%</span></div>" +
        '<div class="meter" role="progressbar" aria-valuenow="' + pct + '" aria-valuemin="0" aria-valuemax="100" aria-label="Course taught so far"><span class="meter-fill" style="width:' + Math.max(pct, c.topics_covered ? 3 : 0) + '%"></span></div>' +
        '<div class="sh-course-sub"><span>' + (c.topics_total ? c.topics_covered + " of " + c.topics_total + " classes taught" : "No classes scheduled yet") + "</span>" +
        '<span class="sh-att-pill' + attTone + '">' + (c.attendance_percentage === null ? "No attendance yet" : "Attendance " + c.attendance_percentage + "%") + "</span></div>";
      box.appendChild(row);
    });
  }

  function alertsCard(items, stillLoading) {
    const box = el("sh-alerts");
    if (!items.length) {
      box.innerHTML = stillLoading
        ? '<div class="skeleton-stack" aria-label="Checking"><div class="skeleton-line"></div><div class="skeleton-line short"></div></div>'
        : '<p class="home-empty">Nothing needs your attention right now.</p>';
      return;
    }
    const rank = { red: 0, amber: 1, blue: 2 };
    box.innerHTML = "";
    items.slice().sort(function (a, b) { return rank[a.tone] - rank[b.tone]; }).slice(0, 5).forEach(function (a) {
      const node = document.createElement(a.href ? "a" : "div");
      node.className = "sh-alert " + a.tone;
      if (a.href) node.href = a.href;
      node.innerHTML = "<strong>" + esc(a.title) + "</strong><span>" + esc(a.text) + "</span>";
      box.appendChild(node);
    });
    if (stillLoading) {
      const more = document.createElement("p");
      more.className = "home-empty";
      more.textContent = "Checking for more\u2026";
      box.appendChild(more);
    }
  }

  // Latest official announcements, with the unread ones marked. Opens the full notice when clicked.
  function loadNews(base) {
    VA.fetchJson("/api/v1/announcements?limit=12").then(function (r) {
      const box = el("sh-news");
      if (!box) return;
      if (!r.ok) { box.innerHTML = '<p class="home-empty">Could not load announcements right now.</p>'; return; }
      if (!r.body.length) { box.innerHTML = '<p class="home-empty">No announcements yet.</p>'; return; }
      box.innerHTML = "";
      // only the newest one on the home page; the rest are under View all
      r.body.slice().sort(function (a, b) { return String(b.published_at || "").localeCompare(String(a.published_at || "")); }).slice(0, 1).forEach(function (a) {
        const d = new Date(a.published_at);
        const node = document.createElement("a");
        node.className = "sh-news-row " + a.priority.toLowerCase() + (a.is_read ? "" : " unread");
        node.href = base + "?open=" + a.id;
        node.innerHTML = "<strong>" + esc((a.priority === "URGENT" ? "URGENT: " : "") + a.title) + "</strong><span>" + esc(a.issuer_name) + ", " + esc(a.issuer_designation) +
          " \u00b7 " + esc(d.toLocaleDateString(undefined, { day: "numeric", month: "short" })) + (a.attachments.length ? " \u00b7 \uD83D\uDCCE " + a.attachments.length : "") + "</span>";
        box.appendChild(node);
      });
    });
  }

  // Every card loads and shows on its own. One slow or failing request never holds back the others.
  function studentHome() {
    loadNews("/dashboard/student/announcements");
    const get = function (url) { return VA.fetchJson(url); };
    const profileP = get("/api/v1/students/me");
    const scheduleP = get("/api/v1/academics/enrollments/me/schedule");
    const overviewP = get("/api/v1/academics/enrollments/me/overview");
    const finesP = get("/api/v1/fines/me/balance");
    const loansP = get("/api/v1/library/me/loans");
    const feesP = get("/api/v1/fees/assignments/me");
    const cfgP = get("/api/v1/attendance/config");
    const assignmentsP = get("/api/v1/assignments/mine");
    const applicationsP = get("/api/v1/applications/mine");
    const failed = function (box) { el(box).innerHTML = '<p class="home-empty">Could not load this right now.</p>'; };

    profileP.then(function (r) { greeting(r.ok && r.body ? r.body.first_name : null); });

    scheduleP.then(function (schedule) {
      const grid = el("sh-class-grid");
      grid.innerHTML = "";
      if (!schedule.ok) { failed("sh-class-grid"); return; }
      const upcoming = fromToday(schedule.body).slice(0, 6);
      if (!upcoming.length) grid.innerHTML = '<p class="home-empty">No upcoming classes scheduled.</p>';
      upcoming.forEach(function (e, i) { grid.appendChild(classCard(e, i === 0)); });
    });

    const thresholdOf = function (cfg) { return cfg.ok ? cfg.body.eligibility_threshold : 75; };
    Promise.all([overviewP, cfgP]).then(function (pair) {
      const overview = pair[0], threshold = thresholdOf(pair[1]);
      if (!overview.ok) { failed("sh-att"); failed("sh-courses"); return; }
      attendanceRing(overview.body, threshold);
      coursesOverview(overview.body, threshold);
    });

    // Alerts & deadlines: each source adds its own items the moment it answers.
    const alerts = [];
    let waiting = 6;
    const settle = function (items) {
      alerts.push.apply(alerts, items || []);
      waiting--;
      alertsCard(alerts, waiting > 0);
    };
    Promise.all([overviewP, cfgP]).then(function (pair) {
      const threshold = thresholdOf(pair[1]), found = [];
      (pair[0].ok ? pair[0].body : []).forEach(function (c) {
        if (c.classes_held === 0 || c.attendance_percentage === null) return;
        if (c.attendance_percentage < threshold) {
          found.push({ tone: "red", title: c.course_code + ": attendance " + c.attendance_percentage + "%", text: "Below the " + threshold + "% needed to pass. Speak to your teacher soon.", href: "/dashboard/student/attendance" });
        } else if (c.attendance_percentage < threshold + 5) {
          found.push({ tone: "amber", title: c.course_code + ": attendance " + c.attendance_percentage + "%", text: "Close to the " + threshold + "% limit. Try not to miss more classes.", href: "/dashboard/student/attendance" });
        }
      });
      settle(found);
    });
    finesP.then(function (fines) {
      const found = [];
      if (fines.ok && fines.body && fines.body.total_pending > 0) {
        found.push({ tone: "amber", title: "Unpaid fines: Rs. " + Number(fines.body.total_pending).toLocaleString(), text: "Pay at the accounts office. Unpaid fines also stop you borrowing books.", href: "/dashboard/student/fines" });
      }
      settle(found);
    });
    loansP.then(function (loans) {
      const found = [], today = isoToday();
      (loans.ok && loans.body ? loans.body.active || [] : []).forEach(function (l) {
        const days = Math.round((new Date(l.due_date + "T00:00:00") - new Date(today + "T00:00:00")) / 86400000);
        if (days < 0) found.push({ tone: "red", title: "Overdue book: " + l.book_title, text: "It was due " + Math.abs(days) + " day" + (Math.abs(days) > 1 ? "s" : "") + " ago. Return it to avoid a late fee.", href: "/dashboard/student/library" });
        else if (days <= 3) found.push({ tone: "amber", title: "Book due " + (days === 0 ? "today" : "in " + days + " day" + (days > 1 ? "s" : "")) + ": " + l.book_title, text: "Return or renew it in the library.", href: "/dashboard/student/library" });
      });
      settle(found);
    });
    applicationsP.then(function (r) {
      const found = [];
      (r.ok ? r.body : []).filter(function (a) { return a.awaiting === "APPLICANT"; }).forEach(function (a) {
        found.push({ tone: "amber", title: "Your application needs a reply: " + a.subject, text: a.reference + " \u2014 the administration asked you a question.", href: "/dashboard/student/applications?open=" + a.id });
      });
      settle(found);
    });
    assignmentsP.then(function (r) {
      const found = [], base = "/dashboard/student/assignments";
      (r.ok ? r.body : []).forEach(function (a) {
        const ms = new Date(a.deadline).getTime() - Date.now();
        if (a.is_open && a.state === "NOT_SUBMITTED" && ms < 3 * 86400000) {
          const when = ms < 3600000 ? "within the hour" : ms < 86400000 ? "in " + Math.ceil(ms / 3600000) + " h" : "in " + Math.ceil(ms / 86400000) + " days";
          found.push({ tone: ms < 86400000 ? "red" : "amber", title: "Assignment due " + when + ": " + a.title, text: a.course_code + " \u2014 hand in your Google Drive link.", href: base + "?open=" + a.id });
        } else if (a.is_open && a.state === "REJECTED") {
          found.push({ tone: "red", title: "Sent back: " + a.title, text: (a.submission && a.submission.feedback) || "Fix it and hand it in again.", href: base + "?open=" + a.id });
        }
      });
      settle(found);
    });
    feesP.then(function (fees) {
      const found = [];
      const dueNow = (fees.ok && fees.body ? fees.body : []).filter(function (f) { return f.status === "ACTIVE" && f.total_due_now > 0; });
      if (dueNow.length) {
        const total = dueNow.reduce(function (n, f) { return n + f.total_due_now; }, 0);
        const overdue = dueNow.some(function (f) { return f.overdue_principal > 0; });
        found.push({ tone: overdue ? "red" : "amber", title: "Fee payment due: Rs. " + Number(total).toLocaleString(), text: overdue ? "Some of this is overdue. Please visit the accounts office." : "Please pay at the accounts office by the due date." });
      }
      settle(found);
    });
  }

  // ------------------------------------------------------------------------
  // Teacher Home. Every card loads and shows on its own, like the student one.
  // ------------------------------------------------------------------------
  function facultyHome() {
    loadNews("/dashboard/faculty/announcements");
    const get = function (url) { return VA.fetchJson(url); };
    const profileP = get("/api/v1/employees/me");
    const scheduleP = get("/api/v1/academics/offerings/me/schedule");
    const overviewP = get("/api/v1/academics/offerings/me/overview");
    const finesP = get("/api/v1/fines/me/balance");
    const loansP = get("/api/v1/library/me/loans");
    const assignmentsP = get("/api/v1/assignments/teaching");
    const applicationsP = get("/api/v1/applications/mine");
    const tasksP = get("/api/v1/tasks/summary");
    const fail = function (id) { el(id).innerHTML = '<p class="home-empty">Could not load this right now.</p>'; };

    profileP.then(function (r) { greeting(r.ok && r.body ? r.body.first_name : null); });

    // Classes: today's first, then the next few. Each knows how many students and whether a topic is set.
    Promise.all([scheduleP, overviewP]).then(function (pair) {
      const schedule = pair[0], overview = pair[1];
      const today = el("home-today"), later = el("home-classes");
      today.innerHTML = ""; later.innerHTML = "";
      if (!schedule.ok) { fail("home-today"); fail("home-classes"); return; }
      const byOffering = {};
      (overview.ok ? overview.body : []).forEach(function (o) { byOffering[o.offering_id] = o; });
      const all = fromToday(schedule.body), iso = isoToday();
      const todays = all.filter(function (e) { return e.session_date === iso; });
      const next = all.filter(function (e) { return e.session_date > iso; }).slice(0, 4);
      if (!todays.length) today.innerHTML = '<p class="home-empty">No classes today. Enjoy the breathing room.</p>';
      todays.forEach(function (e, i) { today.appendChild(SU.teacherClassCard(e, i === 0, byOffering[e.course_offering_id])); });
      if (!next.length) later.innerHTML = '<p class="home-empty">No further classes scheduled.</p>';
      next.forEach(function (e) { later.appendChild(SU.teacherClassCard(e, false, byOffering[e.course_offering_id])); });
    });

    // Attendance ring across all my classes + course list
    overviewP.then(function (r) {
      if (!r.ok) { fail("sh-att"); fail("sh-courses"); return; }
      const rows = r.body, box = el("sh-att"), list = el("sh-courses");
      const held = rows.reduce(function (n, c) { return n + c.classes_held; }, 0);
      const total = rows.reduce(function (n, c) { return n + c.classes_total; }, 0);
      const unmarked = rows.reduce(function (n, c) { return n + c.unmarked_past_classes; }, 0);
      const recorded = rows.filter(function (c) { return c.average_attendance !== null; });
      if (!recorded.length) {
        box.innerHTML = '<p class="home-empty">No attendance has been recorded yet.</p>';
      } else {
        const avg = Math.round(recorded.reduce(function (n, c) { return n + c.average_attendance * c.classes_held; }, 0) / Math.max(held, 1) * 10) / 10;
        box.innerHTML = '<div class="sh-att-body">' + SU.ring(avg, 75, "Average attendance", 128) +
          '<ul class="sh-att-stats"><li><strong>' + held + "</strong> of <strong>" + total + "</strong> classes recorded</li>" +
          "<li><strong>" + unmarked + "</strong> past " + (unmarked === 1 ? "class" : "classes") + " not recorded</li>" +
          "<li>Average across my students</li></ul></div>" +
          '<a class="sh-btn" href="/dashboard/faculty/attendance">Open attendance</a>';
      }
      if (!rows.length) { list.innerHTML = '<p class="home-empty">You have no courses assigned this term.</p>'; return; }
      list.innerHTML = "";
      rows.slice(0, 6).forEach(function (c) {
        const pct = c.classes_total ? Math.round((c.topics_covered / c.classes_total) * 100) : 0;
        const row = document.createElement("div");
        row.className = "sh-course";
        row.innerHTML =
          '<div class="sh-course-top"><strong>' + esc(c.course_code) + " \u2014 " + esc(c.course_title) + "</strong><span class=\"sh-pct\">" + pct + "%</span></div>" +
          '<div class="meter" role="progressbar" aria-valuenow="' + pct + '" aria-valuemin="0" aria-valuemax="100" aria-label="Course taught so far"><span class="meter-fill" style="width:' + Math.max(pct, c.topics_covered ? 3 : 0) + '%"></span></div>' +
          '<div class="sh-course-sub"><span>' + c.enrolled_count + " students \u00b7 " + c.topics_covered + " of " + c.classes_total + " taught</span>" +
          '<span class="sh-att-pill ' + (c.average_attendance === null ? "" : SU.tone(c.average_attendance, 75)) + '">' + (c.average_attendance === null ? "No attendance yet" : "Attendance " + c.average_attendance + "%") + "</span></div>";
        list.appendChild(row);
      });
    });

    // Needs your attention: each source adds its items the moment it answers.
    const alerts = [];
    let waiting = 7;
    const settle = function (items) { alerts.push.apply(alerts, items || []); waiting--; alertsCard(alerts, waiting > 0); };
    overviewP.then(function (r) {
      const found = [];
      (r.ok ? r.body : []).forEach(function (c) {
        if (c.unmarked_past_classes > 0) {
          found.push({ tone: "red", title: c.course_code + ": " + c.unmarked_past_classes + " past " + (c.unmarked_past_classes === 1 ? "class has" : "classes have") + " no attendance",
            text: "Ask the office to apply a correction so students are not marked absent by default.", href: "/dashboard/faculty/attendance#requests" });
        }
      });
      settle(found);
    });
    scheduleP.then(function (r) {
      const found = [];
      const horizon = new Date(); horizon.setDate(horizon.getDate() + 14);
      const limit = horizon.getFullYear() + "-" + String(horizon.getMonth() + 1).padStart(2, "0") + "-" + String(horizon.getDate()).padStart(2, "0");
      const missing = fromToday(r.ok ? r.body : []).filter(function (e) { return e.session_date <= limit && !(e.title || "").trim(); }).length;
      if (missing) found.push({ tone: "amber", title: missing + " upcoming " + (missing === 1 ? "class has" : "classes have") + " no topic yet",
        text: "Students see topics, slides and notes as soon as you add them.", href: "/dashboard/faculty/courses" });
      settle(found);
    });
    finesP.then(function (r) {
      const found = [];
      if (r.ok && r.body && r.body.total_pending > 0) found.push({ tone: "amber", title: "Unpaid fines: Rs. " + Number(r.body.total_pending).toLocaleString(), text: "Unpaid fines also stop you borrowing books.", href: "/dashboard/faculty/fines" });
      settle(found);
    });
    tasksP.then(function (r) {
      const found = [], t = r.ok ? r.body : null;
      if (t && t.needs_response) found.push({ tone: "amber", title: t.needs_response + " task" + (t.needs_response === 1 ? " is" : "s are") + " waiting for your answer", text: "Accept it, or reject it with a reason.", href: "/dashboard/faculty/tasks" });
      if (t && t.overdue) found.push({ tone: "red", title: t.overdue + " of your tasks " + (t.overdue === 1 ? "is" : "are") + " overdue", text: "The deadline has passed. Finish them or explain in the task.", href: "/dashboard/faculty/tasks" });
      settle(found);
    });
    applicationsP.then(function (r) {
      const found = [];
      (r.ok ? r.body : []).filter(function (a) { return a.awaiting === "APPLICANT"; }).forEach(function (a) {
        found.push({ tone: "amber", title: "Your application needs a reply: " + a.subject, text: a.reference + " \u2014 the administration asked you a question.", href: "/dashboard/faculty/applications?open=" + a.id });
      });
      settle(found);
    });
    assignmentsP.then(function (r) {
      const found = [], rows = r.ok ? r.body : [];
      const waitingReview = rows.reduce(function (n, a) { return n + a.awaiting_review; }, 0);
      if (waitingReview) found.push({ tone: "amber", title: waitingReview + " assignment " + (waitingReview === 1 ? "submission is" : "submissions are") + " waiting for your review",
        text: "Accept or reject them so students know where they stand.", href: "/dashboard/faculty/assignments" });
      settle(found);
    });
    loansP.then(function (r) {
      const found = [], iso = isoToday();
      (r.ok && r.body ? r.body.active || [] : []).forEach(function (l) {
        const days = Math.round((new Date(l.due_date + "T00:00:00") - new Date(iso + "T00:00:00")) / 86400000);
        if (days < 0) found.push({ tone: "red", title: "Overdue book: " + l.book_title, text: "Due " + Math.abs(days) + " day" + (Math.abs(days) > 1 ? "s" : "") + " ago. Return it to avoid a late fee.", href: "/dashboard/faculty/library" });
        else if (days <= 3) found.push({ tone: "amber", title: "Book due " + (days === 0 ? "today" : "in " + days + " day" + (days > 1 ? "s" : "")) + ": " + l.book_title, text: "Return or renew it in the library.", href: "/dashboard/faculty/library" });
      });
      settle(found);
    });
  }

  document.addEventListener("vistadmin:ready", function () {
    // Each card handles its own loading and failures, so nothing here can leave the page half-drawn.
    try {
      if (kind === "student") studentHome(); else facultyHome();
    } catch (e) {
      console.error("Home page failed to start", e);
    }
  });
})();