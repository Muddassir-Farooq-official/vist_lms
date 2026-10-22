(function () {
  "use strict";

  // Shared by the teacher and admin attendance pages.
  // Attendance has exactly two states: Present or Absent.
  function esc(v) {
    const d = document.createElement("div");
    d.textContent = v == null ? "" : String(v);
    return d.innerHTML;
  }

  // students: [{student_id, student_name, roll_number}], existing: {student_id: "PRESENT"|"ABSENT"}
  // A student with no record yet defaults to PRESENT (the common case: the
  // teacher only has to change the exceptions).
  function renderRoster(tbody, students, existing) {
    tbody.innerHTML = "";
    students.forEach(function (s) {
      const current = (existing && existing[s.student_id]) || "PRESENT";
      const name = "att-" + s.student_id;
      const tr = document.createElement("tr");
      tr.innerHTML =
        '<td data-label="Student"><strong>' + esc(s.student_name) + "</strong>" +
        '<span class="roll-sub">' + esc(s.roll_number || "\u2014") + (current === "LEAVE" ? " \u00b7 on approved leave (stays so unless you mark Present)" : "") + "</span></td>" +
        '<td data-label="Attendance"><div class="seg" role="radiogroup" aria-label="Attendance for ' + esc(s.student_name) + '">' +
        '<label class="seg-opt seg-present"><input type="radio" class="att-radio" name="' + name + '" value="PRESENT" data-student-id="' + s.student_id + '"' +
        (current === "PRESENT" ? " checked" : "") + ' /><span>Present</span></label>' +
        '<label class="seg-opt seg-absent"><input type="radio" class="att-radio" name="' + name + '" value="ABSENT" data-student-id="' + s.student_id + '"' +
        (current === "ABSENT" ? " checked" : "") + ' /><span>Absent</span></label></div></td>';
      tbody.appendChild(tr);
    });
  }

  // Only the PRESENT ids are sent; everyone else enrolled is recorded as absent.
  function collect(tbody) {
    const out = { present_student_ids: [] };
    tbody.querySelectorAll('.att-radio[value="PRESENT"]:checked').forEach(function (r) {
      out.present_student_ids.push(r.getAttribute("data-student-id"));
    });
    return out;
  }

  function markAll(tbody, status) {
    tbody.querySelectorAll('.att-radio[value="' + status + '"]').forEach(function (r) { r.checked = true; });
  }

  // Server-authoritative clock. Class times are institute wall-clock values, so
  // compare them with the SERVER's institute time, not the browser's timezone.
  let clock = null;
  async function loadClock() {
    if (clock) return clock;
    const result = await window.VistAdmin.fetchJson("/api/v1/attendance/config");
    const cfg = result.ok
      ? result.body
      : { marking_window_minutes: 15, server_now: new Date().toISOString().slice(0, 19), eligibility_threshold: 75, fine_threshold: 65, drop_threshold: 40 };
    const base = new Date(cfg.server_now).getTime();
    const t0 = performance.now();
    clock = {
      config: cfg,
      now: function () { return new Date(base + (performance.now() - t0)); },
      start: function (session) { return new Date(session.session_date + "T" + session.start_time); },
    };
    return clock;
  }

  window.AttendanceUI = { renderRoster: renderRoster, collect: collect, markAll: markAll, loadClock: loadClock, esc: esc };
})();