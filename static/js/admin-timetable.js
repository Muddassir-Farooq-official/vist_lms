(function () {
  "use strict";

  const VA = window.VistAdmin;

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  // =========================================================================
  // Periods (institute-wide daily period structure)
  // =========================================================================
  const periodsTbody = document.getElementById("periods-tbody");
  const periodsEmpty = document.getElementById("periods-empty");
  const addPeriodForm = document.getElementById("add-period-form");
  const addPeriodSubmit = document.getElementById("add-period-submit");
  const addPeriodError = document.getElementById("add-period-error");

  async function loadPeriods() {
    const result = await VA.fetchJson("/api/v1/academics/periods");
    if (!result.ok) return;

    periodsTbody.innerHTML = "";
    periodsEmpty.hidden = result.body.length > 0;
    result.body.forEach(function (period) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>Period " + period.period_number + "</td>" +
        "<td>" + period.start_time.slice(0, 5) + "\u2013" + period.end_time.slice(0, 5) + "</td>" +
        '<td><button type="button" class="danger-btn small-btn period-delete" data-id="' + period.id + '">Delete</button></td>';
      tr.querySelector(".period-delete").addEventListener("click", async function () {
        if (!confirm("Delete Period " + period.period_number + "? Slots already created from it keep their times, but it will no longer show as an option.")) return;
        const result = await VA.fetchJson("/api/v1/academics/periods/" + period.id, { method: "DELETE" });
        if (!result.ok && result.status !== 204) {
          addPeriodError.textContent = extractErrorMessage(result.body, "Could not delete this period.");
          addPeriodError.hidden = false;
          return;
        }
        loadPeriods();
      });
      periodsTbody.appendChild(tr);
    });
  }

  addPeriodForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    addPeriodSubmit.disabled = true;
    addPeriodError.hidden = true;

    const result = await VA.fetchJson("/api/v1/academics/periods", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        period_number: parseInt(document.getElementById("period-number").value, 10),
        start_time: document.getElementById("period-start").value,
        end_time: document.getElementById("period-end").value,
      }),
    });

    addPeriodSubmit.disabled = false;

    if (!result.ok) {
      addPeriodError.textContent = extractErrorMessage(result.body, "Could not add this period.");
      addPeriodError.hidden = false;
      return;
    }

    addPeriodForm.reset();
    loadPeriods();
  });

  // =========================================================================
  // Institute Timetable (read-only overview across every offering)
  // =========================================================================
  const instituteTimetableTbody = document.getElementById("institute-timetable-tbody");
  const instituteTimetableEmpty = document.getElementById("institute-timetable-empty");
  const timetableDayFilter = document.getElementById("timetable-day-filter");
  const timetableRoomFilter = document.getElementById("timetable-room-filter");
  let instituteTimetableCache = [];

  function dayLabelForFilter(dayOfWeek) {
    const s = String(dayOfWeek || "");
    return s.charAt(0) + s.slice(1).toLowerCase();
  }

  function renderInstituteTimetable() {
    const dayValue = timetableDayFilter.value;
    const roomValue = timetableRoomFilter.value.trim().toLowerCase();

    const filtered = instituteTimetableCache.filter(function (slot) {
      if (dayValue && slot.day_of_week !== dayValue) return false;
      if (roomValue && slot.room.toLowerCase().indexOf(roomValue) === -1) return false;
      return true;
    });

    instituteTimetableTbody.innerHTML = "";
    instituteTimetableEmpty.hidden = filtered.length > 0;

    filtered.forEach(function (slot) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + dayLabelForFilter(slot.day_of_week) + "</td>" +
        "<td>" + slot.start_time.slice(0, 5) + "\u2013" + slot.end_time.slice(0, 5) + "</td>" +
        "<td>" + VA.escapeHtml(slot.course_code) + " \u2014 " + VA.escapeHtml(slot.course_title) + "</td>" +
        "<td>" + VA.escapeHtml(slot.term_label) + "</td>" +
        "<td>" + VA.escapeHtml(slot.teacher_name || "\u2014 unassigned") + "</td>" +
        "<td>" + VA.escapeHtml(slot.room) + "</td>";
      instituteTimetableTbody.appendChild(tr);
    });
  }

  async function loadInstituteTimetable() {
    const result = await VA.fetchJson("/api/v1/academics/timetable-slots");
    if (!result.ok) return;
    instituteTimetableCache = result.body;
    renderInstituteTimetable();
  }

  timetableDayFilter.addEventListener("change", renderInstituteTimetable);
  timetableRoomFilter.addEventListener("input", renderInstituteTimetable);

  document.addEventListener("vistadmin:ready", function () {
    loadPeriods();
    loadInstituteTimetable();
  });
})();