(function () {
  "use strict";

  const VA = window.VistAdmin;
  const SU = window.StudentUI;
  const esc = VA.escapeHtml;

  const STATUS_LABEL = { PRESENT: "Present", ABSENT: "Absent", LEAVE: "Approved leave" };

  function verdict(pct, held, threshold) {
    if (!held) return { tone: "", text: "No classes held yet" };
    if (pct >= threshold + 5) return { tone: "good", text: "On track" };
    if (pct >= threshold) return { tone: "warn", text: "Close to the " + threshold + "% limit" };
    return { tone: "bad", text: "Below " + threshold + "% \u2014 at risk" };
  }

  async function loadAttendance() {
    const [overview, cfg, riskRes] = await Promise.all([
      VA.fetchJson("/api/v1/academics/enrollments/me/overview"),
      VA.fetchJson("/api/v1/attendance/config"),
      VA.fetchJson("/api/v1/attendance/me/risk", { quiet: true }),
    ]);
    const risk = {};
    (riskRes.ok ? riskRes.body : []).forEach(function (r) { risk[r.offering_id] = r; });
    const grid = document.getElementById("att-grid");
    const overall = document.getElementById("att-overall");
    const empty = document.getElementById("attendance-empty");
    const notice = document.getElementById("attendance-risk-notice");
    grid.innerHTML = "";
    if (!overview.ok) {
      overall.innerHTML = '<p class="home-empty">Could not load your attendance right now.</p>';
      return;
    }
    const threshold = cfg.ok ? cfg.body.eligibility_threshold : 75;
    const courses = overview.body;
    empty.hidden = courses.length > 0;

    const held = courses.reduce(function (n, c) { return n + c.classes_held; }, 0);
    const attended = courses.reduce(function (n, c) { return n + c.classes_attended; }, 0);
    if (!held) {
      overall.innerHTML = '<p class="home-empty">No classes have been held yet, so there is no attendance to show.</p>';
    } else {
      const pct = Math.round((attended / held) * 1000) / 10;
      overall.innerHTML =
        '<div class="sh-att-body">' + SU.ring(pct, threshold, "Overall attendance", 140) +
        '<ul class="sh-att-stats"><li><strong>' + attended + "</strong> classes attended</li><li><strong>" + (held - attended) + "</strong> classes missed</li>" +
        "<li><strong>" + held + "</strong> classes held so far</li><li>Pass mark <strong>" + threshold + "%</strong></li></ul></div>";
    }

    const atRisk = [];
    courses.forEach(function (c) {
      const pct = c.attendance_percentage;
      const v = verdict(pct, c.classes_held, threshold);
      if (c.classes_held && pct < threshold) atRisk.push(c.course_code + " (" + pct + "%)");
      const card = document.createElement("article");
      card.className = "course-card att-card";
      card.innerHTML =
        '<div class="course-top"><span class="pill">' + esc(c.course_code) + "</span>" + (v.tone ? '<span class="sh-att-pill ' + v.tone + '">' + esc(v.text) + "</span>" : "") + "</div>" +
        "<h3>" + esc(c.course_title) + "</h3>" +
        (c.classes_held
          ? '<div class="att-row">' + SU.ring(pct, threshold, c.course_code + " attendance", 84) +
            '<div class="att-nums"><strong>' + c.classes_attended + " of " + c.classes_held + '</strong><span>classes attended</span>' +
            '<span class="att-miss">' + (c.classes_held - c.classes_attended) + " missed</span></div></div>"
          : '<p class="home-empty">No classes have been held yet.</p>') +
        riskLine(risk[c.offering_id]) +
        '<div class="course-actions"><button type="button" class="role-btn small-btn secondary-btn att-history">Class by class</button></div>';
      card.querySelector(".att-history").addEventListener("click", function () { showHistory(c); });
      grid.appendChild(card);
    });

    if (atRisk.length) {
      const fines = courses.map(function (c) { return risk[c.offering_id]; }).filter(function (r) { return r && r.fine > 0 && !r.charged; });
      const total = fines.reduce(function (n, r) { return n + r.fine; }, 0);
      notice.innerHTML = "<strong>Attendance warning:</strong> you are below the " + threshold + "% needed to pass in " + esc(atRisk.join(", ")) + "." +
        (total ? " As things stand, the attendance fine would be <strong>" + esc(VA.formatMoney(total)) + "</strong>, charged before the final exams. " +
          "Paying it lets your exam marks decide the result. Each course below shows how many classes bring the fine down." : "");
      notice.hidden = false;
    } else {
      notice.hidden = true;
    }
  }

  // One line per course: the fine it means now and the classes in a row that bring it to the next level.
  function riskLine(r) {
    if (!r || !r.message) return "";
    const tone = r.review || (r.charged && r.charged.unpaid) ? "bad" : r.band || (r.percentage < r.minimum) || !r.can_miss ? "warn" : "good";
    return '<div class="att-risk ' + tone + '"><p>' + esc(r.message) + "</p></div>";
  }

  async function showHistory(course) {
    const list = document.getElementById("history-list");
    const emptyMsg = document.getElementById("history-empty");
    document.getElementById("history-title").textContent = course.course_code + " \u2014 " + course.course_title;
    list.innerHTML = '<div class="skeleton-stack"><div class="skeleton-line"></div><div class="skeleton-line short"></div></div>';
    VA.showTab("history");
    window.scrollTo({ top: 0, behavior: "smooth" });
    const result = await VA.fetchJson("/api/v1/attendance/me/offerings/" + course.offering_id + "/records");
    list.innerHTML = "";
    if (!result.ok) return;
    emptyMsg.hidden = result.body.length > 0;
    result.body.forEach(function (r) {
      const row = document.createElement("div");
      row.className = "history-row " + r.status.toLowerCase();
      row.innerHTML = "<span>" + esc(VA.formatDateLong(r.session_date)) + " \u00b7 " + esc(VA.formatTimeShort(r.start_time)) + "</span>" +
        '<span class="history-badge">' + esc(STATUS_LABEL[r.status] || r.status) + "</span>";
      list.appendChild(row);
    });
  }

  document.getElementById("history-close").addEventListener("click", function () { VA.showTab("summary"); });
  document.getElementById("history-list").innerHTML = '<p class="home-empty">Choose a course on the Summary tab to see its class-by-class record.</p>';
  document.addEventListener("vistadmin:ready", loadAttendance);
})();