(function () {
  "use strict";

  const VA = window.VistAdmin;
  const SU = window.StudentUI;
  const esc = VA.escapeHtml;
  const API = "/api/v1/gate";
  const $ = function (id) { return document.getElementById(id); };
  const msg = function (body, fallback) { return body && body.error && body.error.message ? body.error.message : fallback; };
  const PAGE = 30;

  const f = { q: "", kind: "", state: "", program_id: "", semester: "", job_role_id: "" };
  let offset = 0, timer = null, seq = 0;

  const hhmm = function (t) { return t ? String(t).slice(0, 5) : ""; };
  const clock = function (iso) { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); };
  const human = function (m) { const h = Math.floor(m / 60), r = m % 60; return h && r ? h + " h " + r + " min" : h ? h + " h" : r + " min"; };

  // ------------------------------------------------------------------ one person
  function card(p) {
    const el = document.createElement("article");
    const late = p.late_minutes > 0, early = p.early_minutes > 0;
    el.className = "gate-card state-" + p.state.toLowerCase() + (late ? " late" : "");
    el.setAttribute("data-id", p.person_id);
    const chips = [];
    chips.push('<span class="gstate ' + p.state.toLowerCase() + '">' + (p.state === "INSIDE" ? "Inside" : p.state === "LEFT" ? "Left" : "Not arrived") + "</span>");
    if (late) chips.push('<span class="gstate late">Late ' + esc(human(p.late_minutes)) + "</span>");
    if (early) chips.push('<span class="gstate early">Left ' + esc(human(p.early_minutes)) + " early</span>");
    let times = "";
    if (p.check_in_at) times = "In <b>" + esc(clock(p.check_in_at)) + "</b>" + (p.check_out_at ? " \u2192 Out <b>" + esc(clock(p.check_out_at)) + "</b>" : "");
    const expect = !p.working_today ? "Day off today: lateness is not counted" : p.expected_in ? "Expected " + esc(hhmm(p.expected_in)) + (p.expected_out ? "\u2013" + esc(hhmm(p.expected_out)) : "") : "No timing set";
    el.innerHTML =
      '<div class="gate-who">' + SU.avatar(p.name) + "<span><strong>" + esc(p.name) + "</strong><small>" + esc([p.identifier, p.group].filter(Boolean).join(" \u00b7 ")) + "</small></span>" +
      '<span class="gate-kind">' + (p.kind === "STUDENT" ? "Student" : "Staff") + "</span></div>" +
      '<div class="gate-chips">' + chips.join("") + "</div>" +
      '<p class="gate-expect">' + expect + "</p>" + (times ? '<p class="gate-times">' + times + "</p>" : "") +
      (p.fine ? '<p class="gate-fine">Fine request of Rs. ' + esc(Number(p.fine.amount).toLocaleString()) + (p.fine.status === "PENDING_APPROVAL" ? " sent to the administration" : " (" + esc(p.fine.status.toLowerCase()) + ")") + "</p>" : "") +
      '<div class="gate-actions"></div>';
    const bar = el.querySelector(".gate-actions");
    const add = function (label, cls, fn, enabled) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "gbtn " + cls; b.textContent = label; b.disabled = !enabled;
      b.addEventListener("click", function () { fn(b); });
      bar.appendChild(b);
    };
    add("Check in", "in", function (b) { mark(p, "check-in", b); }, p.can_check_in);
    add("Check out", "out", function (b) { mark(p, "check-out", b); }, p.can_check_out);
    if (p.can_undo) add("Undo", "undo", function (b) { undo(p, b); }, true);
    return el;
  }

  function replace(p) {
    const old = document.querySelector('.gate-card[data-id="' + p.person_id + '"]');
    if (old) old.replaceWith(card(p));
  }

  async function mark(p, what, btn) {
    btn.disabled = true;
    const r = await VA.fetchJson(API + "/" + what, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: p.kind, person_id: p.person_id }) });
    if (!r.ok) { VA.toast(msg(r.body, "That did not work."), "error"); btn.disabled = false; return; }
    const c = r.body;
    replace(c);
    if (what === "check-in") {
      if (c.late_minutes > 0) VA.toast(c.name + " marked in \u2014 LATE by " + human(c.late_minutes) + (c.fine ? ". Fine request sent to the administration." : ""), "error");
      else VA.toast(c.name + " marked in" + (c.working_today ? " \u2014 on time" : "") + ".");
    } else {
      VA.toast(c.name + " marked out" + (c.early_minutes > 0 ? " \u2014 left " + human(c.early_minutes) + " early" : "") + ".", c.early_minutes > 0 ? "error" : undefined);
    }
    summary();
    if (f.state) load(false);                                    // a state filter may no longer include them
  }
  async function undo(p, btn) {
    if (!window.confirm("Take back the last mark for " + p.name + "?")) return;
    btn.disabled = true;
    const r = await VA.fetchJson(API + "/logs/" + p.log_id + "/undo", { method: "POST" });
    if (!r.ok) { VA.toast(msg(r.body, "Could not undo it."), "error"); btn.disabled = false; return; }
    replace(r.body); VA.toast("Taken back."); summary();
  }

  // ------------------------------------------------------------------ the list
  async function load(more) {
    const my = ++seq;
    if (!more) { offset = 0; }
    const p = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
    Object.keys(f).forEach(function (k) { if (f[k]) p.set(k, f[k]); });
    const r = await VA.fetchJson(API + "/people?" + p.toString(), { quiet: more });
    if (my !== seq) return;                                                     // an older search finishing late must not overwrite a newer one
    const box = $("g-list");
    if (!more) box.innerHTML = "";
    if (!r.ok) { box.innerHTML = '<p class="home-empty">' + esc(msg(r.body, "Could not search.")) + "</p>"; return; }
    r.body.people.forEach(function (x) { box.appendChild(card(x)); });
    offset += PAGE;
    $("g-more").hidden = !r.body.has_more;
    const n = box.querySelectorAll(".gate-card").length;
    $("g-empty").hidden = n > 0;
    $("g-count").textContent = n ? n + " shown" + (r.body.has_more ? " (more below)" : "") : "";
  }

  async function summary() {
    const r = await VA.fetchJson(API + "/summary", { quiet: true });
    if (!r.ok) return;
    const s = r.body, set = function (id, v) { $(id).textContent = v; };
    set("gs-inside", s.inside_students + s.inside_staff); set("gs-inside-sub", s.inside_students + " students \u00b7 " + s.inside_staff + " staff");
    set("gs-in", s.checked_in_students + s.checked_in_staff); set("gs-in-sub", s.left_today + " already left");
    set("gs-late", s.late_students + s.late_staff); set("gs-late-sub", s.late_students + " students \u00b7 " + s.late_staff + " staff");
    set("gs-miss", s.staff_not_arrived);
  }

  // ------------------------------------------------------------------ filters
  function segment(box, attr, key) {
    box.querySelectorAll("button").forEach(function (b) {
      b.addEventListener("click", function () {
        box.querySelectorAll("button").forEach(function (x) { x.classList.toggle("active", x === b); });
        f[key] = b.getAttribute(attr);
        if (key === "kind") { if (f.kind === "EMPLOYEE") { f.program_id = f.semester = ""; $("g-program").value = $("g-semester").value = ""; } if (f.kind === "STUDENT") { f.job_role_id = ""; $("g-role").value = ""; } }
        load(false);
      });
    });
  }
  function setKind(kind) {
    f.kind = kind;
    $("g-kind").querySelectorAll("button").forEach(function (x) { x.classList.toggle("active", x.getAttribute("data-kind") === kind); });
  }
  segment($("g-kind"), "data-kind", "kind");
  segment($("g-state"), "data-state", "state");
  $("g-q").addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(function () { f.q = $("g-q").value.trim(); load(false); }, 250); });
  $("g-q").addEventListener("keydown", function (e) { if (e.key === "Enter") { clearTimeout(timer); f.q = $("g-q").value.trim(); load(false); } });
  $("g-filter-toggle").addEventListener("click", function () {
    const open = $("g-filters").hidden;
    $("g-filters").hidden = !open; $("g-filter-toggle").setAttribute("aria-expanded", String(open));
  });
  [["g-program", "program_id", "STUDENT"], ["g-semester", "semester", "STUDENT"], ["g-role", "job_role_id", "EMPLOYEE"]].forEach(function (x) {
    $(x[0]).addEventListener("change", function () {
      f[x[1]] = $(x[0]).value;
      if (f[x[1]]) { setKind(x[2]); if (x[2] === "STUDENT") { f.job_role_id = ""; $("g-role").value = ""; } else { f.program_id = f.semester = ""; $("g-program").value = $("g-semester").value = ""; } }
      load(false);
    });
  });
  $("g-clear").addEventListener("click", function () {
    Object.keys(f).forEach(function (k) { f[k] = ""; });
    ["g-q", "g-program", "g-semester", "g-role"].forEach(function (id) { $(id).value = ""; });
    setKind(""); $("g-state").querySelectorAll("button").forEach(function (b, i) { b.classList.toggle("active", i === 0); });
    load(false);
  });
  $("g-more").addEventListener("click", function () { load(true); });

  document.addEventListener("vistadmin:ready", async function () {
    const meta = await VA.fetchJson(API + "/filters", { quiet: true });
    if (meta.ok) {
      $("g-program").innerHTML += meta.body.programs.map(function (x) { return '<option value="' + x.id + '">' + esc(x.code) + "</option>"; }).join("");
      $("g-semester").innerHTML += meta.body.semesters.map(function (n) { return '<option value="' + n + '">Semester ' + n + "</option>"; }).join("");
      $("g-role").innerHTML += meta.body.job_roles.map(function (x) { return '<option value="' + x.id + '">' + esc(x.title) + "</option>"; }).join("");
    }
    summary(); load(false);
    setInterval(summary, 30000);
  });
})();