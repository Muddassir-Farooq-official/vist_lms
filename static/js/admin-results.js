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
        if (!(await VA.confirm("Delete '" + t.name + "'? Only possible if no subject is currently using it.", "Delete"))) return;
        const result = await VA.fetchJson("/api/v1/academics/assessment-types/" + t.id, { method: "DELETE" });
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