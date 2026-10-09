(function () {
  "use strict";

  // Grading page: download / upload the marks sheet for the chosen course.
  // GET /api/v1/academics/offerings/{id}/marks.csv  ·  POST .../marks/upload?dry_run=true|false
  const VA = window.VistAdmin;
  const select = document.getElementById("grading-offering-select");
  const link = document.getElementById("marks-sheet-download");
  const fileInput = document.getElementById("marks-sheet-file");
  const msg = document.getElementById("marks-sheet-msg");
  if (!select || !link) return;

  function say(t) { msg.textContent = t; msg.hidden = !t; }
  let keepMessage = false;
  function sync() {
    link.href = select.value ? "/api/v1/academics/offerings/" + select.value + "/marks.csv" : "#";
    if (!keepMessage) say("");            // a different course: clear; our own refresh after uploading: keep the result visible
    keepMessage = false;
  }

  async function send(dry) {
    if (!select.value) return say("Choose the course first.");
    if (!fileInput.files.length) return say("Choose the filled marks sheet (.csv).");
    if (!dry && !window.confirm("Save these marks? Blank cells are left as they are.")) return;
    const fd = new FormData();
    fd.append("file", fileInput.files[0]);
    const r = await VA.fetchJson("/api/v1/academics/offerings/" + select.value + "/marks/upload?dry_run=" + dry, { method: "POST", body: fd });
    if (!r.ok) return say((r.body && r.body.error && r.body.error.message) || "The sheet could not be read.");
    const b = r.body;
    const problems = b.errors.map(function (e) { return "row " + e.row + ": " + e.message; }).join(" | ");
    say((dry ? b.rows_ok + " student row(s) are fine (" + b.marks + " marks)." : b.marks + " mark(s) saved for " + b.rows_ok + " student(s).") +
        (problems ? " Not saved — " + problems : ""));
    if (!dry) { keepMessage = true; select.dispatchEvent(new Event("change")); }      // refresh the marks grid
  }

  select.addEventListener("change", sync);
  document.getElementById("marks-sheet-check").addEventListener("click", function () { send(true); });
  document.getElementById("marks-sheet-upload").addEventListener("click", function () { send(false); });
  sync();
})();