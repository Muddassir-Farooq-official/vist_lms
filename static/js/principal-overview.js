(function () {
  "use strict";

  // Institute Overview (principal; the administration sees the same page under /dashboard/admin/overview).
  // Read-only: every block uses an endpoint the principal may already read, and loads on its own.
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const money = VA.formatMoney;
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function get(url) { return VA.fetchJson(url, { quiet: true }).then(function (r) { return r.ok ? r.body : null; }).catch(function () { return null; }); }
  function set(id, v) { const el = $(id); if (el) el.textContent = v; }
  function num(v) { return Number(v || 0).toLocaleString("en-PK"); }
  function iso(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
  function bad(id, on) { const el = $(id); if (el) el.classList.toggle("bad", !!on); }

  // The administration opens this page under its own menu: keep its links there too.
  if (location.pathname.indexOf("/dashboard/admin") === 0) {
    document.querySelectorAll('#principal-dash a[href^="/dashboard/principal"]').forEach(function (a) {
      a.setAttribute("href", a.getAttribute("href").replace("/dashboard/principal", "/dashboard/admin"));
    });
  }
  const base = location.pathname.indexOf("/dashboard/admin") === 0 ? "/dashboard/admin" : "/dashboard/principal";

  // ------------------------------------------------------------------ pieces
  function hero(me, o) {
    const h = new Date().getHours();
    set("pv-greet", (h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening") + ", " + (me && me.username ? me.username : "there"));
    set("pv-date", new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" }));
    if (o) {
      const chip = $("pv-term"), terms = o.open_terms || [];
      chip.hidden = false;
      chip.className = "dash-chip" + (terms.length ? "" : " warn");
      chip.textContent = terms.length ? "Term: " + terms.join(", ") : "No open term";
    }
  }

  function staffCounts(board) {
    if (!board || !board.people) return null;
    const due = board.people.filter(function (p) { return p.working_today; });
    const n = function (st) { return due.filter(function (p) { return p.state === st; }).length; };
    return { due: due.length, inNow: n("IN"), left: n("OUT"), excused: n("EXCUSED"), notIn: n("NOT_IN"),
      late: due.filter(function (p) { return p.day && p.day.late_minutes > 0; }).length };
  }

  function progressCounts(rows) {
    const c = { ON_TRACK: 0, SLIGHTLY_DELAYED: 0, DELAYED: 0, NOT_STARTED: 0 };
    (rows || []).forEach(function (r) { if (r.status in c) c[r.status] += 1; else c.DELAYED += 1; });
    return c;
  }

  function kpis(d) {
    const o = d.overview;
    if (o) {
      const st = o.students.by_status || {};
      const total = Object.keys(st).reduce(function (n, k) { return n + st[k]; }, 0);
      set("ov-active", num(st.ACTIVE));
      set("ov-active-sub", num(total) + " on the books");
      const a = o.attendance;
      set("ov-att", a.average_percent == null ? "—" : Math.round(a.average_percent) + "%");
      set("ov-att-sub", a.average_percent == null ? "No classes held yet" : "Pass needs " + a.minimum_percent + "%");
      set("ov-below", num(a.student_courses_below_minimum));
      set("ov-below-sub", a.student_courses_below_minimum ? "student-courses at risk" : "Nobody at risk");
      bad("ov-below-sub", a.student_courses_below_minimum);
      const f = o.fees;
      set("ov-rate", f.collection_rate_percent == null ? "—" : Math.round(f.collection_rate_percent) + "%");
      set("ov-rate-sub", f.collection_rate_percent == null ? "No fees charged yet" : money(f.outstanding) + " still owed");
    }
    if (d.progress) {
      const c = progressCounts(d.progress);
      const behind = c.DELAYED + c.SLIGHTLY_DELAYED;
      set("ov-behind", num(behind));
      set("ov-behind-sub", d.progress.length ? "of " + num(d.progress.length) + " courses" : "No classes scheduled yet");
      bad("ov-behind-sub", c.DELAYED);
    }
    const s = staffCounts(d.staff);
    if (s) {
      set("ov-staff", s.inNow + " / " + s.due);
      set("ov-staff-sub", s.due ? (s.late ? s.late + " late · " : "") + s.notIn + " not in yet" : "Nobody is due today");
    }
  }

  const PG = [["ON_TRACK", "On track", "green"], ["SLIGHTLY_DELAYED", "A little behind", "amber"], ["DELAYED", "Behind", "red"], ["NOT_STARTED", "Not started", "grey"]];
  function progress(rows) {
    if (!rows) { $("pg-list").closest(".dash-panel").hidden = true; return; }
    const c = progressCounts(rows), total = rows.length;
    $("pg-bar").innerHTML = total ? PG.filter(function (p) { return c[p[0]]; }).map(function (p) {
      return '<span class="seg seg-' + p[2] + '" style="width:' + (100 * c[p[0]] / total).toFixed(2) + '%"></span>';
    }).join("") : "";
    $("pg-bar").hidden = !total;
    $("pg-legend").innerHTML = PG.map(function (p) { return '<li><span class="dot dot-' + p[2] + '"></span>' + p[1] + "<b>" + num(c[p[0]]) + "</b></li>"; }).join("");
    const worst = rows.filter(function (r) { return r.sessions_due > 0; })
      .sort(function (a, b) { return a.completion_percentage - b.completion_percentage || b.sessions_missing - a.sessions_missing; }).slice(0, 5);
    $("pg-empty").hidden = worst.length > 0;
    $("pg-list").innerHTML = worst.map(function (r) {
      const pct = Math.max(0, Math.min(100, Math.round(r.completion_percentage || 0)));
      const tone = r.status === "ON_TRACK" ? "green" : r.status === "SLIGHTLY_DELAYED" ? "amber" : "red";
      return '<li><div class="prog-head"><strong>' + esc(r.course_code) + " · " + esc(r.course_title) + "</strong><b>" + pct + "%</b></div>" +
        '<div class="prog-track"><span class="seg seg-' + tone + '" style="width:' + pct + '%"></span></div>' +
        "<small>" + esc(r.teacher_name || "No teacher") + " · " + num(r.sessions_completed) + " of " + num(r.sessions_due) + " topics covered" +
        (r.sessions_missing ? " · " + num(r.sessions_missing) + " behind" : "") + "</small></li>";
    }).join("");
  }

  function attention(d) {
    const out = [];
    function add(count, text, href, tone) { if (count > 0) out.push({ count: count, text: text, href: href, tone: tone || "amber" }); }
    const o = d.overview;
    if (o) {
      if (!(o.open_terms || []).length) out.push({ count: "!", text: "No term is open.", href: base + "/calendar", tone: "red" });
      add(o.teaching.without_teacher, "course offerings have no teacher", base + "/progress", "red");
      add(o.attendance.classes_left_unmarked, "classes whose attendance was never taken", base + "/progress", "red");
      add(o.attendance.student_courses_below_minimum, "student-courses are below " + o.attendance.minimum_percent + "% attendance", base + "/reports");
      const sb = o.results.sheets_by_status || {};
      add(sb.SUBMITTED, "result sheets waiting for exam approval", base + "/reports", "blue");
      add(sb.APPROVED, "approved results not published yet", base + "/reports", "blue");
      add(o.fees.overdue > 0 ? 1 : 0, money(o.fees.overdue) + " of fees is overdue", base + "/reports");
    }
    if (d.progress) add(progressCounts(d.progress).DELAYED, "courses are well behind schedule", base + "/progress", "red");
    if (d.assets) add(d.assets.missing, "assets are missing", base + "/assets", "red");
    if (d.inventory) add(d.inventory.low_stock, "store items are low on stock", base + "/assets", "blue");
    if (d.tasks && base === "/dashboard/admin") add(d.tasks.needs_response + d.tasks.overdue, "of your tasks need an answer or are overdue", base + "/tasks");
    $("attn-clear").hidden = out.length > 0;
    $("attn-list").hidden = out.length === 0;
    $("attn-list").innerHTML = out.map(function (i) {
      return '<li><a href="' + esc(i.href) + '" class="attn-item tone-' + i.tone + '"><span class="attn-count">' + esc(i.count) + '</span><span class="attn-text">' +
        esc(i.text) + '</span><span class="attn-go" aria-hidden="true">›</span></a></li>';
    }).join("");
  }

  function students(o) {
    if (!o) return;
    const st = o.students.by_status || {};
    const parts = [["Active", st.ACTIVE, "green"], ["Suspended", st.SUSPENDED, "amber"], ["Graduated", st.GRADUATED, "blue"], ["Dropped", st.DROPPED, "red"]];
    const total = parts.reduce(function (n, p) { return n + (p[1] || 0); }, 0);
    $("st-bar").innerHTML = total ? parts.filter(function (p) { return p[1]; }).map(function (p) {
      return '<span class="seg seg-' + p[2] + '" style="width:' + (100 * p[1] / total).toFixed(2) + '%"></span>';
    }).join("") : '<span class="seg seg-empty" style="width:100%"></span>';
    $("st-legend").innerHTML = parts.map(function (p) { return '<li><span class="dot dot-' + p[2] + '"></span>' + p[0] + "<b>" + num(p[1]) + "</b></li>"; }).join("");
    const progs = (o.students.active_by_program || []).slice().sort(function (a, b) { return b.students - a.students; });
    if (window.Charts) window.Charts.hbars($("st-programs"), progs.slice(0, 6).map(function (p) { return { label: p.program, count: p.students }; }), { empty: "No active students yet." });
  }

  function results(o) {
    if (!o) return;
    const sb = o.results.sheets_by_status || {};
    const steps = [["DRAFT", "Draft"], ["SUBMITTED", "Waiting"], ["RETURNED", "Sent back"], ["APPROVED", "Approved"], ["PUBLISHED", "Published"]];
    $("res-pipeline").innerHTML = steps.map(function (s) {
      const n = sb[s[0]] || 0;
      return '<li class="' + (n ? "has" : "") + (s[0] === "RETURNED" && n ? " warn" : "") + '"><b>' + num(n) + "</b><span>" + s[1] + "</span></li>";
    }).join("");
    set("res-note", o.results.courses_without_a_sheet ? num(o.results.courses_without_a_sheet) + " course(s) have no result sheet yet." : "");
    const t = o.teaching, a = o.attendance;
    $("teach-list").innerHTML = [
      ["Course offerings running", num(t.course_offerings), ""],
      ["Without a teacher", num(t.without_teacher), t.without_teacher ? "bad" : ""],
      ["Students enrolled in courses", num(t.enrollments), ""],
      ["Classes never marked", num(a.classes_left_unmarked), a.classes_left_unmarked ? "bad" : ""],
    ].map(function (r) { return '<li class="' + r[2] + '"><span>' + esc(r[0]) + "</span><b>" + esc(r[1]) + "</b></li>"; }).join("");
  }

  function fees(o) {
    if (!o) return;
    const f = o.fees, rate = f.collection_rate_percent;
    const ring = $("fee-ring");
    ring.style.setProperty("--p", rate == null ? 0 : Math.max(0, Math.min(100, rate)));
    set("fee-ring-val", rate == null ? "—" : Math.round(rate) + "%");
    $("fee-list").innerHTML = [
      ["Charged", money(f.charged), ""], ["Discounts and scholarships", money(f.discounts), ""],
      ["Still owed", money(f.outstanding), ""], ["Of which overdue", money(f.overdue), f.overdue > 0 ? "bad" : ""],
      ["Collected this month", money(f.collected_this_month), ""],
    ].map(function (r) { return '<li class="' + r[2] + '"><span>' + esc(r[0]) + "</span><b>" + esc(r[1]) + "</b></li>"; }).join("");
  }

  function stats(id, items) {
    $(id).innerHTML = items.map(function (i) { return '<li class="' + (i[2] || "") + '"><b>' + esc(i[1]) + "</b><span>" + esc(i[0]) + "</span></li>"; }).join("");
  }
  function today(d) {
    const s = staffCounts(d.staff);
    if (s) {
      stats("t-staff", [["in now", s.inNow], ["late", s.late, s.late ? "bad" : ""], ["not in", s.notIn], ["left", s.left]]);
      const pct = function (n) { return s.due ? (100 * n / s.due).toFixed(1) : 0; };
      $("t-staff-meter").hidden = !s.due;
      $("t-staff-meter").innerHTML = s.due ? '<span class="seg seg-green" style="width:' + pct(s.inNow + s.left) + '%"></span><span class="seg seg-blue" style="width:' + pct(s.excused) + '%"></span>' : "";
    } else $("t-staff").closest(".today-block").hidden = true;
    if (d.hostel) stats("t-hostel", [["residents", d.hostel.residents], ["free beds", d.hostel.free_beds], ["rooms", d.hostel.rooms]]);
    else $("t-hostel").closest(".today-block").hidden = true;
    if (d.assets || d.inventory) {
      const a = d.assets || {}, inv = d.inventory || {};
      stats("t-assets", [["assets", num(a.assets)], ["missing", num(a.missing), a.missing ? "bad" : ""], ["in repair", num(a.under_repair)], ["low stock", num(inv.low_stock), inv.low_stock ? "bad" : ""]]);
    } else $("t-assets").closest(".today-block").hidden = true;
  }

  const KIND = { HOLIDAY: "Holiday", EXAM: "Exam", EVENT: "Event", TERM: "Term", DEADLINE: "Deadline" };
  function calendar(rows) {
    if (!rows) { $("cal-list").closest(".dash-panel").hidden = true; return; }
    $("cal-empty").hidden = rows.length > 0;
    $("cal-list").innerHTML = rows.slice(0, 5).map(function (e) {
      const d = new Date(e.start_date + "T00:00:00");
      const range = e.end_date && e.end_date !== e.start_date ? "until " + new Date(e.end_date + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : d.toLocaleDateString("en-GB", { weekday: "long" });
      return '<li><span class="ev-date"><b>' + d.getDate() + "</b>" + MONTHS[d.getMonth()] + '</span><span class="ev-body"><strong>' + esc(e.title) + "</strong><small>" +
        '<span class="ev-kind kind-' + esc((e.kind || "").toLowerCase()) + '">' + esc(KIND[e.kind] || e.kind) + "</span> " + esc(range) + "</small></span></li>";
    }).join("");
  }
  function announcements(rows) {
    if (!rows) { $("ann-list").closest(".dash-panel").hidden = true; return; }
    $("ann-empty").hidden = rows.length > 0;
    $("ann-list").innerHTML = rows.slice(0, 3).map(function (a) {
      return '<li><div class="note"><strong>' + esc(a.title) + "</strong><small>" + esc(a.issuer_name || "") +
        (a.published_at ? " · " + esc(VA.formatDateTime(a.published_at)) : "") + "</small><span>" + esc((a.body || "").slice(0, 140)) + "</span></div></li>";
    }).join("");
  }

  // ------------------------------------------------------------------ load
  async function load(me) {
    const now = new Date(), in14 = new Date(now.getTime() + 14 * 864e5);
    const keys = ["overview", "progress", "staff", "hostel", "assets", "inventory", "tasks", "calendar", "announcements"];
    const urls = ["/api/v1/reports/institute-overview", "/api/v1/academics/teacher-progress", "/api/v1/staff-attendance/board", "/api/v1/hostel/summary",
      "/api/v1/assets/summary", "/api/v1/inventory/summary", "/api/v1/tasks/summary",
      "/api/v1/calendar?start=" + iso(now) + "&end=" + iso(in14), "/api/v1/announcements?limit=3"];
    const values = await Promise.all(urls.map(get));
    const d = {};
    keys.forEach(function (k, i) { d[k] = values[i]; });
    if (!d.overview) { const e = $("ov-error"); e.textContent = "Could not load the overview. Please refresh the page."; e.hidden = false; }
    const run = function (fn) { try { fn(); } catch (err) { if (window.console) console.error(err); } };
    run(function () { hero(me, d.overview); });
    run(function () { kpis(d); });
    run(function () { progress(d.progress); });
    run(function () { attention(d); });
    run(function () { students(d.overview); });
    run(function () { results(d.overview); });
    run(function () { fees(d.overview); });
    run(function () { today(d); });
    run(function () { calendar(d.calendar); });
    run(function () { announcements(d.announcements); });
  }

  document.addEventListener("vistadmin:ready", function (ev) { load(ev.detail && ev.detail.me); });
})();
