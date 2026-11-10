(function () {
  "use strict";

  // Hostel warden dashboard. Read-only: hostel summary, rooms with their residents, and the history of stays.
  const D = window.Dash, esc = D.esc;
  const $ = function (id) { return document.getElementById(id); };
  const today = D.iso(new Date());

  function residents(r) { return (r.residents || []).length; }
  function shortDate(iso) { return iso ? new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : ""; }
  function within(iso, days) { return iso && (new Date(today + "T00:00:00") - new Date(iso + "T00:00:00")) / 864e5 <= days; }

  function kpis(d) {
    const s = d.summary, rooms = (d.rooms || []).filter(function (r) { return r.is_active !== false; });
    if (s) {
      D.set("k-res", D.num(s.residents));
      D.set("k-res-sub", "in " + D.num(rooms.filter(function (r) { return residents(r); }).length) + " rooms");
      D.set("k-free", D.num(s.free_beds));
      D.set("k-free-sub", "of " + D.num(s.beds) + " beds");
      const occ = s.beds ? Math.round(100 * s.residents / s.beds) : 0;
      D.set("k-occ", s.beds ? occ + "%" : "—");
      D.set("k-occ-sub", s.beds ? "of all beds are taken" : "No beds yet");
      D.chip("wd-occ", s.beds ? occ + "% full · " + D.num(s.free_beds) + " beds free" : "", false);
      D.set("k-rooms", D.num(s.rooms));
      const full = rooms.filter(function (r) { return residents(r) >= r.capacity; }).length;
      const empty = rooms.filter(function (r) { return !residents(r); }).length;
      D.set("k-rooms-sub", D.num(full) + " full · " + D.num(empty) + " empty");
      D.set("k-unpaid", D.money(s.charges_unpaid));
      D.set("k-unpaid-sub", s.charges_unpaid > 0 ? "residents still owe this" : "Nothing owed");
      D.bad("k-unpaid-sub", s.charges_unpaid > 0);
    }
    const h = d.history || [];
    const ins = h.filter(function (x) { return within(x.from, 30); }).length;
    const outs = h.filter(function (x) { return x.to && within(x.to, 30); }).length;
    D.set("k-moves", D.num(ins + outs));
    D.set("k-moves-sub", D.num(ins) + " moved in · " + D.num(outs) + " left");
  }

  function roomGrid(rooms) {
    if (!rooms) { D.hide("room-grid"); return; }
    const list = rooms.filter(function (r) { return r.is_active !== false; }).sort(function (a, b) { return String(a.code).localeCompare(String(b.code), undefined, { numeric: true }); });
    $("room-empty").hidden = list.length > 0;
    $("room-legend").innerHTML = [["Taken", "blue"], ["Free", "grey"], ["Full room", "green"], ["Empty room", "amber"]].map(function (x) {
      return '<li><span class="dot dot-' + x[1] + '"></span>' + x[0] + "</li>";
    }).join("");
    $("room-grid").innerHTML = list.map(function (r) {
      const n = residents(r), cap = r.capacity || 0;
      const cls = n >= cap ? " is-full" : !n ? " is-empty" : "";
      let beds = "";
      for (let i = 0; i < cap; i++) beds += '<span class="bed' + (i < n ? " taken" : "") + '"></span>';
      const names = (r.residents || []).map(function (x) { return x.name; }).join(", ");
      return '<a class="room-tile' + cls + '" href="/dashboard/warden/hostel" title="' + esc(names || "Nobody") + '"><span class="room-code">' + esc(r.code) + "</span>" +
        '<span class="room-beds" aria-label="' + n + " of " + cap + ' beds taken">' + beds + "</span><small>" + n + "/" + cap + (r.gender && r.gender !== "ANY" ? " · " + (r.gender === "MALE" ? "boys" : "girls") : "") + "</small></a>";
    }).join("");
  }

  function attention(d) {
    const s = d.summary || {}, rooms = d.rooms || [], items = [];
    if (s.charges_unpaid > 0) items.push({ count: "!", text: D.money(s.charges_unpaid) + " of hostel charges is unpaid. Remind the residents.", href: "/dashboard/warden/hostel", tone: "red" });
    const dayOfMonth = new Date().getDate();
    if (s.residents && dayOfMonth <= 10) items.push({ count: "!", text: "Early in the month: add this month's hostel charge if you have not yet.", href: "/dashboard/warden/hostel", tone: "blue" });
    items.push({ count: rooms.filter(function (r) { return r.is_active !== false && !residents(r); }).length, text: "rooms are empty", href: "/dashboard/warden/hostel", tone: "blue" });
    items.push({ count: s.free_beds === 0 && s.beds ? "!" : 0, text: "The hostel is full. No free beds are left.", href: "/dashboard/warden/hostel", tone: "red" });
    if (d.tasks) {
      items.push({ count: d.tasks.needs_response, text: "tasks waiting for your answer", href: "/dashboard/warden/tasks" });
      items.push({ count: d.tasks.overdue, text: "of your tasks are overdue", href: "/dashboard/warden/tasks", tone: "red" });
    }
    (d.applications || []).filter(function (a) { return a.awaiting === "APPLICANT"; }).forEach(function (a) {
      items.push({ count: "!", text: "Your application needs a reply: " + a.subject, href: "/dashboard/warden/applications?open=" + a.id });
    });
    D.attention("attn-list", "attn-clear", items);
  }

  function blocks(rooms) {
    if (!rooms) { D.hide("block-list"); return; }
    const by = {};
    rooms.filter(function (r) { return r.is_active !== false; }).forEach(function (r) {
      const k = r.block || "No block";
      by[k] = by[k] || { beds: 0, taken: 0 };
      by[k].beds += r.capacity || 0; by[k].taken += residents(r);
    });
    const keys = Object.keys(by).sort();
    $("block-list").innerHTML = keys.map(function (k) {
      const b = by[k], pct = b.beds ? Math.round(100 * b.taken / b.beds) : 0;
      return '<li><div class="prog-head"><strong>' + esc(k) + "</strong><b>" + pct + '%</b></div><div class="prog-track"><span class="seg seg-blue" style="width:' + pct + '%"></span></div>' +
        "<small>" + D.num(b.taken) + " of " + D.num(b.beds) + " beds taken · " + D.num(b.beds - b.taken) + " free</small></li>";
    }).join("") || '<li class="dash-empty">No rooms yet.</li>';
  }

  function moves(h) {
    if (!h) { D.hide("move-list"); return; }
    const ev = [];
    h.forEach(function (x) {
      ev.push({ at: x.from, text: x.student + " moved into " + x.room });
      if (x.to) ev.push({ at: x.to, text: x.student + " left " + x.room + (x.reason ? " (" + x.reason + ")" : "") });
    });
    ev.sort(function (a, b) { return String(b.at).localeCompare(String(a.at)); });
    $("move-empty").hidden = ev.length > 0;
    $("move-list").innerHTML = ev.slice(0, 7).map(function (e) {
      return '<li><span class="tl-dot"></span><span class="tl-body">' + esc(e.text) + "<small>" + esc(shortDate(e.at)) + "</small></span></li>";
    }).join("");
  }

  // The administrator can open this panel directly (Panels menu) without "View as": they have no staff record,
  // so skip the personal lists (they would only fail) and say how to see a real person's page.
  function adminLooking(me, box) {
    const roles = (me && me.roles) || [];
    if (!me || me.viewing_as_by || roles.indexOf("ADMIN") === -1) return false;
    const e = $(box);
    if (e) {
      e.className = "dash-alert";
      e.innerHTML = 'You are looking at this panel as the administrator, so personal items (own attendance, pay, requests) are empty. ' +
        'To see one person\u2019s page, use <a href="/dashboard/admin/view-as">View as a person</a>.';
      e.hidden = false;
    }
    return true;
  }

  async function load(me) {
    D.greet("wd-greet", "wd-date", me);
    const urls = {
      summary: "/api/v1/hostel/summary",
      rooms: "/api/v1/hostel/rooms",
      history: "/api/v1/hostel/history",
      tasks: "/api/v1/tasks/summary",
      calendar: D.upcoming(),
      announcements: "/api/v1/announcements?limit=12",
    };
    if (!adminLooking(me, "wd-note")) urls.applications = "/api/v1/applications/mine";
    const d = await D.getAll(urls);
    if (!d.summary) { const e = $("wd-error"); e.textContent = "Could not load the hostel summary. Please refresh the page."; e.hidden = false; }
    D.run(function () { kpis(d); });
    D.run(function () { roomGrid(d.rooms); });
    D.run(function () { attention(d); });
    D.run(function () { blocks(d.rooms); });
    D.run(function () { moves(d.history); });
    D.run(function () { D.calendar("cal-list", "cal-empty", d.calendar, 3); });
    D.run(function () { D.announcements("ann-list", "ann-empty", d.announcements, 1, "/dashboard/warden"); });
  }

  document.addEventListener("vistadmin:ready", function (ev) { load(ev.detail && ev.detail.me); });
})();