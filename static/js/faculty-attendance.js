(function () {
  "use strict";

  const VA = window.VistAdmin;

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  const termSelect = document.getElementById("term-label-input");
  const offeringSelect = document.getElementById("offering-select");
  const sessionPickerArea = document.getElementById("session-picker-area");
  const sessionSelect = document.getElementById("session-select");
  const sessionDetails = document.getElementById("session-details");
  const windowNotice = document.getElementById("window-notice");
  const rosterTable = document.getElementById("roster-table");
  const rosterTbody = document.getElementById("roster-tbody");
  const topicCompletedField = document.getElementById("topic-completed-field");
  const topicCompletedCheckbox = document.getElementById("topic-completed-checkbox");
  const saveActions = document.getElementById("save-actions");
  const saveBtn = document.getElementById("save-attendance-btn");
  const attendanceError = document.getElementById("attendance-error");
  const attendanceSuccess = document.getElementById("attendance-success");

  let offeringsCache = [];
  let sessionsCache = [];
  let enrolledStudents = [];
  let mode = "mark"; // "mark" = save directly, "request" = window closed -> ask the admin
  const AU = window.AttendanceUI;
  const rosterTools = document.getElementById("roster-tools");
  const reasonField = document.getElementById("correction-reason-field");
  const reasonInput = document.getElementById("correction-reason-input");
  const csvLink = document.getElementById("register-csv-link");

  async function loadMyOfferings() {
    const result = await VA.fetchJson("/api/v1/academics/offerings/me");
    if (!result.ok) return;
    offeringsCache = result.body;

    const terms = Array.from(new Set(offeringsCache.map(function (o) { return o.term_label; })));
    termSelect.innerHTML = '<option value="">All terms</option>';
    terms.forEach(function (t) {
      const opt = document.createElement("option");
      opt.value = t;
      opt.textContent = t;
      termSelect.appendChild(opt);
    });

    renderOfferingOptions();
  }

  function renderOfferingOptions() {
    const termFilter = termSelect.value;
    const filtered = offeringsCache.filter(function (o) { return !termFilter || o.term_label === termFilter; });
    offeringSelect.innerHTML = '<option value="">— Choose a course —</option>';
    filtered.forEach(function (o) {
      const opt = document.createElement("option");
      opt.value = o.id;
      opt.textContent = o.course_code + " \u2014 " + o.course_title + " (" + o.term_label + ")";
      offeringSelect.appendChild(opt);
    });
  }

  termSelect.addEventListener("change", renderOfferingOptions);

  offeringSelect.addEventListener("change", async function () {
    if (!offeringSelect.value) {
      sessionPickerArea.hidden = true;
      return;
    }
    csvLink.href = "/api/v1/attendance/offerings/" + offeringSelect.value + "/report.csv";
    csvLink.hidden = false;
    const [sessionsResult, rosterResult] = await Promise.all([
      VA.fetchJson("/api/v1/academics/offerings/" + offeringSelect.value + "/class-sessions"),
      VA.fetchJson("/api/v1/academics/offerings/" + offeringSelect.value + "/roster"),
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
  });

  function resetSessionArea() {
    sessionDetails.hidden = true;
    windowNotice.hidden = true;
    rosterTable.hidden = true;
    rosterTools.hidden = true;
    reasonField.hidden = true;
    reasonInput.value = "";
    topicCompletedField.hidden = true;
    saveActions.hidden = true;
    attendanceError.hidden = true;
    attendanceSuccess.hidden = true;
    mode = "mark";
    saveBtn.textContent = "Save Attendance";
  }

  sessionSelect.addEventListener("change", async function () {
    resetSessionArea();
    if (!sessionSelect.value) return;
    const session = sessionsCache.find(function (s) { return s.id === sessionSelect.value; });
    if (!session) return;
    const clock = await AU.loadClock();
    const windowMinutes = clock.config.marking_window_minutes;

    sessionDetails.innerHTML = "Room: " + VA.escapeHtml(session.room) + " &nbsp;|&nbsp; Time: " +
      VA.formatTimeShort(session.start_time) + " \u2013 " + VA.formatTimeShort(session.end_time);
    sessionDetails.hidden = false;

    topicCompletedField.hidden = false;
    topicCompletedCheckbox.checked = !!session.topic_completed;

    const start = clock.start(session);
    if (clock.now() < start) {
      windowNotice.textContent = "This session hasn't started yet \u2014 attendance can only be marked from its start time onward.";
      windowNotice.hidden = false;
      topicCompletedField.hidden = true;
      return;
    }
    const windowClosed = clock.now() > new Date(start.getTime() + windowMinutes * 60000);

    const existingResult = await VA.fetchJson("/api/v1/attendance/sessions/" + session.id);
    const existingByStudent = {};
    if (existingResult.ok) {
      existingResult.body.forEach(function (r) { existingByStudent[r.student_id] = r.status; });
    }
    if (enrolledStudents.length === 0) {
      windowNotice.textContent = "No student is currently enrolled in this course, so there is nothing to mark. " +
        "(Once a course's results are published its attendance can no longer be changed.)";
      windowNotice.hidden = false;
      return;
    }
    AU.renderRoster(rosterTbody, enrolledStudents, Object.keys(existingByStudent).length ? existingByStudent : null);
    rosterTable.hidden = enrolledStudents.length === 0;
    rosterTools.hidden = enrolledStudents.length === 0;
    saveActions.hidden = false;

    if (windowClosed) {
      mode = "request";
      windowNotice.textContent = "The " + windowMinutes + "-minute window to mark this session has passed. You can still send the register to the admin for approval.";
      windowNotice.hidden = false;
      reasonField.hidden = false;
      topicCompletedField.hidden = true;
      saveBtn.textContent = "Send to admin for approval";
    }
  });

  document.getElementById("mark-all-present-btn").addEventListener("click", function () { AU.markAll(rosterTbody, "PRESENT"); });
  document.getElementById("mark-all-absent-btn").addEventListener("click", function () { AU.markAll(rosterTbody, "ABSENT"); });

  saveBtn.addEventListener("click", async function () {
    if (!sessionSelect.value) return;
    attendanceError.hidden = true;
    attendanceSuccess.hidden = true;
    saveBtn.disabled = true;

    const register = AU.collect(rosterTbody);

    if (mode === "request") {
      const reqResult = await VA.fetchJson("/api/v1/attendance/sessions/" + sessionSelect.value + "/correction-request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({ reason: reasonInput.value.trim() }, register)),
      });
      saveBtn.disabled = false;
      if (!reqResult.ok) {
        attendanceError.textContent = extractErrorMessage(reqResult.body, "Could not send the request.");
        attendanceError.hidden = false;
        return;
      }
      attendanceSuccess.textContent = "Request sent to the admin. You will be notified when it is decided.";
      attendanceSuccess.hidden = false;
      loadMyRequests();
      return;
    }

    const markResult = await VA.fetchJson("/api/v1/attendance/sessions/" + sessionSelect.value + "/mark", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(register),
    });

    if (!markResult.ok) {
      saveBtn.disabled = false;
      attendanceError.textContent = extractErrorMessage(markResult.body, "Could not save attendance.");
      attendanceError.hidden = false;
      return;
    }

    // Topic-completed is a separate signal from attendance itself (see
    // ClassSession.topic_completed's own docstring) -- save it too,
    // in the same action, since that's when the teacher is looking at it.
    const topicResult = await VA.fetchJson("/api/v1/academics/class-sessions/" + sessionSelect.value + "/topic-completed", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ completed: topicCompletedCheckbox.checked }),
    });

    saveBtn.disabled = false;

    if (!topicResult.ok) {
      attendanceError.textContent = "Attendance saved, but could not save topic-completed status.";
      attendanceError.hidden = false;
      return;
    }

    attendanceSuccess.textContent = "Attendance saved.";
    attendanceSuccess.hidden = false;
  });

  // ---- My correction requests -----------------------------------------------
  const REQUEST_LABEL = { PENDING: "Waiting for the office", APPROVED: "Approved \u2014 attendance applied", REJECTED: "Not approved" };

  async function loadMyRequests() {
    const result = await VA.fetchJson("/api/v1/attendance/correction-requests");
    const list = document.getElementById("my-requests-list");
    const empty = document.getElementById("my-requests-empty");
    list.innerHTML = "";
    if (!result.ok) return;
    empty.hidden = result.body.length > 0;
    result.body.forEach(function (r) {
      const status = String(r.status || "").toLowerCase();
      const card = document.createElement("article");
      card.className = "complaint-card " + (status === "approved" ? "resolved" : status === "rejected" ? "dismissed" : "submitted");
      card.innerHTML =
        '<div class="cc-head"><div><strong>' + VA.escapeHtml(r.course_code) + " \u2014 " + VA.escapeHtml(r.course_title || "") + '</strong><span class="cc-role">' +
        VA.escapeHtml(VA.formatDateLong(r.session_date)) + "</span></div>" +
        '<div class="cc-badges"><span class="status-pill ' + status + '">' + VA.escapeHtml(REQUEST_LABEL[r.status] || r.status) + "</span></div></div>" +
        '<p class="cc-meta">' + VA.escapeHtml(r.present_count != null ? r.present_count + " students marked present" : "") + "</p>" +
        '<p class="cc-desc">' + VA.escapeHtml(r.reason) + "</p>" +
        (r.decision_note ? '<div class="cc-note"><strong>Note from the office</strong><span>' + VA.escapeHtml(r.decision_note) + "</span></div>" : "");
      list.appendChild(card);
    });
  }

  // "Take attendance" on the Home page links here with ?offering=...&session=... : open that class's register directly.
  async function applyDeepLink() {
    const params = new URLSearchParams(window.location.search);
    const wantedOffering = params.get("offering"), wantedSession = params.get("session");
    if (!wantedOffering) return;
    const hasOption = function (select, value) { return Array.prototype.some.call(select.options, function (o) { return o.value === value; }); };
    const waitFor = function (test) {
      return new Promise(function (resolve) {
        let tries = 0;
        (function poll() { if (test() || tries++ > 40) resolve(test()); else setTimeout(poll, 100); })();
      });
    };
    if (!(await waitFor(function () { return hasOption(offeringSelect, wantedOffering); }))) return;
    offeringSelect.value = wantedOffering;
    offeringSelect.dispatchEvent(new Event("change"));
    if (wantedSession && (await waitFor(function () { return hasOption(sessionSelect, wantedSession); }))) {
      sessionSelect.value = wantedSession;
      sessionSelect.dispatchEvent(new Event("change"));
    }
  }

  document.addEventListener("vistadmin:ready", function () {
    loadMyOfferings();
    loadMyRequests();
    applyDeepLink();
  });
})();