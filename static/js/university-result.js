(function () {
  "use strict";
  // Results -> University result: import an affiliated program's result from the university, by Reg No.
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  if (!$("ur-program")) return;
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (b, fb) { return (b && b.error && b.error.message) || fb; };
  let loaded = false;

  async function loadLists() {
    if (loaded) return;
    loaded = true;
    const [p, t] = await Promise.all([VA.fetchJson("/api/v1/results/programs-exam-mode", { quiet: true }), VA.fetchJson("/api/v1/academics/academic-terms", { quiet: true })]);
    const aff = (p.ok ? p.body : []).filter(function (x) { return x.exam_body === "AFFILIATED"; });
    $("ur-program").innerHTML = aff.length ? aff.map(function (x) { return '<option value="' + esc(x.id) + '">' + esc(x.code + " — " + x.name) + "</option>"; }).join("")
      : '<option value="">No affiliated program yet (set one on Grading tables &amp; mark split)</option>';
    $("ur-terms").innerHTML = (t.ok ? t.body : []).map(function (x) { return '<option value="' + esc(x.label) + '"></option>'; }).join("");
  }

  async function send(dry) {
    $("ur-error").hidden = true;
    const f = $("ur-file").files[0];
    const say = function (m) { $("ur-error").textContent = m; $("ur-error").hidden = false; return null; };
    if (!$("ur-program").value) return say("Choose the program.");
    if (!$("ur-term").value.trim()) return say("Choose the term.");
    if (!f) return say("Choose the CSV file.");
    const fd = new FormData(); fd.append("file", f);
    const q = new URLSearchParams({ program_id: $("ur-program").value, term_label: $("ur-term").value.trim(), dry_run: dry ? "true" : "false" });
    const r = await VA.fetchJson("/api/v1/results/university/import?" + q.toString(), { method: "POST", body: fd });
    if (!r.ok) return say(err(r.body, "The file could not be read."));
    return r.body;
  }

  $("ur-check").addEventListener("click", async function () {
    const d = await send(true);
    if (!d) return;
    const out = $("ur-result");
    if (d.error_count) {
      out.innerHTML = '<p class="dash-alert error">' + d.error_count + " row(s) have a problem; nothing was saved.</p>" +
        '<div class="table-scroll"><table class="tk-table"><thead><tr><th>Line</th><th>Student</th><th>Course</th><th>Problem</th></tr></thead><tbody>' +
        d.errors.map(function (x) { return "<tr><td>" + esc(x.line) + "</td><td>" + esc(x.who) + "</td><td>" + esc(x.course_code) + "</td><td>" + esc(x.error) + "</td></tr>"; }).join("") +
        "</tbody></table></div>";
      return;
    }
    out.innerHTML = "<p><strong>" + d.result_count + " result(s)</strong> for " + esc(d.program) + ", " + esc(d.term_label) + ": " + d.passed + " pass, " + d.failed + " fail" +
      (d.replacing ? ", <b>" + d.replacing + " replace an earlier result</b>" : "") + ". Grades from the " + esc(d.grading) + " table.</p>" +
      '<div class="table-scroll"><table class="tk-table"><thead><tr><th>Roll no.</th><th>Reg No</th><th>Student</th><th>Course</th><th>%</th><th>Grade</th><th>Result</th><th>Replaces</th></tr></thead><tbody>' +
      d.results.map(function (x) {
        return "<tr><td>" + esc(x.roll_number) + "</td><td>" + esc(x.reg_number || "—") + "</td><td>" + esc(x.name) + "</td><td>" + esc(x.course_code) + "</td><td>" + esc(x.percent) +
          "</td><td><b>" + esc(x.grade) + '</b></td><td class="' + (x.result === "PASS" ? "ok" : "bad") + '">' + esc(x.result) + "</td><td>" + esc(x.replaces || "") + "</td></tr>";
      }).join("") + '</tbody></table></div><div class="form-actions"><button type="button" class="role-btn" id="ur-apply">Save ' + d.result_count + " result(s)</button></div>";
    $("ur-apply").addEventListener("click", async function () {
      if (!(await VA.confirm("Save these results? Students see them at once and are told; GPA, transcripts and promotion use them.", "Save results"))) return;
      this.disabled = true;
      const res = await send(false);
      if (!res) { this.disabled = false; return; }
      if (res.error_count) { out.innerHTML = '<p class="dash-alert error">Something changed meanwhile; check the file again.</p>'; return; }
      out.innerHTML = '<p class="panel-hint">' + res.applied + " university result(s) saved.</p>";
      VA.toast(res.applied + " result(s) saved.");
    });
  });

  document.querySelectorAll('.portal-tab[data-tab="UNIVERSITY"]').forEach(function (b) { b.addEventListener("click", loadLists); });
  if (location.hash === "#UNIVERSITY") loadLists();
})();
