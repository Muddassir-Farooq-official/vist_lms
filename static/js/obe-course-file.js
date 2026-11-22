(function () {
  "use strict";
  // One class's OBE course file (templates/_obe_course_file.html), used by the teacher's page (faculty/obe.html) and by the
  // exam office (admin/obe.html, through window.ObeCourseFile.open). API: /api/v1/obe/offerings/{id}
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const send = function (method, url, body) { return VA.fetchJson(url, { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }); };
  let cf = null, oid = "", office = false, marksFor = null;
  const api = function (p) { return "/api/v1/obe/offerings/" + oid + (p || ""); };

  function editable() { return cf && cf.status === "DRAFT"; }

  function cloRow(c) {
    const plos = cf.plos.map(function (p) {
      return '<label title="' + esc(p.title) + '"><input type="checkbox" class="ob-plo" value="' + p.id + '"' + ((c.plo_ids || []).indexOf(p.id) !== -1 ? " checked" : "") + "> " + esc(p.code) + "</label>";
    }).join("") || '<span class="sd-dim">No PLOs for this program yet (exam office)</span>';
    return '<div class="ob-row" data-id="' + (c.id || "") + '"><input class="text-input ob-code" maxlength="12" value="' + esc(c.code || "") + '" aria-label="Code">' +
      '<textarea class="text-input ob-text" maxlength="1000" aria-label="Outcome">' + esc(c.text || "") + "</textarea>" +
      '<select class="text-input ob-bloom" aria-label="Bloom level">' + cf.bloom.map(function (b) { return '<option value="' + b.code + '"' + (b.code === c.bloom ? " selected" : "") + ">" + b.code + " " + esc(b.name) + "</option>"; }).join("") + "</select>" +
      '<div class="ob-plos">' + plos + '</div><button type="button" class="row-action ob-del ob-edit" aria-label="Remove">✕</button></div>';
  }

  function draw() {
    const can = editable();
    $("ob-status").textContent = { DRAFT: "Draft", SUBMITTED: "Submitted: waiting for the exam office", LOCKED: "Locked" }[cf.status];
    $("ob-status").className = "ob-status " + cf.status;
    $("ob-note").hidden = !(cf.note && cf.status === "DRAFT");
    $("ob-note").textContent = cf.note ? "Sent back: " + cf.note : "";
    $("ob-clos").innerHTML = cf.clos.map(cloRow).join("") || '<p class="panel-hint">No CLOs yet.</p>';
    const cloOpts = function (sel) { return cf.clos.map(function (c) { return '<option value="' + c.id + '"' + (c.id === sel ? " selected" : "") + ">" + esc(c.code) + "</option>"; }).join(""); };
    $("ob-assess").innerHTML = cf.assessments.map(function (a) {
      const items = a.items.length ? a.items : [];
      return '<div class="ob-assess" data-id="' + a.id + '"><strong>' + esc(a.name) + "</strong> <small class=\"sd-dim\">" + a.max_marks + " marks</small>" +
        '<div class="ob-items">' + items.map(function (i) {
          return '<div class="ob-item"><input class="text-input ob-label" maxlength="20" placeholder="Part (Q1) or empty" value="' + esc(i.label || "") + '">' +
            '<input type="number" class="text-input ob-max" min="0.5" step="0.5" value="' + i.max_marks + '" aria-label="Marks">' +
            '<select class="text-input ob-clo" aria-label="CLO">' + cloOpts(i.clo_id) + '</select><button type="button" class="row-action ob-item-del ob-edit" aria-label="Remove">✕</button></div>';
        }).join("") + "</div>" +
        '<div class="decision-bar"><button type="button" class="role-btn secondary-btn ob-edit ob-item-add">Add a line</button>' +
        '<button type="button" class="role-btn ob-edit ob-item-save">Save</button>' +
        (a.parts ? '<button type="button" class="role-btn secondary-btn ob-marks-open">Part marks…</button>' : "") + "</div></div>";
    }).join("") || '<p class="panel-hint">This class has no assessments yet (Grading).</p>';
    document.querySelectorAll(".ob-edit").forEach(function (b) { b.hidden = !can; });
    document.querySelectorAll("#ob-clos input, #ob-clos textarea, #ob-clos select, #ob-assess .ob-items input, #ob-assess .ob-items select").forEach(function (x) { x.disabled = !can; });
    $("ob-actions").innerHTML = can ? '<button type="button" class="role-btn" id="ob-submit">Send to the exam office</button>' :
      '<span class="panel-hint">' + (cf.status === "LOCKED" ? "Locked by the exam office." : "Waiting for the exam office.") + "</span>";
    loadAttainment();
  }

  async function open(id, opts) {
    oid = id;
    office = !!(opts && opts.office);
    marksFor = null;
    $("ob-marks-card").hidden = true;
    const r = await VA.fetchJson(api(""));
    if (!r.ok) { VA.toast(err(r, "Could not load."), "error"); return; }
    cf = r.body;
    draw();
  }

  function collectClos() {
    return Array.prototype.map.call(document.querySelectorAll("#ob-clos .ob-row"), function (row) {
      return { id: row.getAttribute("data-id") || null, code: row.querySelector(".ob-code").value.trim(), text: row.querySelector(".ob-text").value.trim(),
        bloom: row.querySelector(".ob-bloom").value, plo_ids: Array.prototype.map.call(row.querySelectorAll(".ob-plo:checked"), function (c) { return c.value; }) };
    });
  }
  $("ob-add").addEventListener("click", function () {
    const now = collectClos();
    cf.clos = now.concat([{ code: "CLO" + (now.length + 1), text: "", bloom: "C2", plo_ids: [] }]);
    draw();
  });
  $("ob-clos").addEventListener("click", function (e) {
    if (!e.target.classList.contains("ob-del")) return;
    e.target.closest(".ob-row").remove();
  });
  $("ob-save").addEventListener("click", async function () {
    const r = await send("PUT", api("/clos"), { clos: collectClos() });
    if (!r.ok) { VA.toast(err(r, "Not saved."), "error"); return; }
    cf = r.body; draw(); VA.toast("CLOs saved.");
  });
  $("ob-copy").addEventListener("click", async function () {
    const r = await send("POST", api("/copy"));
    if (!r.ok) { VA.toast(err(r, "Nothing to copy."), "error"); return; }
    cf = r.body; draw(); VA.toast("Copied. Check them and save.");
  });

  $("ob-assess").addEventListener("click", async function (e) {
    const box = e.target.closest(".ob-assess");
    if (!box) return;
    const aid = box.getAttribute("data-id");
    if (e.target.classList.contains("ob-item-add")) {
      if (!cf.clos.length || !cf.clos[0].id) { VA.toast("Save the CLOs first.", "error"); return; }
      const a = cf.assessments.find(function (x) { return x.id === aid; });
      a.items = readItems(box).concat([{ label: "", max_marks: a.max_marks, clo_id: cf.clos[0].id }]);
      draw();
    } else if (e.target.classList.contains("ob-item-del")) {
      e.target.closest(".ob-item").remove();
    } else if (e.target.classList.contains("ob-item-save")) {
      const r = await send("PUT", api("/assessments/" + aid + "/items"), { items: readItems(box) });
      if (!r.ok) { VA.toast(err(r, "Not saved."), "error"); return; }
      cf = r.body; draw(); VA.toast("Saved.");
    } else if (e.target.classList.contains("ob-marks-open")) {
      openMarks(aid, box.querySelector("strong").textContent);
    }
  });
  function readItems(box) {
    return Array.prototype.map.call(box.querySelectorAll(".ob-item"), function (it) {
      return { label: it.querySelector(".ob-label").value.trim() || null, max_marks: Number(it.querySelector(".ob-max").value), clo_id: it.querySelector(".ob-clo").value };
    });
  }

  async function openMarks(aid, name) {
    marksFor = aid;
    const r = await VA.fetchJson(api("/assessments/" + aid + "/marks"));
    if (!r.ok) { VA.toast(err(r, "Could not load."), "error"); return; }
    const d = r.body;
    $("ob-marks-title").textContent = "Question-part marks: " + name;
    $("ob-marks").innerHTML = "<thead><tr><th>Student</th>" + d.parts.map(function (p) { return "<th>" + esc(p.label) + " <small>/" + p.max_marks + "</small></th>"; }).join("") + "</tr></thead><tbody>" +
      d.students.map(function (s) {
        return '<tr data-e="' + s.enrollment_id + '"><td><strong>' + esc(s.name) + "</strong><small>" + esc(s.roll_number || "") + "</small></td>" + d.parts.map(function (p) {
          const v = s.marks[p.id];
          return '<td><input type="number" class="text-input ob-pm" data-i="' + p.id + '" min="0" max="' + p.max_marks + '" step="0.5" value="' + (v == null ? "" : v) + '" style="max-width:90px" aria-label="' + esc(p.label) + '"></td>';
        }).join("") + "</tr>";
      }).join("") + "</tbody>";
    $("ob-marks-card").hidden = false;
    $("ob-marks-card").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  $("ob-marks-save").addEventListener("click", async function () {
    const entries = Array.prototype.map.call(document.querySelectorAll(".ob-pm"), function (x) {
      return { item_id: x.getAttribute("data-i"), enrollment_id: x.closest("tr").getAttribute("data-e"), obtained: x.value === "" ? null : Number(x.value) };
    });
    const r = await send("PUT", api("/assessments/" + marksFor + "/marks"), { entries: entries });
    if (!r.ok) { VA.toast(err(r, "Not saved."), "error"); return; }
    VA.toast(r.body.saved + " mark(s) saved.");
    loadAttainment();
  });

  document.addEventListener("click", async function (e) {
    if (!e.target || e.target.id !== "ob-submit") return;
    const r = await send("POST", api("/submit"));
    if (!r.ok) { VA.toast(err(r, "Not sent."), "error"); return; }
    VA.toast("Sent to the exam office.");
    open(oid, { office: office });
  });

  async function loadAttainment() {
    const r = await VA.fetchJson(api("/attainment"));
    if (!r.ok) return;
    const d = r.body;
    $("ob-att-hint").textContent = "A student reaches a CLO at " + d.student_pct + "% of its marks; a CLO is attained when " + d.batch_pct + "% of the students reach it.";
    $("ob-att").innerHTML = d.clos.map(function (c) {
      return "<tr><td><strong>" + esc(c.code) + "</strong><small>" + esc(c.text) + "</small></td><td>" + esc(c.bloom) + "</td><td>" + c.assessed + "</td><td>" +
        (c.average == null ? "—" : c.average + "%") + "</td><td>" + (c.batch_pct == null ? "—" : c.reached + " (" + c.batch_pct + "%)") + "</td><td>" +
        (c.attained == null ? '<span class="sd-dim">no marks yet</span>' : c.attained ? '<span class="ob-yes">Yes</span>' : '<span class="ob-no">No</span>') + "</td></tr>";
    }).join("") || '<tr><td colspan="6" class="sd-dim">No CLOs yet.</td></tr>';
  }

  window.ObeCourseFile = { open: open };

  // The teacher's page: choose one of my classes.
  const pick = $("ob-class");
  if (pick && !pick.hidden) {
    VA.fetchJson("/api/v1/obe/my-classes").then(function (r) {
      const list = r.ok ? r.body : [];
      pick.innerHTML = list.map(function (c) { return '<option value="' + c.offering_id + '">' + esc(c.course + " · " + c.term_label + (c.section && c.section !== "A" ? " (" + c.section + ")" : "")) + "</option>"; }).join("") ||
        '<option value="">No class in an open term</option>';
      if (pick.value) open(pick.value);
    });
    pick.addEventListener("change", function () { if (this.value) open(this.value); });
  }
})();
