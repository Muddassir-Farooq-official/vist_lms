(function () {
  "use strict";

  const VA = window.VistAdmin, SU = window.StudentUI, GU = window.GateUI;
  const esc = VA.escapeHtml;
  const API = "/api/v1/gate";
  const $ = function (id) { return document.getElementById(id); };
  const msg = function (body, fallback) { return body && body.error && body.error.message ? body.error.message : fallback; };
  const json = function (method, data) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }; };
  const pad = function (n) { return String(n).padStart(2, "0"); };
  const ymd = function (d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); };
  const days = {};            // the day-box groups, by name
  let jobRoles = [], editing = null, bulk = false, logKind = "", fineStatus = "PENDING_APPROVAL", repDays = 30, repKind = "", timer = null, rejectFine = null;

  // ------------------------------------------------------------------ the log
  async function loadLog() {
    const p = new URLSearchParams({ limit: "500", day: $("al-day").value });
    if (logKind) p.set("kind", logKind);
    if ($("al-late").checked) p.set("late_only", "true");
    if ($("al-q").value.trim()) p.set("q", $("al-q").value.trim());
    const r = await VA.fetchJson(API + "/logs?" + p.toString());
    const box = $("al-list");
    box.innerHTML = "";
    if (!r.ok) { box.innerHTML = '<p class="home-empty">' + esc(msg(r.body, "Could not load the log.")) + "</p>"; return; }
    r.body.forEach(function (l) { box.appendChild(GU.logCard(l, { onEdit: openEdit, onDelete: removeLog, onUndo: undo })); });
    $("al-empty").hidden = r.body.length > 0;
    const late = r.body.filter(function (l) { return l.late_minutes > 0; }).length;
    $("al-count").textContent = r.body.length ? r.body.length + " entries \u00b7 " + late + " late" : "";
  }
  function openEdit(l) {
    editing = l;
    $("ge-who").textContent = l.name + " \u00b7 " + l.log_date;
    $("ge-in").value = l.check_in_at ? GU.toLocalInput(l.check_in_at) : ""; $("ge-out").value = l.check_out_at ? GU.toLocalInput(l.check_out_at) : "";
    $("ge-clear").checked = false; $("ge-note").value = l.note || ""; $("ge-error").hidden = true;
    $("ge-modal").showModal();
  }
  $("ge-save").addEventListener("click", async function () {
    const body = { note: $("ge-note").value };
    if ($("ge-in").value) body.check_in_time = $("ge-in").value;
    if ($("ge-clear").checked) body.clear_check_out = true; else if ($("ge-out").value) body.check_out_time = $("ge-out").value;
    const r = await VA.fetchJson(API + "/logs/" + editing.id, json("PATCH", body));
    if (!r.ok) { $("ge-error").textContent = msg(r.body, "Could not save."); $("ge-error").hidden = false; return; }
    $("ge-modal").close();
    VA.toast(r.body.warning || "Corrected. Lateness and the fine request have been worked out again.", r.body.warning ? "error" : undefined);
    loadLog(); loadFines();
  });
  $("ge-close").addEventListener("click", function () { $("ge-modal").close(); });
  async function removeLog(l) {
    if (!window.confirm("Remove this entry for " + l.name + "? Any fine request that has not been decided is removed too.")) return;
    const r = await VA.fetchJson(API + "/logs/" + l.id, { method: "DELETE" });
    if (r.ok) { VA.toast("Removed."); loadLog(); loadFines(); } else VA.toast(msg(r.body, "Could not remove it."), "error");
  }
  async function undo(l) {
    const r = await VA.fetchJson(API + "/logs/" + l.id + "/undo", { method: "POST" });
    if (r.ok) { VA.toast("Taken back."); loadLog(); loadFines(); } else VA.toast(msg(r.body, "Could not undo it."), "error");
  }
  $("al-kind").querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { $("al-kind").querySelectorAll("button").forEach(function (x) { x.classList.toggle("active", x === b); }); logKind = b.getAttribute("data-kind"); loadLog(); }); });
  $("al-late").addEventListener("change", loadLog); $("al-day").addEventListener("change", loadLog);
  $("al-q").addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(loadLog, 250); });

  // ------------------------------------------------------------------ fines the administration decides
  function fineCard(r) {
    const el = document.createElement("article");
    const open = r.status === "PENDING_APPROVAL";
    el.className = "asg-card " + (open ? "soon" : r.status === "REJECTED" ? "missed" : "ok");
    el.innerHTML =
      '<div class="asg-top"><span class="pill">' + esc(r.roll_number || "") + '</span><span class="status-pill ' + (open ? "pending" : r.status === "REJECTED" ? "dropped" : "active") + '">' + esc(open ? "Waiting for you" : r.status === "REJECTED" ? "Not charged" : r.status === "PAID" ? "Paid" : "Approved") + "</span></div>" +
      "<h3>" + esc(r.name) + "</h3><p class=\"tk-who\">" + esc(r.group || "") + "</p>" +
      '<p class="asg-snippet">' + (r.log_date ? esc(new Date(r.log_date + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })) + ": " : "") +
      (r.arrived_at ? "arrived <b>" + esc(GU.clock(r.arrived_at)) + "</b>, " : "") + (r.late_minutes ? "<b>" + esc(GU.human(r.late_minutes)) + "</b> after " + esc(GU.hhmm(r.expected_in)) + " \u2192 " + r.hours + " hour" + (r.hours > 1 ? "s" : "") + " late" : esc(r.reason)) + "</p>" +
      (open ? '<div class="fine-decide"><label>Fine (Rs.)<input class="text-input tiny" type="number" min="1" step="1" value="' + Number(r.amount) + '" /></label>' +
        (r.suggested_amount !== null && Number(r.suggested_amount) !== Number(r.amount) ? '<small class="field-hint">The rule gives Rs. ' + Number(r.suggested_amount) + "</small>" : '<small class="field-hint">This is the amount the rule gives</small>') + "</div>" +
        '<div class="asg-actions"></div>' : '<p class="asg-due"><strong>' + esc(GU.money(r.amount)) + "</strong>" + (r.decision_note ? " \u00b7 " + esc(r.decision_note) : "") + "</p>");
    if (open) {
      const bar = el.querySelector(".asg-actions"), input = el.querySelector("input");
      const ok = document.createElement("button"); ok.type = "button"; ok.className = "role-btn small-btn"; ok.textContent = "Approve";
      ok.addEventListener("click", async function () {
        ok.disabled = true;
        const amount = parseFloat(input.value);
        if (!(amount > 0)) { VA.toast("Enter the fine amount.", "error"); ok.disabled = false; return; }
        const res = await VA.fetchJson(API + "/fines/" + r.fine_id + "/decide", json("POST", { action: "APPROVE", amount: amount }));
        if (res.ok) { VA.toast("Approved. " + r.name + " has been told."); loadFines(); } else { VA.toast(msg(res.body, "Could not approve it."), "error"); ok.disabled = false; }
      });
      const no = document.createElement("button"); no.type = "button"; no.className = "danger-btn small-btn"; no.textContent = "Do not charge";
      no.addEventListener("click", function () { rejectFine = r; $("gn-who").textContent = r.name + " \u00b7 " + GU.money(r.amount); $("gn-note").value = ""; $("gn-error").hidden = true; $("gn-modal").showModal(); });
      bar.appendChild(ok); bar.appendChild(no);
    }
    return el;
  }
  async function loadFines() {
    const r = await VA.fetchJson(API + "/fines?status=" + fineStatus);
    const box = $("af-list");
    box.innerHTML = "";
    if (r.ok) { r.body.forEach(function (x) { box.appendChild(fineCard(x)); }); $("af-empty").hidden = r.body.length > 0; }
    const w = await VA.fetchJson(API + "/fines?status=PENDING_APPROVAL");
    if (w.ok) $("ga-tab-fines").textContent = "Fines to approve" + (w.body.length ? " \u00b7 " + w.body.length : "");
  }
  $("af-status").querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { $("af-status").querySelectorAll("button").forEach(function (x) { x.classList.toggle("active", x === b); }); fineStatus = b.getAttribute("data-s"); loadFines(); }); });
  $("gn-ok").addEventListener("click", async function () {
    const note = $("gn-note").value.trim();
    if (note.length < 3) { $("gn-error").textContent = "Give a short reason."; $("gn-error").hidden = false; return; }
    const r = await VA.fetchJson(API + "/fines/" + rejectFine.fine_id + "/decide", json("POST", { action: "REJECT", note: note }));
    if (r.ok) { $("gn-modal").close(); VA.toast("Not charged."); loadFines(); } else { $("gn-error").textContent = msg(r.body, "Could not save."); $("gn-error").hidden = false; }
  });
  $("gn-close").addEventListener("click", function () { $("gn-modal").close(); });

  // ------------------------------------------------------------------ staff timings
  async function loadSettings() {
    const r = await VA.fetchJson(API + "/settings");
    if (!r.ok) return;
    const s = r.body;
    $("sd-in").value = GU.hhmm(s.staff_default_in); $("sd-out").value = GU.hhmm(s.staff_default_out); $("sd-grace").value = s.staff_grace_minutes; days.sd.set(s.staff_default_days);
    $("sr-in").value = GU.hhmm(s.student_in_time); $("sr-out").value = GU.hhmm(s.student_out_time); $("sr-grace").value = s.student_grace_minutes; days.sr.set(s.student_days);
    $("sr-rate").value = Number(s.student_fine_per_hour); $("sr-max").value = s.student_max_fine_per_day === null ? "" : Number(s.student_max_fine_per_day); $("sr-auto").checked = s.student_auto_fine;
    example();
    window.__gate = s;
  }
  function payload(over) {
    const g = window.__gate;
    const staffIn = $("sd-in").value, staffOut = $("sd-out").value;
    return Object.assign({
      student_in_time: $("sr-in").value || GU.hhmm(g.student_in_time), student_out_time: $("sr-out").value || null, student_grace_minutes: parseInt($("sr-grace").value || "0", 10), student_days: days.sr.get(),
      student_fine_per_hour: parseFloat($("sr-rate").value || "0"), student_max_fine_per_day: $("sr-max").value === "" ? null : parseFloat($("sr-max").value), student_auto_fine: $("sr-auto").checked,
      staff_default_in: staffIn || null, staff_default_out: staffOut || null, staff_grace_minutes: parseInt($("sd-grace").value || "0", 10), staff_default_days: days.sd.get(),
    }, over || {});
  }
  async function saveSettings(errId, label) {
    $(errId).hidden = true;
    const r = await VA.fetchJson(API + "/settings", json("PUT", payload()));
    if (!r.ok) { $(errId).textContent = msg(r.body, "Could not save."); $(errId).hidden = false; return; }
    window.__gate = r.body; VA.toast(label + " saved."); loadStaff();
  }
  $("sd-save").addEventListener("click", function () { saveSettings("sd-error", "Default staff timing"); });
  $("sr-save").addEventListener("click", function () { saveSettings("sr-error", "Student rules"); });

  async function loadStaff() {
    const p = new URLSearchParams();
    if ($("st-q").value.trim()) p.set("q", $("st-q").value.trim());
    if ($("st-role").value) p.set("job_role_id", $("st-role").value);
    const r = await VA.fetchJson(API + "/schedules?" + p.toString());
    const box = $("st-list");
    box.innerHTML = "";
    if (!r.ok) return;
    $("st-empty").hidden = r.body.length > 0;
    r.body.forEach(function (e) {
      const row = document.createElement("div");
      row.className = "auth-row" + (e.is_default ? " off" : "");
      row.innerHTML = '<span class="grow"><strong>' + esc(e.name) + "</strong><small>" + esc(e.employee_code) + " \u00b7 " + esc(e.job_title) + "</small></span>" +
        "<span class=\"sched\">" + (e.in_time ? esc(GU.hhmm(e.in_time)) + "\u2013" + esc(GU.hhmm(e.out_time)) : "No timing") + " \u00b7 " + esc(e.days_text) + (e.is_default ? ' <em>default</em>' : "") + "</span>";
      const edit = document.createElement("button"); edit.type = "button"; edit.className = "role-btn small-btn secondary-btn"; edit.textContent = "Set timing";
      edit.addEventListener("click", function () { openTiming(e); });
      row.appendChild(edit);
      if (!e.is_default) {
        const reset = document.createElement("button"); reset.type = "button"; reset.className = "role-btn small-btn secondary-btn"; reset.textContent = "Use default";
        reset.addEventListener("click", async function () { const x = await VA.fetchJson(API + "/schedules/" + e.employee_id, { method: "DELETE" }); if (x.ok) { VA.toast(e.name + " now follows the default timing."); loadStaff(); } });
        row.appendChild(reset);
      }
      box.appendChild(row);
    });
  }
  function openTiming(e) {
    editing = e; bulk = false;
    $("gt-title").textContent = "Timing for " + e.name; $("gt-who").textContent = e.employee_code + " \u00b7 " + e.job_title; $("gt-scope-wrap").hidden = true;
    const g = window.__gate;
    $("gt-in").value = GU.hhmm(e.in_time || g.staff_default_in) || "09:00"; $("gt-out").value = GU.hhmm(e.out_time || g.staff_default_out) || "17:00"; days.gt.set(e.working_days.length ? e.working_days : g.staff_default_days);
    $("gt-error").hidden = true; $("gt-modal").showModal();
  }
  $("st-bulk-open").addEventListener("click", function () {
    bulk = true; editing = null;
    $("gt-title").textContent = "Set the same timing for many"; $("gt-who").textContent = "This replaces each person's own timing."; $("gt-scope-wrap").hidden = false; $("gt-scope").value = "ALL"; $("gt-role").hidden = true;
    const g = window.__gate;
    $("gt-in").value = GU.hhmm(g.staff_default_in) || "09:00"; $("gt-out").value = GU.hhmm(g.staff_default_out) || "17:00"; days.gt.set(g.staff_default_days);
    $("gt-error").hidden = true; $("gt-modal").showModal();
  });
  $("gt-scope").addEventListener("change", function () { $("gt-role").hidden = $("gt-scope").value !== "JOB_ROLE"; });
  $("gt-save").addEventListener("click", async function () {
    const body = { in_time: $("gt-in").value, out_time: $("gt-out").value, working_days: days.gt.get() };
    if (!body.in_time || !body.out_time) { $("gt-error").textContent = "Set both times."; $("gt-error").hidden = false; return; }
    let r;
    if (bulk) {
      body.scope = $("gt-scope").value; if (body.scope === "JOB_ROLE") body.job_role_id = $("gt-role").value;
      if (!window.confirm("Set this timing for " + (body.scope === "ALL" ? "EVERY active employee" : "everyone with that job title") + "?")) return;
      r = await VA.fetchJson(API + "/schedules/bulk", json("POST", body));
    } else r = await VA.fetchJson(API + "/schedules/" + editing.employee_id, json("PUT", body));
    if (!r.ok) { $("gt-error").textContent = msg(r.body, "Could not save."); $("gt-error").hidden = false; return; }
    $("gt-modal").close(); VA.toast(bulk ? r.body.updated + " employees updated." : "Timing saved."); loadStaff();
  });
  $("gt-close").addEventListener("click", function () { $("gt-modal").close(); });
  let st = null;
  $("st-q").addEventListener("input", function () { clearTimeout(st); st = setTimeout(loadStaff, 250); });
  $("st-role").addEventListener("change", loadStaff);

  // ------------------------------------------------------------------ a worked example, so the rule is never a mystery
  function example() {
    const inT = $("sr-in").value, grace = parseInt($("sr-grace").value || "0", 10), rate = parseFloat($("sr-rate").value || "0"), max = $("sr-max").value === "" ? null : parseFloat($("sr-max").value);
    if (!inT) return;
    const parts = inT.split(":").map(Number), base = parts[0] * 60 + parts[1];
    const at = function (mins) { return pad(Math.floor(mins / 60)) + ":" + pad(mins % 60); };
    const fine = function (late) { const h = Math.ceil(late / 60); let a = h * rate; if (max !== null) a = Math.min(a, max); return { h: h, a: a }; };
    const rows = [grace, grace + 1, 45, 60, 61, 125].filter(function (m, i, a) { return a.indexOf(m) === i; }).sort(function (a, b) { return a - b; }).map(function (m) {
      if (m <= grace) return "Arrives " + at(base + m) + " \u2192 on time (inside the grace period)";
      const f = fine(m);
      return "Arrives " + at(base + m) + " \u2192 " + m + " min late \u2192 " + f.h + " hour" + (f.h > 1 ? "s" : "") + " \u2192 <b>Rs. " + f.a.toLocaleString() + "</b>";
    });
    $("sr-example").innerHTML = "<strong>How it works out, with these numbers</strong><ul>" + rows.map(function (x) { return "<li>" + x + "</li>"; }).join("") + "</ul>";
  }
  ["sr-in", "sr-grace", "sr-rate", "sr-max"].forEach(function (id) { $(id).addEventListener("input", example); });

  // ------------------------------------------------------------------ report
  async function loadReport() {
    const p = new URLSearchParams({ days: String(repDays) });
    if (repKind) p.set("kind", repKind);
    const r = await VA.fetchJson(API + "/report?" + p.toString());
    if (!r.ok) return;
    const d = r.body, tile = function (a, b, c, tone) { return '<div class="kpi ' + (tone || "") + '"><span class="kpi-label">' + a + '</span><strong class="kpi-value">' + b + '</strong><span class="kpi-sub">' + (c || "") + "</span></div>"; };
    $("rp-tiles").innerHTML = '<div class="kpi-grid kpi-grid-6">' + tile("Marked in", d.present_days, "days (people \u00d7 days)") + tile("Late", d.late_days, d.late_rate === null ? "" : d.late_rate + "% of arrivals", d.late_days ? "bad" : "") +
      tile("Time lost", GU.human(d.total_late_minutes), "added up") + tile("Fines approved", GU.money(d.fines_approved), "", "good") + tile("Fines waiting", GU.money(d.fines_pending), "for your decision", Number(d.fines_pending) ? "warn" : "") + "</div>";
    const P = Charts.palette();
    Charts.lineChart($("rp-chart"), d.weeks.map(function (w) { return { label: new Date(w.week_start + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" }), late: w.late, present: w.present }; }),
      [{ key: "present", label: "Marked in", color: P.blue }, { key: "late", label: "Late", color: P.red }], { title: "Arrivals and late arrivals per week", empty: "Nothing in the last 12 weeks." });
    const body = $("rp-body");
    body.innerHTML = "";
    $("rp-empty").hidden = d.people.length > 0;
    d.people.forEach(function (x) {
      const tr = document.createElement("tr");
      tr.innerHTML = '<td><span class="tk-person">' + SU.avatar(x.name) + "<span><strong>" + esc(x.name) + "</strong><small>" + esc([x.identifier, x.group].filter(Boolean).join(" \u00b7 ")) + "</small></span></span></td><td>" + x.days_present + '</td><td class="bad">' + x.late_days + "</td><td>" +
        esc(GU.human(x.total_late_minutes)) + "</td><td>" + esc(GU.human(x.average_late_minutes)) + "</td><td>" + esc(GU.human(x.longest_late_minutes)) + "</td><td>" + (x.kind === "STUDENT" ? esc(GU.money(x.fines_approved)) : "\u2014") + "</td><td>" + (x.kind === "STUDENT" ? esc(GU.money(x.fines_pending)) : "\u2014") + "</td>";
      body.appendChild(tr);
    });
  }
  $("rp-days").querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { $("rp-days").querySelectorAll("button").forEach(function (x) { x.classList.toggle("active", x === b); }); repDays = parseInt(b.getAttribute("data-d"), 10); loadReport(); }); });
  $("rp-kind").querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { $("rp-kind").querySelectorAll("button").forEach(function (x) { x.classList.toggle("active", x === b); }); repKind = b.getAttribute("data-k"); loadReport(); }); });

  document.addEventListener("vistadmin:ready", async function () {
    days.sd = GU.dayBoxes($("sd-days"), [0, 1, 2, 3, 4]); days.sr = GU.dayBoxes($("sr-days"), [0, 1, 2, 3, 4]); days.gt = GU.dayBoxes($("gt-days"), [0, 1, 2, 3, 4]);
    $("al-day").value = ymd(new Date());
    const meta = await VA.fetchJson(API + "/filters", { quiet: true });
    if (meta.ok) {
      jobRoles = meta.body.job_roles;
      const opts = jobRoles.map(function (x) { return '<option value="' + x.id + '">' + esc(x.title) + "</option>"; }).join("");
      $("st-role").innerHTML += opts; $("gt-role").innerHTML = opts;
    }
    await loadSettings();
    loadLog(); loadFines(); loadStaff(); loadReport();
    if (new URLSearchParams(window.location.search).get("open") === "fines") VA.showTab("fines");
  });
})();