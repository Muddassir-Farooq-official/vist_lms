(function () {
  "use strict";

  // Staff attendance: /api/v1/staff-attendance/...  (guard: today's board; office: + month; administration: + corrections, timings, holidays, import)
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/staff-attendance";
  const $ = function (id) { return document.getElementById(id); };
  const msg = function (r, fallback) { return (r.body && r.body.error && r.body.error.message) || fallback; };
  const json = function (method, data) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }; };
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const ymd = function (d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); };
  const human = function (m) { const h = Math.floor(m / 60); return h ? h + " h " + (m % 60) + " min" : m + " min"; };
  let canManage = false, canView = false, today = null, timer = null, timingTarget = null, dayTarget = null, monthCache = null;

  // ------------------------------------------------------------------ daily board
  function stateCell(p) {
    const d = p.day;
    if (d && d.excused) return '<span class="status-pill enrolled">Excused</span><small>' + esc(d.excuse_reason || "") + "</small>";
    if (!p.working_today && !(d && d.check_in)) return '<span class="status-pill">Off day</span>';
    const parts = [];
    if (d && d.late_minutes > 0) parts.push('<span class="status-pill dropped">Late ' + human(d.late_minutes) + "</span>");
    if (d && d.early_minutes > 0) parts.push('<span class="status-pill dropped">Left early ' + human(d.early_minutes) + "</span>");
    if (!parts.length) parts.push(p.state === "NOT_IN" ? '<span class="status-pill pending">Not in yet</span>' : '<span class="status-pill active">On time</span>');
    return parts.join(" ");
  }

  async function loadBoard() {
    if (!$("sb-body")) return;
    const p = new URLSearchParams();
    if ($("sb-q").value.trim()) p.set("q", $("sb-q").value.trim());
    if ($("sb-state").value) p.set("state", $("sb-state").value);
    if ($("sb-day") && $("sb-day").value && canView) p.set("day", $("sb-day").value);
    const r = await VA.fetchJson(API + "/board?" + p.toString());
    if (!r.ok) return VA.toast(msg(r, "Could not load the list."), "error");
    today = r.body.today;
    const isToday = String(r.body.day) === String(r.body.today);
    const tb = $("sb-body");
    tb.innerHTML = "";
    r.body.people.forEach(function (x) {
      const d = x.day, t = x.timing;
      const tr = document.createElement("tr");
      tr.innerHTML = "<td><strong>" + esc(x.name) + "</strong><small>" + esc(x.employee_code + " · " + (x.job_title || "")) + "</small></td>" +
        "<td>" + esc(t.in_time + "–" + t.out_time) + "<small>" + esc(t.days_label + (t.grace_in_minutes || t.grace_out_minutes ? " · relax " + t.grace_in_minutes + "/" + t.grace_out_minutes + " min" : "")) + "</small></td>" +
        "<td><b>" + esc(d && d.check_in || "—") + "</b></td><td><b>" + esc(d && d.check_out || "—") + "</b></td><td>" + stateCell(x) + '</td><td class="row-actions"></td>';
      const cell = tr.querySelector(".row-actions");
      const add = function (label, cls, fn) { const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn " + (cls || ""); b.textContent = label; b.addEventListener("click", fn); cell.appendChild(b); };
      if (isToday && !(d && d.excused)) {
        if (!d || !d.check_in) add("In", "", function () { mark(x, "IN"); });
        else if (!d.check_out) add("Out", "", function () { mark(x, "OUT"); });
        if (d && d.check_in) add("Undo", "secondary-btn", function () { undo(x); });
      }
      if (canManage) add("Correct", "secondary-btn", function () { openDay(x.employee_id, x.name, r.body.day, d); });
      tb.appendChild(tr);
    });
    $("sb-empty").hidden = r.body.people.length > 0;
    const inCount = r.body.people.filter(function (x) { return x.state === "IN" || x.state === "OUT"; }).length;
    const late = r.body.people.filter(function (x) { return x.day && x.day.late_minutes > 0; }).length;
    $("sb-count").textContent = (isToday ? "Today, " + r.body.now + ": " : r.body.day + ": ") + inCount + " marked in, " + late + " late, " + r.body.people.length + " listed.";
  }
  async function mark(x, action) {
    const r = await VA.fetchJson(API + "/mark", json("POST", { employee_id: x.employee_id, action: action }));
    if (!r.ok) return VA.toast(msg(r, "Could not mark it."), "error");
    const d = r.body.day;
    VA.toast(x.name + (action === "IN" ? " in at " + d.check_in + (d.late_minutes ? " (late " + human(d.late_minutes) + ")" : "") : " out at " + d.check_out + (d.early_minutes ? " (left early " + human(d.early_minutes) + ")" : "")) + ".", d.late_minutes || d.early_minutes ? "error" : undefined);
    loadBoard();
  }
  async function undo(x) {
    if (!window.confirm("Take back the latest mark for " + x.name + "?")) return;
    const r = await VA.fetchJson(API + "/undo/" + x.employee_id, { method: "POST" });
    if (!r.ok) return VA.toast(msg(r, "Could not take it back."), "error");
    VA.toast("Taken back."); loadBoard();
  }
  if ($("sb-q")) {
    $("sb-q").addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(loadBoard, 300); });
    $("sb-state").addEventListener("change", loadBoard);
    if ($("sb-day")) $("sb-day").addEventListener("change", loadBoard);
  }

  // ------------------------------------------------------------------ correcting a day
  function openDay(employeeId, name, day, d) {
    dayTarget = { employee_id: employeeId, day: day };
    $("dc-title").textContent = name; $("dc-sub").textContent = "Day: " + day + (d && d.expected_in ? " · expected " + d.expected_in + "–" + d.expected_out : "");
    $("dc-in").value = d && d.check_in || ""; $("dc-out").value = d && d.check_out || ""; $("dc-excused").checked = !!(d && d.excused);
    $("dc-reason").value = d && d.excuse_reason || ""; $("dc-reason-wrap").hidden = !(d && d.excused); $("dc-note").value = d && d.note || ""; $("dc-error").hidden = true;
    $("dc-modal").showModal();
  }
  async function saveDay(body) {
    const r = await VA.fetchJson(API + "/days/" + dayTarget.employee_id + "/" + dayTarget.day, json("PUT", body));
    if (!r.ok) { $("dc-error").textContent = msg(r, "Could not save it."); $("dc-error").hidden = false; return; }
    $("dc-modal").close(); VA.toast("Saved. It is in the history."); loadBoard();
    if ($("em-modal") && $("em-modal").open) openEmployeeMonth(monthCache);
  }
  if ($("dc-form")) {
    $("dc-excused").addEventListener("change", function () { $("dc-reason-wrap").hidden = !$("dc-excused").checked; });
    $("dc-form").addEventListener("submit", function (e) {
      e.preventDefault();
      saveDay({ check_in: $("dc-in").value || null, check_out: $("dc-out").value || null, excused: $("dc-excused").checked,
        excuse_reason: $("dc-excused").checked ? $("dc-reason").value.trim() : null, note: $("dc-note").value.trim() });
    });
    $("dc-clear").addEventListener("click", function () { if (window.confirm("Clear this day completely? (It will count as absent on a working day.)")) saveDay({ clear: true, note: $("dc-note").value.trim() }); });
    $("dc-close").addEventListener("click", function () { $("dc-modal").close(); });
  }

  // ------------------------------------------------------------------ month
  async function loadMonth() {
    if (!$("sm-body") || !$("sm-month").value) return;
    const r = await VA.fetchJson(API + "/month?month=" + $("sm-month").value + "-01");
    if (!r.ok) return VA.toast(msg(r, "Could not load the month."), "error");
    const tb = $("sm-body");
    tb.innerHTML = "";
    r.body.forEach(function (x) {
      const tr = document.createElement("tr");
      tr.innerHTML = "<td><strong>" + esc(x.name) + "</strong><small>" + esc(x.employee_code + " · " + x.job_title + (x.is_teacher ? " (teacher)" : "")) + "</small></td>" +
        '<td class="num">' + x.scheduled + '</td><td class="num">' + x.present + (x.excused ? "<small>" + x.excused + " excused</small>" : "") + '</td><td class="num ' + (x.absent ? "bad" : "") + '">' + x.absent +
        '</td><td class="num">' + x.late + '</td><td class="num">' + x.early + '</td><td class="num">' + x.missing_out + (x.recorded ? "" : "<small>nothing recorded</small>") + '</td><td class="row-actions"></td>';
      const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn secondary-btn"; b.textContent = "Days";
      b.addEventListener("click", function () { openEmployeeMonth({ id: x.employee_id, month: $("sm-month").value + "-01" }); });
      tr.querySelector(".row-actions").appendChild(b);
      tb.appendChild(tr);
    });
    $("sm-empty").hidden = r.body.length > 0;
  }
  async function openEmployeeMonth(target) {
    monthCache = target;
    const r = await VA.fetchJson(API + "/month/" + target.id + "?month=" + target.month);
    if (!r.ok) return VA.toast(msg(r, "Could not open it."), "error");
    const x = r.body, s = x.summary;
    $("em-title").textContent = x.name + " (" + x.employee_code + ")";
    $("em-sub").textContent = "Working days " + s.scheduled + " · present " + s.present + " · absent " + s.absent + " · late " + s.late + " · left early " + s.early + " · no check-out " + s.missing_out +
      (s.absent_dates.length ? " · absent on " + s.absent_dates.join(", ") : "");
    const rows = x.days.slice();
    s.absent_dates.forEach(function (dt) { rows.push({ work_date: dt, absent: true }); });
    rows.sort(function (a, b) { return String(a.work_date).localeCompare(String(b.work_date)); });
    const tb = $("em-body");
    tb.innerHTML = "";
    rows.forEach(function (d) {
      const tr = document.createElement("tr");
      const status = d.absent ? '<span class="status-pill dropped">Absent</span>' : d.excused ? '<span class="status-pill enrolled">Excused</span><small>' + esc(d.excuse_reason || "") + "</small>" :
        [d.late_minutes ? "late " + human(d.late_minutes) : "", d.early_minutes ? "left early " + human(d.early_minutes) : "", d.check_in && !d.check_out ? "no check-out" : "", !d.is_working_day ? "off day" : ""].filter(Boolean).join(", ") || "on time";
      tr.innerHTML = "<td>" + esc(String(d.work_date)) + "</td><td>" + esc(d.check_in || "—") + "</td><td>" + esc(d.check_out || "—") + "</td><td>" + esc(d.expected_in ? d.expected_in + "–" + d.expected_out : "") +
        "</td><td>" + status + (d.source && d.source !== "MARKED" ? " <small>(" + esc(d.source.toLowerCase()) + ")</small>" : "") + '</td><td class="row-actions"></td>';
      if (canManage) {
        const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn secondary-btn"; b.textContent = "Correct";
        b.addEventListener("click", function () { openDay(x.employee_id, x.name, d.work_date, d.absent ? null : d); });
        tr.querySelector(".row-actions").appendChild(b);
      }
      tb.appendChild(tr);
    });
    if (!$("em-modal").open) $("em-modal").showModal();
  }
  if ($("sm-load")) {
    $("sm-load").addEventListener("click", loadMonth);
    $("em-close").addEventListener("click", function () { $("em-modal").close(); loadMonth(); });
  }

  // ------------------------------------------------------------------ timings
  function timingText(t) { return t ? esc(t.in_time + "–" + t.out_time) : '<span class="cc-meta">default</span>'; }
  async function loadTimings() {
    if (!$("tm-roles")) return;
    const r = await VA.fetchJson(API + "/timings");
    if (!r.ok) return;
    const d = r.body.default;
    $("tm-default").innerHTML = "<p><b>" + esc(d.in_time + "–" + d.out_time) + "</b> · " + esc(d.days_label) + " · relaxation " + d.grace_in_minutes + " min on arrival, " + d.grace_out_minutes + " min on leaving</p>";
    if (canManage) {
      const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn"; b.textContent = "Change the default";
      b.addEventListener("click", function () { openTiming("default", "Default timing", d); }); $("tm-default").appendChild(b);
    }
    const roles = $("tm-roles"); roles.innerHTML = "";
    r.body.job_roles.forEach(function (j) {
      const t = j.timing;
      const tr = document.createElement("tr");
      tr.innerHTML = "<td><strong>" + esc(j.title) + "</strong>" + (j.is_teacher ? "<small>teacher</small>" : "") + "</td><td>" + timingText(t) + "</td><td>" + esc(t ? t.days_label : "") + "</td><td>" + (t ? t.grace_in_minutes + " / " + t.grace_out_minutes + " min" : "") + '</td><td class="row-actions"></td>';
      if (canManage) {
        const cell = tr.querySelector(".row-actions");
        const e = document.createElement("button"); e.type = "button"; e.className = "role-btn small-btn"; e.textContent = t ? "Change" : "Set timing";
        e.addEventListener("click", function () { openTiming("job-roles/" + j.job_role_id, j.title, t || d); }); cell.appendChild(e);
        if (t) { const c = document.createElement("button"); c.type = "button"; c.className = "role-btn small-btn secondary-btn"; c.textContent = "Use default";
          c.addEventListener("click", function () { clearTiming("job-roles/" + j.job_role_id); }); cell.appendChild(c); }
      }
      roles.appendChild(tr);
    });
    const emps = $("tm-emps"); emps.innerHTML = "";
    r.body.employees.forEach(function (x) {
      const t = x.timing;
      const tr = document.createElement("tr");
      tr.innerHTML = "<td><strong>" + esc(x.name) + "</strong><small>" + esc(x.employee_code) + "</small></td><td>" + timingText(t) + "</td><td>" + esc(t.days_label) + "</td><td>" + t.grace_in_minutes + " / " + t.grace_out_minutes + ' min</td><td class="row-actions"></td>';
      if (canManage) {
        const cell = tr.querySelector(".row-actions");
        const e = document.createElement("button"); e.type = "button"; e.className = "role-btn small-btn"; e.textContent = "Change";
        e.addEventListener("click", function () { openTiming("employees/" + x.employee_id, x.name, t); }); cell.appendChild(e);
        const c = document.createElement("button"); c.type = "button"; c.className = "role-btn small-btn secondary-btn"; c.textContent = "Remove";
        c.addEventListener("click", function () { clearTiming("employees/" + x.employee_id); }); cell.appendChild(c);
      }
      emps.appendChild(tr);
    });
    $("tm-emps-empty").hidden = r.body.employees.length > 0;
  }
  function openTiming(target, title, t) {
    timingTarget = target;
    $("tm-title").textContent = "Timing: " + title;
    $("tm-in").value = t.in_time; $("tm-out").value = t.out_time; $("tm-gin").value = t.grace_in_minutes; $("tm-gout").value = t.grace_out_minutes;
    $("tm-days").innerHTML = DAYS.map(function (dname, i) { return '<label class="daybox"><input type="checkbox" value="' + i + '"' + (t.working_days.indexOf(i) !== -1 ? " checked" : "") + " /><span>" + dname + "</span></label>"; }).join("");
    $("tm-error").hidden = true;
    $("tm-modal").showModal();
  }
  async function clearTiming(target) {
    const r = await VA.fetchJson(API + "/timings/" + target, { method: "DELETE" });
    if (!r.ok && r.status !== 204) return VA.toast(msg(r, "Could not remove it."), "error");
    VA.toast("Removed."); loadTimings();
  }
  if ($("tm-form")) {
    $("tm-form").addEventListener("submit", async function (e) {
      e.preventDefault();
      const days = Array.prototype.map.call($("tm-days").querySelectorAll("input:checked"), function (c) { return parseInt(c.value, 10); });
      const body = { in_time: $("tm-in").value, out_time: $("tm-out").value, grace_in_minutes: parseInt($("tm-gin").value || "0", 10), grace_out_minutes: parseInt($("tm-gout").value || "0", 10), working_days: days };
      const r = await VA.fetchJson(API + "/timings/" + timingTarget, json("PUT", body));
      if (!r.ok && r.status !== 204) { $("tm-error").textContent = msg(r, "Could not save it."); $("tm-error").hidden = false; return; }
      $("tm-modal").close(); VA.toast("Saved. It applies from the next mark; days already recorded keep their timing."); loadTimings();
    });
    $("tm-close").addEventListener("click", function () { $("tm-modal").close(); });
    let empTimer = null;
    $("tm-emp-q").addEventListener("input", function () {
      clearTimeout(empTimer);
      empTimer = setTimeout(async function () {
        const list = $("tm-emp-list"); list.innerHTML = "";
        const q = $("tm-emp-q").value.trim();
        if (q.length < 2) return;
        const r = await VA.fetchJson(API + "/board?q=" + encodeURIComponent(q), { quiet: true });
        if (!r.ok) return;
        r.body.people.slice(0, 10).forEach(function (x) {
          const b = document.createElement("button"); b.type = "button"; b.className = "picker-item";
          b.innerHTML = "<strong>" + esc(x.name) + "</strong><small>" + esc(x.employee_code + " · " + (x.job_title || "") + " · now " + x.timing.in_time + "–" + x.timing.out_time) + "</small>";
          b.addEventListener("click", function () { list.innerHTML = ""; $("tm-emp-q").value = ""; openTiming("employees/" + x.employee_id, x.name, x.timing); });
          list.appendChild(b);
        });
      }, 300);
    });
  }

  // ------------------------------------------------------------------ holidays and import
  if ($("hd-save")) {
    $("hd-save").addEventListener("click", async function () {
      if (!$("hd-day").value || $("hd-reason").value.trim().length < 3) return VA.toast("Choose the day and give the reason.", "error");
      if (!window.confirm("Excuse " + $("hd-day").value + " for every member of staff?")) return;
      const r = await VA.fetchJson(API + "/holiday", json("POST", { day: $("hd-day").value, reason: $("hd-reason").value.trim() }));
      if (!r.ok) return VA.toast(msg(r, "Could not save it."), "error");
      VA.toast(r.body.employees + " staff excused for " + $("hd-day").value + "."); $("hd-reason").value = "";
    });
    const send = async function (dry) {
      const f = $("im-file").files[0];
      if (!f) return VA.toast("Choose the CSV file first.", "error");
      if (!dry && !window.confirm("Import these times? Existing times on the same days are replaced.")) return;
      const fd = new FormData(); fd.append("file", f);
      const r = await VA.fetchJson(API + "/import?dry_run=" + dry, { method: "POST", body: fd });
      const box = $("im-result");
      if (!r.ok) { box.textContent = msg(r, "Could not read the file."); box.hidden = false; return; }
      box.textContent = (dry ? r.body.rows_ok + " row(s) are fine." : r.body.rows_ok + " row(s) imported.") + (r.body.errors.length ? " Problems: " + r.body.errors.map(function (e) { return "row " + e.row + ": " + e.message; }).join(" | ") : "");
      box.hidden = false;
      if (!dry) loadBoard();
    };
    $("im-check").addEventListener("click", function () { send(true); });
    $("im-go").addEventListener("click", function () { send(false); });
  }

  document.addEventListener("vistadmin:ready", function (event) {
    const perms = (event.detail && event.detail.me && event.detail.me.permissions) || [];
    canManage = perms.indexOf("staff_attendance.manage") !== -1;
    canView = perms.indexOf("staff_attendance.view") !== -1;
    document.querySelectorAll("[data-manage-only]").forEach(function (el) { el.hidden = !canManage; });
    if ($("sb-day") && canView) { $("sb-day").hidden = false; }
    const d = new Date();
    if ($("sb-day")) $("sb-day").value = ymd(d);
    if ($("sm-month")) $("sm-month").value = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0");
    if ($("hd-day")) $("hd-day").value = ymd(d);
    loadBoard();
    document.querySelectorAll(".portal-tab").forEach(function (t) {
      t.addEventListener("click", function () {
        const n = t.getAttribute("data-tab");
        if (n === "month") loadMonth();
        if (n === "timings") loadTimings();
      });
    });
  });
})();