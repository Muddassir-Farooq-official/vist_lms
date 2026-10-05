(function () {
  "use strict";

  const VA = window.VistAdmin;
  const SU = window.StudentUI;
  const esc = VA.escapeHtml;

  const STATUS_LABEL = { PRESENT: "Present", ABSENT: "Absent" };

  function verdict(pct, held, threshold) {
    if (!held) return { tone: "", text: "No classes held yet" };
    if (pct >= threshold + 5) return { tone: "good", text: "On track" };
    if (pct >= threshold) return { tone: "warn", text: "Close to the " + threshold + "% limit" };
    return { tone: "bad", text: "Below " + threshold + "% \u2014 at risk" };
  }

  async function loadAttendance() {
    const [overview, cfg] = await Promise.all([
      VA.fetchJson("/api/v1/academics/enrollments/me/overview"),
      VA.fetchJson("/api/v1/attendance/config"),
    ]);
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
    const fineLine = cfg.ok ? cfg.body.fine_threshold : 65;
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
        '<div class="course-actions"><button type="button" class="role-btn small-btn secondary-btn att-history">Class by class</button></div>';
      card.querySelector(".att-history").addEventListener("click", function () { showHistory(c); });
      grid.appendChild(card);
    });

    if (atRisk.length) {
      notice.innerHTML = "<strong>Attendance warning:</strong> you are below the " + threshold + "% needed to pass in " + esc(atRisk.join(", ")) +
        ". At the end of term, " + fineLine + "% or less means a fine and a blocked account.";
      notice.hidden = false;
    } else {
      notice.hidden = true;
    }
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