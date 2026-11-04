(function () {
  "use strict";

  // Tasks → My to-do (every member of staff) and To-do reports (administration). API: /api/v1/todos
  const VA = window.VistAdmin, esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/todos";
  const STATUS = [["TODO", "To do"], ["IN_PROGRESS", "In progress"], ["DONE", "Done"], ["NOT_DONE", "Not done"]];
  if (!$("td-list")) return;

  // A reminder links to /tasks?day=2026-11-04#todo: open that day's list.
  const askedDay = new URLSearchParams(window.location.search).get("day");
  let day = /^\d{4}-\d{2}-\d{2}$/.test(askedDay || "") ? askedDay : null, today = null, data = null;

  function msg(r, fallback) { return (r.body && r.body.error && r.body.error.message) || fallback; }
  function iso(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
  function shift(isoDay, n) { const d = new Date(isoDay + "T12:00:00"); d.setDate(d.getDate() + n); return iso(d); }
  function nice(isoDay) {
    const d = new Date(isoDay + "T12:00:00");
    return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  }
  function json(method, body) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }; }

  // ------------------------------------------------------------------ my list
  async function load() {
    const r = await VA.fetchJson(API + "/me" + (day ? "?day=" + day : ""), { quiet: true });
    if (!r.ok) return VA.toast(msg(r, "Could not load your to-do list."), "error");
    data = r.body; day = data.date; today = data.today;
    $("td-date").value = day;
    render();
  }

  function render() {
    const items = data.items;
    const when = day === today ? "Today" : day === shift(today, -1) ? "Yesterday" : day === shift(today, 1) ? "Tomorrow" : nice(day);
    const pct = data.total ? Math.round(100 * data.done / data.total) : 0;
    $("td-progress").innerHTML = "<b>" + esc(when) + "</b>" + (when !== nice(day) ? ' <span class="cc-meta">' + esc(nice(day)) + "</span>" : "") +
      (data.total ? '<div class="td-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + pct + '"><span style="width:' + pct + '%"></span></div>' +
        '<small>' + data.done + " of " + data.total + " done</small>" : "");
    $("td-form").hidden = !data.editable;
    $("td-locked").hidden = data.editable;
    const carry = data.unfinished_before;
    $("td-carry").hidden = !carry;
    if (carry) $("td-carry-text").textContent = carry.count + " unfinished item" + (carry.count === 1 ? "" : "s") + " from " + nice(carry.date) + ".";
    $("td-empty").hidden = items.length > 0;
    $("td-list").innerHTML = items.map(function (i) {
      const locked = !i.editable;
      const select = locked ? '<span class="td-status s-' + esc(i.status) + '">' + esc(i.status_label) + "</span>" :
        '<select class="select-input td-select s-' + esc(i.status) + '" data-status="' + esc(i.id) + '" aria-label="Status of ' + esc(i.title) + '">' +
          STATUS.map(function (s) { return '<option value="' + s[0] + '"' + (s[0] === i.status ? " selected" : "") + ">" + s[1] + "</option>"; }).join("") + "</select>";
      return '<li class="td-item s-' + esc(i.status) + '">' +
        '<div class="td-text"><span class="td-title">' + esc(i.title) + "</span>" + (i.carried ? ' <span class="td-tag">carried over</span>' : "") +
          (i.note ? '<small class="td-note">' + esc(i.note) + "</small>" : "") + "</div>" + select +
        (locked ? "" : '<button type="button" class="row-action" data-edit="' + esc(i.id) + '">Edit</button>' +
          (i.status !== "DONE" && day >= today ? '<button type="button" class="row-action danger" data-del="' + esc(i.id) + '" aria-label="Remove ' + esc(i.title) + '">Remove</button>' : "")) +
        "</li>";
    }).join("");
  }

  $("td-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const title = $("td-title").value.trim();
    if (title.length < 2) { $("td-title").focus(); return VA.toast("Write what you plan to do.", "error"); }
    $("td-add").disabled = true;
    const r = await VA.fetchJson(API, json("POST", { work_date: day, title: title, note: $("td-note").value.trim() || null }));
    $("td-add").disabled = false;
    if (!r.ok) return VA.toast(msg(r, "Could not add it."), "error");
    $("td-title").value = ""; $("td-note").value = ""; $("td-title").focus();
    load();
  });

  $("td-list").addEventListener("change", async function (e) {
    const sel = e.target.closest("[data-status]");
    if (!sel) return;
    const r = await VA.fetchJson(API + "/" + sel.getAttribute("data-status"), json("PATCH", { status: sel.value }));
    if (!r.ok) { VA.toast(msg(r, "Could not change it."), "error"); return load(); }
    load();
  });

  $("td-list").addEventListener("click", async function (e) {
    const edit = e.target.closest("[data-edit]"), del = e.target.closest("[data-del]");
    if (edit) {
      const item = data.items.filter(function (i) { return i.id === edit.getAttribute("data-edit"); })[0];
      const title = await VA.ask({ title: "Change this item", label: "What you plan to do", value: item.title, ok: "Save", min: 2 });
      if (!title) return;
      const r = await VA.fetchJson(API + "/" + item.id, json("PATCH", { title: title }));
      if (!r.ok) return VA.toast(msg(r, "Could not change it."), "error");
      load();
    } else if (del) {
      const r = await VA.fetchJson(API + "/" + del.getAttribute("data-del"), { method: "DELETE" });
      if (!r.ok) return VA.toast(msg(r, "Could not remove it."), "error");
      load();
    }
  });

  $("td-carry-btn").addEventListener("click", async function () {
    const r = await VA.fetchJson(API + "/carry-over", json("POST", { to_date: day }));
    if (!r.ok) return VA.toast(msg(r, "Could not bring them over."), "error");
    VA.toast(r.body.moved + " item" + (r.body.moved === 1 ? "" : "s") + " brought over.");
    load();
  });
  $("td-prev").addEventListener("click", function () { day = shift(day, -1); load(); });
  $("td-next").addEventListener("click", function () { day = shift(day, 1); load(); });
  $("td-today").addEventListener("click", function () { day = null; load(); });
  $("td-date").addEventListener("change", function () { if ($("td-date").value) { day = $("td-date").value; load(); } });

  // ------------------------------------------------------------------ administration: monthly report
  let report = null;
  function month() { return $("tr-month").value; }
  async function loadReport() {
    const r = await VA.fetchJson(API + "/report" + (month() ? "?month=" + month() : ""));
    if (!r.ok) return VA.toast(msg(r, "Could not load the to-do report."), "error");
    report = r.body;
    if (!$("tr-month").value) $("tr-month").value = report.month;
    $("tr-csv").href = API + "/report.csv?month=" + report.month;
    $("tr-csv").setAttribute("data-download", "todo-" + report.month + ".csv");
    $("tr-pdf").href = API + "/report.pdf?month=" + report.month;
    $("tr-pdf").setAttribute("data-download", "todo-" + report.month + ".pdf");
    const t = report.totals;
    $("tr-totals").innerHTML = [[t.people_with_lists + " of " + t.people, "staff wrote a list in " + report.label], [t.items, "items planned"],
      [t.done, "done"], [t.not_done + t.left_open, "not done"], [t.done_pct == null ? "—" : t.done_pct + "%", "done overall"]]
      .map(function (x) { return '<div class="td-total"><b>' + esc(String(x[0])) + "</b><span>" + esc(x[1]) + "</span></div>"; }).join("");
    renderReport();
  }
  function renderReport() {
    const q = $("tr-q").value.trim().toLowerCase();
    const rows = report.people.filter(function (p) { return !q || (p.name + " " + p.code + " " + p.department).toLowerCase().indexOf(q) !== -1; });
    $("tr-body").innerHTML = rows.map(function (p) {
      return '<tr class="' + (p.days_with_list ? "" : "td-none") + '"><td><button type="button" class="link-btn" data-person="' + esc(p.user_id) + '">' + esc(p.name) + "</button>" +
        (p.code ? ' <small class="cc-meta">' + esc(p.code) + "</small>" : "") + "</td><td>" + esc(p.department || "") + "</td><td>" + p.days_with_list +
        (report.working_days_so_far ? ' <small class="cc-meta">/ ' + report.working_days_so_far + "</small>" : "") + "</td><td>" + p.items + "</td><td>" + p.done + "</td><td>" + p.in_progress +
        "</td><td>" + (p.not_done + p.left_open) + "</td><td>" + p.carried + "</td><td>" + (p.done_pct == null ? "—" : p.done_pct + "%") + "</td><td>" +
        esc(p.last_day ? nice(p.last_day) : "No list") + "</td></tr>";
    }).join("");
    $("tr-empty").hidden = rows.length > 0;
  }
  async function openPerson(userId) {
    const r = await VA.fetchJson(API + "/report/person/" + userId + "?month=" + report.month);
    if (!r.ok) return VA.toast(msg(r, "Could not load this person's lists."), "error");
    const d = r.body, p = d.person;
    $("tr-d-title").textContent = p.name + " — " + d.label;
    $("tr-d-meta").textContent = [p.code, p.department].filter(Boolean).join(" · ");
    $("tr-d-csv").href = API + "/report.csv?month=" + d.month + "&user_id=" + userId;
    $("tr-d-csv").setAttribute("data-download", "todo-" + d.month + "-" + (p.code || "person") + ".csv");
    $("tr-d-pdf").href = API + "/report.pdf?month=" + d.month + "&user_id=" + userId;
    $("tr-d-pdf").setAttribute("data-download", "todo-" + d.month + "-" + (p.code || "person") + ".pdf");
    const label = { LEFT_OPEN: "Left open" };
    $("tr-d-days").innerHTML = d.days.length ? d.days.map(function (x) {
      return '<h3 class="dash-sub-h">' + esc(nice(x.date)) + '</h3><ul class="td-list td-readonly">' + x.items.map(function (i) {
        const st = i.effective === "LEFT_OPEN" ? "LEFT_OPEN" : i.status;
        return '<li class="td-item s-' + esc(st) + '"><div class="td-text"><span class="td-title">' + esc(i.title) + "</span>" + (i.carried ? ' <span class="td-tag">carried over</span>' : "") +
          (i.note ? '<small class="td-note">' + esc(i.note) + "</small>" : "") + '</div><span class="td-status s-' + esc(st) + '">' + esc(label[st] || i.status_label) + "</span></li>";
      }).join("") + "</ul>";
    }).join("") : '<p class="empty-state">No to-do list in this month.</p>';
    $("tr-dialog").showModal();
  }

  if ($("tr-body")) {
    $("tr-month").addEventListener("change", loadReport);
    $("tr-q").addEventListener("input", function () { if (report) renderReport(); });
    $("tr-body").addEventListener("click", function (e) { const b = e.target.closest("[data-person]"); if (b) openPerson(b.getAttribute("data-person")); });
    $("tr-d-close").addEventListener("click", function () { $("tr-dialog").close(); });
  }

  let started = false, reportStarted = false;
  function start() {
    if (!started) { started = true; load(); }
    if ($("tr-body") && !reportStarted && !document.querySelector('[data-tab-panel="todo-report"]').hidden) { reportStarted = true; loadReport(); }
  }
  document.querySelectorAll('.portal-tab[data-tab="todo"], .portal-tab[data-tab="todo-report"]').forEach(function (b) {
    b.addEventListener("click", function () { setTimeout(start, 0); });
  });
  document.addEventListener("vistadmin:ready", function () {
    if (!document.querySelector('[data-tab-panel="todo"]').hidden || ($("tr-body") && !document.querySelector('[data-tab-panel="todo-report"]').hidden)) start();
  });
})();