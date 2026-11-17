(function () {
  "use strict";

  const VA = window.VistAdmin;

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  const typesTbody = document.getElementById("types-tbody");
  const typesEmpty = document.getElementById("types-empty");
  const addTypeForm = document.getElementById("add-type-form");
  const addTypeSubmit = document.getElementById("add-type-submit");
  const addTypeError = document.getElementById("add-type-error");

  async function loadTypes() {
    const result = await VA.fetchJson("/api/v1/academics/assessment-types");
    typesTbody.innerHTML = "";
    if (!result.ok) return;

    typesEmpty.hidden = result.body.length > 0;
    typesTbody.closest("table").hidden = result.body.length === 0;
    const cats = {};
    result.body.forEach(function (t) { const c = t.category || "Other"; cats[c] = (cats[c] || 0) + 1; });
    const summary = document.getElementById("types-summary");
    if (summary) summary.innerHTML = Object.keys(cats).sort().map(function (c) {
      return '<span class="crs-chip"><b>' + cats[c] + "</b> " + VA.escapeHtml(c) + "</span>";
    }).join("");
    const dl = document.getElementById("type-categories");
    if (dl) dl.innerHTML = Object.keys(cats).filter(function (c) { return c !== "Other"; }).map(function (c) { return '<option value="' + VA.escapeHtml(c) + '">'; }).join("");
    result.body.forEach(function (t) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td><strong>" + VA.escapeHtml(t.name) + "</strong></td>" +
        "<td>" + (t.category ? '<span class="crs-chip">' + VA.escapeHtml(t.category) + "</span>" : '<span class="sd-dim">\u2014</span>') + "</td>" +
        '<td class="num"><strong>' + t.max_marks + "</strong></td>" +
        '<td class="crs-actions"><button type="button" class="sd-view tt-del type-delete" data-id="' + t.id + '">Delete</button></td>';
      tr.querySelector(".type-delete").addEventListener("click", async function () {
        const base = "/api/v1/academics/assessment-types/" + t.id;
        const u = await VA.fetchJson(base + "/usage");
        let result;
        if (u.ok && u.body.subjects > 0) {
          // In use: an administrator can force it, deleting every student's marks for it (after a CSV backup).
          const b = u.body;
          if (b.supplementary_attempts) { addTypeError.textContent = b.supplementary_attempts + " supplementary re-sit(s) use '" + t.name + "', so it cannot be deleted."; addTypeError.hidden = false; return; }
          const typed = await VA.ask({ title: "Delete '" + t.name + "' and its marks?", label: "Type " + t.name + " to confirm", value: "", ok: "Download backup and delete",
            intro: "WARNING: " + b.subjects + " subject(s) use this exam type. Deleting it deletes " + b.marks + " mark(s) of " + b.students + " student(s). " +
              (b.subjects_published ? b.subjects_published + " subject(s) already have published results: those results and transcripts stay exactly as they are. " : "") +
              "Subjects waiting for approval go back to their teachers. A CSV backup of every deleted mark downloads first. This cannot be undone." });
          if (typed == null) return;
          if (typed.trim().toLowerCase() !== t.name.trim().toLowerCase()) { addTypeError.textContent = "The name did not match, nothing was deleted."; addTypeError.hidden = false; return; }
          if (!(await VA.downloadFile(base + "/marks.csv", "marks-backup-" + t.name.replace(/[^A-Za-z0-9]+/g, "-") + ".csv"))) {
            addTypeError.textContent = "The backup could not be downloaded, so nothing was deleted."; addTypeError.hidden = false; return;
          }
          result = await VA.fetchJson(base + "?force=true&confirm_name=" + encodeURIComponent(typed.trim()), { method: "DELETE" });
        } else {
          if (!(await VA.confirm("Delete '" + t.name + "'? No subject is using it.", "Delete"))) return;
          result = await VA.fetchJson(base, { method: "DELETE" });
        }
        if (!result.ok && result.status !== 204) {
          addTypeError.textContent = extractErrorMessage(result.body, "Could not delete this type.");
          addTypeError.hidden = false;
          return;
        }
        addTypeError.hidden = true;
        loadTypes();
      });
      typesTbody.appendChild(tr);
    });
  }

  addTypeForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    addTypeSubmit.disabled = true;
    addTypeError.hidden = true;

    const result = await VA.fetchJson("/api/v1/academics/assessment-types", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: document.getElementById("type-name").value.trim(),
        category: document.getElementById("type-category").value.trim() || null,
        max_marks: parseFloat(document.getElementById("type-max-marks").value),
      }),
    });

    addTypeSubmit.disabled = false;

    if (!result.ok) {
      addTypeError.textContent = extractErrorMessage(result.body, "Could not add this type.");
      addTypeError.hidden = false;
      return;
    }

    addTypeForm.reset();
    VA.toast("Type added.");
    loadTypes();
  });

  document.addEventListener("vistadmin:ready", function () {
    loadTypes();
  });
})();