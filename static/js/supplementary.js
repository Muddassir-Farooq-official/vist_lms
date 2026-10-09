(function () {
  "use strict";

  // Exam office: /api/v1/supplementary/...
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/supplementary";
  let attempts = [];

  function say(text) { const el = document.getElementById("sp-msg"); el.textContent = text; el.hidden = !text; }
  function errText(r) { return (r.body && r.body.error && r.body.error.message) || "That did not work."; }
  function term() { return document.getElementById("sp-term").value; }

  async function load() {
    // Note: does not clear the message line, so "Saved" / "Published" stays visible after the list refreshes.
    if (!term()) return say("Choose the term.");
    const [e, a] = await Promise.all([VA.fetchJson(API + "/eligible?term_label=" + encodeURIComponent(term())),
                                      VA.fetchJson(API + "/attempts?term_label=" + encodeURIComponent(term()))]);
    if (!e.ok) return say(errText(e));
    document.getElementById("sp-eligible-panel").hidden = false;
    document.getElementById("sp-eligible").innerHTML = e.body.map(function (s) {
      return '<tr><td><input type="checkbox" value="' + s.enrollment_id + '" aria-label="Select" /></td><td>' + esc(s.roll_number || "") + "</td><td>" +
        esc(s.student_name || "") + "</td><td>" + esc(s.course_code + " " + s.course_title) + '</td><td class="num">' + esc(s.percentage + "% " + s.letter_grade) + "</td></tr>";
    }).join("") || '<tr><td colspan="5">Nobody else failed on marks in this term.</td></tr>';
    attempts = a.ok ? a.body : [];
    document.getElementById("sp-attempts-panel").hidden = !attempts.length;
    document.getElementById("sp-attempts").innerHTML = attempts.map(function (x) {
      const done = x.status === "PUBLISHED";
      const res = done ? (x.result.changed ? "Passed " + x.result.letter_grade + " (" + x.result.percentage + "%)" : (x.result.absent ? "Absent — repeat course" : "Not passed (" + x.result.percentage + "%)"))
                       : (x.status === "MARKED" ? "Marked, not published" : "Waiting for marks");
      return "<tr><td>" + esc(x.roll_number || "") + "</td><td>" + esc(x.course_code) + "</td><td>" + esc(x.component + " /" + x.component_max) + "</td><td>" +
        (done ? esc(x.obtained_marks === null ? "—" : String(x.obtained_marks)) :
          '<input type="number" min="0" max="' + x.component_max + '" step="0.5" class="text-input small-input" data-mark="' + x.id + '" value="' + (x.obtained_marks === null ? "" : x.obtained_marks) + '" aria-label="Marks" />') +
        "</td><td>" + (done ? (x.absent ? "Yes" : "") : '<input type="checkbox" data-absent="' + x.id + '"' + (x.absent ? " checked" : "") + ' aria-label="Absent" />') +
        "</td><td>" + esc(res) + "</td><td>" + (done ? "" : '<button type="button" class="row-action" data-save="' + x.id + '">Save</button> <button type="button" class="row-action" data-cancel="' + x.id + '">Cancel</button>') + "</td></tr>";
    }).join("");
    document.querySelectorAll("[data-save]").forEach(function (b) { b.addEventListener("click", function () { save(b.dataset.save); }); });
    document.querySelectorAll("[data-cancel]").forEach(function (b) {
      b.addEventListener("click", async function () {
        if (!window.confirm("Cancel this registration?")) return;
        const r = await VA.fetchJson(API + "/attempts/" + b.dataset.cancel, { method: "DELETE" });
        if (!r.ok) return say(errText(r));
        load();
      });
    });
  }

  async function save(id) {
    const absent = document.querySelector('[data-absent="' + id + '"]').checked;
    const raw = document.querySelector('[data-mark="' + id + '"]').value;
    const body = absent ? { absent: true } : { obtained_marks: raw === "" ? null : Number(raw) };
    const r = await VA.fetchJson(API + "/attempts/" + id + "/mark", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) return say(errText(r));
    const p = r.body.preview;
    say(absent ? "Saved as absent." : "Saved. If published now: " + p.percentage + "% — " + (p.outcome === "PASSED" ? "pass, grade " + p.letter_grade + (p.capped ? " (capped)" : "") : "not passed") + ".");
    load();
  }

  document.getElementById("sp-load").addEventListener("click", function () { say(""); load(); });
  document.getElementById("sp-all").addEventListener("change", function (e) {
    document.querySelectorAll("#sp-eligible input[type=checkbox]").forEach(function (c) { c.checked = e.target.checked; });
  });
  document.getElementById("sp-register").addEventListener("click", async function () {
    const ids = Array.prototype.map.call(document.querySelectorAll("#sp-eligible input:checked"), function (c) { return c.value; });
    if (!ids.length) return say("Tick the students to register.");
    const r = await VA.fetchJson(API + "/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enrollment_ids: ids }) });
    if (!r.ok) return say(errText(r));
    say(r.body.registered + " registered.");
    load();
  });
  document.getElementById("sp-publish").addEventListener("click", async function () {
    const ids = attempts.filter(function (x) { return x.status === "MARKED"; }).map(function (x) { return x.id; });
    if (!ids.length) return say("No marked results to publish. Save marks first.");
    if (!window.confirm("Publish " + ids.length + " supplementary result(s)? Passes replace the failed results and students are told.")) return;
    const r = await VA.fetchJson(API + "/publish", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ attempt_ids: ids }) });
    if (!r.ok) return say(errText(r));
    say(r.body.published + " published: " + r.body.passed + " passed, " + r.body.not_passed + " not passed.");
    load();
  });
})();