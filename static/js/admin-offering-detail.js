(function () {
  "use strict";

  const VA = window.VistAdmin;
  const OFFERING_ID = window.OFFERING_ID;

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  function resourceTypeLabel(t) {
    return t.charAt(0) + t.slice(1).toLowerCase();
  }

  function dayLabel(dayOfWeek) {
    const s = String(dayOfWeek || "");
    return s.charAt(0) + s.slice(1).toLowerCase();
  }

  function statusPillHtml(status) {
    const label = String(status || "").replace(/_/g, " ");
    return '<span class="status-pill ' + String(status || "").toLowerCase() + '">' + VA.escapeHtml(label) + "</span>";
  }

  // ---------------------------------------------------------------------
  // Overview + Term Dates + Syllabus
  // ---------------------------------------------------------------------
  const offeringSummary = document.getElementById("offering-summary");
  const offeringPageTitle = document.getElementById("offering-page-title");
  const termStartInput = document.getElementById("term-start-input");
  const termEndInput = document.getElementById("term-end-input");
  const termDatesSaveBtn = document.getElementById("term-dates-save-btn");
  const termDatesError = document.getElementById("term-dates-error");
  const termDatesSuccess = document.getElementById("term-dates-success");
  const syllabusInput = document.getElementById("syllabus-input");
  const syllabusUploadBtn = document.getElementById("syllabus-upload-btn");
  const syllabusDownloadLink = document.getElementById("syllabus-download-link");
  const syllabusRemoveBtn = document.getElementById("syllabus-remove-btn");
  const syllabusError = document.getElementById("syllabus-error");

  let offeringLocked = false;
  let lastSessions = [];

  function renderLockBar() {
    const bar = document.getElementById("lock-status");
    if (!bar) return;
    const locked = lastSessions.filter(function (x) { return x.content_locked; }).length;
    let html = offeringLocked
      ? "\uD83D\uDD12 <strong>Whole course locked</strong> \u2014 the teacher can view the syllabus and every class, but cannot change or delete anything."
      : "\uD83D\uDD13 Course content is open to the teacher.";
    if (locked) html += " <strong>" + locked + "</strong> class(es) locked individually.";
    bar.innerHTML = html;
  }

  async function changeLock(body, confirmText) {
    if (confirmText && !window.confirm(confirmText)) return false;
    const err = document.getElementById("lock-error");
    err.hidden = true;
    const r = await VA.fetchJson("/api/v1/academics/offerings/" + OFFERING_ID + "/content-lock", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!r.ok) { err.textContent = extractErrorMessage(r.body, "Could not change the lock."); err.hidden = false; return false; }
    return true;
  }

  function updateSyllabusDisplay(offering) {
    syllabusError.hidden = true;
    if (offering.has_syllabus) {
      syllabusDownloadLink.href = "/api/v1/academics/offerings/" + offering.id + "/syllabus";
      syllabusDownloadLink.setAttribute("download", offering.syllabus_original_name || "syllabus.pdf");
      syllabusDownloadLink.hidden = false;
      syllabusRemoveBtn.hidden = false;
    } else {
      syllabusDownloadLink.hidden = true;
      syllabusRemoveBtn.hidden = true;
    }
  }

  async function loadOffering() {
    const result = await VA.fetchJson("/api/v1/academics/offerings/" + OFFERING_ID);
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load this offering."));
      return;
    }
    const offering = result.body;
    offeringPageTitle.textContent = offering.course_code + " \u2014 " + offering.course_title + " (" + offering.term_label + ", Section " + (offering.section_label || "A") + ")";
    document.title = offering.course_code + " \u2014 VIST LMS Admin";

    offeringSummary.innerHTML =
      "<dt>Credit Hours</dt><dd>" + offering.credit_hours + "</dd>" +
      "<dt>Term</dt><dd>" + VA.escapeHtml(offering.term_label) + "</dd>" +
      '<dt>Teacher</dt><dd id="offering-teacher-dd">' + VA.escapeHtml(offering.teacher_name || "\u2014 unassigned") + " </dd>" +
      "<dt>Open for Enrollment</dt><dd>" + (offering.is_open_for_enrollment ? "Yes" : "No") + "</dd>" +
      "<dt>Enrolled</dt><dd>" + offering.enrolled_count + "</dd>";

    const programsResult = await VA.fetchJson("/api/v1/academics/courses/" + offering.course_id + "/programs");
    if (programsResult.ok && programsResult.body.length > 0) {
      const classText = programsResult.body.map(function (link) {
        return link.program_name + " \u2014 Semester " + link.semester_number;
      }).join("; ");
      offeringSummary.innerHTML += "<dt>Taught In (Class)</dt><dd>" + VA.escapeHtml(classText) + "</dd>";
    } else {
      offeringSummary.innerHTML += "<dt>Taught In (Class)</dt><dd>Not linked to any program's curriculum yet \u2014 add it in the Curriculum tab.</dd>";
    }

    if (window.TeacherPicker) {
      const teacherBtn = document.createElement("button");
      teacherBtn.type = "button";
      teacherBtn.className = "role-btn small-btn" + (offering.teacher_name ? " secondary-btn" : "");
      teacherBtn.textContent = offering.teacher_name ? "Change teacher" : "Assign teacher";
      teacherBtn.addEventListener("click", function () { window.TeacherPicker.open(offering, function () { loadOffering(); }); });
      document.getElementById("offering-teacher-dd").appendChild(teacherBtn);
    }

    offeringLocked = !!offering.content_locked;
    renderLockBar();
    updateSyllabusDisplay(offering);
    termStartInput.value = offering.term_start_date || "";
    termEndInput.value = offering.term_end_date || "";

    loadPeriods();
    loadTimetableSlots();
    loadClassSessions();
  }

  termDatesSaveBtn.addEventListener("click", async function () {
    termDatesError.hidden = true;
    termDatesSuccess.hidden = true;
    termDatesSaveBtn.disabled = true;

    const result = await VA.fetchJson("/api/v1/academics/offerings/" + OFFERING_ID, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        term_start_date: termStartInput.value || null,
        term_end_date: termEndInput.value || null,
      }),
    });

    termDatesSaveBtn.disabled = false;

    if (!result.ok) {
      termDatesError.textContent = extractErrorMessage(result.body, "Could not save term dates.");
      termDatesError.hidden = false;
      return;
    }
    termDatesSuccess.hidden = false;
  });

  syllabusUploadBtn.addEventListener("click", async function () {
    syllabusError.hidden = true;
    if (!syllabusInput.files.length) {
      syllabusError.textContent = "Choose a PDF file first.";
      syllabusError.hidden = false;
      return;
    }
    syllabusUploadBtn.disabled = true;
    const formData = new FormData();
    formData.append("file", syllabusInput.files[0]);
    const result = await VA.fetchJson("/api/v1/academics/offerings/" + OFFERING_ID + "/syllabus", {
      method: "POST",
      body: formData,
    });
    syllabusUploadBtn.disabled = false;

    if (!result.ok) {
      syllabusError.textContent = extractErrorMessage(result.body, "Could not upload syllabus.");
      syllabusError.hidden = false;
      return;
    }
    syllabusInput.value = "";
    updateSyllabusDisplay(result.body);
  });

  syllabusRemoveBtn.addEventListener("click", async function () {
    syllabusRemoveBtn.disabled = true;
    const result = await VA.fetchJson("/api/v1/academics/offerings/" + OFFERING_ID + "/syllabus", { method: "DELETE" });
    syllabusRemoveBtn.disabled = false;
    if (!result.ok) {
      syllabusError.textContent = extractErrorMessage(result.body, "Could not remove syllabus.");
      syllabusError.hidden = false;
      return;
    }
    updateSyllabusDisplay(result.body);
  });

  // ---------------------------------------------------------------------
  // Periods (read-only picker here; managed on the Timetable page)
  // ---------------------------------------------------------------------
  const tsPeriodSelect = document.getElementById("ts-period");

  async function loadPeriods() {
    const result = await VA.fetchJson("/api/v1/academics/periods");
    if (!result.ok) return;
    tsPeriodSelect.innerHTML = "";
    if (result.body.length === 0) {
      tsPeriodSelect.innerHTML = '<option value="">No periods defined yet \u2014 add one on the Timetable page</option>';
      return;
    }
    result.body.forEach(function (period) {
      const opt = document.createElement("option");
      opt.value = period.id;
      opt.textContent = "Period " + period.period_number + " (" + period.start_time.slice(0, 5) + "\u2013" + period.end_time.slice(0, 5) + ")";
      tsPeriodSelect.appendChild(opt);
    });
  }

  // ---------------------------------------------------------------------
  // Timetable slots + Auto-Assign
  // ---------------------------------------------------------------------
  const timetableSlotsTbody = document.getElementById("timetable-slots-tbody");
  const timetableSlotsEmpty = document.getElementById("timetable-slots-empty");
  const addSlotForm = document.getElementById("add-slot-form");
  const addSlotSubmit = document.getElementById("add-slot-submit");
  const addSlotError = document.getElementById("add-slot-error");
  const addSlotSuccess = document.getElementById("add-slot-success");
  const autoAssignForm = document.getElementById("auto-assign-form");
  const autoAssignSubmit = document.getElementById("auto-assign-submit");
  const autoAssignError = document.getElementById("auto-assign-error");
  const autoAssignSuccess = document.getElementById("auto-assign-success");

  async function loadTimetableSlots() {
    const result = await VA.fetchJson("/api/v1/academics/offerings/" + OFFERING_ID + "/timetable-slots");
    timetableSlotsTbody.innerHTML = "";
    if (!result.ok) return;

    timetableSlotsEmpty.hidden = result.body.length > 0;
    result.body.forEach(function (slot) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(dayLabel(slot.day_of_week)) + "</td>" +
        "<td>" + slot.start_time.slice(0, 5) + "\u2013" + slot.end_time.slice(0, 5) + "</td>" +
        "<td>" + VA.escapeHtml(slot.room) + "</td>" +
        '<td><button type="button" class="danger-btn small-btn slot-delete" data-id="' + slot.id + '">Delete</button></td>';
      tr.querySelector(".slot-delete").addEventListener("click", async function () {
        if (!confirm("Delete this recurring slot? Its not-yet-occurred sessions will be cancelled; past sessions are kept.")) return;
        const delResult = await VA.fetchJson("/api/v1/academics/timetable-slots/" + slot.id, { method: "DELETE" });
        if (!delResult.ok) {
          addSlotError.textContent = extractErrorMessage(delResult.body, "Could not delete this slot.");
          addSlotError.hidden = false;
          return;
        }
        loadTimetableSlots();
        loadClassSessions();
      });
      timetableSlotsTbody.appendChild(tr);
    });
  }

  addSlotForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    addSlotSubmit.disabled = true;
    addSlotError.hidden = true;
    addSlotSuccess.hidden = true;

    const result = await VA.fetchJson("/api/v1/academics/offerings/" + OFFERING_ID + "/timetable-slots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        day_of_week: document.getElementById("ts-day").value,
        room: document.getElementById("ts-room").value.trim(),
        period_id: tsPeriodSelect.value,
      }),
    });

    addSlotSubmit.disabled = false;

    if (!result.ok) {
      addSlotError.textContent = extractErrorMessage(result.body, "Could not add this slot.");
      addSlotError.hidden = false;
      return;
    }

    addSlotForm.reset();
    addSlotSuccess.textContent = "Slot added \u2014 " + result.body.sessions_generated + " class session(s) generated.";
    addSlotSuccess.hidden = false;
    loadTimetableSlots();
    loadClassSessions();
  });

  autoAssignForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    autoAssignSubmit.disabled = true;
    autoAssignError.hidden = true;
    autoAssignSuccess.hidden = true;

    const result = await VA.fetchJson("/api/v1/academics/offerings/" + OFFERING_ID + "/timetable-slots/auto-assign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sessions_per_week: parseInt(document.getElementById("aa-sessions-per-week").value, 10),
        room: document.getElementById("aa-room").value.trim(),
      }),
    });

    autoAssignSubmit.disabled = false;

    if (!result.ok) {
      autoAssignError.textContent = extractErrorMessage(result.body, "Could not auto-assign a timetable.");
      autoAssignError.hidden = false;
      return;
    }

    autoAssignForm.reset();
    const days = result.body.slots.map(function (s) { return dayLabel(s.day_of_week); }).join(", ");
    autoAssignSuccess.textContent = "Assigned " + result.body.slots.length + " slot(s) on " + days + " \u2014 " + result.body.sessions_generated + " class session(s) generated.";
    autoAssignSuccess.hidden = false;
    loadTimetableSlots();
    loadClassSessions();
  });

  // ---------------------------------------------------------------------
  // Class Sessions — edit/cancel, topic-completed, and the unified
  // Add Resource action (Upload File OR Paste Link)
  // ---------------------------------------------------------------------
  const classSessionsList = document.getElementById("class-sessions-list");
  const classSessionsEmpty = document.getElementById("class-sessions-empty");

  async function loadClassSessions() {
    const result = await VA.fetchJson("/api/v1/academics/offerings/" + OFFERING_ID + "/class-sessions");
    classSessionsList.innerHTML = "";
    if (!result.ok) return;

    classSessionsEmpty.hidden = result.body.length > 0;
    const sessions = result.body.slice().sort(function (a, b) { return a.session_date.localeCompare(b.session_date); });
    lastSessions = sessions;
    renderLockBar();

    const held = sessions.filter(function (x) { return x.status !== "CANCELLED"; });
    const covered = held.filter(function (x) { return x.topic_completed; }).length;
    const cov = document.getElementById("coverage-summary");
    if (held.length) {
      const pct = Math.round((covered / held.length) * 100);
      cov.innerHTML = "<strong>" + covered + " of " + held.length + " topics covered</strong>" +
        '<div class="meter"><span class="meter-fill" style="width:' + Math.max(pct, covered ? 2 : 0) + '%"></span></div>';
    } else {
      cov.innerHTML = "";
    }

    sessions.forEach(function (session) {
      const block = document.createElement("div");
      block.className = "panel" + (session.topic_completed && session.status !== "CANCELLED" ? " plan-done" : "") + (session.content_locked ? " plan-locked" : "");
      const cancelled = session.status === "CANCELLED";

      const resourcesHtml = session.resources.map(function (r) {
        const href = r.external_url ? r.external_url : "/api/v1/academics/class-session-resources/" + r.id;
        return '<span style="display:inline-block;margin:4px 8px 4px 0">' +
          '<a class="role-btn small-btn" href="' + VA.escapeHtml(href) + '" target="_blank" rel="noopener">' +
          resourceTypeLabel(r.resource_type) + ": " + VA.escapeHtml(r.original_name) + (r.external_url ? " \u2197" : "") + '</a> ' +
          '<button type="button" class="danger-btn small-btn session-resource-delete" data-id="' + r.id + '">Remove</button>' +
          "</span>";
      }).join("");

      block.innerHTML =
        '<div style="display:flex;justify-content:space-between;align-items:center">' +
          '<strong>' + VA.formatDateLong(session.session_date) + " \u00b7 " + VA.formatTimeShort(session.start_time) + "\u2013" + VA.formatTimeShort(session.end_time) +
          " \u00b7 " + VA.escapeHtml(session.room) +
          (cancelled ? ' <span class="status-pill cancelled">CANCELLED</span>' : "") +
          (session.topic_completed && !cancelled ? ' <span class="badge-covered">\u2713 Covered</span>' : "") +
          (session.content_locked ? ' <span class="badge-locked">\uD83D\uDD12 Locked</span>' : "") +
          (session.title ? " \u2014 " + VA.escapeHtml(session.title) : "") + "</strong>" +
          (cancelled ? "" :
            '<span>' +
              '<button type="button" class="role-btn small-btn session-edit" data-id="' + session.id + '">Edit</button> ' +
              '<button type="button" class="role-btn small-btn secondary-btn session-lock">' + (session.content_locked ? "Unlock" : "Lock") + '</button> ' +
              '<button type="button" class="danger-btn small-btn session-cancel" data-id="' + session.id + '">Cancel</button>' +
            '</span>') +
        "</div>" +
        (cancelled && session.cancelled_reason ? '<p class="panel-hint">Reason: ' + VA.escapeHtml(session.cancelled_reason) + "</p>" : "") +
        (cancelled ? "" :
          '<div class="session-edit-form" hidden>' +
            '<div class="form-grid two-col">' +
              '<div class="form-field"><label>Date</label><input type="date" class="text-input session-edit-date" value="' + session.session_date + '" /></div>' +
              '<div class="form-field"><label>Room</label><input type="text" class="text-input session-edit-room" maxlength="50" value="' + VA.escapeHtml(session.room) + '" /></div>' +
              '<div class="form-field"><label>Start Time</label><input type="time" class="text-input session-edit-start" value="' + session.start_time.slice(0, 5) + '" /></div>' +
              '<div class="form-field"><label>End Time</label><input type="time" class="text-input session-edit-end" value="' + session.end_time.slice(0, 5) + '" /></div>' +
              '<div class="form-field"><label>Topic / Title</label><input type="text" class="text-input session-edit-title" maxlength="200" value="' + VA.escapeHtml(session.title || "") + '" /></div>' +
              '<div class="form-field two-span"><label>Description</label><input type="text" class="text-input session-edit-description" maxlength="1000" value="' + VA.escapeHtml(session.description || "") + '" /></div>' +
              '<div class="form-field checkbox-field two-span"><label><input type="checkbox" class="session-edit-topic-completed" ' + (session.topic_completed ? "checked" : "") + ' /> Topic fully covered</label></div>' +
            '</div>' +
            '<button type="button" class="role-btn small-btn session-edit-save">Save</button> ' +
            '<button type="button" class="back-link small-btn session-edit-cancel-btn" type="button">Cancel</button>' +
          '</div>');

      if (!cancelled) {
        block.innerHTML +=
          '<div class="session-resources">' + (resourcesHtml || '<p class="panel-hint">No files or links yet.</p>') + "</div>" +
          '<div class="resource-add-area" style="margin-top:8px">' +
            '<select class="select-input resource-mode">' +
              '<option value="file">Upload File</option>' +
              '<option value="link">Paste Link</option>' +
            '</select>' +
            '<select class="select-input session-resource-type"><option value="NOTES">Notes</option><option value="SLIDES">Slides</option><option value="OTHER">Other</option></select>' +
            '<span class="resource-file-inputs"><input type="file" class="session-resource-input" accept="application/pdf" /></span>' +
            '<span class="resource-link-inputs" hidden>' +
              '<input type="text" class="text-input session-resource-title" placeholder="Title (e.g. Lecture Slides)" style="width:180px;display:inline-block" /> ' +
              '<input type="text" class="text-input session-resource-url" placeholder="https://..." style="width:220px;display:inline-block" />' +
            '</span>' +
            '<button type="button" class="role-btn small-btn session-resource-add">Add Resource</button>' +
          "</div>";
      }

      const sessionError = document.createElement("p");
      sessionError.className = "dash-alert error";
      sessionError.hidden = true;
      block.appendChild(sessionError);

      if (!cancelled) {
        const editForm = block.querySelector(".session-edit-form");
        block.querySelector(".session-edit").addEventListener("click", function () {
          editForm.hidden = !editForm.hidden;
        });
        block.querySelector(".session-edit-cancel-btn").addEventListener("click", function () {
          editForm.hidden = true;
        });
        block.querySelector(".session-edit-save").addEventListener("click", async function () {
          sessionError.hidden = true;
          const result = await VA.fetchJson("/api/v1/academics/class-sessions/" + session.id, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              session_date: block.querySelector(".session-edit-date").value,
              room: block.querySelector(".session-edit-room").value.trim(),
              start_time: block.querySelector(".session-edit-start").value,
              end_time: block.querySelector(".session-edit-end").value,
              title: block.querySelector(".session-edit-title").value.trim() || null,
              description: block.querySelector(".session-edit-description").value.trim() || null,
            }),
          });
          if (!result.ok) {
            sessionError.textContent = extractErrorMessage(result.body, "Could not save changes.");
            sessionError.hidden = false;
            return;
          }
          const topicResult = await VA.fetchJson("/api/v1/academics/class-sessions/" + session.id + "/topic-completed", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ completed: block.querySelector(".session-edit-topic-completed").checked }),
          });
          if (!topicResult.ok) {
            sessionError.textContent = "Session details saved, but could not save topic-completed status.";
            sessionError.hidden = false;
            return;
          }
          loadClassSessions();
        });

        block.querySelector(".session-lock").addEventListener("click", async function () {
          const r = await VA.fetchJson("/api/v1/academics/class-sessions/" + session.id + "/content-lock", {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ locked: !session.content_locked }),
          });
          if (!r.ok) { VA.showError(extractErrorMessage(r.body, "Could not change the lock.")); return; }
          VA.clearError();
          loadClassSessions();
        });

        block.querySelector(".session-cancel").addEventListener("click", async function () {
          const reason = prompt("Reason for cancelling this session (e.g. a holiday):");
          if (!reason) return;
          const result = await VA.fetchJson("/api/v1/academics/class-sessions/" + session.id + "/cancel", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ reason: reason }),
          });
          if (!result.ok) {
            sessionError.textContent = extractErrorMessage(result.body, "Could not cancel this session.");
            sessionError.hidden = false;
            return;
          }
          loadClassSessions();
        });

        block.querySelectorAll(".session-resource-delete").forEach(function (btn) {
          btn.addEventListener("click", async function () {
            const result = await VA.fetchJson("/api/v1/academics/class-session-resources/" + btn.getAttribute("data-id"), { method: "DELETE" });
            if (!result.ok && result.status !== 204) {
              sessionError.textContent = extractErrorMessage(result.body, "Could not remove resource.");
              sessionError.hidden = false;
              return;
            }
            loadClassSessions();
          });
        });

        const resourceModeSelect = block.querySelector(".resource-mode");
        const fileInputsSpan = block.querySelector(".resource-file-inputs");
        const linkInputsSpan = block.querySelector(".resource-link-inputs");
        resourceModeSelect.addEventListener("change", function () {
          const isLink = resourceModeSelect.value === "link";
          fileInputsSpan.hidden = isLink;
          linkInputsSpan.hidden = !isLink;
        });

        block.querySelector(".session-resource-add").addEventListener("click", async function () {
          sessionError.hidden = true;
          const typeSelect = block.querySelector(".session-resource-type");
          let result;

          if (resourceModeSelect.value === "link") {
            const title = block.querySelector(".session-resource-title").value.trim();
            const url = block.querySelector(".session-resource-url").value.trim();
            if (!title || !url) {
              sessionError.textContent = "Give the link a title and a URL.";
              sessionError.hidden = false;
              return;
            }
            result = await VA.fetchJson("/api/v1/academics/class-sessions/" + session.id + "/resources/link", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ resource_type: typeSelect.value, external_url: url, title: title }),
            });
          } else {
            const fileInput = block.querySelector(".session-resource-input");
            if (!fileInput.files.length) {
              sessionError.textContent = "Choose a PDF file first.";
              sessionError.hidden = false;
              return;
            }
            const formData = new FormData();
            formData.append("file", fileInput.files[0]);
            const params = new URLSearchParams({ resource_type: typeSelect.value });
            result = await VA.fetchJson("/api/v1/academics/class-sessions/" + session.id + "/resources?" + params.toString(), {
              method: "POST",
              body: formData,
            });
          }

          if (!result.ok) {
            sessionError.textContent = extractErrorMessage(result.body, "Could not add this resource.");
            sessionError.hidden = false;
            return;
          }
          loadClassSessions();
        });
      }

      classSessionsList.appendChild(block);
    });
  }

  // ---------------------------------------------------------------------
  // Content lock bar
  // ---------------------------------------------------------------------
  document.getElementById("lock-past-btn").addEventListener("click", async function () {
    if (await changeLock({ locked: true, scope: "PAST_CLASSES" }, "Lock every class that has already happened? The teacher will no longer be able to change or delete their content.")) loadClassSessions();
  });
  document.getElementById("lock-course-btn").addEventListener("click", async function () {
    if (await changeLock({ locked: true, scope: "COURSE" }, "Lock the WHOLE course? The teacher will not be able to change the syllabus or any class, including upcoming ones.")) { await loadOffering(); loadClassSessions(); }
  });
  document.getElementById("unlock-all-btn").addEventListener("click", async function () {
    if (!window.confirm("Unlock the course and every class so the teacher can edit again?")) return;
    const a = await changeLock({ locked: false, scope: "COURSE" });
    const b = a && await changeLock({ locked: false, scope: "ALL_CLASSES" });
    if (b) { await loadOffering(); loadClassSessions(); }
  });

  loadOffering();
})();