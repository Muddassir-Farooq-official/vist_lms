(function () {
  "use strict";
  // Results -> Grading tables & mark split: named tables per affiliating body, each program's table and split, subject splits,
  // and which part each catalog assessment counts in.
  const VA = window.VistAdmin;
  const R = "/api/v1/results";
  const $ = function (id) { return document.getElementById(id); };
  if (!$("gt-list")) return;
  const PARTS = ["SESSIONAL", "MID", "FINAL", "PRACTICAL"];
  const PART_LABEL = { SESSIONAL: "Sessional", MID: "Mid-term", FINAL: "Final", PRACTICAL: "Practical / clinical" };
  let tables = [], programs = [], splits = [], courses = [], editing = null;

  function esc(v) { return VA.escapeHtml(v == null ? "" : String(v)); }
  function err(b, f) { return (b && b.error && b.error.message) || f; }
  function num(id) { const v = $(id).value.trim(); return v === "" ? null : Number(v); }
  function setNum(id, v) { $(id).value = v == null ? "" : v; }
  function send(url, method, body) { return VA.fetchJson(url, { method: method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) }); }

  // ------------------------------------------------------------ list of tables
  function drawTables() {
    const box = $("gt-list");
    if (!tables.length) { box.innerHTML = '<p class="cc-meta">No grading tables yet. Every program uses the default scale. Add the ready-made drafts or a new table.</p>'; return; }
    box.innerHTML = tables.map(function (t) {
      const bits = [t.system === "ANNUAL" ? "Annual" : "Semester", "pass " + t.pass_percent + "%"];
      if (t.theory_pass_percent != null) bits.push("theory " + t.theory_pass_percent + "%");
      if (t.practical_pass_percent != null) bits.push("practical " + t.practical_pass_percent + "%");
      if (t.resit_cap_percent != null) bits.push("re-sit max " + t.resit_cap_percent + "%");
      if (t.sliding_points) bits.push("sliding points");
      return '<div class="gt-card"><div><strong>' + esc(t.name) + "</strong>" + (t.is_draft ? ' <span class="status-pill dropped">Draft: check it</span>' : "") +
        '<div class="cc-meta">' + esc(t.body || "") + "</div><div class=\"cc-meta\">" + esc(bits.join(" · ")) + " · " + t.bands.length + " grades" +
        (t.programs.length ? " · used by " + esc(t.programs.map(function (p) { return p.code; }).join(", ")) : " · not used yet") + "</div>" +
        (t.notes ? '<div class="cc-meta">' + esc(t.notes) + "</div>" : "") + "</div>" +
        '<div class="gt-card-actions"><button type="button" class="row-action gt-edit" data-id="' + esc(t.id) + '">Edit</button>' +
        (t.programs.length ? "" : ' <button type="button" class="row-action gt-del" data-id="' + esc(t.id) + '">Delete</button>') + "</div></div>";
    }).join("");
    box.querySelectorAll(".gt-edit").forEach(function (b) { b.addEventListener("click", function () { openEditor(tables.find(function (t) { return t.id === b.getAttribute("data-id"); })); }); });
    box.querySelectorAll(".gt-del").forEach(function (b) {
      b.addEventListener("click", async function () {
        if (!(await VA.confirm("Delete this grading table? No program uses it."))) return;
        const r = await send(R + "/grading-tables/" + b.getAttribute("data-id"), "DELETE");
        if (!r.ok) return VA.toast(err(r.body, "Could not delete."), "error");
        load();
      });
    });
  }

  // ------------------------------------------------------------ editor
  function bandRow(b) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td><input class="text-input gb-letter" maxlength="4" value="' + esc(b.letter) + '" /></td>' +
      '<td><input class="text-input gb-min" type="number" min="0" max="100" step="0.5" value="' + esc(b.min_percent) + '" /></td>' +
      '<td><input class="text-input gb-pts" type="number" min="0" max="10" step="0.01" value="' + esc(b.grade_points) + '" /></td>' +
      '<td class="gt-low"><input class="text-input gb-low" type="number" min="0" max="10" step="0.01" value="' + esc(b.grade_points_low) + '" /></td>' +
      '<td><input type="checkbox" class="gb-pass"' + (b.is_passing ? " checked" : "") + " /></td>" +
      '<td><input class="text-input gb-div" maxlength="40" value="' + esc(b.division) + '" placeholder="e.g. 1st Division" /></td>' +
      '<td><input class="text-input gb-rem" maxlength="60" value="' + esc(b.remark) + '" /></td>' +
      '<td><button type="button" class="row-action gb-x" aria-label="Remove">×</button></td>';
    tr.querySelector(".gb-x").addEventListener("click", function () { tr.remove(); });
    $("gt-bands").appendChild(tr);
  }
  function slidingChanged() { document.querySelectorAll("#gt-modal .gt-low").forEach(function (c) { c.hidden = !$("gt-sliding").checked; }); }

  function openEditor(t) {
    editing = t || null;
    $("gt-title").textContent = t ? "Edit " + t.name : "New grading table";
    $("gt-name").value = t ? t.name : ""; $("gt-body").value = t ? t.body || "" : ""; $("gt-system").value = t ? t.system : "SEMESTER";
    setNum("gt-pass", t ? t.pass_percent : 50); setNum("gt-tpass", t ? t.theory_pass_percent : null); setNum("gt-ppass", t ? t.practical_pass_percent : null);
    setNum("gt-cap", t ? t.resit_cap_percent : null); setNum("gt-promo", t ? t.min_gpa_promotion : null); setNum("gt-prob", t ? t.probation_gpa : null);
    setNum("gt-degree", t ? t.min_cgpa_degree : null);
    $("gt-sliding").checked = !!(t && t.sliding_points); $("gt-draft").checked = !!(t && t.is_draft); $("gt-notes").value = t ? t.notes || "" : "";
    $("gt-bands").innerHTML = "";
    (t ? t.bands : [{ letter: "A", min_percent: 80, grade_points: 4, is_passing: true }, { letter: "B", min_percent: 65, grade_points: 3, is_passing: true },
      { letter: "C", min_percent: 50, grade_points: 2, is_passing: true }, { letter: "F", min_percent: 0, grade_points: 0, is_passing: false }]).forEach(bandRow);
    slidingChanged();
    $("gt-error").hidden = true;
    $("gt-modal").showModal();
  }

  async function saveEditor() {
    const bands = Array.from($("gt-bands").querySelectorAll("tr")).map(function (tr) {
      const low = tr.querySelector(".gb-low").value.trim();
      return { letter: tr.querySelector(".gb-letter").value.trim(), min_percent: Number(tr.querySelector(".gb-min").value), grade_points: Number(tr.querySelector(".gb-pts").value),
        grade_points_low: $("gt-sliding").checked && low !== "" ? Number(low) : null, is_passing: tr.querySelector(".gb-pass").checked,
        division: tr.querySelector(".gb-div").value.trim() || null, remark: tr.querySelector(".gb-rem").value.trim() || null };
    });
    const body = { name: $("gt-name").value.trim(), body: $("gt-body").value.trim() || null, system: $("gt-system").value, pass_percent: num("gt-pass"),
      theory_pass_percent: num("gt-tpass"), practical_pass_percent: num("gt-ppass"), resit_cap_percent: num("gt-cap"), min_gpa_promotion: num("gt-promo"),
      probation_gpa: num("gt-prob"), min_cgpa_degree: num("gt-degree"), sliding_points: $("gt-sliding").checked, is_draft: $("gt-draft").checked,
      notes: $("gt-notes").value.trim() || null, bands: bands };
    if (body.pass_percent == null) { $("gt-error").textContent = "Give the overall pass mark."; $("gt-error").hidden = false; return; }
    const r = editing ? await send(R + "/grading-tables/" + editing.id, "PUT", body) : await send(R + "/grading-tables", "POST", body);
    if (!r.ok) { $("gt-error").textContent = err(r.body, "Could not save the table."); $("gt-error").hidden = false; return; }
    $("gt-modal").close();
    VA.toast("Table saved. Results already published keep their grades.");
    load();
  }

  // ------------------------------------------------------------ programs: table + split
  function splitOf(key, id) { return splits.find(function (s) { return s[key] === id; }); }
  function splitCells(s) {
    return PARTS.map(function (p) {
      return '<td><input class="text-input gs-part" data-part="' + p + '" type="number" min="0" max="1000" step="1" style="width:80px" value="' + (s ? esc(s[p.toLowerCase()]) : "") + '" /></td>';
    }).join("");
  }
  function readSplit(tr) {
    const v = {}; let any = false;
    tr.querySelectorAll(".gs-part").forEach(function (i) { const n = i.value.trim() === "" ? 0 : Number(i.value); v[i.getAttribute("data-part")] = n; if (n > 0) any = true; });
    return any ? v : null;
  }
  function examCell(p) {
    const aff = p.exam_body === "AFFILIATED", parts = p.university_parts || [];
    return '<td class="gp-exam"><select class="select-input gp-body"><option value="INSTITUTE">This institute</option><option value="AFFILIATED"' + (aff ? " selected" : "") +
      ">Affiliating university</option></select>" + '<div class="gp-parts"' + (aff ? "" : " hidden") + ">" + ["MID", "FINAL", "PRACTICAL"].map(function (x) {
        return '<label><input type="checkbox" class="gp-part" value="' + x + '"' + (parts.indexOf(x) >= 0 ? " checked" : "") + " /> " + x.charAt(0) + x.slice(1).toLowerCase() + "</label>";
      }).join("") + '</div><button type="button" class="row-action gp-body-save">Save</button></td>';
  }

  function drawPrograms() {
    const tbody = $("gt-programs").querySelector("tbody");
    const usable = tables.filter(function (t) { return !t.is_draft; });
    tbody.innerHTML = programs.map(function (p) {
      const current = tables.find(function (t) { return t.programs.some(function (x) { return x.id === p.id; }); });
      const s = splitOf("program_id", p.id);
      return '<tr data-id="' + esc(p.id) + '"><td><strong>' + esc(p.code) + '</strong><div class="cc-meta">' + esc(p.name) + "</div></td>" +
        '<td><select class="select-input gp-table"><option value="">Default scale</option>' + usable.map(function (t) {
          return '<option value="' + esc(t.id) + '"' + (current && current.id === t.id ? " selected" : "") + ">" + esc(t.name) + "</option>"; }).join("") + "</select></td>" +
        examCell(p) + splitCells(s) + '<td><button type="button" class="row-action gp-save">Save split</button>' + (s ? ' <button type="button" class="row-action gp-clear">Remove split</button>' : "") + "</td></tr>";
    }).join("");
    tbody.querySelectorAll(".gp-body").forEach(function (sel) {
      const box = sel.closest("td").querySelector(".gp-parts");
      sel.addEventListener("change", function () { box.hidden = sel.value !== "AFFILIATED"; });
    });
    tbody.querySelectorAll(".gp-body-save").forEach(function (b) {
      b.addEventListener("click", async function () {
        const td = b.closest("td"), id = b.closest("tr").getAttribute("data-id");
        const parts = Array.from(td.querySelectorAll(".gp-part:checked")).map(function (c) { return c.value; });
        const r = await send(R + "/programs/" + id + "/exam-body", "PUT", { exam_body: td.querySelector(".gp-body").value, university_parts: parts });
        if (!r.ok) return VA.toast(err(r.body, "Could not save."), "error");
        VA.toast(r.body.exam_body === "AFFILIATED" ? "Saved: " + r.body.university_parts.join(", ").toLowerCase() + " by the university." : "Saved: the institute conducts every exam.");
        load();
      });
    });
    tbody.querySelectorAll(".gp-table").forEach(function (sel) {
      sel.addEventListener("change", async function () {
        const id = sel.closest("tr").getAttribute("data-id");
        const r = await send(R + "/programs/" + id + "/grading-table", "PUT", { scheme_id: sel.value || null });
        if (!r.ok) { VA.toast(err(r.body, "Could not change the table."), "error"); return load(); }
        VA.toast("Saved. Results not yet published use the new table; published ones keep theirs.");
        load();
      });
    });
    tbody.querySelectorAll(".gp-save").forEach(function (b) {
      b.addEventListener("click", async function () {
        const tr = b.closest("tr"), v = readSplit(tr);
        if (!v) return VA.toast("Give at least one part's marks, or press Remove split.", "error");
        const r = await send(R + "/mark-splits", "PUT", { program_id: tr.getAttribute("data-id"), values: v });
        if (!r.ok) return VA.toast(err(r.body, "Could not save the split."), "error");
        VA.toast("Split saved."); load();
      });
    });
    tbody.querySelectorAll(".gp-clear").forEach(function (b) {
      b.addEventListener("click", async function () {
        const r = await send(R + "/mark-splits", "PUT", { program_id: b.closest("tr").getAttribute("data-id"), values: null });
        if (!r.ok) return VA.toast(err(r.body, "Could not remove the split."), "error");
        load();
      });
    });
  }

  function drawCourses() {
    const tbody = $("gt-courses").querySelector("tbody");
    const own = splits.filter(function (s) { return s.course_id; });
    if (!own.length) { tbody.innerHTML = '<tr><td colspan="6" class="sd-dim">No subject has its own split.</td></tr>'; return; }
    tbody.innerHTML = own.map(function (s) {
      const c = courses.find(function (x) { return x.id === s.course_id; });
      return '<tr data-id="' + esc(s.course_id) + '"><td><strong>' + esc(c ? c.code : "?") + "</strong> " + esc(c ? c.title : "") + "</td>" + splitCells(s) +
        '<td><button type="button" class="row-action gc-save">Save</button> <button type="button" class="row-action gc-clear">Remove</button></td></tr>';
    }).join("");
    tbody.querySelectorAll(".gc-save").forEach(function (b) {
      b.addEventListener("click", async function () {
        const tr = b.closest("tr"), v = readSplit(tr);
        if (!v) return VA.toast("Give at least one part's marks, or press Remove.", "error");
        const r = await send(R + "/mark-splits", "PUT", { course_id: tr.getAttribute("data-id"), values: v });
        if (!r.ok) return VA.toast(err(r.body, "Could not save."), "error");
        VA.toast("Saved."); load();
      });
    });
    tbody.querySelectorAll(".gc-clear").forEach(function (b) {
      b.addEventListener("click", async function () {
        const r = await send(R + "/mark-splits", "PUT", { course_id: b.closest("tr").getAttribute("data-id"), values: null });
        if (!r.ok) return VA.toast(err(r.body, "Could not remove."), "error");
        load();
      });
    });
  }

  async function drawTypes() {
    const r = await VA.fetchJson("/api/v1/academics/assessment-types", { quiet: true });
    const tbody = $("gt-types").querySelector("tbody");
    if (!r.ok) { tbody.innerHTML = ""; return; }
    tbody.innerHTML = r.body.map(function (t) {
      return '<tr data-id="' + esc(t.id) + '"><td>' + esc(t.name) + "</td><td>" + esc(t.max_marks) + '</td><td><select class="select-input gty">' +
        PARTS.map(function (p) { return '<option value="' + p + '"' + ((t.component || "SESSIONAL") === p ? " selected" : "") + ">" + PART_LABEL[p] + "</option>"; }).join("") + "</select></td></tr>";
    }).join("") || '<tr><td colspan="3" class="sd-dim">No assessments in the catalog yet.</td></tr>';
    tbody.querySelectorAll(".gty").forEach(function (sel) {
      sel.addEventListener("change", async function () {
        const res = await send("/api/v1/academics/assessment-types/" + sel.closest("tr").getAttribute("data-id") + "/component", "PUT", { component: sel.value });
        if (!res.ok) return VA.toast(err(res.body, "Could not save."), "error");
        VA.toast("Saved.");
      });
    });
  }

  async function load() {
    const [t, p, s, c] = await Promise.all([VA.fetchJson(R + "/grading-tables"), VA.fetchJson(R + "/programs-exam-mode", { quiet: true }),
      VA.fetchJson(R + "/mark-splits"), courses.length ? Promise.resolve({ ok: true, body: courses }) : VA.fetchJson("/api/v1/academics/courses", { quiet: true })]);
    tables = t.ok ? t.body : []; programs = p.ok ? p.body : []; splits = s.ok ? s.body : []; courses = c.ok ? c.body : [];
    drawTables(); drawPrograms(); drawCourses(); drawTypes();
  }

  $("gt-new").addEventListener("click", function () { openEditor(null); });
  $("gt-add-band").addEventListener("click", function () { bandRow({ letter: "", min_percent: "", grade_points: "", is_passing: true }); slidingChanged(); });
  $("gt-sliding").addEventListener("change", slidingChanged);
  $("gt-save").addEventListener("click", saveEditor);
  $("gt-close").addEventListener("click", function () { $("gt-modal").close(); });
  $("gt-drafts").addEventListener("click", async function () {
    const r = await send(R + "/grading-tables/drafts", "POST");
    if (!r.ok) return VA.toast(err(r.body, "Could not add the drafts."), "error");
    VA.toast(r.body.added.length ? "Added as drafts: " + r.body.added.join(", ") + ". Check each one, then untick Draft." : "The ready-made drafts are already here.");
    load();
  });
  $("gt-add-course").addEventListener("click", async function () {
    const taken = splits.filter(function (s) { return s.course_id; }).map(function (s) { return s.course_id; });
    const options = courses.filter(function (c) { return taken.indexOf(c.id) < 0; }).map(function (c) { return { value: c.id, label: c.code + " — " + c.title }; });
    if (!options.length) return VA.toast("Every subject already has its own split.", "error");
    const pick = await VA.ask({ title: "Subject with its own split", input: "select", options: options, value: options[0].value, label: "Subject", ok: "Next" });
    if (!pick) return;
    const r = await send(R + "/mark-splits", "PUT", { course_id: pick, values: { SESSIONAL: 20, MID: 30, FINAL: 50, PRACTICAL: 0 } });
    if (!r.ok) return VA.toast(err(r.body, "Could not add."), "error");
    VA.toast("Added with Sessional 20, Mid 30, Final 50. Change the numbers and press Save.");
    load();
  });

  let started = false;
  document.querySelectorAll('.portal-tab[data-tab="TABLES"]').forEach(function (b) { b.addEventListener("click", function () { if (!started) { started = true; load(); } }); });
  if (location.hash === "#TABLES") { started = true; load(); }
})();
