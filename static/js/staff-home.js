(function () {
  "use strict";

  // General staff dashboard (office boy, IT, driver, cleaner...). Only the person's own things:
  // today's arrival, this month's attendance, tasks, applications, purchase requests, pay, calendar and notices.
  const D = window.Dash, esc = D.esc;
  const $ = function (id) { return document.getElementById(id); };
  const today = D.iso(new Date());
  const OPEN_TASK = { PENDING: "Accept or reject", ACCEPTED: "In progress", AWAITING_CONFIRMATION: "Waiting for confirmation" };
  const APP_STATE = { SUBMITTED: "Sent", UNDER_REVIEW: "Being reviewed", MORE_INFO_NEEDED: "Needs your reply", APPROVED: "Approved", REJECTED: "Rejected", WITHDRAWN: "Withdrawn" };
  const BUY_STATE = { REQUESTED: "Waiting", APPROVED: "Approved", REJECTED: "Rejected", ORDERED: "Ordered", RECEIVED: "Received", PAID: "Done", CANCELLED: "Cancelled" };

  function shortDate(v) { return v ? new Date(v).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : ""; }
  function todayRow(att) { return ((att && att.days) || []).filter(function (r) { return r && r.work_date === today; })[0] || null; }

  function kpis(d) {
    const att = d.attendance, s = att && att.summary, row = todayRow(att);
    if (att) {
      const t = s.timing || {};
      const workday = row ? row.is_working_day : (t.working_days || []).indexOf((new Date().getDay() + 6) % 7) !== -1;
      if (row && row.excused) { D.set("k-today", "Excused"); D.set("k-today-sub", row.excuse_reason || "A day off for you"); }
      else if (row && row.check_in) {
        D.set("k-today", row.check_in);
        D.set("k-today-sub", row.check_out ? "Left at " + row.check_out : !row.is_working_day ? "Came in on a day off" : (row.late_minutes > 0 ? "Arrived " + row.late_minutes + " min late" : "Arrived on time"));
        D.bad("k-today-sub", !row.check_out && row.late_minutes > 0);
      } else if (!workday) { D.set("k-today", "Day off"); D.set("k-today-sub", "Not a working day for you"); }
      else { D.set("k-today", "Not in"); D.set("k-today-sub", t.in_time ? "Your time is " + t.in_time : "The gate marks you in"); }
      D.chip("st-today", t.in_time ? "Your timing " + t.in_time + "–" + t.out_time + (t.days_label ? " · " + t.days_label : "") : "", false);
      D.set("k-present", D.num(s.present));
      D.set("k-present-sub", "of " + D.num(s.scheduled) + " working days so far");
      D.set("k-late", D.num(s.late));
      D.set("k-late-sub", s.absent ? D.num(s.absent) + " days absent" : "No days absent");
      D.bad("k-late-sub", s.absent > 0);
    } else {
      ["k-today", "k-present", "k-late"].forEach(function (id) { D.set(id, "—"); });
      D.set("k-today-sub", "No attendance record yet");
    }
    const tk = d.tasks;
    if (tk) {
      D.set("k-tasks", D.num(tk.open + tk.needs_response));
      D.set("k-tasks-sub", tk.overdue ? D.num(tk.overdue) + " overdue" : (tk.needs_response ? D.num(tk.needs_response) + " to accept" : "Nothing overdue"));
      D.bad("k-tasks-sub", tk.overdue > 0);
    }
    const apps = (d.applications || []).filter(function (a) { return a.awaiting; });
    const buys = (d.purchases || []).filter(function (p) { return ["REQUESTED", "APPROVED", "ORDERED"].indexOf(p.status) !== -1; });
    D.set("k-req", D.num(apps.length + buys.length));
    D.set("k-req-sub", D.num(apps.length) + " applications · " + D.num(buys.length) + " purchases");
    const pay = (d.payslips || [])[0];
    D.set("k-pay", pay ? D.money(pay.net) : "—");
    D.set("k-pay-sub", pay ? pay.month_label : "No payslip yet");
  }

  function tasks(list) {
    if (!list) { D.hide("task-list"); return; }
    const open = list.filter(function (t) { return OPEN_TASK[t.status]; })
      .sort(function (a, b) { return String(a.deadline).localeCompare(String(b.deadline)); }).slice(0, 6);
    $("task-empty").hidden = open.length > 0;
    D.people("task-list", open.map(function (t) {
      return { title: t.title, sub: (t.assigner ? "From " + t.assigner.name + " · " : "") + OPEN_TASK[t.status],
               value: (t.is_overdue ? "Overdue " : "Due ") + shortDate(t.deadline), bad: t.is_overdue, href: "/dashboard/staff/tasks" };
    }));
  }

  function attention(d) {
    const items = [], s = d.attendance && d.attendance.summary;
    if (d.tasks) {
      items.push({ count: d.tasks.needs_response, text: "new tasks waiting for you to accept or reject", href: "/dashboard/staff/tasks" });
      items.push({ count: d.tasks.overdue, text: "of your tasks are overdue", href: "/dashboard/staff/tasks", tone: "red" });
    }
    (d.applications || []).filter(function (a) { return a.awaiting === "APPLICANT"; }).forEach(function (a) {
      items.push({ count: "!", text: "Your application needs a reply: " + a.subject, href: "/dashboard/staff/applications?open=" + a.id, tone: "red" });
    });
    if (s) items.push({ count: s.missing_out, text: "days this month with no leaving time. Tell the gate when you leave.", href: "/dashboard/staff/payslips", tone: "blue" });
    if (d.profile && d.profile.pending) items.push({ count: "!", text: "Your profile change is waiting for the office", href: "/dashboard/staff/profile", tone: "blue" });
    D.attention("attn-list", "attn-clear", items);
  }

  function month(att) {
    if (!att) { D.hide("att-facts"); D.set("att-ring-text", "No attendance has been recorded for you yet."); return; }
    const s = att.summary, pct = s.scheduled ? Math.round(100 * s.present / s.scheduled) : 0;
    $("att-ring").style.setProperty("--p", pct);
    D.set("att-ring-val", s.scheduled ? pct + "%" : "—");
    if (!s.scheduled) D.set("att-ring-text", "No working days yet this month.");
    D.facts("att-facts", [["Present", D.num(s.present)], ["Absent", D.num(s.absent), s.absent ? "bad" : ""], ["Late", D.num(s.late), s.late ? "bad" : ""],
      ["Left early", D.num(s.early)], ["Excused", D.num(s.excused)], ["No leaving time", D.num(s.missing_out), s.missing_out ? "bad" : ""]]);
  }

  function requests(d) {
    if (!d.applications && !d.purchases) { D.hide("req-list"); return; }
    const rows = (d.applications || []).map(function (a) {
      return { at: a.created_at, title: a.subject, sub: a.type_name + " · " + shortDate(a.created_at), value: APP_STATE[a.status] || a.status,
               bad: a.awaiting === "APPLICANT" || a.status === "REJECTED", href: "/dashboard/staff/applications?open=" + a.id };
    }).concat((d.purchases || []).map(function (p) {
      return { at: p.created_at, title: p.title, sub: "Purchase " + p.reference + " · " + D.money(p.estimated_total), value: BUY_STATE[p.status] || p.status,
               bad: p.status === "REJECTED", href: "/dashboard/staff/purchases" };
    })).sort(function (a, b) { return String(b.at).localeCompare(String(a.at)); }).slice(0, 6);
    $("req-empty").hidden = rows.length > 0;
    D.people("req-list", rows);
  }

  async function load(me) {
    D.greet("st-greet", "st-date", me);
    const d = await D.getAll({
      attendance: "/api/v1/staff-attendance/me",
      tasks: "/api/v1/tasks/summary",
      inbox: "/api/v1/tasks/inbox",
      applications: "/api/v1/applications/mine",
      purchases: "/api/v1/purchases/mine?limit=20",
      payslips: "/api/v1/payroll/me",
      profile: "/api/v1/profile-changes/me",
      calendar: D.upcoming(),
      announcements: "/api/v1/announcements?limit=1",
    });
    if (!d.tasks && !d.applications) { const e = $("st-error"); e.textContent = "Could not load your dashboard. Please refresh the page."; e.hidden = false; }
    D.run(function () { kpis(d); });
    D.run(function () { tasks(d.inbox); });
    D.run(function () { attention(d); });
    D.run(function () { month(d.attendance); });
    D.run(function () { requests(d); });
    D.run(function () { D.calendar("cal-list", "cal-empty", d.calendar, 3); });
    D.run(function () { D.announcements("ann-list", "ann-empty", d.announcements, 1, "/dashboard/staff/announcements"); });
  }

  document.addEventListener("vistadmin:ready", function (ev) { load(ev.detail && ev.detail.me); });
})();
