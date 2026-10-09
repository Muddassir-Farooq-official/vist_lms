(function () {
  "use strict";

  // CSV Imports > Past results: POST /api/v1/results/history/import?dry_run=true|false
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const fileInput = document.getElementById("history-file");
  if (!fileInput) return;
  const errBox = document.getElementById("history-error");
  const out = document.getElementById("history-result");

  function show(r) {
    const b = r.body || {};
    if (!r.ok) {
      errBox.textContent = (b.error && b.error.message) || "The file could not be processed.";
      errBox.hidden = false;
      return;
    }
    let html;
    if (!b.ok) {
      html = "<p><strong>Nothing was imported.</strong> Fix these rows and try again:</p><table class=\"dash-table\"><thead><tr><th>Row</th><th>Column</th><th>Problem</th></tr></thead><tbody>" +
        b.errors.map(function (e) { return "<tr><td>" + e.row + "</td><td>" + esc(e.field) + "</td><td>" + esc(e.message) + "</td></tr>"; }).join("") + "</tbody></table>";
    } else {
      html = "<p><strong>" + (b.dry_run ? "The file is valid." : "Imported.") + "</strong> " +
        b.to_import + " result(s) " + (b.dry_run ? "will be imported" : "imported") + " for " + b.students + " student(s) across " + b.terms.length + " term(s)" +
        (b.already_imported ? "; " + b.already_imported + " row(s) were already imported and are skipped" : "") +
        (b.new_courses.length ? "; new courses: " + esc(b.new_courses.join(", ")) : "") + ".</p>" +
        (b.dry_run && b.to_import ? "<p>Press <strong>Import</strong> to save them.</p>" : "");
    }
    out.innerHTML = html;
    out.hidden = false;
  }

  async function send(dryRun) {
    errBox.hidden = true;
    out.hidden = true;
    if (!fileInput.files.length) {
      errBox.textContent = "Choose the CSV file first.";
      errBox.hidden = false;
      return;
    }
    if (!dryRun && !window.confirm("Import these past results? They will count in students' CGPA straight away.")) return;
    const fd = new FormData();
    fd.append("file", fileInput.files[0]);
    show(await VA.fetchJson("/api/v1/results/history/import?dry_run=" + (dryRun ? "true" : "false"), { method: "POST", body: fd }));
  }

  document.getElementById("history-check-btn").addEventListener("click", function () { send(true); });
  document.getElementById("history-import-btn").addEventListener("click", function () { send(false); });
})();