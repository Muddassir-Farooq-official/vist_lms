(function () {
  "use strict";
  // OBE for the exam office and administration (admin/obe.html): PLOs per program, course files to lock, PLO attainment.
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const send = function (method, url, body) { return VA.fetchJson(url, { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }); };
  const API = "/api/v1/obe";
  let current = null;

  document.querySelector(".dc-areas").addEventListener("click", function (e) {
    const b = e.target.closest("button[data-tab]");
    if (!b) return;
    document.querySelectorAll(".dc-areas button").forEach(function (x) { x.setAttribute("aria-selected", x === b ? "true" : "false"); });
    document.querySelectorAll("[data-pane]").forEach(function (p) { p.hidden = p.getAttribute("data-pane") !== b.getAttribute("data-tab"); });
    if (b.getAttribute("data-tab") === "files") loadFiles();
  });

  // ---------------------------------------------------------------- PLOs
  async function loadPrograms() {
    const r = await VA.fetchJson(API + "/programs");
    const list = r.ok ? r.body : [];
    const opts = list.map(function (p) { return '<option value="' + p.id + '">' + esc(p.code + " — " + p.name) + " (" + p.plos + " PLOs)</option>"; }).join("");
    $("op-prog").innerHTML = opts; $("or-prog").innerHTML = opts;
    if (list.length) loadProgram();
  }
  function ploRow(p) {
    return '<div class="ob-item" data-id="' + (p.id || "") + '"><input class="text-input op-code" maxlength="12" value="' + esc(p.code || "") + '" aria-label="Code">' +
      '<input class="text-input op-title" maxlength="300" value="' + esc(p.title || "") + '" style="grid-column: span 2" aria-label="Outcome">' +
      '<button type="button" class="row-action op-del" aria-label="Remove">✕</button></div>';
  }
  async function loadProgram() {
    const r = await VA.fetchJson(API + "/programs/" + $("op-prog").value);
    if (!r.ok) { $("op-msg").textContent = err(r, "Could not load."); return; }
    current = r.body;
    $("op-st").value = current.student_pct; $("op-bt").value = current.batch_pct;
    $("op-plos").innerHTML = current.plos.map(ploRow).join("") || '<p class="panel-hint">No PLOs yet: add them (PLO1, PLO2 …).</p>';
  }
  function readPlos() {
    return Array.prototype.map.call(document.querySelectorAll("#op-plos .ob-item"), function (row) {
      return { id: row.getAttribute("data-id") || null, code: row.querySelector(".op-code").value.trim(), title: row.querySelector(".op-title").value.trim() };
    });
  }
  $("op-prog").addEventListener("change", loadProgram);
  $("op-add").addEventListener("click", function () {
    const now = readPlos();
    $("op-plos").innerHTML = now.concat([{ code: "PLO" + (now.length + 1), title: "" }]).map(ploRow).join("");
  });
  $("op-plos").addEventListener("click", function (e) { if (e.target.classList.contains("op-del")) e.target.closest(".ob-item").remove(); });
  $("op-save").addEventListener("click", async function () {
    const r = await send("PUT", API + "/programs/" + $("op-prog").value, { plos: readPlos(), student_pct: Number($("op-st").value), batch_pct: Number($("op-bt").value) });
    $("op-msg").textContent = r.ok ? "Saved." : err(r, "Not saved.");
    if (r.ok) loadProgram();
  });

  // ---------------------------------------------------------------- course files
  async function loadFiles() {
    const r = await VA.fetchJson(API + "/files" + ($("of-status").value ? "?status=" + $("of-status").value : ""));
    const names = { DRAFT: "With the teacher", SUBMITTED: "Waiting to be checked", LOCKED: "Locked" };
    $("of-body").innerHTML = (r.ok ? r.body : []).map(function (f) {
      return "<tr><td><strong>" + esc(f.course) + "</strong><small>Section " + esc(f.section) + "</small></td><td>" + esc(f.term_label) + "</td><td>" + f.clos + "</td><td>" +
        '<span class="ob-status ' + f.status + '">' + names[f.status] + '</span></td><td><button type="button" class="row-action of-open" data-id="' + f.offering_id + '" data-name="' + esc(f.course) + '">Open</button></td></tr>';
    }).join("") || '<tr><td colspan="5" class="sd-dim">Nothing here.</td></tr>';
  }
  $("of-status").addEventListener("change", loadFiles);
  let openId = null;
  $("of-body").addEventListener("click", function (e) {
    const b = e.target.closest(".of-open");
    if (!b) return;
    openId = b.getAttribute("data-id");
    $("of-title").textContent = b.getAttribute("data-name");
    $("of-detail").hidden = false; $("of-file").hidden = false;
    window.ObeCourseFile.open(openId, { office: true });
  });
  async function decide(lock) {
    const r = await send("POST", API + "/offerings/" + openId + "/decide", { lock: lock, note: $("of-note").value.trim() || null });
    if (!r.ok) { VA.toast(err(r, "Not done."), "error"); return; }
    VA.toast(lock ? "Locked." : "Sent back to the teacher.");
    $("of-note").value = "";
    loadFiles(); window.ObeCourseFile.open(openId, { office: true });
  }
  $("of-lock").addEventListener("click", function () { decide(true); });
  $("of-return").addEventListener("click", function () { decide(false); });

  // ---------------------------------------------------------------- PLO attainment
  $("or-run").addEventListener("click", async function () {
    const terms = $("or-term").value.split(",").map(function (t) { return t.trim(); }).filter(Boolean);
    const q = terms.map(function (t) { return "term=" + encodeURIComponent(t); }).join("&");
    const r = await VA.fetchJson(API + "/programs/" + $("or-prog").value + "/report" + (q ? "?" + q : ""));
    if (!r.ok) { $("or-hint").textContent = err(r, "Could not load."); return; }
    const d = r.body;
    $("or-hint").textContent = d.classes + " class(es) counted. A student reaches a PLO at " + d.student_pct + "%; a PLO is attained when " + d.batch_pct + "% of the students reach it.";
    $("or-body").innerHTML = d.plos.map(function (p) {
      return "<tr><td><strong>" + esc(p.code) + "</strong><small>" + esc(p.title) + "</small></td><td>" + p.assessed + "</td><td>" + (p.average == null ? "—" : p.average + "%") +
        "</td><td>" + (p.batch_pct == null ? "—" : p.reached + " (" + p.batch_pct + "%)") + "</td><td>" +
        (p.attained == null ? '<span class="sd-dim">no marks yet</span>' : p.attained ? '<span class="ob-yes">Yes</span>' : '<span class="ob-no">No</span>') + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">No PLOs for this program.</td></tr>';
  });

  loadPrograms();
})();
