(function () {
  "use strict";

  const VA = window.VistAdmin;
  let myEnrollmentsCache = [];

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  function statusPillHtml(status) {
    return '<span class="status-pill ' + (status || "").toLowerCase() + '">' + VA.escapeHtml(status) + "</span>";
  }

  const SU = window.StudentUI;
  const esc = VA.escapeHtml;
  let overviewByOffering = {};
  let threshold = 75;

  // A course as a card: who teaches it, how much has been taught, how attendance is going.
  function courseCardHtml(c) {
    const o = c.overview;
    const pct = o && o.topics_total ? Math.round((o.topics_covered / o.topics_total) * 100) : 0;
    const att = o && o.attendance_percentage !== null && o.attendance_percentage !== undefined ? o.attendance_percentage : null;
    const progress = o
      ? '<div class="meter" role="progressbar" aria-valuenow="' + pct + '" aria-valuemin="0" aria-valuemax="100" aria-label="Course taught so far"><span class="meter-fill" style="width:' + Math.max(pct, o.topics_covered ? 3 : 0) + '%"></span></div>' +
        '<div class="sh-course-sub"><span>' + (o.topics_total ? o.topics_covered + " of " + o.topics_total + " classes taught" : "No classes scheduled yet") + "</span>" +
        '<span class="sh-att-pill ' + (att === null ? "" : SU.tone(att, threshold)) + '">' + (att === null ? "No attendance yet" : "Attendance " + att + "%") + "</span></div>"
      : "";
    return '<div class="course-top"><span class="pill">' + esc(c.course_code) + '</span><span class="pill">' + esc(c.term_label || "") + "</span>" +
      (c.status ? SU.pill(c.status) : "") + (c.outcome ? SU.pill(c.outcome) : "") + (c.is_retake ? '<span class="pill">Retake</span>' : "") + "</div>" +
      "<h3>" + esc(c.course_title) + "</h3>" +
      '<p class="course-meta">' + esc(c.credit_hours) + " credit hour" + (c.credit_hours === 1 ? "" : "s") + (c.section_label && c.section_label !== "A" ? " \u00b7 Section " + esc(c.section_label) : "") + "</p>" +
      SU.teacherLine(c.teacher_name) + progress;
  }

  // ---------------------------------------------------------------------
  // My enrollments
  // ---------------------------------------------------------------------
  const enrollmentsGrid = document.getElementById("my-enrollments-grid");
  const enrollmentsEmpty = document.getElementById("my-enrollments-empty");

  async function loadMyEnrollments() {
    const [result, overview, cfg] = await Promise.all([
      VA.fetchJson("/api/v1/academics/enrollments/me"),
      VA.fetchJson("/api/v1/academics/enrollments/me/overview"),
      VA.fetchJson("/api/v1/attendance/config"),
    ]);
    if (!result.ok) {
      enrollmentsGrid.innerHTML = "";
      VA.showError(extractErrorMessage(result.body, "Could not load your enrollments."));
      return;
    }
    VA.clearError();
    myEnrollmentsCache = result.body;
    threshold = cfg.ok ? cfg.body.eligibility_threshold : 75;
    overviewByOffering = {};
    (overview.ok ? overview.body : []).forEach(function (o) { overviewByOffering[o.offering_id] = o; });

    enrollmentsGrid.innerHTML = "";
    enrollmentsEmpty.hidden = result.body.length > 0;
    result.body.forEach(function (e) {
      const ov = overviewByOffering[e.course_offering_id];
      // The overview only lists classes the student is still attending, so a COMPLETED enrollment that has one is a
      // course whose result was published while the term is still running: show it as ENROLLED (with its result).
      const running = e.status === "COMPLETED" && !!ov;
      const card = document.createElement("article");
      card.className = "course-card" + (e.status === "ENROLLED" || running ? "" : " is-done");
      card.innerHTML = courseCardHtml(Object.assign({}, e, { status: running ? "ENROLLED" : e.status, overview: ov, teacher_name: ov && ov.teacher_name, section_label: ov && ov.section_label })) +
        '<div class="course-actions"><button type="button" class="role-btn small-btn course-materials">Materials</button></div>';
      card.querySelector(".course-materials").addEventListener("click", function () {
        openMaterialsModal(e.course_offering_id, e.course_code + " \u2014 " + e.course_title);
      });
      enrollmentsGrid.appendChild(card);
    });
  }

  // ---------------------------------------------------------------------
  // Open courses (own program + semester) and other terms
  // ---------------------------------------------------------------------
  const eligibleGrid = document.getElementById("eligible-offerings-grid");
  const eligibleEmpty = document.getElementById("eligible-offerings-empty");
  const enrollError = document.getElementById("enroll-error");

  function createEnrollButton(offering, onEnrolled) {
    const btn = document.createElement("button");
    // Any enrollment that was not withdrawn counts: a published result marks it COMPLETED while the term is still running.
    const mine = myEnrollmentsCache.find(function (e) {
      return e.course_offering_id === offering.id && e.status !== "WITHDRAWN";
    });
    const alreadyEnrolled = !!mine;
    btn.className = "role-btn small-btn";
    btn.type = "button";
    if (alreadyEnrolled) {
      btn.textContent = mine.status === "COMPLETED" && !overviewByOffering[offering.id] ? "Completed \u2713" : "Enrolled \u2713";
      btn.disabled = true;
      return btn;
    }
    btn.textContent = "Enroll";
    btn.addEventListener("click", async function () {
      btn.disabled = true;
      enrollError.hidden = true;
      const result = await VA.fetchJson("/api/v1/academics/enrollments/me", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ course_offering_id: offering.id }),
      });
      if (!result.ok) {
        enrollError.textContent = extractErrorMessage(result.body, "Could not enroll.");
        enrollError.hidden = false;
        btn.disabled = false;
        return;
      }
      await loadMyEnrollments();
      onEnrolled();
    });
    return btn;
  }

  function offeringCard(o, reload, showCount) {
    const card = document.createElement("article");
    const blocked = o.can_enroll === false;
    card.className = "course-card" + (blocked ? " is-blocked" : "");
    const seats = o.capacity ? (o.enrolled_count >= o.capacity ? "Full" : (o.capacity - o.enrolled_count) + " seats left") : o.enrolled_count + " enrolled";
    card.innerHTML =
      '<div class="course-top"><span class="pill">' + esc(o.course_code) + '</span><span class="pill">' + esc(o.term_label || "") + "</span>" +
      (o.is_retake ? '<span class="pill pill-now">Retake</span>' : "") + "</div>" +
      "<h3>" + esc(o.course_title) + "</h3>" +
      '<p class="course-meta">' + esc(o.credit_hours) + " credit hours \u00b7 Section " + esc(o.section_label || "A") + (showCount ? " \u00b7 " + esc(seats) : "") + "</p>" +
      SU.teacherLine(o.teacher_name) +
      (blocked && o.reason ? '<p class="course-reason">' + esc(o.reason) + "</p>" : "") +
      '<div class="course-actions"></div>';
    const actions = card.querySelector(".course-actions");
    if (blocked) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "role-btn small-btn secondary-btn";
      b.disabled = true;
      b.textContent = "Not available to you";
      actions.appendChild(b);
    } else {
      actions.appendChild(createEnrollButton(o, reload));
    }
    return card;
  }

  async function loadEligibleOfferings() {
    const result = await VA.fetchJson("/api/v1/academics/offerings/eligible-for-me");
    eligibleGrid.innerHTML = "";
    if (!result.ok) return;
    eligibleEmpty.hidden = result.body.length > 0;
    result.body.forEach(function (o) { eligibleGrid.appendChild(offeringCard(o, loadEligibleOfferings, true)); });
  }

  const termLabelInput = document.getElementById("term-label-input");
  const browseGrid = document.getElementById("browse-offerings-grid");
  const browseEmpty = document.getElementById("browse-offerings-empty");

  async function loadTermsDropdown() {
    const result = await VA.fetchJson("/api/v1/academics/terms");
    if (!result.ok) return;
    const previous = termLabelInput.value;
    termLabelInput.innerHTML = '<option value="">Select a term...</option>';
    result.body.forEach(function (term) {
      const opt = document.createElement("option");
      opt.value = term;
      opt.textContent = term;
      termLabelInput.appendChild(opt);
    });
    if (previous) termLabelInput.value = previous;
  }

  document.getElementById("refresh-terms-btn").addEventListener("click", loadTermsDropdown);

  async function loadOfferings() {
    const term = termLabelInput.value;
    if (!term) return;
    enrollError.hidden = true;
    const result = await VA.fetchJson("/api/v1/academics/offerings/browse-for-me?" + new URLSearchParams({ term_label: term }));
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load offerings."));
      return;
    }
    VA.clearError();
    browseGrid.innerHTML = "";
    // The server lists only courses open for enrollment, best matches first. Ones I am already in live under "My courses".
    const openOnes = result.body.filter(function (o) { return !o.already_enrolled; });
    if (openOnes.length === 0) {
      browseEmpty.textContent = "No courses are currently open for enrollment in that term.";
      browseEmpty.hidden = false;
      return;
    }
    browseEmpty.hidden = true;
    openOnes.forEach(function (o) { browseGrid.appendChild(offeringCard(o, loadOfferings, true)); });
  }

  document.getElementById("browse-term-btn").addEventListener("click", loadOfferings);

  // ---------------------------------------------------------------------
  // Materials viewer (syllabus + lectures + resources)
  // ---------------------------------------------------------------------
  const materialsModal = document.getElementById("materials-modal");
  const materialsTitle = document.getElementById("materials-title");
  const materialsSyllabus = document.getElementById("materials-syllabus");
  const materialsLectures = document.getElementById("materials-lectures");
  const materialsEmpty = document.getElementById("materials-empty");

  function resourceTypeLabel(t) {
    if (t === "NOTES") return "Notes";
    if (t === "SLIDES") return "Slides";
    return "Other";
  }

  async function openMaterialsModal(offeringId, title) {
    materialsTitle.textContent = title;
    materialsSyllabus.innerHTML = "";
    materialsLectures.innerHTML = "";
    materialsEmpty.hidden = true;
    materialsModal.showModal();

    const offeringResult = await VA.fetchJson("/api/v1/academics/offerings/" + offeringId);
    if (offeringResult.ok && offeringResult.body.has_syllabus) {
      materialsSyllabus.innerHTML =
        '<a class="role-btn small-btn" href="/api/v1/academics/offerings/' + offeringId + '/syllabus" target="_blank" rel="noopener">Download Syllabus</a>';
    }

    const sessionsResult = await VA.fetchJson("/api/v1/academics/offerings/" + offeringId + "/class-sessions");
    if (!sessionsResult.ok) {
      materialsEmpty.textContent = extractErrorMessage(sessionsResult.body, "Could not load class sessions.");
      materialsEmpty.hidden = false;
      return;
    }
    const sessions = sessionsResult.body.slice().sort(function (a, b) { return a.session_date.localeCompare(b.session_date); });
    if (sessions.length === 0) {
      materialsEmpty.hidden = false;
      return;
    }

    // Progress line: how much of the course the teacher has covered so far.
    const held = sessions.filter(function (x) { return x.status !== "CANCELLED"; });
    const covered = held.filter(function (x) { return x.topic_completed; }).length;
    if (held.length) {
      const pct = Math.round((covered / held.length) * 100);
      const summary = document.createElement("div");
      summary.className = "plan-summary";
      summary.innerHTML = "<strong>" + covered + " of " + held.length + " topics covered so far</strong>" +
        '<div class="meter"><span class="meter-fill" style="width:' + Math.max(pct, covered ? 2 : 0) + '%"></span></div>';
      materialsLectures.appendChild(summary);
    }

    sessions.forEach(function (session) {
      const cancelled = session.status === "CANCELLED";
      const block = document.createElement("div");
      block.className = "panel lecture-card" + (session.topic_completed && !cancelled ? " plan-done" : "");
      let resourcesHtml = "";
      if (cancelled) {
        resourcesHtml = "";
      } else if (session.resources.length === 0) {
        resourcesHtml = '<p class="panel-hint">No files uploaded for this session yet.</p>';
      } else {
        resourcesHtml = session.resources.map(function (r) {
          return '<a class="role-btn small-btn" style="margin-right:8px;margin-bottom:6px;display:inline-block" href="/api/v1/academics/class-session-resources/' + r.id + '" target="_blank" rel="noopener">' +
            resourceTypeLabel(r.resource_type) + ": " + VA.escapeHtml(r.original_name) + "</a>";
        }).join("");
      }
      block.innerHTML =
        "<strong>" + session.session_date + " \u00b7 " + session.start_time.slice(0, 5) + "\u2013" + session.end_time.slice(0, 5) +
        " \u00b7 " + VA.escapeHtml(session.room) +
        (cancelled ? ' <span class="status-pill cancelled">CANCELLED</span>' : "") +
        (session.topic_completed && !cancelled ? ' <span class="badge-covered">\u2713 Covered</span>' : "") +
        (session.title ? " \u2014 " + VA.escapeHtml(session.title) : "") + "</strong>" +
        (cancelled && session.cancelled_reason ? '<p class="panel-hint">Reason: ' + VA.escapeHtml(session.cancelled_reason) + "</p>" : "") +
        (session.description ? '<p class="panel-hint">' + VA.escapeHtml(session.description) + "</p>" : "") +
        "<div>" + resourcesHtml + "</div>";
      materialsLectures.appendChild(block);
    });
  }

  document.getElementById("close-materials-modal").addEventListener("click", function () {
    materialsModal.close();
  });

  document.addEventListener("vistadmin:ready", async function () {
    await loadMyEnrollments();
    loadEligibleOfferings();
    loadTermsDropdown();
  });
})();