(function () {
  "use strict";

  // Library dashboard (librarian; the administration opens it from its "Library" link). Read-only.
  const D = window.Dash, VA = window.VistAdmin, esc = D.esc;
  const $ = function (id) { return document.getElementById(id); };
  const today = D.iso(new Date());

  function daysBetween(a, b) { return Math.round((new Date(b + "T00:00:00") - new Date(a + "T00:00:00")) / 864e5); }
  function shortDate(iso) { return new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" }); }
  function who(i) { return (i.borrower_name || "—") + (i.borrower_type === "EMPLOYEE" ? " (staff)" : ""); }

  function kpis(d) {
    const s = d.stats;
    if (s) {
      D.set("stat-titles", D.num(s.total_titles));
      D.set("stat-titles-sub", D.num(s.total_copies) + " copies on the shelves");
      D.set("stat-available", D.num(s.available_copies));
      D.set("stat-available-sub", s.total_copies ? Math.round(100 * s.available_copies / s.total_copies) + "% of all copies" : "No copies yet");
      D.set("stat-issued", D.num(s.issued_copies));
      D.set("stat-overdue", D.num(s.overdue_count));
      D.set("stat-requests", D.num(s.pending_requests_count));
      D.set("stat-requests-sub", s.pending_requests_count ? "approve or reject them" : "Nothing waiting");
    }
    const active = d.active || [];
    const dueToday = active.filter(function (i) { return i.due_date === today; }).length;
    const week = D.iso(D.addDays(7));
    const dueWeek = active.filter(function (i) { return i.due_date >= today && i.due_date <= week; }).length;
    D.set("stat-issued-sub", dueToday ? D.num(dueToday) + " due back today" : "none due back today");
    D.set("stat-week", D.num(dueWeek));
    D.set("stat-week-sub", "in the next 7 days");
    const over = d.overdue || [];
    if (over.length) {
      const worst = Math.max.apply(null, over.map(function (i) { return daysBetween(i.due_date, today); }));
      D.set("stat-overdue-sub", "the oldest is " + worst + " days late");
      D.bad("stat-overdue-sub", true);
    } else D.set("stat-overdue-sub", "Nothing overdue");
    D.chip("lb-due", dueToday ? D.num(dueToday) + " book" + (dueToday === 1 ? "" : "s") + " due back today" : "", false);
  }

  function overdue(rows) {
    if (!rows) { D.hide("over-list"); return; }
    const list = rows.slice().sort(function (a, b) { return a.due_date.localeCompare(b.due_date); }).slice(0, 6);
    $("over-empty").hidden = list.length > 0;
    D.people("over-list", list.map(function (i) {
      const late = daysBetween(i.due_date, today);
      return { title: who(i), sub: i.book_title + " · " + i.accession_code + " · due " + shortDate(i.due_date), value: late + " day" + (late === 1 ? "" : "s") + " late", bad: true, href: "/dashboard/library/overdue" };
    }));
  }

  function attention(d) {
    const s = d.stats || {}, active = d.active || [];
    const items = [
      { count: s.pending_requests_count, text: "borrow requests waiting for a decision", href: "/dashboard/library/requests" },
      { count: (d.overdue || []).length, text: "loans are overdue: remind the borrowers", href: "/dashboard/library/overdue", tone: "red" },
      { count: active.filter(function (i) { return i.due_date === today; }).length, text: "books are due back today", href: "/dashboard/library/issue-return#issued", tone: "blue" },
    ];
    const out = (d.books || []).filter(function (b) { return b.total_copies > 0 && b.available_copies === 0; }).length;
    items.push({ count: out, text: "titles have every copy out on loan", href: "/dashboard/library/catalogue", tone: "blue" });
    if (d.tasks) {
      items.push({ count: d.tasks.needs_response, text: "tasks waiting for your answer", href: "/dashboard/library/tasks" });
      items.push({ count: d.tasks.overdue, text: "of your tasks are overdue", href: "/dashboard/library/tasks", tone: "red" });
    }
    D.attention("attn-list", "attn-clear", items);
  }

  function collection(d) {
    const s = d.stats;
    if (!s) { D.hide("col-bar"); return; }
    const over = s.overdue_count || 0, onLoan = Math.max(0, (s.issued_copies || 0) - over), avail = s.available_copies || 0;
    const other = Math.max(0, (s.total_copies || 0) - avail - (s.issued_copies || 0));
    const parts = [["On the shelf", avail, "green"], ["On loan", onLoan, "blue"], ["Overdue", over, "red"], ["Lost / repair", other, "amber"]];
    const total = parts.reduce(function (n, p) { return n + p[1]; }, 0);
    $("col-bar").innerHTML = total ? parts.filter(function (p) { return p[1]; }).map(function (p) {
      return '<span class="seg seg-' + p[2] + '" style="width:' + (100 * p[1] / total).toFixed(2) + '%"></span>';
    }).join("") : '<span class="seg seg-empty" style="width:100%"></span>';
    $("col-legend").innerHTML = parts.map(function (p) { return '<li><span class="dot dot-' + p[2] + '"></span>' + p[0] + "<b>" + D.num(p[1]) + "</b></li>"; }).join("");
    const by = {};
    (d.books || []).forEach(function (b) { const k = b.category || "Other"; by[k] = (by[k] || 0) + (b.total_copies || 0); });
    const cats = Object.keys(by).map(function (k) { return { label: k, count: by[k] }; }).sort(function (a, b) { return b.count - a.count; }).slice(0, 6);
    if (window.Charts) window.Charts.hbars($("col-cats"), cats, { empty: "No books in the catalogue yet." });
  }

  function soon(active) {
    if (!active) { D.hide("soon-list"); return; }
    const week = D.iso(D.addDays(7));
    const list = active.filter(function (i) { return i.due_date >= today && i.due_date <= week; }).sort(function (a, b) { return a.due_date.localeCompare(b.due_date); }).slice(0, 6);
    $("soon-empty").hidden = list.length > 0;
    D.people("soon-list", list.map(function (i) {
      const n = daysBetween(today, i.due_date);
      return { title: i.book_title, sub: who(i) + " · " + i.accession_code, value: n === 0 ? "Today" : n === 1 ? "Tomorrow" : "in " + n + " days" };
    }));
  }

  function activity(s) {
    if (!s) { D.hide("act-list"); return; }
    const ev = [];
    (s.recent_issues || []).forEach(function (i) { ev.push({ at: i.issued_at, text: "lent “" + i.book_title + "” to " + who(i) }); });
    (s.recent_returns || []).forEach(function (i) { ev.push({ at: i.returned_at || i.issued_at, text: who(i) + " returned “" + i.book_title + "”" + (i.fine_amount ? " (fine " + D.money(i.fine_amount) + ")" : "") }); });
    ev.sort(function (a, b) { return String(b.at).localeCompare(String(a.at)); });
    $("act-empty").hidden = ev.length > 0;
    $("act-list").innerHTML = ev.slice(0, 7).map(function (e) {
      return '<li><span class="tl-dot"></span><span class="tl-body">' + esc(e.text) + "<small>" + esc(e.at ? shortDate(String(e.at).slice(0, 10)) : "") + "</small></span></li>";
    }).join("");
  }

  function requests(rows) {
    if (!rows) { D.hide("req-list"); return; }
    const list = rows.filter(function (r) { return r.status === "PENDING"; }).slice(0, 5);
    $("req-empty").hidden = list.length > 0;
    D.people("req-list", list.map(function (r) {
      return { title: r.borrower_name || "—", sub: r.book_title + (r.borrower_identifier ? " · " + r.borrower_identifier : "") + (r.note ? " · " + r.note : ""),
        value: VA.formatDateTime(r.created_at).split(",")[0], href: "/dashboard/library/requests" };
    }));
  }

  async function load(me) {
    D.greet("lb-greet", "lb-date", me);
    const d = await D.getAll({
      stats: "/api/v1/library/dashboard",
      overdue: "/api/v1/library/issues/overdue",
      active: "/api/v1/library/issues/active",
      requests: "/api/v1/library/requests",
      books: "/api/v1/library/books?page_size=200",
      tasks: "/api/v1/tasks/summary",
      calendar: D.upcoming(),
      announcements: "/api/v1/announcements?limit=3",
    });
    if (d.books && d.books.items) d.books = d.books.items;
    if (!d.stats) { const e = $("lb-error"); e.textContent = "Could not load the dashboard. Please refresh the page."; e.hidden = false; }
    D.run(function () { kpis(d); });
    D.run(function () { overdue(d.overdue); });
    D.run(function () { attention(d); });
    D.run(function () { collection(d); });
    D.run(function () { soon(d.active); });
    D.run(function () { activity(d.stats); });
    D.run(function () { requests(d.requests); });
    D.run(function () { D.calendar("cal-list", "cal-empty", d.calendar, 4); });
    D.run(function () { D.announcements("ann-list", "ann-empty", d.announcements, 3, "/dashboard/library/announcements"); });
  }

  document.addEventListener("vistadmin:ready", function (ev) { load(ev.detail && ev.detail.me); });
})();
