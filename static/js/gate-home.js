(function () {
  "use strict";

  // Gate dashboard (security guard). Read-only: the gate summary, who is inside, today's visitors and the staff board.
  const D = window.Dash, esc = D.esc;
  const $ = function (id) { return document.getElementById(id); };
  const STATE = { IN: ["In", "active"], OUT: ["Gone home", "draft"], EXCUSED: ["Excused", "pending"], NOT_IN: ["Not in yet", "rejected"] };

  function clock() {
    D.set("gt-clock", new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }));
  }
  function since(mins) {
    if (mins == null) return "";
    if (mins < 60) return mins + " min";
    const h = Math.floor(mins / 60), m = mins % 60;
    return h + " h" + (m ? " " + m + " min" : "");
  }
  function due(board) { return ((board && board.people) || []).filter(function (p) { return p.working_today; }); }

  function kpis(d) {
    const s = d.summary;
    if (s) {
      D.set("k-inside", D.num(s.inside_now));
      const people = (d.inside || []).reduce(function (n, v) { return n + (v.people_count || 1); }, 0);
      D.set("k-inside-sub", s.inside_now ? D.num(people) + " people in " + D.num(s.inside_now) + " groups" : "Nobody inside");
      D.set("k-visits", D.num(s.entries_today));
      D.set("k-visits-sub", D.num(s.people_today) + " people came in today");
      D.set("k-old", D.num(s.inside_since_before_today));
      D.set("k-old-sub", s.inside_since_before_today ? "never marked out: check them" : "Nobody left over");
      D.bad("k-old-sub", s.inside_since_before_today);
    }
    const p = due(d.board);
    const n = function (st) { return p.filter(function (x) { return x.state === st; }).length; };
    D.set("k-staff", D.num(n("IN")));
    D.set("k-staff-sub", p.length ? "of " + p.length + " due today" : "Nobody due today");
    D.set("k-notin", D.num(n("NOT_IN")));
    D.set("k-notin-sub", !p.length ? "Nobody due today" : n("NOT_IN") ? "not marked in yet" : "Everyone is in");
    D.set("k-left", D.num(n("OUT")));
    D.set("k-left-sub", "marked out today");
  }

  function inside(rows) {
    if (!rows) { D.hide("inside-list"); return; }
    const list = rows.slice().sort(function (a, b) { return (b.minutes_inside || 0) - (a.minutes_inside || 0); });
    $("inside-empty").hidden = list.length > 0;
    D.people("inside-list", list.slice(0, 8).map(function (v) {
      const long = (v.minutes_inside || 0) >= 180;
      return { title: v.visitor_name + (v.people_count > 1 ? " +" + (v.people_count - 1) : ""),
        sub: v.purpose + (v.person_to_meet ? " · to meet " + v.person_to_meet : "") + (v.vehicle_no ? " · " + v.vehicle_no : "") + " · in at " + v.entry_time,
        value: since(v.minutes_inside), bad: long, href: "/dashboard/gate" };
    }));
  }

  function attention(d) {
    const s = d.summary || {}, items = [];
    items.push({ count: s.inside_since_before_today, text: "visitors came in before today and were never marked out", href: "/dashboard/gate", tone: "red" });
    const long = (d.inside || []).filter(function (v) { return (v.minutes_inside || 0) >= 180; }).length;
    items.push({ count: long, text: "visitors have been inside for more than 3 hours", href: "/dashboard/gate" });
    const p = due(d.board), now = new Date(), hm = String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
    const late = p.filter(function (x) { return x.state === "NOT_IN" && x.timing && x.timing.in_time && x.timing.in_time < hm; }).length;
    items.push({ count: late, text: "staff were due in already and are not marked in", href: "/dashboard/gate/staff", tone: "blue" });
    if (d.tasks) {
      items.push({ count: d.tasks.needs_response, text: "tasks waiting for your answer", href: "/dashboard/gate/tasks" });
      items.push({ count: d.tasks.overdue, text: "of your tasks are overdue", href: "/dashboard/gate/tasks", tone: "red" });
    }
    D.attention("attn-list", "attn-clear", items);
  }

  function staff(board) {
    if (!board) { D.hide("staff-list"); return; }
    const p = due(board);
    $("staff-empty").hidden = p.length > 0;
    const n = function (st) { return p.filter(function (x) { return x.state === st; }).length; };
    $("staff-meter").hidden = !p.length;
    $("staff-meter").innerHTML = p.length ? '<span class="seg seg-green" style="width:' + (100 * n("IN") / p.length).toFixed(1) + '%"></span><span class="seg seg-blue" style="width:' +
      (100 * (n("OUT") + n("EXCUSED")) / p.length).toFixed(1) + '%"></span>' : "";
    const order = { NOT_IN: 0, IN: 1, EXCUSED: 2, OUT: 3 };
    D.people("staff-list", p.slice().sort(function (a, b) { return order[a.state] - order[b.state]; }).slice(0, 8).map(function (x) {
      const st = STATE[x.state] || [x.state, ""], day = x.day || {};
      return { title: x.name, sub: (x.job_title || "") + " · " + (x.timing ? x.timing.in_time + "–" + x.timing.out_time : "") + (day.check_in ? " · in " + day.check_in : "") + (day.late_minutes ? " (" + day.late_minutes + " min late)" : ""),
        value: st[0], bad: x.state === "NOT_IN", href: "/dashboard/gate/staff" };
    }));
  }

  function log(rows) {
    if (!rows) { D.hide("log-list"); return; }
    const today = D.iso(new Date()), ev = [];
    rows.filter(function (v) { return v.entry_date === today; }).forEach(function (v) {
      ev.push({ t: v.entry_time, text: v.visitor_name + " came in (" + v.purpose + ")" });
      if (v.exit_time) ev.push({ t: v.exit_time, text: v.visitor_name + " left" });
    });
    ev.sort(function (a, b) { return String(b.t).localeCompare(String(a.t)); });
    $("log-empty").hidden = ev.length > 0;
    $("log-list").innerHTML = ev.slice(0, 8).map(function (e) {
      return '<li><span class="tl-dot"></span><span class="tl-body">' + esc(e.text) + "<small>" + esc(e.t) + "</small></span></li>";
    }).join("");
  }

  async function load(me) {
    D.greet("gt-greet", "gt-date", me);
    clock(); setInterval(clock, 30000);
    const today = D.iso(new Date());
    const d = await D.getAll({
      summary: "/api/v1/gate/summary",
      inside: "/api/v1/gate/inside",
      visitors: "/api/v1/gate/visitors?from_date=" + today + "&to_date=" + today,
      board: "/api/v1/staff-attendance/board",
      tasks: "/api/v1/tasks/summary",
      calendar: D.upcoming(),
      announcements: "/api/v1/announcements?limit=12",
    });
    if (!d.summary) { const e = $("gt-error"); e.textContent = "Could not load the gate summary. Please refresh the page."; e.hidden = false; }
    D.run(function () { kpis(d); });
    D.run(function () { inside(d.inside); });
    D.run(function () { attention(d); });
    D.run(function () { staff(d.board); });
    D.run(function () { log(d.visitors); });
    D.run(function () { D.calendar("cal-list", "cal-empty", d.calendar, 3); });
    D.run(function () { D.announcements("ann-list", "ann-empty", d.announcements, 1, "/dashboard/gate/announcements"); });
  }

  document.addEventListener("vistadmin:ready", function (ev) { load(ev.detail && ev.detail.me); });
})();
