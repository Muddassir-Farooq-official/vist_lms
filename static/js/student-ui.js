/* Pieces shared by the student pages: avatars, class cards, the attendance ring, status pills. */
/* Despite the file name, this is used by the student AND faculty portals (window.PortalUI is the same object). */
window.StudentUI = (function () {
  "use strict";
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const CIRC = 2 * Math.PI * 52;

  function initials(name) {
    const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
    return parts.length ? (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() : "?";
  }
  function hue(name) {
    let h = 0;
    for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
    return h;
  }
  function avatar(name) {
    return '<span class="sh-avatar" style="--h:' + hue(name) + '">' + esc(initials(name)) + "</span>";
  }
  function teacherLine(name) {
    return name ? '<span class="sh-teacher">' + avatar(name) + esc(name) + "</span>" : '<span class="sh-teacher muted">Teacher not assigned yet</span>';
  }

  function isoToday() {
    const d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function dayLabel(iso) {
    const t = new Date();
    t.setDate(t.getDate() + 1);
    const tomorrow = t.getFullYear() + "-" + String(t.getMonth() + 1).padStart(2, "0") + "-" + String(t.getDate()).padStart(2, "0");
    if (iso === isoToday()) return "Today";
    if (iso === tomorrow) return "Tomorrow";
    return new Date(iso + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  }

  // One rule everywhere: good = comfortably above the pass mark, warn = within 5 points of it, bad = below it.
  function tone(pct, threshold) {
    return pct >= threshold + 5 ? "good" : pct >= threshold ? "warn" : "bad";
  }

  function ring(pct, threshold, label, size) {
    return '<svg class="sh-ring ' + tone(pct, threshold) + '" style="width:' + (size || 128) + "px;height:" + (size || 128) + 'px" viewBox="0 0 120 120" role="img" aria-label="' + esc(label || "Attendance") + " " + pct + ' percent">' +
      '<circle class="ring-bg" cx="60" cy="60" r="52"/><circle class="ring-fg" cx="60" cy="60" r="52" transform="rotate(-90 60 60)" stroke-dasharray="' +
      (CIRC * Math.min(pct, 100) / 100).toFixed(2) + " " + CIRC.toFixed(2) + '"/><text x="60" y="68" text-anchor="middle" class="ring-text">' + pct + "%</text></svg>";
  }

  function pill(text, cls) {
    return '<span class="status-pill ' + (cls || String(text).toLowerCase()) + '">' + esc(text) + "</span>";
  }

  // One upcoming class. "Add to Calendar" downloads a standard calendar file built by the server.
  function classCard(e, isNext) {
    const label = dayLabel(e.session_date);
    const card = document.createElement("article");
    card.className = "sh-class" + (isNext ? " is-next" : "");
    card.innerHTML =
      '<div class="sh-class-top"><span class="pill' + (label === "Today" ? " pill-now" : "") + '">' + esc(label) + "</span>" +
      (isNext ? '<span class="sh-next">Next up</span>' : "") + "</div>" +
      "<h3>" + esc(e.course_code) + " \u2014 " + esc(e.course_title) + "</h3>" +
      '<p class="sh-when">' + esc(VA.formatDateLong(e.session_date)) + " \u00b7 " + esc(VA.formatTimeShort(e.start_time)) + "\u2013" + esc(VA.formatTimeShort(e.end_time)) + "</p>" +
      '<p class="sh-where">Room ' + esc(e.room) + (e.section_label && e.section_label !== "A" ? " \u00b7 Section " + esc(e.section_label) : "") + "</p>" +
      (e.title ? '<p class="sh-topic">Topic: ' + esc(e.title) + "</p>" : "") +
      '<div class="sh-class-foot">' + (e.teacher_name ? teacherLine(e.teacher_name) : "") + '<button type="button" class="sh-cal">Add to Calendar</button></div>';
    card.querySelector(".sh-cal").addEventListener("click", function () {
      VA.downloadFile("/api/v1/academics/class-sessions/" + e.id + "/calendar.ics", e.course_code + "-" + e.session_date + ".ics", "calendar file");
    });
    return card;
  }

  function fromToday(entries) {
    const today = isoToday();
    return entries.filter(function (e) { return e.session_date >= today; })
      .sort(function (a, b) { return (a.session_date + a.start_time).localeCompare(b.session_date + b.start_time); });
  }

  function money(n) {
    return "Rs. " + Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
  }

  // A class on the TEACHER's side: how many students, whether a topic is set, and the next thing to do.
  function teacherClassCard(e, isNext, overview) {
    const label = dayLabel(e.session_date);
    const started = e.session_date <= isoToday();
    const card = document.createElement("article");
    card.className = "sh-class" + (isNext ? " is-next" : "");
    const students = overview ? '<p class="sh-where">' + esc(overview.enrolled_count) + " students enrolled</p>" : "";
    card.innerHTML =
      '<div class="sh-class-top"><span class="pill' + (label === "Today" ? " pill-now" : "") + '">' + esc(label) + "</span>" +
      (isNext ? '<span class="sh-next">Next up</span>' : "") + "</div>" +
      "<h3>" + esc(e.course_code) + " \u2014 " + esc(e.course_title) + "</h3>" +
      '<p class="sh-when">' + esc(VA.formatDateLong(e.session_date)) + " \u00b7 " + esc(VA.formatTimeShort(e.start_time)) + "\u2013" + esc(VA.formatTimeShort(e.end_time)) + "</p>" +
      '<p class="sh-where">Room ' + esc(e.room) + (e.section_label && e.section_label !== "A" ? " \u00b7 Section " + esc(e.section_label) : "") + "</p>" + students +
      (e.title ? '<p class="sh-topic">Topic: ' + esc(e.title) + "</p>" : '<p class="sh-notopic">No topic added yet</p>') +
      '<div class="sh-class-foot"><a class="role-btn small-btn" href="' +
      (started ? "/dashboard/faculty/attendance?offering=" + e.course_offering_id + "&session=" + e.id : "/dashboard/faculty/courses?offering=" + e.course_offering_id) + '">' +
      (started ? "Take attendance" : (e.title ? "Edit lesson plan" : "Add topic & slides")) + '</a><button type="button" class="sh-cal">Add to Calendar</button></div>';
    card.querySelector(".sh-cal").addEventListener("click", function () {
      VA.downloadFile("/api/v1/academics/class-sessions/" + e.id + "/calendar.ics", e.course_code + "-" + e.session_date + ".ics", "calendar file");
    });
    return card;
  }

  return { teacherClassCard: teacherClassCard, initials: initials, hue: hue, avatar: avatar, teacherLine: teacherLine, isoToday: isoToday, dayLabel: dayLabel, tone: tone, ring: ring,
           pill: pill, classCard: classCard, fromToday: fromToday, money: money };
})();
window.PortalUI = window.StudentUI;