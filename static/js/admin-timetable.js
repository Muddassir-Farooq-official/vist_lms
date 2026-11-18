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

  const KIND = { CLASS: "Class period", ZERO: "Zero period / Assembly", BREAK: "Break", STUDY: "Library / Self-study" };
  const DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"];
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const t12 = VA.formatTimeShort;
  const changeBox = document.getElementById("period-change");
  function daysText(days) {
    return days && days.length ? days.map(function (d) { return d.charAt(0) + d.slice(1, 3).toLowerCase(); }).join(", ") : "Every day";
  }
  function dayBoxes(selected, name) {
    return DAYS.map(function (d) {
      return '<label><input type="checkbox" name="' + name + '" value="' + d + '"' + (selected.indexOf(d) >= 0 ? " checked" : "") + " /> " +
        d.charAt(0) + d.slice(1, 3).toLowerCase() + "</label>";
    }).join("") + '<span class="sd-dim">None ticked = every day</span>';
  }

  function openChange(period) {
    const today = new Date();
    const iso = today.getFullYear() + "-" + String(today.getMonth() + 1).padStart(2, "0") + "-" + String(today.getDate()).padStart(2, "0");
    changeBox.hidden = false;
    changeBox.innerHTML = "<h3 class=\"tt-sub\" style=\"margin-top:0\">Change Period " + esc(period.period_number) + " (" + esc(t12(period.start_time)) + "–" + esc(t12(period.end_time)) + ")</h3>" +
      '<form class="ar-form" id="pc-form">' +
      '<label>Starts<input type="time" id="pc-start" class="text-input" required value="' + esc(period.start_time.slice(0, 5)) + '" /></label>' +
      '<label>Ends<input type="time" id="pc-end" class="text-input" required value="' + esc(period.end_time.slice(0, 5)) + '" /></label>' +
      '<label>Type<select id="pc-kind" class="select-input">' + Object.keys(KIND).map(function (k) {
        return '<option value="' + k + '"' + (k === period.kind ? " selected" : "") + ">" + KIND[k] + "</option>"; }).join("") + "</select></label>" +
      '<label>Name<input type="text" id="pc-label" class="text-input" maxlength="40" value="' + esc(period.label || "") + '" /></label>' +
      '<label>Classes move from<input type="date" id="pc-from" class="text-input" value="' + iso + '" /></label>' +
      '<label class="pc-shift"><span><input type="checkbox" id="pc-shift" checked /> Move the later periods by the same minutes (keeps breaks the same length)</span></label>' +
      '<div class="pc-days">' + dayBoxes(period.days || [], "pc-day") + "</div>" +
      '<div class="att-go"><button type="submit" class="role-btn">Preview</button> <button type="button" class="row-action" id="pc-cancel">Cancel</button></div></form>' +
      '<div id="pc-report" class="pc-report"></div>';
    changeBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
    document.getElementById("pc-cancel").addEventListener("click", function () { changeBox.hidden = true; changeBox.innerHTML = ""; });
    const payload = function (apply) {
      return {
        start_time: document.getElementById("pc-start").value, end_time: document.getElementById("pc-end").value,
        kind: document.getElementById("pc-kind").value, label: document.getElementById("pc-label").value,
        days: Array.from(changeBox.querySelectorAll('input[name="pc-day"]:checked')).map(function (i) { return i.value; }),
        from_date: document.getElementById("pc-from").value || null, shift_later: document.getElementById("pc-shift").checked, apply: apply,
      };
    };
    const send = function (apply) {
      return VA.fetchJson("/api/v1/academics/periods/" + period.id + "/change", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload(apply)) });
    };
    document.getElementById("pc-form").addEventListener("submit", async function (e) {
      e.preventDefault();
      const out = document.getElementById("pc-report");
      const r = await send(false);
      if (!r.ok) { out.innerHTML = '<p class="dash-alert error">' + esc(extractErrorMessage(r.body, "This change is not possible.")) + "</p>"; return; }
      const d = r.body;
      out.innerHTML = (d.changes.length ? "<strong>These periods move:</strong><ul>" + d.changes.map(function (c) {
          return "<li>Period " + esc(c.period_number) + (c.label ? " (" + esc(c.label) + ")" : "") + ": " + esc(c.from) + " → <strong>" + esc(c.to) + "</strong>" +
            (c.slots ? " · " + c.slots + " weekly class(es), " + c.classes + " dated class(es)" : "") + "</li>"; }).join("") + "</ul>"
          : "<p>No times change.</p>") +
        "<p>Classes before " + esc(VA.formatDateLong ? VA.formatDateLong(d.from_date) : d.from_date) + " stay as they were." +
        (d.kept_with_attendance ? " " + d.kept_with_attendance + " class(es) with attendance already taken keep their recorded times." : "") +
        (d.day_ends ? " The last period will end at <strong>" + esc(d.day_ends) + "</strong>." : "") + "</p>" +
        (d.warnings.length ? '<p class="dash-alert">' + d.warnings.map(esc).join("<br>") + "</p>" : "") +
        '<div class="att-go"><button type="button" class="role-btn" id="pc-apply">Save this change</button></div>';
      document.getElementById("pc-apply").addEventListener("click", async function () {
        this.disabled = true;
        const res = await send(true);
        if (!res.ok) { out.innerHTML = '<p class="dash-alert error">' + esc(extractErrorMessage(res.body, "Could not save.")) + "</p>"; return; }
        VA.toast("Period changed" + (res.body.classes ? "; " + res.body.classes + " class(es) moved." : "."));
        changeBox.hidden = true; changeBox.innerHTML = "";
        loadPeriods();
        if (typeof loadInstituteTimetable === "function") loadInstituteTimetable();
      });
    });
  }

  async function loadPeriods() {
    const result = await VA.fetchJson("/api/v1/academics/periods");
    if (!result.ok) return;

    periodsTbody.innerHTML = "";
    periodsEmpty.hidden = result.body.length > 0;
    const numInput = document.getElementById("period-number");
    if (numInput && !numInput.value) {
      numInput.value = result.body.reduce(function (m, p) { return Math.max(m, p.period_number); }, 0) + 1;
    }
    result.body.slice().sort(function (a, b) { return a.start_time.localeCompare(b.start_time) || a.period_number - b.period_number; }).forEach(function (period) {
      const tr = document.createElement("tr");
      const mins = function (t) { return parseInt(t.slice(0, 2), 10) * 60 + parseInt(t.slice(3, 5), 10); };
      const len = mins(period.end_time) - mins(period.start_time);
      tr.innerHTML =
        "<td><strong>Period " + esc(period.period_number) + "</strong>" + (period.label ? '<br><span class="sd-dim">' + esc(period.label) + "</span>" : "") + "</td>" +
        '<td><span class="pc-kind ' + esc(period.kind) + '">' + esc(KIND[period.kind] || period.kind) + "</span></td>" +
        "<td>" + esc(t12(period.start_time)) + "–" + esc(t12(period.end_time)) + "</td>" +
        '<td class="sd-dim">' + (len > 0 ? len + " min" : "—") + "</td>" +
        "<td>" + esc(daysText(period.days)) + "</td>" +
        '<td class="crs-actions"><button type="button" class="row-action period-change">Change…</button> ' +
        '<button type="button" class="sd-view tt-del period-delete" data-id="' + period.id + '">Delete</button></td>';
      tr.querySelector(".period-change").addEventListener("click", function () { openChange(period); });
      tr.querySelector(".period-delete").addEventListener("click", async function () {
        if (!(await VA.confirm("Delete Period " + period.period_number + "? Slots already created from it keep their times, but it will no longer show as an option. To give it new times, use Change… instead.", "Delete"))) return;
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
        kind: document.getElementById("period-kind").value,
        label: document.getElementById("period-label").value.trim() || null,
        days: Array.from(document.querySelectorAll("#period-days input:checked")).map(function (i) { return i.value; }),
      }),
    });

    addPeriodSubmit.disabled = false;

    if (!result.ok) {
      addPeriodError.textContent = extractErrorMessage(result.body, "Could not add this period.");
      addPeriodError.hidden = false;
      return;
    }

    addPeriodForm.reset();
    VA.toast("Period added.");
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
      const hay = [slot.room, slot.course_code, slot.course_title, slot.teacher_name].join(" ").toLowerCase();
      if (roomValue && hay.indexOf(roomValue) === -1) return false;
      return true;
    });

    instituteTimetableTbody.innerHTML = "";
    instituteTimetableEmpty.hidden = filtered.length > 0;
    instituteTimetableTbody.closest("table").hidden = filtered.length === 0;
    const countEl = document.getElementById("institute-timetable-count");
    if (countEl) countEl.textContent = filtered.length + " of " + instituteTimetableCache.length + " slot" + (instituteTimetableCache.length === 1 ? "" : "s");

    filtered.forEach(function (slot) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + dayLabelForFilter(slot.day_of_week) + "</td>" +
        "<td>" + VA.formatTimeShort(slot.start_time) + "\u2013" + VA.formatTimeShort(slot.end_time) + "</td>" +
        '<td><span class="crs-code">' + VA.escapeHtml(slot.course_code) + "</span> " + VA.escapeHtml(slot.course_title) + "</td>" +
        "<td>" + VA.escapeHtml(slot.term_label) + "</td>" +
        "<td>" + (slot.teacher_name ? VA.escapeHtml(slot.teacher_name) : '<span class="sd-dim">Unassigned</span>') + "</td>" +
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