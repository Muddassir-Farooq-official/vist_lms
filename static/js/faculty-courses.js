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

  function resourceTypeLabel(t) {
    if (t === "NOTES") return "Notes";
    if (t === "SLIDES") return "Slides";
    return "Other";
  }

  // ---------------------------------------------------------------------
  // My offerings list
  // ---------------------------------------------------------------------
  const offeringsTbody = document.getElementById("my-offerings-tbody");
  const offeringsEmpty = document.getElementById("my-offerings-empty");

  const SU = window.StudentUI;
  const offeringsGrid = document.getElementById("my-offerings-grid");

  // Each course as a card: who is in it, how much has been taught, how attendance is going, what needs doing.
  async function loadMyOfferings() {
    const [result, overview] = await Promise.all([
      VA.fetchJson("/api/v1/academics/offerings/me"),
      VA.fetchJson("/api/v1/academics/offerings/me/overview"),
    ]);
    if (!result.ok) {
      offeringsGrid.innerHTML = "";
      VA.showError(extractErrorMessage(result.body, "Could not load your courses."));
      return;
    }
    VA.clearError();
    const ov = {};
    (overview.ok ? overview.body : []).forEach(function (o) { ov[o.offering_id] = o; });
    offeringsGrid.innerHTML = "";
    offeringsEmpty.hidden = result.body.length > 0;

    result.body.forEach(function (o) {
      const x = ov[o.id];
      const pct = x && x.classes_total ? Math.round((x.topics_covered / x.classes_total) * 100) : 0;
      const att = x && x.average_attendance !== null && x.average_attendance !== undefined ? x.average_attendance : null;
      const card = document.createElement("article");
      card.className = "course-card";
      card.innerHTML =
        '<div class="course-top"><span class="pill">' + VA.escapeHtml(o.course_code) + '</span><span class="pill">' + VA.escapeHtml(o.term_label) + "</span>" +
        '<span class="pill">Section ' + VA.escapeHtml(o.section_label || "A") + "</span>" +
        (o.is_open_for_enrollment ? '<span class="status-pill active">Open</span>' : '<span class="status-pill dropped">Closed</span>') +
        (o.content_locked ? '<span class="badge-locked">\uD83D\uDD12 Locked</span>' : "") + "</div>" +
        "<h3>" + VA.escapeHtml(o.course_title) + "</h3>" +
        '<p class="course-meta">' + VA.escapeHtml(o.credit_hours) + " credit hours \u00b7 " + VA.escapeHtml(o.enrolled_count) + " students enrolled</p>" +
        (x
          ? '<div class="meter" role="progressbar" aria-valuenow="' + pct + '" aria-valuemin="0" aria-valuemax="100" aria-label="Course taught so far"><span class="meter-fill" style="width:' + Math.max(pct, x.topics_covered ? 3 : 0) + '%"></span></div>' +
            '<div class="sh-course-sub"><span>' + (x.classes_total ? x.topics_covered + " of " + x.classes_total + " classes taught" : "No classes scheduled yet") + "</span>" +
            '<span class="sh-att-pill ' + (att === null ? "" : SU.tone(att, 75)) + '">' + (att === null ? "No attendance yet" : "Attendance " + att + "%") + "</span></div>" +
            (x.unmarked_past_classes ? '<p class="course-reason">' + x.unmarked_past_classes + " past " + (x.unmarked_past_classes === 1 ? "class has" : "classes have") + " no attendance recorded.</p>" : "")
          : "") +
        '<div class="course-actions"><button type="button" class="role-btn small-btn course-plan">Lesson plan &amp; materials</button>' +
        '<a class="role-btn small-btn secondary-btn" href="/dashboard/faculty/attendance?offering=' + o.id + '">Attendance</a></div>';
      card.querySelector(".course-plan").addEventListener("click", function () { openViewModal(o); });
      offeringsGrid.appendChild(card);
    });

    // Arrived from a link such as "Add topic & slides" on the Home page: open that course straight away.
    const wanted = new URLSearchParams(window.location.search).get("offering");
    const target = wanted && result.body.find(function (o) { return o.id === wanted; });
    if (target && !window.__planOpened) {
      window.__planOpened = true;
      openViewModal(target);
    }
  }

  // ---------------------------------------------------------------------
  // Course modal — syllabus, course plan (topic / slides / notes per class), roster
  // ---------------------------------------------------------------------
  const manageModal = document.getElementById("manage-offering-modal");
  const manageTitle = document.getElementById("manage-offering-title");
  const syllabusView = document.getElementById("syllabus-view");
  const syllabusFile = document.getElementById("syllabus-file");
  const syllabusRemove = document.getElementById("syllabus-remove");
  const rosterTbody = document.getElementById("roster-tbody");
  const lecturesList = document.getElementById("lectures-list");
  const lecturesEmpty = document.getElementById("lectures-empty");
  const contentError = document.getElementById("content-error");
  const contentSuccess = document.getElementById("content-success");

  let activeOffering = null;
  let flashTimer = null;

  function flash(message) {
    contentError.hidden = true;
    contentSuccess.textContent = message;
    contentSuccess.hidden = false;
    clearTimeout(flashTimer);
    flashTimer = setTimeout(function () { contentSuccess.hidden = true; }, 2500); // never leave a stale message on screen
  }
  function fail(message) {
    clearTimeout(flashTimer);
    contentSuccess.hidden = true;
    contentError.textContent = message;
    contentError.hidden = false;
  }
  function today() {
    const d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }

  function courseLocked() { return !!(activeOffering && activeOffering.content_locked); }

  function renderLockState() {
    const locked = courseLocked();
    document.getElementById("lock-banner").hidden = !locked;
    syllabusFile.disabled = locked;
    syllabusFile.closest("label").style.display = locked ? "none" : "";
    syllabusRemove.hidden = locked || !activeOffering.has_syllabus;
  }

  function renderSummary() {
    const held = planSessions.filter(function (s) { return s.status !== "CANCELLED"; });
    const done = held.filter(function (s) { return s.topic_completed; }).length;
    const box = document.getElementById("plan-summary");
    if (!held.length) { box.innerHTML = ""; return; }
    const pct = Math.round((done / held.length) * 100);
    box.innerHTML = "<strong>" + done + " of " + held.length + " topics covered</strong>" +
      '<div class="meter"><span class="meter-fill" style="width:' + Math.max(pct, done ? 2 : 0) + '%"></span></div>';
  }

  function renderSyllabus() {
    if (activeOffering.has_syllabus) {
      syllabusView.innerHTML =
        '<a class="role-btn small-btn" href="/api/v1/academics/offerings/' + activeOffering.id +
        '/syllabus" target="_blank" rel="noopener">Download syllabus</a>';
    } else {
      syllabusView.innerHTML = '<p class="panel-hint">No syllabus uploaded yet.</p>';
    }
    syllabusRemove.hidden = courseLocked() || !activeOffering.has_syllabus;
  }

  async function openViewModal(offering) {
    activeOffering = offering;
    manageTitle.textContent = offering.course_code + " \u2014 " + offering.course_title + " (" + offering.term_label + ", Section " + (offering.section_label || "A") + ")";
    contentError.hidden = true;
    contentSuccess.hidden = true;
    renderSyllabus();
    renderLockState();
    manageModal.showModal();
    await Promise.all([loadRoster(offering.id), loadClassSessions(offering.id)]);
  }

  document.getElementById("close-manage-offering-modal").addEventListener("click", function () {
    manageModal.close();
    loadMyOfferings();
  });

  syllabusFile.addEventListener("change", async function () {
    if (!syllabusFile.files.length) return;
    const fd = new FormData();
    fd.append("file", syllabusFile.files[0]);
    const r = await VA.fetchJson("/api/v1/academics/offerings/" + activeOffering.id + "/syllabus", { method: "POST", body: fd });
    syllabusFile.value = "";
    if (!r.ok) { fail(extractErrorMessage(r.body, "Could not upload the syllabus.")); return; }
    activeOffering.has_syllabus = true;
    renderSyllabus();
    flash("Syllabus uploaded.");
  });
  syllabusRemove.addEventListener("click", async function () {
    if (!window.confirm("Remove the syllabus?")) return;
    const r = await VA.fetchJson("/api/v1/academics/offerings/" + activeOffering.id + "/syllabus", { method: "DELETE" });
    if (!r.ok) { fail(extractErrorMessage(r.body, "Could not remove the syllabus.")); return; }
    activeOffering.has_syllabus = false;
    renderSyllabus();
    flash("Syllabus removed.");
  });

  async function loadRoster(offeringId) {
    const result = await VA.fetchJson("/api/v1/academics/offerings/" + offeringId + "/roster");
    rosterTbody.innerHTML = "";
    if (!result.ok) return;

    // Bulk percentages — one call for the whole roster instead of one per row.
    const pctResult = await VA.fetchJson("/api/v1/attendance/offerings/" + offeringId + "/percentages");
    const percentages = pctResult.ok ? pctResult.body.percentages : {};

    result.body.forEach(function (e) {
      const tr = document.createElement("tr");
      const pct = percentages[e.student_id];
      const pctText = pct ? pct.percentage + "%" : "\u2014";
      tr.innerHTML =
        "<td>" + VA.escapeHtml(e.student_name) + "</td>" +
        "<td>" + VA.escapeHtml(e.roll_number || "\u2014") + "</td>" +
        "<td>" + statusPillHtml(e.status) + "</td>" +
        "<td>" + (e.is_retake ? "Yes" : "\u2014") + "</td>" +
        "<td>" + pctText + "</td>";
      rosterTbody.appendChild(tr);
    });
  }

  // ---- The course plan ---------------------------------------------------
  let planSessions = [];

  function resourceChip(r) {
    const href = r.external_url ? r.external_url : "/api/v1/academics/class-session-resources/" + r.id;
    const wrap = document.createElement("span");
    wrap.className = "chip";
    wrap.innerHTML =
      '<a href="' + VA.escapeHtml(href) + '" target="_blank" rel="noopener">' + resourceTypeLabel(r.resource_type) + ": " +
      VA.escapeHtml(r.original_name) + (r.external_url ? " \u2197" : "") + "</a>" +
      '<button type="button" class="chip-x" aria-label="Remove ' + VA.escapeHtml(r.original_name) + '">\u2715</button>';
    wrap.querySelector(".chip-x").addEventListener("click", async function () {
      if (!window.confirm("Remove \u201c" + r.original_name + "\u201d from this class?")) return;
      const res = await VA.fetchJson("/api/v1/academics/class-session-resources/" + r.id, { method: "DELETE" });
      if (!res.ok) { fail(extractErrorMessage(res.body, "Could not remove it.")); return; }
      flash("Removed.");
      loadClassSessions(activeOffering.id);
    });
    return wrap;
  }

  function planCard(session) {
    const cancelled = session.status === "CANCELLED";
    const locked = !cancelled && (courseLocked() || session.content_locked);
    const card = document.createElement("div");
    card.className = "plan-card" + (cancelled ? " plan-cancelled" : "") + (session.topic_completed && !cancelled ? " plan-done" : "") + (locked ? " plan-locked" : "");
    const head = document.createElement("div");
    head.className = "plan-head";
    head.innerHTML =
      "<strong>" + VA.formatDateLong(session.session_date) + " \u00b7 " + VA.formatTimeShort(session.start_time) + "\u2013" + VA.formatTimeShort(session.end_time) + "</strong>" +
      '<span class="list-sub">Room ' + VA.escapeHtml(session.room) + "</span>" +
      (cancelled ? '<span class="status-pill cancelled">CANCELLED</span>' : "") +
      '<span class="badge-covered"' + (session.topic_completed && !cancelled ? "" : " hidden") + ">\u2713 Covered</span>" +
      (locked ? '<span class="badge-locked">\uD83D\uDD12 Locked by admin</span>' : "");
    card.appendChild(head);
    if (cancelled) return card;

    // "Mark as covered": lives here, next to the topic, so it is not buried on the attendance page.
    // It stays available on locked classes (it records what happened; it does not change content).
    const isFuture = session.session_date > today();
    const cover = document.createElement("label");
    cover.className = "cover-toggle";
    cover.innerHTML = '<input type="checkbox"' + (session.topic_completed ? " checked" : "") + (isFuture ? " disabled" : "") + " /><span>" +
      (isFuture ? "Can be marked as covered once the class has started" : "Mark this topic as covered") + "</span>";
    cover.querySelector("input").addEventListener("change", async function (e) {
      const want = e.target.checked;
      const r = await VA.fetchJson("/api/v1/academics/class-sessions/" + session.id + "/topic-completed", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ completed: want }),
      });
      if (!r.ok) { e.target.checked = !want; fail(extractErrorMessage(r.body, "Could not update the topic.")); return; }
      session.topic_completed = want;
      card.classList.toggle("plan-done", want);
      head.querySelector(".badge-covered").hidden = !want;
      renderSummary();
    });
    card.appendChild(cover);

    const topicRow = document.createElement("div");
    topicRow.className = "plan-topic-row";
    topicRow.innerHTML =
      '<input type="text" class="text-input plan-topic" maxlength="200" placeholder="' + (locked ? "No topic entered" : "Topic for this class") + '" aria-label="Topic" value="' + VA.escapeHtml(session.title || "") + '"' + (locked ? " readonly" : "") + " />" +
      '<span class="plan-saved" hidden>Saved \u2713</span>';
    const input = topicRow.querySelector("input");
    const saved = topicRow.querySelector(".plan-saved");
    input.addEventListener("change", async function () {
      const r = await VA.fetchJson("/api/v1/academics/class-sessions/" + session.id, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: input.value.trim() || null }),
      });
      if (!r.ok) { fail(extractErrorMessage(r.body, "Could not save the topic.")); return; }
      session.title = input.value.trim() || null;
      saved.hidden = false;
      setTimeout(function () { saved.hidden = true; }, 1500);
    });
    card.appendChild(topicRow);

    const chips = document.createElement("div");
    chips.className = "plan-resources";
    session.resources.forEach(function (r) {
      const chip = resourceChip(r);
      if (locked) chip.querySelector(".chip-x").remove();       // view-only: nothing can be removed
      chips.appendChild(chip);
    });
    card.appendChild(chips);
    if (locked) return card;                                    // no add / upload controls on locked content

    const actions = document.createElement("div");
    actions.className = "plan-actions";
    actions.innerHTML =
      '<button type="button" class="role-btn small-btn secondary-btn plan-add-link">+ Link</button>' +
      '<label class="role-btn small-btn secondary-btn file-btn">+ PDF<input type="file" accept="application/pdf,.pdf" hidden /></label>';
    card.appendChild(actions);

    const form = document.createElement("div");
    form.className = "plan-linkform";
    form.hidden = true;
    form.innerHTML =
      '<select class="select-input" aria-label="Type"><option value="SLIDES">Slides</option><option value="NOTES">Notes</option><option value="OTHER">Other</option></select>' +
      '<input type="text" class="text-input" maxlength="200" placeholder="Name, e.g. Week 3 slides" aria-label="Name" />' +
      '<input type="url" class="text-input" maxlength="2048" placeholder="https://\u2026" aria-label="Link" />' +
      '<div class="plan-actions"><button type="button" class="role-btn small-btn plan-link-save">Add link</button><button type="button" class="role-btn small-btn secondary-btn plan-link-cancel">Cancel</button></div>';
    card.appendChild(form);

    actions.querySelector(".plan-add-link").addEventListener("click", function () { form.hidden = !form.hidden; });
    form.querySelector(".plan-link-cancel").addEventListener("click", function () { form.hidden = true; });
    form.querySelector(".plan-link-save").addEventListener("click", async function () {
      const fields = form.querySelectorAll("input, select");
      const type = fields[0].value, name = fields[1].value.trim(), url = fields[2].value.trim();
      if (!/^https?:\/\//i.test(url)) { fail("The link must start with http:// or https://"); return; }
      const r = await VA.fetchJson("/api/v1/academics/class-sessions/" + session.id + "/resources/link", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resource_type: type, external_url: url, title: name || url }),
      });
      if (!r.ok) { fail(extractErrorMessage(r.body, "Could not add the link.")); return; }
      flash("Link added.");
      loadClassSessions(activeOffering.id);
    });
    actions.querySelector('input[type="file"]').addEventListener("change", async function (e) {
      const file = e.target.files[0];
      if (!file) return;
      const fd = new FormData();
      fd.append("file", file);
      const r = await VA.fetchJson("/api/v1/academics/class-sessions/" + session.id + "/resources?resource_type=NOTES", { method: "POST", body: fd });
      e.target.value = "";
      if (!r.ok) { fail(extractErrorMessage(r.body, "Could not upload the file.")); return; }
      flash("File uploaded.");
      loadClassSessions(activeOffering.id);
    });
    return card;
  }

  async function loadClassSessions(offeringId) {
    const result = await VA.fetchJson("/api/v1/academics/offerings/" + offeringId + "/class-sessions");
    lecturesList.innerHTML = "";
    if (!result.ok) return;
    planSessions = result.body.slice().sort(function (a, b) { return (a.session_date + a.start_time).localeCompare(b.session_date + b.start_time); });
    lecturesEmpty.hidden = planSessions.length > 0;
    renderSummary();
    if (!planSessions.length) return;

    const t = today();
    const upcoming = planSessions.filter(function (s) { return s.session_date >= t; });
    const past = planSessions.filter(function (s) { return s.session_date < t; });
    upcoming.forEach(function (s) { lecturesList.appendChild(planCard(s)); });
    if (!upcoming.length) lecturesList.innerHTML = '<p class="panel-hint">No upcoming classes.</p>';
    if (past.length) {
      const details = document.createElement("details");
      details.className = "paste-plan";
      details.innerHTML = "<summary>Earlier classes (" + past.length + ")</summary>";
      past.forEach(function (s) { details.appendChild(planCard(s)); });
      lecturesList.appendChild(details);
    }
  }

  // Paste a list of topics: one per line, filled into the upcoming classes in order.
  document.getElementById("plan-apply").addEventListener("click", async function () {
    const lines = document.getElementById("plan-paste").value.split("\n").map(function (l) { return l.trim(); }).filter(Boolean);
    if (!lines.length) { fail("Paste at least one topic first."); return; }
    const t = today();
    if (courseLocked()) { fail("This course's content is locked by the admin."); return; }
    const targets = planSessions.filter(function (s) { return s.session_date >= t && s.status !== "CANCELLED" && !s.content_locked; });
    if (!targets.length) { fail("There are no upcoming classes to fill."); return; }
    const overwrites = targets.slice(0, lines.length).filter(function (s) { return s.title; }).length;
    if (overwrites && !window.confirm(overwrites + " class(es) already have a topic. Replace them?")) return;
    const items = targets.slice(0, lines.length).map(function (s, i) { return { class_session_id: s.id, title: lines[i].slice(0, 200) }; });
    const r = await VA.fetchJson("/api/v1/academics/offerings/" + activeOffering.id + "/lecture-plan", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items: items }),
    });
    if (!r.ok) { fail(extractErrorMessage(r.body, "Could not save the topics.")); return; }
    const extra = lines.length - targets.length;
    flash("Filled " + items.length + " class(es)." + (extra > 0 ? " " + extra + " extra topic(s) had no class to go to." : ""));
    document.getElementById("plan-paste").value = "";
    loadClassSessions(activeOffering.id);
  });

  document.addEventListener("vistadmin:ready", function () {
    loadMyOfferings();
  });
})();