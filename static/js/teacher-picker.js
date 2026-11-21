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
      '  <div id="tp-sugg-wrap" hidden><strong>Suggested</strong> <span class="sd-dim">least busy first; credit hours a week now → with this section, and their most</span>' +
      '    <div id="tp-sugg" class="tp-sugg"></div></div>' +
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
      '  <div id="tp-override" hidden><div class="form-field"><label for="tp-reason">Reason to allow it anyway (kept in the activity log)</label>' +
      '    <input type="text" id="tp-reason" class="text-input" maxlength="300" /></div>' +
      '    <button type="button" class="role-btn" id="tp-allow">Allow and save</button></div>' +
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
    dialog.querySelector("#tp-sugg").addEventListener("click", function (e) {
      const b = e.target.closest("[data-tid]");
      if (!b) return;
      search.value = b.dataset.label;
      save.dataset.teacherId = b.dataset.tid;
      save.disabled = false;
      results.hidden = true;
    });
    dialog.querySelector("#tp-allow").addEventListener("click", function (e) {
      const reason = dialog.querySelector("#tp-reason").value.trim();
      if (reason.length < 5) { dialog.querySelector("#tp-reason").focus(); return; }
      submit(save.dataset.teacherId || null, e.currentTarget, reason);
    });
    dialog.querySelector("#tp-remove").addEventListener("click", function (e) {
      if (!window.confirm("Remove the teacher from this course? It will show as unassigned until you choose another.")) return;
      submit(null, e.currentTarget);
    });
    dialog.querySelector("#tp-close").addEventListener("click", function () { dialog.close(); });
  }

  async function submit(teacherId, btn, overrideReason) {
    const err = dialog.querySelector("#tp-error");
    err.hidden = true;
    btn.disabled = true;
    const body = { assigned_teacher_id: teacherId };
    if (overrideReason) body.load_override_reason = overrideReason;
    const r = await window.VistAdmin.fetchJson("/api/v1/academics/offerings/" + current.id, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    btn.disabled = false;
    if (!r.ok) {
      err.textContent = (r.body && r.body.error && r.body.error.message) || "Could not save the teacher.";
      err.hidden = false;
      const over = r.body && r.body.error && r.body.error.details && r.body.error.details.kind === "TEACHER_LOAD";
      dialog.querySelector("#tp-override").hidden = !over;
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
    dialog.querySelector("#tp-override").hidden = true;
    dialog.querySelector("#tp-reason").value = "";
    dialog.showModal();
    dialog.querySelector("#tp-search").focus();
    loadSuggestions(offering.id);
  }

  // The teachers for this section, best first: taught it before / teaches in its program, under-loaded, least busy.
  async function loadSuggestions(offeringId) {
    const wrap = dialog.querySelector("#tp-sugg-wrap"), box = dialog.querySelector("#tp-sugg");
    wrap.hidden = true;
    const r = await window.VistAdmin.fetchJson("/api/v1/academics/timetable/smart/workload/candidates/" + offeringId, { quiet: true });
    if (!r.ok || !current || current.id !== offeringId) return;
    const esc = window.VistAdmin.escapeHtml;
    const list = r.body.teachers.filter(function (t) { return !t.current; }).slice(0, 8);
    if (!list.length) return;
    box.innerHTML = list.map(function (t, i) {
      const notes = [t.taught_before ? "taught it before" : "", t.same_program ? "teaches in this program" : "",
        t.over ? "would be over the most" : "", t.under ? "under-loaded" : "", t.clash ? t.clash : ""].filter(Boolean).join(" · ");
      const label = t.name + " (" + t.code + ")";
      return '<button type="button" data-tid="' + esc(t.id) + '" data-label="' + esc(label) + '" class="' + (t.over || t.clash ? "tp-bad" : "") + '">' +
        "<span>" + (i === 0 && t.id === r.body.suggested ? "<strong>Suggested: </strong>" : "") + esc(t.name) + "<br><small>" + esc(notes || t.department || "") + "</small></span>" +
        "<span>" + t.credit_hours + " → <strong>" + t.after + "</strong> / " + t.max + "</span></button>";
    }).join("");
    wrap.hidden = false;
  }

  window.TeacherPicker = { open: open };
})();