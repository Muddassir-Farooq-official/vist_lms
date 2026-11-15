(function () {
  "use strict";
  // Results -> Class results & promotion: one program + semester (year) + term; result cards, gazette, promote / undo.
  const VA = window.VistAdmin;
  const R = "/api/v1/results";
  const $ = function (id) { return document.getElementById(id); };
  if (!$("cl-table")) return;
  let data = null, lastBatch = null;
  function esc(v) { return VA.escapeHtml(v == null ? "" : String(v)); }
  function err(b, f) { return (b && b.error && b.error.message) || f; }
  function f2(v) { return v == null ? "—" : Number(v).toFixed(2); }
  function query() { return "program_id=" + encodeURIComponent($("cl-program").value) + "&semester=" + encodeURIComponent($("cl-semester").value) + "&term_label=" + encodeURIComponent($("cl-term").value); }

  function draw() {
    const word = data.period_word;
    $("cl-summary").innerHTML = [["Students", data.rows.length], ["Promoted", data.counts.PROMOTED], ["On probation", data.counts.PROBATION],
      ["Not promoted", data.counts.NOT_PROMOTED], ["Result pending", data.counts.PENDING], ["No subjects", data.counts.NO_SUBJECTS], ["Table", data.grading.name]].map(function (k) {
      return '<div class="kpi-card"><div class="kpi-label">' + esc(k[0]) + '</div><div class="kpi-value">' + esc(k[1]) + "</div></div>"; }).join("");
    $("cl-table").querySelector("thead").innerHTML = '<tr><th><input type="checkbox" id="cl-all" aria-label="Tick all who can move up" /></th><th>Roll no.</th><th>Name</th>' +
      (data.annual ? "<th>Marks</th><th>%</th><th>Division</th>" : "<th>" + word + " GPA</th>") + "<th>Status</th><th>To clear</th><th>Position</th></tr>";
    $("cl-table").querySelector("tbody").innerHTML = data.rows.map(function (r) {
      const can = r.status === "PROMOTED" || r.status === "PROBATION";
      return '<tr><td><input type="checkbox" class="cl-pick" value="' + esc(r.student_id) + '"' + (can ? " checked" : "") + (r.status === "PENDING" || r.status === "NO_SUBJECTS" ? " disabled" : "") + ' /></td>' +
        "<td>" + esc(r.roll_number) + "</td><td>" + esc(r.name) + (r.guardian_name ? '<div class="cc-meta">' + esc(r.guardian_name) + "</div>" : "") + "</td>" +
        (data.annual ? "<td>" + esc(r.total_obtained) + "/" + esc(r.total_max) + "</td><td>" + f2(r.percentage) + "</td><td>" + esc(r.division || "—") + "</td>" : "<td>" + f2(r.gpa) + "</td>") +
        '<td class="' + (r.status === "NOT_PROMOTED" ? "bad" : r.status === "PROBATION" ? "warn" : r.status === "PENDING" || r.status === "NO_SUBJECTS" ? "" : "ok") + '">' + esc(r.status_label) + "</td>" +
        "<td>" + esc(r.failed.join(", ") || "—") + "</td><td>" + (r.position ? esc(["", "1st", "2nd", "3rd"][r.position]) : "") + "</td></tr>";
    }).join("") || '<tr><td colspan="8" class="sd-dim">No active students in this program and ' + word.toLowerCase() + ".</td></tr>";
    $("cl-all").addEventListener("change", function (e) { document.querySelectorAll(".cl-pick:not(:disabled)").forEach(function (c) { c.checked = e.target.checked; }); });
    $("cl-actions").hidden = false;
    $("cl-cards").href = R + "/class/result-cards.pdf?" + query();
    $("cl-gazette").href = R + "/class/gazette.pdf?" + query();
    $("cl-promote").textContent = "Promote ticked students to " + word.toLowerCase() + " " + (data.semester + 1);
  }

  async function load() {
    if (!$("cl-program").value || !$("cl-term").value) return VA.toast("Choose a program and a term.", "error");
    const r = await VA.fetchJson(R + "/class?" + query());
    if (!r.ok) return VA.toast(err(r.body, "Could not load the class."), "error");
    data = r.body; lastBatch = null; $("cl-undo").hidden = true;
    draw();
  }

  async function init() {
    const [p, t] = await Promise.all([VA.fetchJson("/api/v1/programs", { quiet: true }), VA.fetchJson("/api/v1/academics/academic-terms", { quiet: true })]);
    $("cl-program").innerHTML = (p.ok ? p.body : []).map(function (x) { return '<option value="' + esc(x.id) + '">' + esc(x.code + " — " + x.name) + "</option>"; }).join("");
    $("cl-term").innerHTML = (t.ok ? t.body : []).map(function (x) { return '<option value="' + esc(x.label) + '">' + esc(x.label) + "</option>"; }).join("");
  }

  $("cl-load").addEventListener("click", load);
  $("cl-promote").addEventListener("click", async function () {
    const ids = Array.from(document.querySelectorAll(".cl-pick:checked")).map(function (c) { return c.value; });
    if (!ids.length) return VA.toast("Tick the students who move up.", "error");
    if (!(await VA.confirm("Move " + ids.length + " student(s) to " + data.period_word.toLowerCase() + " " + (data.semester + 1) + "? You can undo it."))) return;
    const r = await VA.fetchJson(R + "/class/promote", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ program_id: data.program.id, semester: data.semester, student_ids: ids }) });
    if (!r.ok) return VA.toast(err(r.body, "Could not promote."), "error");
    lastBatch = r.body.batch;
    VA.toast(r.body.promoted + " promoted to " + data.period_word.toLowerCase() + " " + r.body.to_semester + (r.body.skipped ? " (" + r.body.skipped + " had already moved)" : "") + ".");
    await load();
    lastBatch = r.body.batch; $("cl-undo").hidden = false;
  });
  $("cl-undo").addEventListener("click", async function () {
    if (!lastBatch) return;
    const r = await VA.fetchJson(R + "/class/promote/undo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ batch: lastBatch }) });
    if (!r.ok) return VA.toast(err(r.body, "Could not undo."), "error");
    VA.toast(r.body.moved_back + " moved back.");
    lastBatch = null; $("cl-undo").hidden = true;
    load();
  });

  let started = false;
  document.querySelectorAll('.portal-tab[data-tab="CLASS"]').forEach(function (b) { b.addEventListener("click", function () { if (!started) { started = true; init(); } }); });
})();
