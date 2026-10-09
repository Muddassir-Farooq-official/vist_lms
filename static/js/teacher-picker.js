(function () {
  "use strict";

  // Assign, change or remove the teacher of one course offering.
  // Used on Courses > Offerings and on an offering's own page.
  //   window.TeacherPicker.open(offering, onSaved)
  // offering: { id, course_code, course_title, section_label, term_label, teacher_name, curriculum }
  // PATCH /api/v1/academics/offerings/{id}  { assigned_teacher_id: uuid | null }

  let dialog = null;
  let current = null;
  let onSavedCb = null;
  let debounce = null;

  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "modal";
    dialog.id = "teacher-picker-modal";
    dialog.innerHTML =
      '<div class="modal-body">' +
      '  <h2 id="tp-title">Teacher</h2>' +
      '  <p class="panel-hint" id="tp-context"></p>' +
      '  <p id="tp-current"></p>' +
      '  <div class="form-field">' +
      '    <label for="tp-search">New teacher (search by name or employee code)</label>' +
      '    <input type="text" id="tp-search" class="text-input" autocomplete="off" />' +
      '    <div id="tp-results" class="search-results-dropdown" hidden></div>' +
      "  </div>" +
      '  <div class="form-actions">' +
      '    <button type="button" class="role-btn" id="tp-save" disabled>Save</button>' +
      '    <button type="button" class="danger-btn" id="tp-remove" hidden>Remove teacher</button>' +
      "  </div>" +
      '  <p class="panel-hint">If the course already has a timetable, the new teacher must be free at those times.</p>' +
      '  <p id="tp-error" class="dash-alert error" hidden></p>' +
      '  <button id="tp-close" class="back-link modal-close-btn" type="button">Close</button>' +
      "</div>";
    document.body.appendChild(dialog);

    const search = dialog.querySelector("#tp-search");
    const results = dialog.querySelector("#tp-results");
    const save = dialog.querySelector("#tp-save");

    search.addEventListener("input", function () {
      save.disabled = true;
      save.dataset.teacherId = "";
      clearTimeout(debounce);
      const q = search.value.trim();
      if (q.length < 2) { results.hidden = true; return; }
      debounce = setTimeout(async function () {
        const r = await window.VistAdmin.fetchJson("/api/v1/employees?" + new URLSearchParams({ q: q, role: "TEACHER", page_size: "10" }));
        if (!r.ok) return;
        const items = (r.body && r.body.items) || [];
        results.innerHTML = "";
        if (!items.length) {
          results.innerHTML = '<div class="search-result-item search-result-empty">No teachers match that search.</div>';
          results.hidden = false;
          return;
        }
        items.forEach(function (emp) {
          const item = document.createElement("div");
          item.className = "search-result-item";
          item.textContent = emp.first_name + " " + emp.last_name + " (" + emp.employee_code + ")";
          item.addEventListener("click", function () {
            search.value = item.textContent;
            save.dataset.teacherId = emp.id;
            save.disabled = false;
            results.hidden = true;
          });
          results.appendChild(item);
        });
        results.hidden = false;
      }, 250);
    });

    save.addEventListener("click", function () { submit(save.dataset.teacherId || null, save); });
    dialog.querySelector("#tp-remove").addEventListener("click", function (e) {
      if (!window.confirm("Remove the teacher from this course? It will show as unassigned until you choose another.")) return;
      submit(null, e.currentTarget);
    });
    dialog.querySelector("#tp-close").addEventListener("click", function () { dialog.close(); });
  }

  async function submit(teacherId, btn) {
    const err = dialog.querySelector("#tp-error");
    err.hidden = true;
    btn.disabled = true;
    const r = await window.VistAdmin.fetchJson("/api/v1/academics/offerings/" + current.id, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ assigned_teacher_id: teacherId }),
    });
    btn.disabled = false;
    if (!r.ok) {
      err.textContent = (r.body && r.body.error && r.body.error.message) || "Could not save the teacher.";
      err.hidden = false;
      return;
    }
    dialog.close();
    if (onSavedCb) onSavedCb(r.body);
  }

  function open(offering, onSaved) {
    if (!dialog) build();
    current = offering;
    onSavedCb = onSaved;
    const has = !!offering.teacher_name;
    dialog.querySelector("#tp-title").textContent = has ? "Change teacher" : "Assign teacher";
    const where = (offering.curriculum && offering.curriculum.length) ? " · " + offering.curriculum.join(", ") : "";
    dialog.querySelector("#tp-context").textContent =
      offering.course_code + " — " + offering.course_title + " · Section " + (offering.section_label || "A") +
      " · " + offering.term_label + where;
    dialog.querySelector("#tp-current").textContent = "Current teacher: " + (offering.teacher_name || "none (unassigned)");
    dialog.querySelector("#tp-remove").hidden = !has;
    dialog.querySelector("#tp-search").value = "";
    dialog.querySelector("#tp-results").hidden = true;
    dialog.querySelector("#tp-save").disabled = true;
    dialog.querySelector("#tp-error").hidden = true;
    dialog.showModal();
    dialog.querySelector("#tp-search").focus();
  }

  window.TeacherPicker = { open: open };
})();