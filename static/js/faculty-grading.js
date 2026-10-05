(function () {
  "use strict";

  const VA = window.VistAdmin;

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  function statusPillHtml(status) {
    return '<span class="status-pill ' + (status || "").toLowerCase() + '">' + VA.escapeHtml(status) + "</span>";
  }

  const offeringSelect = document.getElementById("grading-offering-select");
  const noOfferings = document.getElementById("grading-no-offerings");
  const componentsPanel = document.getElementById("grading-components-panel");
  const componentsTbody = document.getElementById("components-tbody");
  const componentsEmpty = document.getElementById("components-empty");
  const addComponentForm = document.getElementById("add-component-form");
  const addComponentSubmit = document.getElementById("add-component-submit");
  const compTypeSelect = document.getElementById("comp-type-select");
  const noMoreTypesEmpty = document.getElementById("no-more-types-empty");
  const componentError = document.getElementById("component-error");
  const marksPanel = document.getElementById("grading-marks-panel");
  const marksTheadRow = document.getElementById("marks-thead-row");
  const marksTbody = document.getElementById("marks-tbody");
  const marksEmpty = document.getElementById("marks-empty");
  const marksError = document.getElementById("marks-error");

  let activeOfferingId = null;
  let activeComponents = []; // [{id, assessment_type_id, assessment_type_name, category, max_marks}]
  let catalogCache = []; // the full institute-wide AssessmentType catalog
  let activeRoster = []; // full EnrollmentOut list

  async function loadMyOfferings() {
    const result = await VA.fetchJson("/api/v1/academics/offerings/me");
    if (!result.ok) return;

    offeringSelect.innerHTML = '<option value="">— Choose a course —</option>';
    if (result.body.length === 0) {
      noOfferings.hidden = false;
      return;
    }
    noOfferings.hidden = true;

    result.body.forEach(function (o) {
      const opt = document.createElement("option");
      opt.value = o.id;
      opt.textContent = o.course_code + " — " + o.course_title + " (" + o.term_label + ")";
      offeringSelect.appendChild(opt);
    });
  }

  offeringSelect.addEventListener("change", async function () {
    activeOfferingId = offeringSelect.value;
    if (!activeOfferingId) {
      componentsPanel.hidden = true;
      marksPanel.hidden = true;
      return;
    }
    componentsPanel.hidden = false;
    marksPanel.hidden = false;
    await loadCatalog();
    await loadComponents();
    await loadSheet();
    await loadRosterAndMarks();
    applyStructureLock();
  });

  // ---------------------------------------------------------------------
  // Catalog (institute-wide, admin-managed) + this course's picks from it
  // ---------------------------------------------------------------------
  async function loadCatalog() {
    const result = await VA.fetchJson("/api/v1/academics/assessment-types");
    catalogCache = result.ok ? result.body : [];
  }

  function renderCatalogDropdown() {
    const addedIds = new Set(activeComponents.map(function (c) { return c.assessment_type_id; }));
    const available = catalogCache.filter(function (t) { return !addedIds.has(t.id); });

    compTypeSelect.innerHTML = "";
    if (available.length === 0) {
      noMoreTypesEmpty.hidden = false;
      addComponentForm.hidden = true;
      return;
    }
    noMoreTypesEmpty.hidden = true;
    addComponentForm.hidden = false;
    available.forEach(function (t) {
      const opt = document.createElement("option");
      opt.value = t.id;
      opt.textContent = t.name + (t.category ? " (" + t.category + ")" : "") + " \u2014 /" + t.max_marks;
      compTypeSelect.appendChild(opt);
    });
  }

  async function loadComponents() {
    const result = await VA.fetchJson("/api/v1/academics/offerings/" + activeOfferingId + "/assessments");
    componentsTbody.innerHTML = "";
    activeComponents = result.ok ? result.body : [];

    if (activeComponents.length === 0) {
      componentsEmpty.hidden = false;
    } else {
      componentsEmpty.hidden = true;
      activeComponents.forEach(function (c) {
        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" + VA.escapeHtml(c.assessment_type_name) + "</td>" +
          "<td>" + VA.escapeHtml(c.category || "\u2014") + "</td>" +
          '<td class="num">' + c.max_marks + "</td>" +
          '<td><button type="button" class="danger-btn small-btn component-delete" data-id="' + c.id + '">Remove</button></td>';
        componentsTbody.appendChild(tr);
      });
      componentsTbody.querySelectorAll(".component-delete").forEach(function (btn) {
        btn.addEventListener("click", async function () {
          if (!confirm("Remove this from the course? Only possible if no student has a mark recorded against it yet.")) return;
          const result = await VA.fetchJson("/api/v1/academics/offering-assessments/" + btn.getAttribute("data-id"), {
            method: "DELETE",
          });
          if (!result.ok && result.status !== 204) {
            componentError.textContent = extractErrorMessage(result.body, "Could not remove this.");
            componentError.hidden = false;
            return;
          }
          componentError.hidden = true;
          await loadComponents();
          renderCatalogDropdown();
          await loadRosterAndMarks();
        });
      });
    }

    renderCatalogDropdown();
  }

  addComponentForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!activeOfferingId || !compTypeSelect.value) return;
    addComponentSubmit.disabled = true;
    componentError.hidden = true;

    const result = await VA.fetchJson("/api/v1/academics/offerings/" + activeOfferingId + "/assessments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ assessment_type_id: compTypeSelect.value }),
    });

    addComponentSubmit.disabled = false;

    if (!result.ok) {
      componentError.textContent = extractErrorMessage(result.body, "Could not add this to the course.");
      componentError.hidden = false;
      return;
    }

    await loadComponents();
    await loadSheet();
    await loadRosterAndMarks();
    applyStructureLock();
  });

  // ---------------------------------------------------------------------
  // Approval status: marks are a DRAFT until submitted, approved and published
  // ---------------------------------------------------------------------
  let sheet = null;
  const sheetBox = document.getElementById("sheet-status");
  const API_RESULTS = "/api/v1/results/offerings/";
  const when = function (iso) { return iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : ""; };

  async function loadSheet() {
    const r = await VA.fetchJson(API_RESULTS + activeOfferingId + "/sheet");
    sheet = r.ok ? r.body : null;
    renderSheet();
  }

  function renderSheet() {
    if (!sheet) { sheetBox.hidden = true; return; }
    const c = sheet.completeness, st = sheet.status;
    let tone = "draft", title = "", text = "", action = "";
    if (st === "DRAFT" || st === "RETURNED") {
      tone = st === "RETURNED" ? "returned" : "draft";
      title = st === "RETURNED" ? "Sent back for correction" : "Draft";
      text = st === "RETURNED"
        ? '<b>Reason:</b> ' + VA.escapeHtml(sheet.return_reason || "") + " Correct the marks, then submit again."
        : "Students cannot see these marks yet. " + c.complete_students + " of " + c.students + " students have every mark entered.";
      if (!c.assessments) text += " Add at least one result type above first.";
      else if (c.missing_students) text += " Still missing: " + VA.escapeHtml(c.missing_names.join(", ")) + (c.missing_students > c.missing_names.length ? " and " + (c.missing_students - c.missing_names.length) + " more" : "") + ".";
      action = '<button type="button" class="role-btn" id="sheet-submit"' + (sheet.can_submit ? "" : " disabled") + '>Submit for approval</button>';
    } else if (st === "SUBMITTED") {
      tone = "submitted"; title = "Submitted for approval"; text = "Sent on " + when(sheet.submitted_at) + ". The marks are locked while they are checked. If something needs fixing they will be sent back to you with a reason.";
    } else if (st === "APPROVED") {
      tone = "approved"; title = "Approved"; text = "Checked and approved on " + when(sheet.approved_at) + ". The administration will publish them to students.";
    } else {
      tone = "published"; title = "Published"; text = "Students can see their results (published " + when(sheet.published_at) + "). Only the administration can correct a published mark, and every correction is recorded." + (sheet.correction_count ? " " + sheet.correction_count + " correction" + (sheet.correction_count > 1 ? "s" : "") + " so far." : "");
    }
    sheetBox.className = "sheet-status " + tone;
    sheetBox.innerHTML = '<div><strong>' + title + "</strong><span>" + text + "</span></div>" + action;
    sheetBox.hidden = false;
    const btn = document.getElementById("sheet-submit");
    if (btn) btn.addEventListener("click", submitSheet);
  }

  async function submitSheet() {
    if (!window.confirm("Submit these marks for approval? They will be locked until they are approved or sent back to you.")) return;
    const r = await VA.fetchJson(API_RESULTS + activeOfferingId + "/submit", { method: "POST" });
    if (!r.ok) { marksError.textContent = extractErrorMessage(r.body, "Could not submit the marks."); marksError.hidden = false; return; }
    marksError.hidden = true;
    VA.toast("Submitted for approval. The marks are now locked.");
    sheet = r.body;
    renderSheet();
    await loadRosterAndMarks();
    applyStructureLock();
  }

  // Once the marks are submitted the structure (which result types apply) cannot change either.
  function applyStructureLock() {
    const locked = !!sheet && !sheet.can_edit;
    const form = document.getElementById("add-component-form");
    if (form) form.hidden = locked;
    const none = document.getElementById("no-more-types-empty");
    if (none && locked) none.hidden = true;
    document.querySelectorAll("#components-tbody button").forEach(function (b) { b.hidden = locked; });
  }

  // ---------------------------------------------------------------------
  // Marks — one row per roster entry, one editable column per assessment
  // ---------------------------------------------------------------------
  async function loadRosterAndMarks() {
    const rosterResult = await VA.fetchJson("/api/v1/academics/offerings/" + activeOfferingId + "/roster");
    activeRoster = rosterResult.ok ? rosterResult.body : [];

    marksTheadRow.innerHTML = "<th>Student</th><th>Roll No.</th>";
    activeComponents.forEach(function (c) {
      const th = document.createElement("th");
      th.textContent = c.assessment_type_name + " (/" + c.max_marks + ")";
      marksTheadRow.appendChild(th);
    });
    const outcomeTh = document.createElement("th");
    outcomeTh.textContent = "Outcome";
    marksTheadRow.appendChild(outcomeTh);

    marksTbody.innerHTML = "";
    marksError.hidden = true;

    if (activeComponents.length === 0) {
      marksEmpty.hidden = false;
      return;
    }
    marksEmpty.hidden = true;

    for (const enrollment of activeRoster) {
      if (enrollment.status === "WITHDRAWN") continue; // marks can't be recorded for a withdrawn enrollment

      const marksResult = await VA.fetchJson("/api/v1/academics/enrollments/" + enrollment.id + "/marks");
      const marksByAssessment = {};
      if (marksResult.ok) {
        marksResult.body.forEach(function (m) {
          marksByAssessment[m.offering_assessment_id] = m.obtained_marks;
        });
      }

      const tr = document.createElement("tr");
      tr.dataset.enrollmentId = enrollment.id;
      let cellsHtml = "<td>" + VA.escapeHtml(enrollment.student_name) + "</td>" +
        "<td>" + VA.escapeHtml(enrollment.roll_number || "\u2014") + "</td>";
      activeComponents.forEach(function (c) {
        const existing = marksByAssessment[c.id];
        cellsHtml += '<td><input type="number" class="text-input mark-input" style="width:80px" ' +
          'data-assessment-id="' + c.id + '" data-enrollment-id="' + enrollment.id + '" ' +
          'min="0" max="' + c.max_marks + '" step="0.01" value="' + (existing !== undefined ? existing : "") + '"' + (sheet && !sheet.can_edit ? " disabled" : "") + ' /></td>';
      });
      const outcomeCell = enrollment.outcome ? statusPillHtml(enrollment.outcome) : "\u2014";
      cellsHtml += '<td class="marks-outcome-cell">' + outcomeCell + "</td>";
      tr.innerHTML = cellsHtml;
      marksTbody.appendChild(tr);
    }

    marksTbody.querySelectorAll(".mark-input").forEach(function (input) {
      input.addEventListener("blur", async function () {
        if (input.value === "") return;
        const assessmentId = input.getAttribute("data-assessment-id");
        const enrollmentId = input.getAttribute("data-enrollment-id");
        const result = await VA.fetchJson(
          "/api/v1/academics/enrollments/" + enrollmentId + "/marks/" + assessmentId,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ obtained_marks: parseFloat(input.value) }),
          }
        );
        if (!result.ok) {
          marksError.textContent = extractErrorMessage(result.body, "Could not save this mark.");
          marksError.hidden = false;
          return;
        }
        marksError.hidden = true;

        // Refresh just this row's outcome cell — the outcome may have
        // just been auto-computed if this was the last assessment needed.
        const rosterResult = await VA.fetchJson("/api/v1/academics/offerings/" + activeOfferingId + "/roster");
        if (rosterResult.ok) {
          const updated = rosterResult.body.find(function (e) { return e.id === enrollmentId; });
          if (updated) {
            const row = marksTbody.querySelector('tr[data-enrollment-id="' + enrollmentId + '"]');
            if (row) {
              const cell = row.querySelector(".marks-outcome-cell");
              cell.innerHTML = updated.outcome ? statusPillHtml(updated.outcome) : "\u2014";
            }
          }
        }
      });
    });
  }

  document.addEventListener("vistadmin:ready", function () {
    loadMyOfferings();
  });
})();