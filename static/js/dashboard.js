(function () {
  "use strict";

  // Admin dashboard. Reads only existing endpoints; each block loads on its own, so one slow or refused
  // request never blanks the page (the block just hides or shows a dash).
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const money = VA.formatMoney;

  function get(url) { return VA.fetchJson(url, { quiet: true }).then(function (r) { return r.ok ? r.body : null; }).catch(function () { return null; }); }
  function set(id, v) { const el = $(id); if (el) el.textContent = v; }
  function num(v) { return Number(v || 0).toLocaleString("en-PK"); }
  function iso(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
  function compact(v) {
    const n = Math.abs(v);
    if (n >= 1e7) return (v / 1e6).toFixed(0) + "M";
    if (n >= 1e6) return (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (n >= 1e3) return (v / 1e3).toFixed(0) + "k";
    return String(Math.round(v));
  }
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  // ------------------------------------------------------------------ greeting
  function hero(me, overview) {
    const h = new Date().getHours();
    const part = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    set("dh-greet", part + ", " + (me && me.username ? me.username : "there"));
    set("dh-date", new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" }));
    const chip = $("dh-term");
    if (overview) {
      const terms = overview.open_terms || [];
      chip.hidden = false;
      chip.className = "dash-chip" + (terms.length ? "" : " warn");
      chip.textContent = terms.length ? "Term: " + terms.join(", ") : "No open term";
    }
  }

  // ------------------------------------------------------------------ KPIs
  function kpis(d) {
    const c = (d.dash && d.dash.student_status_counts) || {};
    if (d.dash) {
      set("k-students", num(c.active));
      set("k-students-sub", num(c.total) + " on the books · " + num(d.dash.active_programs) + " programs");
    }
    const f = d.overview && d.overview.fees;
    if (f) {
      set("k-collected", money(f.collected_this_month));
      set("k-collected-sub", f.collection_rate_percent == null ? "No fees charged yet" : f.collection_rate_percent + "% of all fees collected");
      set("k-owed", money(f.outstanding));
      set("k-owed-sub", f.overdue > 0 ? money(f.overdue) + " overdue" : "Nothing overdue");
      if (f.overdue > 0) $("k-owed-sub").classList.add("bad");
    }
    const a = d.overview && d.overview.attendance;
    if (a) {
      set("k-attendance", a.average_percent == null ? "—" : Math.round(a.average_percent) + "%");
      set("k-attendance-sub", a.average_percent == null ? "No classes held yet" : num(a.student_courses_below_minimum) + " below " + a.minimum_percent + "%");
      if (a.student_courses_below_minimum > 0) $("k-attendance-sub").classList.add("bad");
    }
    const s = staffCounts(d.staff);
    if (s) {
      set("k-staff", s.inNow + " / " + s.due);
      set("k-staff-sub", s.due ? (s.late ? s.late + " late · " : "") + s.notIn + " not in yet" : "Nobody is due today");
    }
    const w = waitingItems(d).reduce(function (n, i) { return n + (i.counts ? i.count : 0); }, 0);
    set("k-waiting", num(w));
    set("k-waiting-sub", w ? "requests and reviews" : "All clear");
  }

  function staffCounts(board) {
    if (!board || !board.people) return null;
    const due = board.people.filter(function (p) { return p.working_today; });
    const n = function (st) { return due.filter(function (p) { return p.state === st; }).length; };
    return {
      due: due.length, inNow: n("IN"), left: n("OUT"), excused: n("EXCUSED"), notIn: n("NOT_IN"),
      late: due.filter(function (p) { return p.day && p.day.late_minutes > 0; }).length,
    };
  }

  // ------------------------------------------------------------------ needs attention
  function waitingItems(d) {
    const out = [];
    function add(count, text, href, tone, counts) { if (count > 0) out.push({ count: count, text: text, href: href, tone: tone || "amber", counts: counts !== false }); }
    if (d.approvals) add(d.approvals.waiting, "requests need a second approval", "/dashboard/admin/approvals", "red");
    if (d.profileChanges) add(d.profileChanges.waiting, "profile changes or photos to approve", "/dashboard/admin/profile-changes", "blue");
    if (d.applications) add(d.applications.kpis.needs_action, "applications waiting for a decision", "/dashboard/admin/applications");
    if (d.complaints) add(d.complaints.kpis.open + d.complaints.kpis.under_review, "complaints still open", "/dashboard/admin/complaints");
    if (d.corrections) add(d.corrections.length, "attendance corrections to decide", "/dashboard/admin/attendance");
    if (d.unmarked) add(d.unmarked.length, "classes whose attendance was never taken", "/dashboard/admin/attendance", "red");
    const sheets = (d.overview && d.overview.results && d.overview.results.sheets_by_status) || {};
    add(sheets.SUBMITTED || 0, "result sheets waiting for approval", "/dashboard/admin/result-sheets");
    add(sheets.APPROVED || 0, "approved results not published yet", "/dashboard/admin/result-sheets", "blue");
    if (d.purchases) add(d.purchases.waiting_for_approval, "purchase requests to approve", "/dashboard/admin/inventory");
    if (d.accounts && d.accounts.pending_expenses) add(d.accounts.pending_expenses.count, "expenses waiting for approval", "/dashboard/admin/finance");
    if (d.tasks) add(d.tasks.needs_response + d.tasks.overdue, "of your tasks need an answer or are overdue", "/dashboard/admin/tasks");
    if (d.overview && d.overview.teaching) add(d.overview.teaching.without_teacher, "course offerings have no teacher", "/dashboard/admin/courses", "red", false);
    if (d.inventory) add(d.inventory.low_stock, "store items are low on stock", "/dashboard/admin/inventory", "blue", false);
    if (d.library) add(d.library.overdue_count, "library books are overdue", "/library", "blue", false);
    if (d.assets) {
      add(d.assets.disposals_waiting, "asset disposals waiting", "/dashboard/admin/assets", "amber");
      add(d.assets.missing, "assets are missing", "/dashboard/admin/assets", "red", false);
    }
    if (d.overview && !(d.overview.open_terms || []).length) out.push({ count: "!", text: "No term is open. Classes and fees need one.", href: "/dashboard/admin/courses", tone: "red", counts: false });
    return out;
  }

  function attention(d) {
    const items = waitingItems(d);
    const list = $("attn-list");
    $("attn-clear").hidden = items.length > 0;
    list.hidden = items.length === 0;
    list.innerHTML = items.map(function (i) {
      return '<li><a href="' + esc(i.href) + '" class="attn-item tone-' + i.tone + '"><span class="attn-count">' + esc(i.count) + '</span><span class="attn-text">' +
        esc(i.text) + '</span><span class="attn-go" aria-hidden="true">›</span></a></li>';
    }).join("");
  }

  // ------------------------------------------------------------------ money chart (income vs expenses, 12 months)
  function moneyChart(acc) {
    const el = $("m-chart");
    if (!acc || !acc.monthly) { el.closest(".dash-panel").hidden = true; return; }
    const rows = acc.monthly;
    const inc = rows.reduce(function (n, r) { return n + (r.income || 0); }, 0);
    const exp = rows.reduce(function (n, r) { return n + (r.expenses || 0); }, 0);
    set("m-income", money(inc)); set("m-expenses", money(exp)); set("m-net", money(inc - exp));
    $("m-net").classList.toggle("bad", inc - exp < 0);
    const max = Math.max.apply(null, rows.map(function (r) { return Math.max(r.income || 0, r.expenses || 0); }).concat([0]));
    if (!max) { el.innerHTML = '<p class="chart-empty">No money has been recorded in the last 12 months.</p>'; return; }
    const W = 640, H = 220, L = 44, R = 8, T = 10, B = 28;
    const top = niceTop(max);
    const slot = (W - L - R) / rows.length, bw = Math.min(14, slot / 3);
    const y = function (v) { return T + (H - T - B) * (1 - v / top); };
    const P = window.Charts ? window.Charts.palette() : { blue: "#2454d6", amber: "#d97706", grid: "#e3e8f2" };
    let svg = "";
    [0, 0.5, 1].forEach(function (f) {
      const v = top * f;
      svg += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + y(v) + '" y2="' + y(v) + '" stroke="' + P.grid + '"/><text x="' + (L - 6) + '" y="' + (y(v) + 4) + '" text-anchor="end" class="axis">' + compact(v) + "</text>";
    });
    rows.forEach(function (r, i) {
      const cx = L + slot * i + slot / 2;
      const m = Number((r.month || "").slice(5, 7));
      const tip = (MONTHS[m - 1] || r.month) + " " + (r.month || "").slice(0, 4);
      [["income", P.blue, -bw - 1], ["expenses", P.amber, 1]].forEach(function (s) {
        const v = r[s[0]] || 0, h = (H - T - B) * (v / top);
        svg += '<rect x="' + (cx + s[2]).toFixed(1) + '" y="' + (y(0) - h).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(0, h).toFixed(1) + '" rx="3" fill="' + s[1] +
          '"><title>' + esc(tip + " — " + s[0] + ": " + money(v)) + "</title></rect>";
      });
      if (rows.length <= 12 || i % 2 === 0) svg += '<text x="' + cx.toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle" class="axis">' + esc(MONTHS[m - 1] || "") + "</text>";
    });
    el.innerHTML = '<svg viewBox="0 0 ' + W + " " + H + '" class="bar-chart" role="img" aria-label="Income and expenses for each of the last 12 months">' + svg + "</svg>" +
      '<ul class="chart-legend inline"><li><span class="swatch" style="background:' + P.blue + '"></span>Income</li><li><span class="swatch" style="background:' + P.amber + '"></span>Expenses</li></ul>';
  }
  function niceTop(v) { const p = Math.pow(10, Math.floor(Math.log10(v))); const n = v / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }

  // ------------------------------------------------------------------ students
  function students(dash) {
    if (!dash) return;
    const c = dash.student_status_counts || {};
    const parts = [["Active", c.active, "green"], ["Suspended", c.suspended, "amber"], ["Graduated", c.graduated, "blue"], ["Dropped", c.dropped, "red"]];
    const total = c.total || 0;
    $("st-bar").innerHTML = total ? parts.filter(function (p) { return p[1] > 0; }).map(function (p) {
      return '<span class="seg seg-' + p[2] + '" style="width:' + (100 * p[1] / total).toFixed(2) + '%"></span>';
    }).join("") : '<span class="seg seg-empty" style="width:100%"></span>';
    $("st-legend").innerHTML = parts.map(function (p) {
      return '<li><span class="dot dot-' + p[2] + '"></span>' + p[0] + "<b>" + num(p[1]) + "</b></li>";
    }).join("");
    const progs = (dash.students_by_program || []).slice().sort(function (a, b) { return b.student_count - a.student_count; });
    const el = $("st-programs");
    if (window.Charts) window.Charts.hbars(el, progs.slice(0, 6).map(function (p) { return { label: p.program_code || p.program_name, count: p.student_count }; }), { empty: "No programs yet." });
  }

  // ------------------------------------------------------------------ on campus today
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
    if (d.gate) stats("t-gate", [["inside now", d.gate.inside_now], ["visits today", d.gate.entries_today], ["people today", d.gate.people_today]]);
    else $("t-gate").closest(".today-block").hidden = true;
    if (d.hostel) stats("t-hostel", [["residents", d.hostel.residents], ["free beds", d.hostel.free_beds], ["rooms", d.hostel.rooms]]);
    else $("t-hostel").closest(".today-block").hidden = true;
  }

  // ------------------------------------------------------------------ teaching and results
  function facts(id, rows) {
    $(id).innerHTML = rows.map(function (r) { return '<li class="' + (r[2] || "") + '"><span>' + esc(r[0]) + "</span><b>" + esc(r[1]) + "</b></li>"; }).join("");
  }
  function teaching(o) {
    if (!o) { $("teach-list").closest(".dash-panel").hidden = true; return; }
    const t = o.teaching || {}, a = o.attendance || {}, r = (o.results && o.results.sheets_by_status) || {};
    facts("teach-list", [
      ["Course offerings running", num(t.course_offerings)],
      ["Without a teacher", num(t.without_teacher), t.without_teacher ? "bad" : ""],
      ["Students enrolled in courses", num(t.enrollments)],
      ["Classes never marked", num(a.classes_left_unmarked), a.classes_left_unmarked ? "bad" : ""],
      ["Below " + (a.minimum_percent || 75) + "% attendance", num(a.student_courses_below_minimum), a.student_courses_below_minimum ? "bad" : ""],
    ]);
    facts("results-list", [
      ["Marks being entered", num(r.DRAFT)], ["Waiting for approval", num(r.SUBMITTED)], ["Sent back to teachers", num(r.RETURNED)],
      ["Approved, not published", num(r.APPROVED)], ["Published", num(r.PUBLISHED)],
    ]);
  }

  // ------------------------------------------------------------------ calendar, announcements, activity
  const KIND = { HOLIDAY: "Holiday", EXAM: "Exam", EVENT: "Event", TERM: "Term", DEADLINE: "Deadline" };
  function calendar(rows) {
    const list = $("cal-list");
    if (!rows) { list.closest(".dash-panel").hidden = true; return; }
    $("cal-empty").hidden = rows.length > 0;
    list.innerHTML = rows.slice(0, 5).map(function (e) {
      const d = new Date(e.start_date + "T00:00:00");
      const range = e.end_date && e.end_date !== e.start_date ? "until " + new Date(e.end_date + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : d.toLocaleDateString("en-GB", { weekday: "long" });
      return '<li><span class="ev-date"><b>' + d.getDate() + "</b>" + MONTHS[d.getMonth()] + '</span><span class="ev-body"><strong>' + esc(e.title) + "</strong><small>" +
        '<span class="ev-kind kind-' + esc((e.kind || "").toLowerCase()) + '">' + esc(KIND[e.kind] || e.kind) + "</span> " + esc(range) + "</small></span></li>";
    }).join("");
  }
  function announcements(rows) {
    const list = $("ann-list");
    if (!rows) { list.closest(".dash-panel").hidden = true; return; }
    $("ann-empty").hidden = rows.length > 0;
    list.innerHTML = rows.slice(0, 3).map(function (a) {
      return '<li><a href="/dashboard/admin/announcements"><strong>' + esc(a.title) + "</strong><small>" + esc(a.issuer_name || "") +
        (a.published_at ? " · " + esc(VA.formatDateTime(a.published_at)) : "") + "</small><span>" + esc((a.body || "").slice(0, 120)) + "</span></a></li>";
    }).join("");
  }

  const ACTIONS = {
    "auth.login_success": "signed in", "auth.login_failed": "failed to sign in", "auth.logout": "signed out", "auth.password_changed": "changed their password",
  };
  function describe(row) {
    if (ACTIONS[row.action]) return ACTIONS[row.action];
    const parts = String(row.action || "").split(".");
    const verb = (parts[1] || parts[0] || "").replace(/_/g, " ");
    const thing = (parts.length > 1 ? parts[0] : row.entity_type || "").replace(/_/g, " ");
    return (thing ? thing + " " : "") + verb;
  }
  function activity(dash) {
    const rows = (dash && dash.recent_activity) || [];
    $("activity-empty").hidden = rows.length > 0;
    $("activity-list").innerHTML = rows.slice(0, 8).map(function (r) {
      return '<li><span class="tl-dot"></span><span class="tl-body"><b>' + esc(r.actor_username || "System") + "</b> " + esc(describe(r)) +
        '<small>' + esc(VA.formatDateTime(r.created_at)) + "</small></span></li>";
    }).join("");
  }
  function imports(dash) {
    const rows = (dash && dash.recent_imports) || [];
    $("imports-panel").hidden = rows.length === 0;
    $("imports-tbody").innerHTML = rows.map(function (row) {
      return "<tr><td>" + esc(row.filename) + '</td><td><span class="status-pill ' + esc((row.status || "").toLowerCase()) + '">' + esc(row.status) + '</span></td><td class="num">' +
        esc(row.imported_rows) + " / " + esc(row.total_rows) + "</td><td>" + esc(VA.formatDateTime(row.created_at)) + "</td></tr>";
    }).join("");
  }

  // ------------------------------------------------------------------ load
  async function load(me) {
    const now = new Date(), in14 = new Date(now.getTime() + 14 * 864e5);
    const keys = ["profileChanges", "dash", "overview", "accounts", "approvals", "applications", "complaints", "corrections", "unmarked", "purchases", "tasks",
      "inventory", "library", "assets", "staff", "gate", "hostel", "calendar", "announcements"];
    const urls = ["/api/v1/profile-changes/count", "/api/v1/reports/admin-dashboard", "/api/v1/reports/institute-overview", "/api/v1/accounts/summary", "/api/v1/approvals/count",
      "/api/v1/applications/overview", "/api/v1/complaints/analytics/overview", "/api/v1/attendance/correction-requests?status=PENDING",
      "/api/v1/attendance/needs-attention", "/api/v1/purchases/summary", "/api/v1/tasks/summary", "/api/v1/inventory/summary",
      "/api/v1/library/dashboard", "/api/v1/assets/summary", "/api/v1/staff-attendance/board", "/api/v1/gate/summary", "/api/v1/hostel/summary",
      "/api/v1/calendar?start=" + iso(now) + "&end=" + iso(in14), "/api/v1/announcements?limit=3"];
    const values = await Promise.all(urls.map(get));
    const d = {};
    keys.forEach(function (k, i) { d[k] = values[i]; });
    if (!d.dash && !d.overview) {
      const e = $("dash-error"); e.textContent = "Could not load the dashboard. Please refresh the page."; e.hidden = false;
    }
    const run = function (fn) { try { fn(); } catch (err) { if (window.console) console.error(err); } };
    run(function () { hero(me, d.overview); });
    run(function () { kpis(d); });
    run(function () { attention(d); });
    run(function () { moneyChart(d.accounts); });
    run(function () { students(d.dash); });
    run(function () { today(d); });
    run(function () { teaching(d.overview); });
    run(function () { calendar(d.calendar); });
    run(function () { announcements(d.announcements); });
    run(function () { activity(d.dash); });
    run(function () { imports(d.dash); });
  }

  document.addEventListener("vistadmin:ready", function (ev) { load(ev.detail && ev.detail.me); });
})();
