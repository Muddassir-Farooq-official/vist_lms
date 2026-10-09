(function () {
  "use strict";

  const VA = window.VistAdmin;

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  // ---------------------------------------------------------------------
  // Needs Attention
  // ---------------------------------------------------------------------
  const needsAttentionTbody = document.getElementById("needs-attention-tbody");
  const needsAttentionEmpty = document.getElementById("needs-attention-empty");

  async function loadNeedsAttention() {
    const result = await VA.fetchJson("/api/v1/attendance/needs-attention");
    needsAttentionTbody.innerHTML = "";
    if (!result.ok) return;

    needsAttentionEmpty.hidden = result.body.length > 0;
    const total = parseInt((result.headers && result.headers.get("X-Total-Count")) || "0", 10);
    const capNote = document.getElementById("needs-attention-cap");
    if (capNote) {
      capNote.hidden = !(total > result.body.length);
      capNote.textContent = "Showing the oldest " + result.body.length + " of " + total + " classes with no attendance. Deal with these first (mark them, or cancel a holiday from the Holidays tab) and the rest will appear.";
    }
    result.body.forEach(function (entry) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.formatDateLong(entry.session_date) + "</td>" +
        "<td>" + VA.formatTimeShort(entry.start_time) + "</td>" +
        "<td>" + VA.escapeHtml(entry.course_code) + " \u2014 " + VA.escapeHtml(entry.course_title) + "</td>" +
        "<td>" + VA.escapeHtml(entry.teacher_name || "\u2014 unassigned") + "</td>" +
        "<td>" + VA.escapeHtml(entry.room) + "</td>" +
        '<td><button type="button" class="role-btn small-btn attend-now-btn" data-offering="' + entry.course_offering_id + '" data-session="' + entry.id + '">Mark Now</button></td>';
      tr.querySelector(".attend-now-btn").addEventListener("click", async function () {
        await selectOfferingById(entry.course_offering_id);
        document.getElementById("session-select").value = entry.id;
        document.getElementById("session-select").dispatchEvent(new Event("change"));
        document.getElementById("session-picker-area").scrollIntoView({ behavior: "smooth" });
      });
      needsAttentionTbody.appendChild(tr);
    });
  }

  // ---------------------------------------------------------------------
  // Term / Offering / Session pickers
  // ---------------------------------------------------------------------
  const termLabelInput = document.getElementById("term-label-input");
  const offeringSelect = document.getElementById("offering-select");
  const sessionPickerArea = document.getElementById("session-picker-area");
  const sessionSelect = document.getElementById("session-select");
  const sessionDetails = document.getElementById("session-details");
  const windowNotice = document.getElementById("window-notice");
  const rosterTable = document.getElementById("roster-table");
  const rosterTbody = document.getElementById("roster-tbody");
  const lateNoteField = document.getElementById("late-note-field");
  const lateNoteInput = document.getElementById("late-note-input");
  const saveActions = document.getElementById("save-actions");
  const saveBtn = document.getElementById("save-attendance-btn");
  const attendanceError = document.getElementById("attendance-error");
  const attendanceSuccess = document.getElementById("attendance-success");

  let offeringsCache = [];
  let sessionsCache = [];
  let enrolledStudents = [];
  const AU = window.AttendanceUI;
  const rosterToolsEl = document.getElementById("roster-tools");
  const csvLink = document.getElementById("register-csv-link");

  async function loadTermsDropdown() {
    const result = await VA.fetchJson("/api/v1/academics/terms");
    if (!result.ok) return;
    termLabelInput.innerHTML = '<option value="">Select a term...</option>';
    result.body.forEach(function (term) {
      const opt = document.createElement("option");
      opt.value = term;
      opt.textContent = term;
      termLabelInput.appendChild(opt);
    });
  }

  document.getElementById("refresh-terms-btn").addEventListener("click", loadTermsDropdown);

  async function loadOfferingsForTerm() {
    if (!termLabelInput.value) return;
    const result = await VA.fetchJson("/api/v1/academics/offerings?term_label=" + encodeURIComponent(termLabelInput.value));
    offeringSelect.innerHTML = '<option value="">— Choose a course offering —</option>';
    if (!result.ok) return;
    offeringsCache = result.body;
    offeringsCache.forEach(function (o) {
      const opt = document.createElement("option");
      opt.value = o.id;
      opt.textContent = o.course_code + " \u2014 " + o.course_title + (o.teacher_name ? " (" + o.teacher_name + ")" : " (unassigned)");
      offeringSelect.appendChild(opt);
    });
    offeringSelect.hidden = false;
    sessionPickerArea.hidden = true;
  }

  document.getElementById("load-offerings-btn").addEventListener("click", loadOfferingsForTerm);

  async function selectOfferingById(offeringId) {
    // Used by the "Mark Now" shortcut from Needs Attention — the term
    // dropdown may not have this offering's term loaded yet, so fetch
    // it directly rather than requiring the admin to find it manually.
    if (!offeringsCache.some(function (o) { return o.id === offeringId; })) {
      const detailResult = await VA.fetchJson("/api/v1/academics/offerings/" + offeringId);
      if (detailResult.ok) {
        offeringsCache.push(detailResult.body);
        const opt = document.createElement("option");
        opt.value = detailResult.body.id;
        opt.textContent = detailResult.body.course_code + " \u2014 " + detailResult.body.course_title;
        offeringSelect.appendChild(opt);
        offeringSelect.hidden = false;
      }
    }
    offeringSelect.value = offeringId;
    await loadSessionsForOffering(offeringId);
  }

  offeringSelect.addEventListener("change", async function () {
    if (!offeringSelect.value) {
      sessionPickerArea.hidden = true;
      return;
    }
    await loadSessionsForOffering(offeringSelect.value);
  });

  async function loadSessionsForOffering(offeringId) {
    csvLink.href = "/api/v1/attendance/offerings/" + offeringId + "/report.csv";
    csvLink.hidden = false;
    loadLockPreview(offeringId);
    const [sessionsResult, rosterResult] = await Promise.all([
      VA.fetchJson("/api/v1/academics/offerings/" + offeringId + "/class-sessions"),
      VA.fetchJson("/api/v1/academics/offerings/" + offeringId + "/roster"),
    ]);
    sessionsCache = sessionsResult.ok ? sessionsResult.body.filter(function (s) { return s.status !== "CANCELLED"; }) : [];
    sessionsCache.sort(function (a, b) { return a.session_date.localeCompare(b.session_date); });
    enrolledStudents = rosterResult.ok ? rosterResult.body.filter(function (e) { return e.status === "ENROLLED"; }) : [];

    sessionSelect.innerHTML = '<option value="">— Choose a session —</option>';
    sessionsCache.forEach(function (s) {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = VA.formatDateLong(s.session_date) + " \u00b7 " + VA.formatTimeShort(s.start_time) + (s.title ? " \u2014 " + s.title : "");
      sessionSelect.appendChild(opt);
    });
    sessionPickerArea.hidden = false;
    resetSessionArea();
  }

  function resetSessionArea() {
    sessionDetails.hidden = true;
    windowNotice.hidden = true;
    rosterTable.hidden = true;
    rosterToolsEl.hidden = true;
    lateNoteField.hidden = true;
    lateNoteInput.value = "";
    saveActions.hidden = true;
    attendanceError.hidden = true;
    attendanceSuccess.hidden = true;
  }

  let serverClock = null;
  function isBeforeStart(session) {
    return serverClock.now() < serverClock.start(session);
  }

  sessionSelect.addEventListener("change", async function () {
    resetSessionArea();
    if (!sessionSelect.value) return;
    const session = sessionsCache.find(function (s) { return s.id === sessionSelect.value; });
    if (!session) return;
    serverClock = await AU.loadClock();

    sessionDetails.innerHTML = "Room: " + VA.escapeHtml(session.room) + " &nbsp;|&nbsp; Time: " +
      VA.formatTimeShort(session.start_time) + " \u2013 " + VA.formatTimeShort(session.end_time);
    sessionDetails.hidden = false;

    if (isBeforeStart(session)) {
      windowNotice.textContent = "This session hasn't started yet \u2014 attendance can only be marked from its start time onward.";
      windowNotice.hidden = false;
      return; // roster/save stay hidden — nothing to mark yet, even for admin
    }

    const existingResult = await VA.fetchJson("/api/v1/attendance/sessions/" + session.id);
    const existingByStudent = {};
    if (existingResult.ok) {
      existingResult.body.forEach(function (r) { existingByStudent[r.student_id] = r.status; });
    }

    if (enrolledStudents.length === 0) {
      // After results are published every enrollment is completed, so there is nobody left to mark.
      windowNotice.textContent = "No student is currently enrolled in this course, so there is nothing to mark. " +
        "(Once a course's results are published its attendance can no longer be changed.)";
      windowNotice.hidden = false;
      return;
    }
    AU.renderRoster(rosterTbody, enrolledStudents, Object.keys(existingByStudent).length ? existingByStudent : null);
    rosterTable.hidden = enrolledStudents.length === 0;
    rosterToolsEl.hidden = enrolledStudents.length === 0;
    saveActions.hidden = enrolledStudents.length === 0;
    // Always offer the note field up front for admin — it's only
    // enforced server-side when actually outside the window, but
    // showing it proactively saves a failed-then-retry round trip.
    lateNoteField.hidden = false;
  });

  saveBtn.addEventListener("click", async function () {
    if (!sessionSelect.value) return;
    attendanceError.hidden = true;
    attendanceSuccess.hidden = true;
    saveBtn.disabled = true;

    const register = AU.collect(rosterTbody);
    register.late_note = lateNoteInput.value.trim() || null;

    const result = await VA.fetchJson("/api/v1/attendance/sessions/" + sessionSelect.value + "/mark", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(register),
    });

    saveBtn.disabled = false;

    if (!result.ok) {
      attendanceError.textContent = extractErrorMessage(result.body, "Could not save attendance.");
      attendanceError.hidden = false;
      return;
    }

    attendanceSuccess.textContent = "Attendance saved.";
    attendanceSuccess.hidden = false;
    loadNeedsAttention();
    if (offeringSelect.value) loadLockPreview(offeringSelect.value);
  });

  document.getElementById("mark-all-present-btn").addEventListener("click", function () { AU.markAll(rosterTbody, "PRESENT"); });
  document.getElementById("mark-all-absent-btn").addEventListener("click", function () { AU.markAll(rosterTbody, "ABSENT"); });

  // ---------------------------------------------------------------------
  // Correction requests from teachers
  // ---------------------------------------------------------------------
  const correctionsTbody = document.getElementById("corrections-tbody");
  const correctionsEmpty = document.getElementById("corrections-empty");

  async function loadCorrections() {
    const result = await VA.fetchJson("/api/v1/attendance/correction-requests?status=PENDING");
    correctionsTbody.innerHTML = "";
    if (!result.ok) return;
    correctionsEmpty.hidden = result.body.length > 0;
    result.body.forEach(function (r) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.formatDateLong(r.session_date) + "</td>" +
        "<td>" + VA.escapeHtml(r.course_code) + " \u2014 " + VA.escapeHtml(r.course_title) + "</td>" +
        "<td>" + VA.escapeHtml(r.requested_by_name || "\u2014") + "</td>" +
        "<td>" + VA.escapeHtml(r.reason) + "</td>" +
        "<td>" + r.present_count + " marked present</td>" +
        '<td class="row-actions"></td>';
      const cell = tr.querySelector(".row-actions");
      [["Approve", true, "role-btn small-btn"], ["Reject", false, "danger-btn small-btn"]].forEach(function (def) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = def[2];
        btn.textContent = def[0];
        btn.addEventListener("click", async function () {
          const note = def[1] ? null : window.prompt("Reason for rejecting (the teacher will see this):", "");
          if (!def[1] && note === null) return;
          btn.disabled = true;
          const res = await VA.fetchJson("/api/v1/attendance/correction-requests/" + r.id + "/decision", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ approve: def[1], note: note || null }),
          });
          if (!res.ok) {
            VA.showError(extractErrorMessage(res.body, "Could not record the decision."));
            btn.disabled = false;
            return;
          }
          VA.clearError();
          loadCorrections();
          loadNeedsAttention();
        });
        cell.appendChild(btn);
      });
      correctionsTbody.appendChild(tr);
    });
  }

  // ---------------------------------------------------------------------
  // Lock attendance (with preview)
  // ---------------------------------------------------------------------
  const lockPanel = document.getElementById("lock-panel");
  const lockPreviewBox = document.getElementById("lock-preview");
  const lockBtn = document.getElementById("lock-attendance-btn");
  const lockError = document.getElementById("lock-error");
  const lockSuccess = document.getElementById("lock-success");

  async function loadLockPreview(offeringId) {
    lockError.hidden = true;
    lockSuccess.hidden = true;
    const result = await VA.fetchJson("/api/v1/attendance/offerings/" + offeringId + "/lock-preview");
    if (!result.ok) { lockPanel.hidden = true; return; }
    const p = result.body;
    lockPanel.hidden = false;
    if (p.already_locked) {
      lockPreviewBox.innerHTML = "<strong>This course's attendance is already locked.</strong>";
      lockBtn.disabled = true;
      return;
    }
    lockPreviewBox.innerHTML =
      "<ul><li>Enrolled students: " + p.enrolled_count + "</li>" +
      "<li>Classes held but not recorded: <strong>" + p.unmarked_overdue_sessions + "</strong>" +
      (p.unmarked_overdue_sessions ? " (resolve in Needs Attention first)" : "") + "</li>" +
      "<li>Classes still to come: " + p.future_sessions_remaining + (p.future_sessions_remaining ? " (they cannot be marked after locking)" : "") + "</li>" +
      "<li>Below " + p.eligibility_threshold + "% (cannot pass): " + p.below_eligibility + "</li>" +
      "<li>Would be fined Rs. " + p.fine_amount + " (\u2264 " + p.fine_threshold + "%; registration and result card held until paid): " + p.would_fine + "</li>" +
      "<li>Would be dropped from the course (\u2264 " + p.drop_threshold + "%): " + p.would_drop + "</li></ul>" +
      (p.early_reason ? "<p><strong>Not yet:</strong> " + VA.escapeHtml(p.early_reason) + ". Lock at the end of term (an administrator can lock early with a reason).</p>" : "");
    lockBtn.disabled = p.unmarked_overdue_sessions > 0;   // early locks are allowed for admins with a reason (asked on click)
    lockBtn.setAttribute("data-summary", p.would_fine + " fined, " + p.would_drop + " dropped");
  }

  lockBtn.addEventListener("click", async function () {
    if (!offeringSelect.value) return;
    if (!window.confirm("Lock attendance for this course? " + lockBtn.getAttribute("data-summary") + ". This cannot be undone.")) return;
    lockBtn.disabled = true;
    lockError.hidden = true;
    let result = await VA.fetchJson("/api/v1/attendance/offerings/" + offeringSelect.value + "/lock", { method: "POST" });
    const det = result.body && result.body.error && result.body.error.details;
    if (!result.ok && det && det.override_allowed) {
      // Locking before the term is over: an administrator may still do it, with a reason kept on record.
      const why = window.prompt("Too early to lock: " + det.early + ".\n\nLock early anyway? Type the reason (kept in the audit log), or Cancel.");
      if (why && why.trim()) {
        result = await VA.fetchJson("/api/v1/attendance/offerings/" + offeringSelect.value + "/lock", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ early_reason: why.trim() }),
        });
      }
    }
    if (!result.ok) {
      lockError.textContent = extractErrorMessage(result.body, "Could not lock attendance.");
      lockError.hidden = false;
      loadLockPreview(offeringSelect.value);
      return;
    }
    lockSuccess.textContent = "Locked. " + result.body.fined_count + " student(s) fined, " + result.body.dropped_count + " dropped.";
    lockSuccess.hidden = false;
    loadLockPreview(offeringSelect.value);
    lockSuccess.hidden = false;
  });

  // ---------------------------------------------------------------------
  // Cancel a whole day
  // ---------------------------------------------------------------------
  document.getElementById("holiday-btn").addEventListener("click", async function () {
    const err = document.getElementById("holiday-error");
    const ok = document.getElementById("holiday-success");
    err.hidden = true;
    ok.hidden = true;
    const date = document.getElementById("holiday-date").value;
    const reason = document.getElementById("holiday-reason").value.trim();
    if (!date || !reason) { err.textContent = "Choose the date and give a reason."; err.hidden = false; return; }
    if (!window.confirm("Cancel EVERY scheduled class on " + date + "?")) return;
    const result = await VA.fetchJson("/api/v1/academics/class-sessions/cancel-day", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_date: date, reason: reason }),
    });
    if (!result.ok) { err.textContent = extractErrorMessage(result.body, "Could not cancel the day."); err.hidden = false; return; }
    ok.textContent = "Cancelled " + result.body.cancelled + " class(es); " + result.body.skipped_attendance_already_taken + " skipped because attendance was already taken.";
    ok.hidden = false;
    loadNeedsAttention();
  });

  document.addEventListener("vistadmin:ready", function () {
    loadTermsDropdown();
    loadNeedsAttention();
    loadCorrections();
  });
})();