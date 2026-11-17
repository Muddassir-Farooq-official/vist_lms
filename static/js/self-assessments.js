(function () {
  "use strict";
  // Self-assessments: marks of paper quizzes/tests (teacher enters; student and parent see). Never part of results.
  const VA = window.VistAdmin;
  const API = "/api/v1/self-assessments";
  const $ = function (id) { return document.getElementById(id); };
  const root = $("sa-root");
  if (!root) return;
  const mode = root.dataset.mode;
  function esc(v) { return VA.escapeHtml(v == null ? "" : String(v)); }
  function err(b, f) { return (b && b.error && b.error.message) || f; }
  function day(iso) { try { return new Date(String(iso).slice(0, 10) + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }); } catch (e) { return iso; } }
  function num(v) { return v == null ? "—" : String(Math.round(v * 100) / 100); }

  // ------------------------------------------------------------ student / parent
  function drawMine(courses) {
    const box = $("sa-mine");
    if (!courses.length) { box.innerHTML = '<p class="cc-meta">No self-assessment marks yet.</p>'; return; }
    box.innerHTML = courses.map(function (c) {
      return '<div class="sa-course"><h3>' + esc(c.code) + " — " + esc(c.title) + (c.average_percent != null ? ' <span class="crs-chip">Average ' + esc(c.average_percent) + "%</span>" : "") +
        '</h3><div class="table-scroll"><table class="tk-table"><thead><tr><th>Date</th><th>Assessment</th><th>Marks</th><th>Class average</th><th>Remark</th></tr></thead><tbody>' +
        c.items.map(function (i) {
          const mine = i.absent ? '<span class="sd-dim">Absent</span>' : (i.obtained == null ? '<span class="sd-dim">Not entered</span>' : "<strong>" + num(i.obtained) + "</strong> / " + num(i.max_marks));
          return "<tr><td>" + esc(day(i.held_on)) + "</td><td>" + esc(i.title) + ' <span class="cc-meta">' + esc(i.kind_label) + (i.topic ? " · " + esc(i.topic) : "") + "</span></td><td>" +
            mine + "</td><td>" + (i.class_average == null ? "—" : num(i.class_average) + " / " + num(i.max_marks)) + "</td><td>" + esc(i.remark || "") + "</td></tr>";
        }).join("") + "</tbody></table></div></div>";
    }).join("");
  }

  async function loadMine(url) {
    const r = await VA.fetchJson(url);
    if (!r.ok) { $("sa-mine").innerHTML = '<p class="cc-meta">' + esc(err(r.body, "Could not load the marks.")) + "</p>"; return; }
    drawMine(r.body);
  }

  if (mode === "student") { loadMine(API + "/me"); return; }
  if (mode === "parent") {
    (async function () {
      const k = await VA.fetchJson("/api/v1/parents/me/children");
      const kids = k.ok ? k.body : [];
      $("sa-child").innerHTML = kids.map(function (x) { return '<option value="' + esc(x.id) + '">' + esc(x.name) + "</option>"; }).join("");
      if (!kids.length) { $("sa-mine").innerHTML = '<p class="cc-meta">No children are linked to your account.</p>'; return; }
      $("sa-child").addEventListener("change", function () { loadMine(API + "/children/" + $("sa-child").value); });
      loadMine(API + "/children/" + kids[0].id);
    })();
    return;
  }

  // ------------------------------------------------------------ teacher
  let view = null, editing = null, marking = null;
  function course() { return $("sa-course").value; }

  function draw() {
    const items = view.items;
    $("sa-items").innerHTML = items.length ? items.map(function (i) {
      return '<div class="gt-card"><div><strong>' + esc(i.title) + '</strong> <span class="crs-chip">' + esc(i.kind_label) + '</span><div class="cc-meta">' + esc(day(i.held_on)) +
        " · out of " + num(i.max_marks) + (i.topic ? " · " + esc(i.topic) : "") + '</div><div class="cc-meta">Entered ' + i.entered + " · absent " + i.absent +
        (i.average != null ? " · average " + num(i.average) + " · highest " + num(i.highest) + " · lowest " + num(i.lowest) + " · under half " + i.under_half : "") +
        '</div></div><div class="gt-card-actions"><button type="button" class="row-action sa-marks" data-id="' + esc(i.id) + '">Marks</button> ' +
        '<button type="button" class="row-action sa-edit" data-id="' + esc(i.id) + '">Edit</button> <button type="button" class="row-action sa-del" data-id="' + esc(i.id) + '">Delete</button></div></div>';
    }).join("") : '<p class="cc-meta">No self-assessments in this course yet. Press "New self-assessment" after a quiz or class test.</p>';
    $("sa-grid").querySelector("thead").innerHTML = "<tr><th>Roll no.</th><th>Name</th>" + items.map(function (i) { return "<th>" + esc(i.title) + "<br><span class=cc-meta>/" + num(i.max_marks) + "</span></th>"; }).join("") + "<th>Average %</th></tr>";
    $("sa-grid").querySelector("tbody").innerHTML = view.rows.map(function (r) {
      return "<tr><td>" + esc(r.roll_number) + "</td><td>" + esc(r.name) + "</td>" + r.marks.map(function (m) { return "<td>" + (m.absent ? "A" : num(m.obtained)) + "</td>"; }).join("") +
        "<td>" + (r.average_percent == null ? "—" : esc(r.average_percent)) + "</td></tr>";
    }).join("") || '<tr><td colspan="3" class="sd-dim">No students enrolled.</td></tr>';
    document.querySelectorAll(".sa-marks").forEach(function (b) { b.addEventListener("click", function () { openMarks(b.dataset.id); }); });
    document.querySelectorAll(".sa-edit").forEach(function (b) { b.addEventListener("click", function () { openForm(items.find(function (i) { return i.id === b.dataset.id; })); }); });
    document.querySelectorAll(".sa-del").forEach(function (b) {
      b.addEventListener("click", async function () {
        if (!(await VA.confirm("Delete this self-assessment and all its marks?", "Delete"))) return;
        const r = await VA.fetchJson(API + "/" + b.dataset.id, { method: "DELETE" });
        if (!r.ok && r.status !== 204) return VA.toast(err(r.body, "Could not delete."), "error");
        load();
      });
    });
  }

  async function load() {
    if (!course()) return;
    const r = await VA.fetchJson(API + "/courses/" + course());
    if (!r.ok) return VA.toast(err(r.body, "Could not load."), "error");
    view = r.body; draw();
  }

  function openForm(i) {
    editing = i || null;
    $("sa-title").textContent = i ? "Edit " + i.title : "New self-assessment";
    $("sa-f-title").value = i ? i.title : ""; $("sa-f-kind").value = i ? i.kind : "QUIZ";
    $("sa-f-date").value = i ? String(i.held_on).slice(0, 10) : new Date().toISOString().slice(0, 10);
    $("sa-f-max").value = i ? i.max_marks : 10; $("sa-f-topic").value = i ? i.topic || "" : "";
    $("sa-error").hidden = true; $("sa-modal").showModal();
  }

  async function saveForm() {
    const body = { title: $("sa-f-title").value.trim(), kind: $("sa-f-kind").value, held_on: $("sa-f-date").value, max_marks: Number($("sa-f-max").value), topic: $("sa-f-topic").value.trim() || null };
    const r = editing ? await VA.fetchJson(API + "/" + editing.id, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      : await VA.fetchJson(API + "/courses/" + course(), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { $("sa-error").textContent = err(r.body, "Could not save."); $("sa-error").hidden = false; return; }
    $("sa-modal").close();
    await load();
    if (!editing) openMarks(r.body.id);
  }

  function openMarks(id) {
    marking = view.items.find(function (i) { return i.id === id; });
    if (!marking) return;
    const col = view.items.indexOf(marking);
    $("sa-marks-title").textContent = marking.title + " — out of " + num(marking.max_marks);
    $("sa-marks-body").innerHTML = view.rows.map(function (r) {
      const m = r.marks[col];
      return '<tr data-id="' + esc(r.enrollment_id) + '"><td>' + esc(r.roll_number) + "</td><td>" + esc(r.name) + '</td><td><input type="number" class="text-input small-input sm-v" min="0" max="' +
        marking.max_marks + '" step="0.5" value="' + (m.obtained == null ? "" : m.obtained) + '" aria-label="Marks" /></td><td><input type="checkbox" class="sm-a"' + (m.absent ? " checked" : "") +
        ' aria-label="Absent" /></td><td><input class="text-input sm-r" maxlength="200" value="' + esc(m.remark || "") + '" aria-label="Remark" /></td></tr>';
    }).join("");
    $("sa-marks-error").hidden = true; $("sa-marks-modal").showModal();
  }

  async function saveMarks() {
    const entries = Array.from($("sa-marks-body").querySelectorAll("tr")).map(function (tr) {
      const v = tr.querySelector(".sm-v").value.trim(), a = tr.querySelector(".sm-a").checked;
      return { enrollment_id: tr.dataset.id, obtained: a || v === "" ? null : Number(v), absent: a, remark: tr.querySelector(".sm-r").value.trim() || null };
    });
    const r = await VA.fetchJson(API + "/" + marking.id + "/marks", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ entries: entries }) });
    if (!r.ok) { $("sa-marks-error").textContent = err(r.body, "Could not save the marks."); $("sa-marks-error").hidden = false; return; }
    $("sa-marks-modal").close(); VA.toast("Marks saved. Students can see them now."); load();
  }

  $("sa-csv").addEventListener("change", async function (e) {
    const f = e.target.files[0]; e.target.value = "";
    if (!f || !marking) return;
    const fd = new FormData(); fd.append("file", f);
    const r = await VA.fetchJson(API + "/" + marking.id + "/marks.csv", { method: "POST", body: fd });
    if (!r.ok) { $("sa-marks-error").textContent = err(r.body, "Could not read the file."); $("sa-marks-error").hidden = false; return; }
    $("sa-marks-modal").close(); VA.toast(r.body.saved + " marks uploaded."); load();
  });
  $("sa-new").addEventListener("click", function () { if (course()) openForm(null); else VA.toast("You have no courses yet.", "error"); });
  $("sa-save").addEventListener("click", saveForm);
  $("sa-close").addEventListener("click", function () { $("sa-modal").close(); });
  $("sa-marks-save").addEventListener("click", saveMarks);
  $("sa-marks-close").addEventListener("click", function () { $("sa-marks-modal").close(); });
  $("sa-course").addEventListener("change", load);
  (async function () {
    const r = await VA.fetchJson(API + "/my-courses");
    const list = r.ok ? r.body : [];
    $("sa-course").innerHTML = list.map(function (c) { return '<option value="' + esc(c.id) + '">' + esc(c.label + " — " + c.title + (c.term_label ? " (" + c.term_label + ")" : "")) + "</option>"; }).join("") ||
      '<option value="">No courses</option>';
    load();
  })();
})();
