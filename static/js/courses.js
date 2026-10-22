(function () {
  "use strict";

  const VA = window.VistAdmin;

  // =========================================================================
  // Tabs — same pattern as the student/faculty portal pages
  // =========================================================================
  const tabButtons = document.querySelectorAll(".portal-tab");
  const tabPanels = document.querySelectorAll(".portal-tab-panel");

  function activateTab(tabName) {
    tabButtons.forEach(function (btn) {
      btn.classList.toggle("active", btn.dataset.tab === tabName);
    });
    tabPanels.forEach(function (panel) {
      panel.hidden = panel.dataset.tabPanel !== tabName;
    });
  }

  tabButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      activateTab(btn.dataset.tab);
    });
  });

  let coursesCache = [];
  let programsCache = [];
  let selectedProgramId = "";
  let teacherSearchDebounce = null;

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  // =========================================================================
  // Course catalog
  // =========================================================================
  const coursesTbody = document.getElementById("courses-tbody");
  const lcCourseSelect = document.getElementById("lc-course");
  const aoCourseSelect = document.getElementById("ao-course");

  async function loadCourses() {
    const result = await VA.fetchJson("/api/v1/academics/courses");
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load courses."));
      return;
    }
    VA.clearError();
    coursesCache = result.body;

    coursesTbody.innerHTML = "";
    coursesCache.forEach(function (c) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(c.code) + "</td>" +
        "<td>" + VA.escapeHtml(c.title) + "</td>" +
        '<td class="num">' + c.credit_hours + "</td>" +
        "<td>" + (c.is_active ? '<span class="status-pill active">Active</span>' : '<span class="status-pill dropped">Inactive</span>') + "</td>" +
        '<td class="row-actions"></td>';

      const editBtn = document.createElement("button");
      editBtn.className = "role-btn small-btn";
      editBtn.type = "button";
      editBtn.textContent = "Edit";
      editBtn.addEventListener("click", function () {
        openEditCourseModal(c);
      });
      tr.querySelector(".row-actions").appendChild(editBtn);

      coursesTbody.appendChild(tr);
    });

    [lcCourseSelect, aoCourseSelect].forEach(function (select) {
      const currentValue = select.value;
      select.innerHTML = '<option value="">Select&hellip;</option>';
      coursesCache.filter(function (c) { return c.is_active; }).forEach(function (c) {
        const opt = document.createElement("option");
        opt.value = c.id;
        opt.textContent = c.code + " \u2014 " + c.title + " (" + c.credit_hours + " cr)";
        select.appendChild(opt);
      });
      select.value = currentValue;
    });
  }

  const addCourseModal = document.getElementById("add-course-modal");
  const addCourseForm = document.getElementById("add-course-form");
  const addCourseSubmit = document.getElementById("add-course-submit");
  const addCourseError = document.getElementById("add-course-error");

  document.getElementById("open-add-course").addEventListener("click", function () {
    addCourseError.hidden = true;
    addCourseForm.reset();
    addCourseModal.showModal();
  });
  document.getElementById("close-add-course-modal").addEventListener("click", function () {
    addCourseModal.close();
  });

  addCourseForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    addCourseSubmit.disabled = true;
    addCourseError.hidden = true;

    const result = await VA.fetchJson("/api/v1/academics/courses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code: document.getElementById("ac-code").value.trim(),
        title: document.getElementById("ac-title").value.trim(),
        credit_hours: parseInt(document.getElementById("ac-credit-hours").value, 10),
        description: document.getElementById("ac-description").value.trim() || null,
      }),
    });

    addCourseSubmit.disabled = false;

    if (!result.ok) {
      addCourseError.textContent = extractErrorMessage(result.body, "Could not add course.");
      addCourseError.hidden = false;
      return;
    }

    addCourseModal.close();
    loadCourses();
  });

  // -------------------------------------------------------------------
  // Edit Course
  // -------------------------------------------------------------------
  const editCourseModal = document.getElementById("edit-course-modal");
  const editCourseForm = document.getElementById("edit-course-form");
  const editCourseSubmit = document.getElementById("edit-course-submit");
  const editCourseError = document.getElementById("edit-course-error");
  const ecCodeHint = document.getElementById("ec-code-hint");
  let activeEditCourseId = null;

  function openEditCourseModal(course) {
    activeEditCourseId = course.id;
    editCourseError.hidden = true;
    ecCodeHint.textContent = "Code: " + course.code + " (not editable)";
    document.getElementById("ec-title").value = course.title;
    document.getElementById("ec-credit-hours").value = course.credit_hours;
    document.getElementById("ec-description").value = course.description || "";
    document.getElementById("ec-active").value = String(course.is_active);
    editCourseModal.showModal();
  }

  document.getElementById("close-edit-course-modal").addEventListener("click", function () {
    editCourseModal.close();
  });

  editCourseForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!activeEditCourseId) return;
    editCourseSubmit.disabled = true;
    editCourseError.hidden = true;

    const result = await VA.fetchJson("/api/v1/academics/courses/" + activeEditCourseId, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: document.getElementById("ec-title").value.trim(),
        credit_hours: parseInt(document.getElementById("ec-credit-hours").value, 10),
        description: document.getElementById("ec-description").value.trim() || null,
        is_active: document.getElementById("ec-active").value === "true",
      }),
    });

    editCourseSubmit.disabled = false;

    if (!result.ok) {
      editCourseError.textContent = extractErrorMessage(result.body, "Could not save changes.");
      editCourseError.hidden = false;
      return;
    }

    editCourseModal.close();
    loadCourses();
  });

  // =========================================================================
  // Program curriculum
  // =========================================================================
  const curriculumProgramSelect = document.getElementById("curriculum-program-select");
  const curriculumTbody = document.getElementById("curriculum-tbody");
  const curriculumEmpty = document.getElementById("curriculum-empty");
  const linkCourseForm = document.getElementById("link-course-form");
  const linkCourseSubmit = document.getElementById("link-course-submit");
  const linkCourseError = document.getElementById("link-course-error");

  async function loadPrograms() {
    const result = await VA.fetchJson("/api/v1/programs");
    if (!result.ok) return;
    programsCache = result.body;

    curriculumProgramSelect.innerHTML = '<option value="">Select a program&hellip;</option>';
    programsCache.forEach(function (p) {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.code + " \u2014 " + p.name;
      curriculumProgramSelect.appendChild(opt);
    });
  }

  async function loadCurriculum(programId) {
    if (!programId) {
      curriculumTbody.innerHTML = "";
      curriculumEmpty.hidden = false;
      return;
    }
    const result = await VA.fetchJson("/api/v1/academics/programs/" + programId + "/curriculum");
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load curriculum."));
      return;
    }
    VA.clearError();

    curriculumTbody.innerHTML = "";
    if (result.body.length === 0) {
      curriculumEmpty.hidden = false;
      return;
    }
    curriculumEmpty.hidden = true;

    result.body
      .sort(function (a, b) { return a.semester_number - b.semester_number; })
      .forEach(function (link) {
        const tr = document.createElement("tr");
        tr.innerHTML =
          '<td class="num">' + link.semester_number + "</td>" +
          "<td>" + VA.escapeHtml(link.course_code) + " \u2014 " + VA.escapeHtml(link.course_title) + "</td>" +
          '<td class="num">' + link.credit_hours + "</td>";
        curriculumTbody.appendChild(tr);
      });
  }

  curriculumProgramSelect.addEventListener("change", function () {
    selectedProgramId = curriculumProgramSelect.value;
    loadCurriculum(selectedProgramId);
  });

  linkCourseForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    linkCourseError.hidden = true;

    if (!selectedProgramId) {
      linkCourseError.textContent = "Select a program above first.";
      linkCourseError.hidden = false;
      return;
    }

    linkCourseSubmit.disabled = true;
    const result = await VA.fetchJson("/api/v1/academics/courses/" + lcCourseSelect.value + "/programs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        program_id: selectedProgramId,
        semester_number: parseInt(document.getElementById("lc-semester").value, 10),
      }),
    });
    linkCourseSubmit.disabled = false;

    if (!result.ok) {
      linkCourseError.textContent = extractErrorMessage(result.body, "Could not add to curriculum.");
      linkCourseError.hidden = false;
      return;
    }

    linkCourseForm.reset();
    loadCurriculum(selectedProgramId);
  });

  // =========================================================================
  // Term offerings
  // =========================================================================
  const termLabelInput = document.getElementById("term-label-input");
  const offeringsTbody = document.getElementById("offerings-tbody");
  const offeringsEmpty = document.getElementById("offerings-empty");
  const onlyUnassigned = document.getElementById("only-unassigned");
  const unassignedCount = document.getElementById("unassigned-count");
  onlyUnassigned.addEventListener("change", function () { loadOfferings(); });

  async function loadOfferings() {
    const term = termLabelInput.value.trim();
    if (!term) return;
    const result = await VA.fetchJson("/api/v1/academics/offerings?" + new URLSearchParams({ term_label: term }));
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load offerings."));
      return;
    }
    VA.clearError();

    offeringsTbody.innerHTML = "";
    const unassigned = result.body.filter(function (o) { return !o.assigned_teacher_id; }).length;
    unassignedCount.textContent = unassigned ? "(" + unassigned + " without a teacher)" : "(every course has a teacher)";
    const rows = onlyUnassigned.checked ? result.body.filter(function (o) { return !o.assigned_teacher_id; }) : result.body;
    if (rows.length === 0) {
      offeringsEmpty.textContent = result.body.length ? "Every course in this term has a teacher." : "Select a term above to see its offerings, or create a new one.";
      offeringsEmpty.hidden = false;
      return;
    }
    offeringsEmpty.hidden = true;

    rows.forEach(function (o) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(o.course_code) + " \u2014 " + VA.escapeHtml(o.course_title) + "</td>" +
        "<td>" + VA.escapeHtml((o.curriculum && o.curriculum.length) ? o.curriculum.join(", ") : "\u2014 not in a curriculum") + "</td>" +
        "<td>" + VA.escapeHtml(o.section_label || "A") + "</td>" +
        '<td class="num">' + o.credit_hours + "</td>" +
        '<td class="teacher-cell">' + (o.teacher_name ? VA.escapeHtml(o.teacher_name) : '<span class="status-pill dropped">Unassigned</span>') + " </td>" +
        '<td class="num">' + o.enrolled_count + " / " + (o.capacity == null ? "\u221e" : o.capacity) + "</td>" +
        "<td>" + (o.is_open_for_enrollment ? '<span class="status-pill active">Open</span>' : '<span class="status-pill dropped">Closed</span>') + "</td>" +
        '<td class="row-actions"></td>';

      const actionsCell = tr.querySelector(".row-actions");

      const teacherBtn = document.createElement("button");
      teacherBtn.className = "role-btn small-btn" + (o.teacher_name ? " secondary-btn" : "");
      teacherBtn.type = "button";
      teacherBtn.textContent = o.teacher_name ? "Change" : "Assign";
      teacherBtn.addEventListener("click", function () {
        window.TeacherPicker.open(o, function () { loadOfferings(); });
      });
      tr.querySelector(".teacher-cell").appendChild(teacherBtn);

      if (!o.enrolled_count) {
        const delBtn = document.createElement("button");
        delBtn.className = "role-btn small-btn danger-btn";
        delBtn.type = "button";
        delBtn.textContent = "Delete\u2026";
        delBtn.addEventListener("click", async function () {
          if (!(await VA.confirm("Delete " + o.course_code + " section " + (o.section_label || "A") + "? It was created by mistake and nobody has enrolled. Its timetable slots go with it.", "Delete"))) return;
          const r = await VA.fetchJson("/api/v1/academics/offerings/" + o.id, { method: "DELETE" });
          if (!r.ok) { VA.showError(extractErrorMessage(r.body, "Could not delete it.")); return; }
          VA.toast("Offering deleted."); loadOfferings();
        });
        actionsCell.appendChild(delBtn);
      }

      const toggleBtn = document.createElement("button");
      toggleBtn.className = "role-btn small-btn";
      toggleBtn.type = "button";
      toggleBtn.textContent = o.is_open_for_enrollment ? "Close Enrollment" : "Open Enrollment";
      toggleBtn.addEventListener("click", async function () {
        toggleBtn.disabled = true;
        const result = await VA.fetchJson("/api/v1/academics/offerings/" + o.id, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ is_open_for_enrollment: !o.is_open_for_enrollment }),
        });
        if (!result.ok) {
          VA.showError(extractErrorMessage(result.body, "Could not update offering."));
          toggleBtn.disabled = false;
          return;
        }
        loadOfferings();
      });
      actionsCell.appendChild(toggleBtn);

      const enrollBtn = document.createElement("button");
      enrollBtn.className = "role-btn small-btn";
      enrollBtn.type = "button";
      enrollBtn.textContent = "Enroll Students";
      enrollBtn.addEventListener("click", function () { openBatchEnroll(o); });
      actionsCell.appendChild(enrollBtn);

      const seatsBtn = document.createElement("button");
      seatsBtn.className = "role-btn small-btn";
      seatsBtn.type = "button";
      seatsBtn.textContent = "Seats";
      seatsBtn.addEventListener("click", async function () {
        const answer = (await window.VistAdmin.promptText("Seats for " + o.course_code + " section " + o.section_label + " (empty = unlimited):", o.capacity == null ? "" : o.capacity));
        if (answer === null) return;
        const value = answer.trim() === "" ? null : parseInt(answer, 10);
        if (value !== null && (isNaN(value) || value < 1)) { VA.showError("Seats must be a whole number of 1 or more."); return; }
        const r = await VA.fetchJson("/api/v1/academics/offerings/" + o.id, {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ capacity: value }),
        });
        if (!r.ok) { VA.showError(extractErrorMessage(r.body, "Could not update seats.")); return; }
        loadOfferings();
      });
      actionsCell.appendChild(seatsBtn);

      const materialsBtn = document.createElement("button");
      materialsBtn.className = "role-btn small-btn";
      materialsBtn.type = "button";
      materialsBtn.textContent = "Manage";
      materialsBtn.addEventListener("click", function () {
        window.location.href = "/dashboard/admin/courses/offering/" + o.id;
      });
      actionsCell.appendChild(materialsBtn);

      offeringsTbody.appendChild(tr);
    });
  }

  document.getElementById("load-offerings-btn").addEventListener("click", loadOfferings);

  async function loadTermsDropdown(keepSelection) {
    const result = await VA.fetchJson("/api/v1/academics/terms");
    if (!result.ok) return;
    const previous = keepSelection ? termLabelInput.value : "";
    termLabelInput.innerHTML = '<option value="">Select a term&hellip;</option>';
    result.body.forEach(function (term) {
      const opt = document.createElement("option");
      opt.value = term;
      opt.textContent = term;
      termLabelInput.appendChild(opt);
    });
    if (previous) termLabelInput.value = previous;
  }

  termLabelInput.addEventListener("change", function () {
    if (termLabelInput.value) loadOfferings();
  });

  document.getElementById("refresh-terms-btn").addEventListener("click", function () {
    loadTermsDropdown(true);
  });

  // =========================================================================
  // End Semester
  // =========================================================================
  const endSemesterBtn = document.getElementById("end-semester-btn");
  const endSemesterError = document.getElementById("end-semester-error");
  const endSemesterSuccess = document.getElementById("end-semester-success");

  endSemesterBtn.addEventListener("click", async function () {
    const term = termLabelInput.value.trim();
    endSemesterError.hidden = true;
    endSemesterSuccess.hidden = true;
    if (!term) {
      endSemesterError.textContent = "Enter a term label above first.";
      endSemesterError.hidden = false;
      return;
    }

    endSemesterBtn.disabled = true;
    const previewResult = await VA.fetchJson("/api/v1/academics/end-semester/preview?" + new URLSearchParams({ term_label: term }));
    endSemesterBtn.disabled = false;

    if (!previewResult.ok) {
      endSemesterError.textContent = extractErrorMessage(previewResult.body, "Could not preview End Semester.");
      endSemesterError.hidden = false;
      return;
    }

    const p = previewResult.body;
    let message =
      "End \"" + term + "\"? This closes " + p.offerings_affected + " offering(s) to new enrollment and marks " +
      p.enrollments_to_complete + " currently-enrolled student(s) as Completed for it.";
    if (p.ungraded_count > 0) {
      message += "\n\nWARNING: " + p.ungraded_count + " of those still have no Pass/Fail outcome recorded. " +
        "They'll be marked Completed with no grade — you can still set one afterward via each enrollment, but " +
        "this is your last chance to grade them first if you'd rather do that now.";
    }
    message += " This can't be undone from here.";

    let overrideReason = null;
    if (p.override_needed) {
      overrideReason = await VA.ask({
        title: "Some courses are not finished", danger: true, ok: "End it anyway", input: "textarea", min: 10, label: "Why end the semester now? (kept on record)",
        intro: message + " NOT FINISHED: " + p.not_ready.join("; ") + ". Students in these courses will be closed out without a published result. " +
          "Normally: lock their attendance and publish their results first, then come back.",
      });
      if (!overrideReason) return;
    } else {
      const ok = await VA.ask({ title: "End the semester?", intro: message, ok: "End semester", input: "none", danger: true });
      if (!ok) return;
    }

    endSemesterBtn.disabled = true;
    const params = { term_label: term };
    if (overrideReason) params.override_reason = overrideReason;
    const result = await VA.fetchJson("/api/v1/academics/end-semester?" + new URLSearchParams(params), {
      method: "POST",
    });
    endSemesterBtn.disabled = false;

    if (!result.ok) {
      endSemesterError.textContent = extractErrorMessage(result.body, "Could not end the semester.");
      endSemesterError.hidden = false;
      return;
    }

    endSemesterSuccess.textContent =
      "Closed " + result.body.offerings_closed + " offering(s) and completed " +
      result.body.enrollments_completed + " enrollment(s) for " + result.body.term_label + ".";
    endSemesterSuccess.hidden = false;
    loadOfferings();
  });

  // =========================================================================
  // Add offering modal
  // =========================================================================
  const addOfferingModal = document.getElementById("add-offering-modal");
  const addOfferingForm = document.getElementById("add-offering-form");
  const addOfferingSubmit = document.getElementById("add-offering-submit");
  const addOfferingError = document.getElementById("add-offering-error");
  const teacherSearchInput = document.getElementById("ao-teacher-search");
  const teacherResults = document.getElementById("ao-teacher-results");
  const teacherIdInput = document.getElementById("ao-teacher-id");

  document.getElementById("open-add-offering").addEventListener("click", function () {
    addOfferingError.hidden = true;
    addOfferingForm.reset();
    teacherIdInput.value = "";
    document.getElementById("ao-term").value = termLabelInput.value.trim();
    addOfferingModal.showModal();
  });
  document.getElementById("close-add-offering-modal").addEventListener("click", function () {
    addOfferingModal.close();
  });

  teacherSearchInput.addEventListener("input", function () {
    teacherIdInput.value = "";
    clearTimeout(teacherSearchDebounce);
    const q = teacherSearchInput.value.trim();
    if (q.length < 2) {
      teacherResults.hidden = true;
      return;
    }
    teacherSearchDebounce = setTimeout(async function () {
      const result = await VA.fetchJson("/api/v1/employees?" + new URLSearchParams({ q: q, role: "TEACHER", page_size: "10" }));
      if (!result.ok) return;
      const items = (result.body && result.body.items) || [];

      teacherResults.innerHTML = "";
      if (items.length === 0) {
        teacherResults.innerHTML = '<div class="search-result-item search-result-empty">No teachers match that search.</div>';
        teacherResults.hidden = false;
        return;
      }
      items.forEach(function (emp) {
        const item = document.createElement("div");
        item.className = "search-result-item";
        item.textContent = emp.first_name + " " + emp.last_name + " (" + emp.employee_code + ")";
        item.addEventListener("click", function () {
          teacherIdInput.value = emp.id;
          teacherSearchInput.value = item.textContent;
          teacherResults.hidden = true;
        });
        teacherResults.appendChild(item);
      });
      teacherResults.hidden = false;
    }, 300);
  });

  addOfferingForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    addOfferingSubmit.disabled = true;
    addOfferingError.hidden = true;

    const result = await VA.fetchJson("/api/v1/academics/offerings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        course_id: aoCourseSelect.value,
        term_label: document.getElementById("ao-term").value.trim(),
        assigned_teacher_id: teacherIdInput.value || null,
        term_start_date: document.getElementById("ao-term-start").value || null,
        term_end_date: document.getElementById("ao-term-end").value || null,
        section_label: document.getElementById("ao-section").value.trim() || "A",
        capacity: document.getElementById("ao-capacity").value ? parseInt(document.getElementById("ao-capacity").value, 10) : null,
      }),
    });

    addOfferingSubmit.disabled = false;

    if (!result.ok) {
      addOfferingError.textContent = extractErrorMessage(result.body, "Could not create offering.");
      addOfferingError.hidden = false;
      return;
    }

    addOfferingModal.close();
    await loadTermsDropdown(true);
    termLabelInput.value = result.body.term_label;
    loadOfferings();
  });


  // =========================================================================
  // Open a semester, batch enroll, term readiness
  // =========================================================================
  const generateModal = document.getElementById("generate-offerings-modal");
  const goProgram = document.getElementById("go-program");
  const beProgram = document.getElementById("be-program");

  async function fillProgramSelects() {
    const result = await VA.fetchJson("/api/v1/programs");
    if (!result.ok) return;
    const items = Array.isArray(result.body) ? result.body : result.body.items || [];
    [goProgram, beProgram].forEach(function (select) {
      const first = select.options[0];
      select.innerHTML = "";
      select.appendChild(first);
      items.forEach(function (p) {
        const opt = document.createElement("option");
        opt.value = p.id;
        opt.textContent = p.code + " \u2014 " + p.name;
        select.appendChild(opt);
      });
    });
  }

  document.getElementById("open-generate-offerings").addEventListener("click", async function () {
    document.getElementById("go-error").hidden = true;
    document.getElementById("go-success").hidden = true;
    await fillProgramSelects();
    if (termLabelInput.value) document.getElementById("go-term").value = termLabelInput.value;
    generateModal.showModal();
  });
  document.getElementById("close-generate-offerings-modal").addEventListener("click", function () { generateModal.close(); });

  document.getElementById("generate-offerings-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    const err = document.getElementById("go-error");
    const ok = document.getElementById("go-success");
    err.hidden = true;
    ok.hidden = true;
    const btn = document.getElementById("go-submit");
    btn.disabled = true;
    const result = await VA.fetchJson("/api/v1/academics/offerings/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        program_id: goProgram.value,
        semester_number: parseInt(document.getElementById("go-semester").value, 10),
        term_label: document.getElementById("go-term").value.trim(),
        section_label: document.getElementById("go-section").value.trim() || "A",
        term_start_date: document.getElementById("go-start").value,
        term_end_date: document.getElementById("go-end").value,
        capacity: document.getElementById("go-capacity").value ? parseInt(document.getElementById("go-capacity").value, 10) : null,
        open_for_enrollment: document.getElementById("go-open").checked,
      }),
    });
    btn.disabled = false;
    if (!result.ok) {
      err.textContent = extractErrorMessage(result.body, "Could not open the semester.");
      err.hidden = false;
      return;
    }
    const r = result.body;
    ok.textContent = "Created " + r.created.length + " offering(s); skipped " + r.skipped.length + " (already existed or inactive).";
    ok.hidden = false;
    await loadTermsDropdown(true);
    termLabelInput.value = r.term_label;
    loadOfferings();
  });

  // ---- Batch enroll ---------------------------------------------------------
  const batchModal = document.getElementById("batch-enroll-modal");
  let batchOfferingId = null;

  async function openBatchEnroll(o) {
    batchOfferingId = o.id;
    document.getElementById("be-title").textContent = "Enroll students in " + o.course_code + " (section " + o.section_label + ")";
    document.getElementById("be-error").hidden = true;
    document.getElementById("be-result").hidden = true;
    document.getElementById("be-section").value = o.section_label || "";
    await fillProgramSelects();
    batchModal.showModal();
  }
  document.getElementById("close-batch-enroll-modal").addEventListener("click", function () { batchModal.close(); loadOfferings(); });

  document.getElementById("be-submit").addEventListener("click", async function () {
    const err = document.getElementById("be-error");
    const box = document.getElementById("be-result");
    err.hidden = true;
    box.hidden = true;
    const semester = document.getElementById("be-semester").value;
    const body = {
      program_id: beProgram.value || null,
      semester_number: semester ? parseInt(semester, 10) : null,
      section_label: document.getElementById("be-section").value.trim() || null,
    };
    const btn = document.getElementById("be-submit");
    btn.disabled = true;
    const result = await VA.fetchJson("/api/v1/academics/offerings/" + batchOfferingId + "/enroll-batch", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    btn.disabled = false;
    if (!result.ok) {
      err.textContent = extractErrorMessage(result.body, "Could not enroll students.");
      err.hidden = false;
      return;
    }
    const r = result.body;
    let html = "<strong>Enrolled " + r.enrolled + " student(s)</strong>; already enrolled: " + r.already_enrolled + "; could not enroll: " + r.failed_count + ".";
    if (r.failed.length) {
      html += "<ul>" + r.failed.map(function (f) {
        return "<li>" + VA.escapeHtml((f.roll_number || "?") + " " + f.name + " \u2014 " + f.reason) + "</li>";
      }).join("") + "</ul>";
    }
    box.innerHTML = html;
    box.hidden = false;
  });

  // ---- Whole-term timetable tools ---------------------------------------------
  function renderBulk(box, r, verb) {
    let html = "<strong>" + verb + " " + r.scheduled.length + " course(s)</strong>";
    if (r.skipped.length) html += "; " + r.skipped.length + " already had a timetable";
    html += r.failed.length ? "; <strong>" + r.failed.length + " need attention:</strong>" : ".";
    if (r.failed.length) {
      html += "<ul>" + r.failed.map(function (f) {
        return "<li>" + VA.escapeHtml(f.course_code + " (section " + f.section + "): " + f.reason) + "</li>";
      }).join("") + "</ul>";
    }
    box.innerHTML = html;
    box.hidden = false;
  }

  const autoModal = document.getElementById("auto-schedule-modal");
  document.getElementById("open-auto-schedule").addEventListener("click", function () {
    if (!termLabelInput.value) { VA.showError("Select a term first."); return; }
    VA.clearError();
    document.getElementById("as-term").value = termLabelInput.value;
    document.getElementById("as-error").hidden = true;
    document.getElementById("as-result").hidden = true;
    autoModal.showModal();
  });
  document.getElementById("close-auto-schedule-modal").addEventListener("click", function () { autoModal.close(); loadOfferings(); });
  document.getElementById("as-run").addEventListener("click", async function () {
    const err = document.getElementById("as-error");
    const box = document.getElementById("as-result");
    err.hidden = true;
    box.hidden = true;
    const rooms = document.getElementById("as-rooms").value.split(",").map(function (x) { return x.trim(); }).filter(Boolean);
    if (!rooms.length) { err.textContent = "Enter at least one room."; err.hidden = false; return; }
    const perWeek = document.getElementById("as-per-week").value;
    const btn = document.getElementById("as-run");
    btn.disabled = true;
    const result = await VA.fetchJson("/api/v1/academics/timetable/auto-schedule-term", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ term_label: termLabelInput.value, rooms: rooms, sessions_per_week: perWeek ? parseInt(perWeek, 10) : null }),
    });
    btn.disabled = false;
    if (!result.ok) { err.textContent = extractErrorMessage(result.body, "Could not create the timetable."); err.hidden = false; return; }
    renderBulk(box, result.body, "Timetabled");
  });

  const copyModal = document.getElementById("copy-timetable-modal");
  document.getElementById("open-copy-timetable").addEventListener("click", function () {
    if (!termLabelInput.value) { VA.showError("Select the term to copy INTO first."); return; }
    VA.clearError();
    const source = document.getElementById("ct-source");
    source.innerHTML = "";
    Array.prototype.forEach.call(termLabelInput.options, function (o) {
      if (o.value && o.value !== termLabelInput.value) {
        const opt = document.createElement("option");
        opt.value = o.value;
        opt.textContent = o.value;
        source.appendChild(opt);
      }
    });
    if (!source.options.length) { VA.showError("There is no other term to copy from yet."); return; }
    document.getElementById("ct-target").value = termLabelInput.value;
    document.getElementById("ct-error").hidden = true;
    document.getElementById("ct-result").hidden = true;
    copyModal.showModal();
  });
  document.getElementById("close-copy-timetable-modal").addEventListener("click", function () { copyModal.close(); loadOfferings(); });
  document.getElementById("ct-run").addEventListener("click", async function () {
    const err = document.getElementById("ct-error");
    const box = document.getElementById("ct-result");
    err.hidden = true;
    box.hidden = true;
    const btn = document.getElementById("ct-run");
    btn.disabled = true;
    const result = await VA.fetchJson("/api/v1/academics/timetable/copy-from-term", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_term: document.getElementById("ct-source").value, target_term: termLabelInput.value }),
    });
    btn.disabled = false;
    if (!result.ok) { err.textContent = extractErrorMessage(result.body, "Could not copy the timetable."); err.hidden = false; return; }
    renderBulk(box, result.body, "Copied");
  });

  // ---- Term readiness -----------------------------------------------------------
  const ISSUE_TEXT = {
    NO_TEACHER: "no teacher", NO_TIMETABLE: "no timetable", NO_TERM_DATES: "no term dates",
    TOPICS_MISSING: "class topics not entered yet (next 14 days)",
    OVER_CAPACITY: "over capacity", NO_STUDENTS: "no students yet",
  };
  document.getElementById("check-readiness-btn").addEventListener("click", async function () {
    const term = termLabelInput.value.trim();
    const panel = document.getElementById("readiness-panel");
    if (!term) { VA.showError("Select a term first."); return; }
    const result = await VA.fetchJson("/api/v1/academics/allocation-overview?" + new URLSearchParams({ term_label: term }));
    if (!result.ok) { VA.showError(extractErrorMessage(result.body, "Could not load readiness.")); return; }
    VA.clearError();
    const r = result.body;
    let html = "<strong>" + VA.escapeHtml(r.term_label) + ": " + r.summary.sections + " section(s), " +
      r.summary.sections_with_problems + " with problems, " + r.summary.uncovered_courses + " curriculum course(s) with no offering.</strong>";
    const problems = r.sections.filter(function (x) { return x.issues.length; });
    if (problems.length) {
      html += "<p>Needs attention:</p><ul>" + problems.map(function (x) {
        return "<li>" + VA.escapeHtml(x.course_code + " (section " + x.section_label + "): " +
          x.issues.map(function (i) { return ISSUE_TEXT[i] || i; }).join(", ")) + "</li>";
      }).join("") + "</ul>";
    }
    if (r.courses_without_offering.length) {
      html += "<p>Courses students are waiting for, but not opened this term:</p><ul>" + r.courses_without_offering.map(function (g) {
        return "<li>" + VA.escapeHtml(g.program_code + " sem " + g.semester + " \u2014 " + g.course_code + " " + g.course_title + " (" + g.students_waiting + " students)") + "</li>";
      }).join("") + "</ul>";
    }
    if (r.teacher_loads.length) {
      html += "<p>Teacher load:</p><ul>" + r.teacher_loads.map(function (t) {
        return "<li>" + VA.escapeHtml(t.teacher_name + ": " + t.sections + " section(s), " + t.weekly_hours + " hrs/week") + "</li>";
      }).join("") + "</ul>";
    }
    panel.innerHTML = html;
    panel.hidden = false;
  });

  // =========================================================================
  // Teacher Performance
  // =========================================================================
  const performanceTbody = document.getElementById("performance-tbody");
  const performanceEmpty = document.getElementById("performance-empty");

  async function loadTeacherPerformance() {
    const result = await VA.fetchJson("/api/v1/academics/teacher-performance");
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load teacher performance."));
      return;
    }
    VA.clearError();

    performanceTbody.innerHTML = "";
    if (result.body.length === 0) {
      performanceEmpty.hidden = false;
      return;
    }
    performanceEmpty.hidden = true;

    result.body.forEach(function (t) {
      const tr = document.createElement("tr");
      const passRateText = t.pass_rate === null ? "\u2014" : Math.round(t.pass_rate * 100) + "%";
      tr.innerHTML =
        "<td>" + VA.escapeHtml(t.teacher_name) + " (" + VA.escapeHtml(t.employee_code) + ")</td>" +
        '<td class="num">' + t.offerings_count + "</td>" +
        '<td class="num">' + t.total_enrollments + "</td>" +
        '<td class="num">' + t.passed_count + "</td>" +
        '<td class="num">' + t.failed_count + "</td>" +
        '<td class="num">' + passRateText + "</td>" +
        '<td class="num">' + t.complaints_against + "</td>";
      performanceTbody.appendChild(tr);
    });
  }

  document.getElementById("refresh-performance-btn").addEventListener("click", loadTeacherPerformance);

  document.addEventListener("vistadmin:ready", function () {
    loadCourses();
    loadPrograms();
    loadTermsDropdown(false);
    loadTeacherPerformance();
  });
})();