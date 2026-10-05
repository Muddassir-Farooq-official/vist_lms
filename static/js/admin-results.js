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
    result.body.forEach(function (t) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(t.name) + "</td>" +
        "<td>" + VA.escapeHtml(t.category || "\u2014") + "</td>" +
        '<td class="num">' + t.max_marks + "</td>" +
        '<td><button type="button" class="danger-btn small-btn type-delete" data-id="' + t.id + '">Delete</button></td>';
      tr.querySelector(".type-delete").addEventListener("click", async function () {
        if (!confirm("Delete '" + t.name + "'? Only possible if no subject is currently using it.")) return;
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
    loadTypes();
  });

  document.addEventListener("vistadmin:ready", function () {
    loadTypes();
  });
})();