(function () {
  "use strict";

  const root = document.getElementById("weekly-timetable");
  if (!root) return;
  const VA = window.VistAdmin;
  const API = "/api/v1/academics/timetable";

  const els = {
    title: document.getElementById("tt-title"),
    pdf: document.getElementById("tt-pdf"),
    term: document.getElementById("tt-term"),
    program: document.getElementById("tt-program"),
    semester: document.getElementById("tt-semester"),
    section: document.getElementById("tt-section"),
    teacher: document.getElementById("tt-teacher"),
    room: document.getElementById("tt-room"),
    reset: document.getElementById("tt-reset"),
    box: document.getElementById("tt-filter-box"),
    count: document.getElementById("tt-filter-count"),
    dayTabs: document.getElementById("tt-daytabs"),
    grid: document.getElementById("tt-grid"),
    sheet: root.querySelector(".tt-sheet"),
    empty: document.getElementById("tt-empty"),
    error: document.getElementById("tt-error"),
  };

  const DAY_LABEL = { MONDAY: "Monday", TUESDAY: "Tuesday", WEDNESDAY: "Wednesday", THURSDAY: "Thursday", FRIDAY: "Friday", SATURDAY: "Saturday", SUNDAY: "Sunday" };
  const DAY_SHORT = { MONDAY: "Mon", TUESDAY: "Tue", WEDNESDAY: "Wed", THURSDAY: "Thu", FRIDAY: "Fri", SATURDAY: "Sat", SUNDAY: "Sun" };
  const JS_DAY = ["SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY"];

  let options = null;
  let activeDay = null;
  let roomTimer = null;
  let lastData = null;

  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };

  // One stable colour per course, so a subject looks the same everywhere it appears.
  function hue(code) {
    let h = 0;
    for (let i = 0; i < code.length; i++) h = (h * 31 + code.charCodeAt(i) * (i + 3)) % 360;
    return h;
  }

  function params() {
    const p = new URLSearchParams();
    if (els.term.value) p.set("term_label", els.term.value);
    if (els.program.value) p.set("program_id", els.program.value);
    if (els.semester.value) p.set("semester_number", els.semester.value);
    if (els.section.value) p.set("section_label", els.section.value);
    if (els.teacher.value) p.set("teacher_id", els.teacher.value);
    if (els.room.value.trim()) p.set("room", els.room.value.trim());
    return p;
  }

  function fillSelect(select, items, allLabel, valueOf, textOf) {
    const keep = select.value;
    select.innerHTML = "";
    if (allLabel) {
      const o = document.createElement("option");
      o.value = "";
      o.textContent = allLabel;
      select.appendChild(o);
    }
    items.forEach(function (item) {
      const o = document.createElement("option");
      o.value = valueOf(item);
      o.textContent = textOf(item);
      select.appendChild(o);
    });
    if (keep && Array.prototype.some.call(select.options, function (o) { return o.value === keep; })) select.value = keep;
  }

  // Semester choices, each with how many classes it has for the OTHER filters currently chosen,
  // so nobody has to guess which semester a course sits in.
  function fillSemesters(counts) {
    let max = 8;
    const program = options.programs.find(function (p) { return p.id === els.program.value; });
    if (program) max = program.total_semesters;
    else if (options.programs.length) max = Math.max.apply(null, options.programs.map(function (p) { return p.total_semesters; }));
    if (counts) counts.forEach(function (c) { if (c.semester > max) max = c.semester; });
    const byNumber = {};
    (counts || []).forEach(function (c) { byNumber[c.semester] = c.courses; });
    const n = [];
    for (let i = 1; i <= max; i++) n.push(i);
    fillSelect(els.semester, n, "All semesters", String, function (i) {
      if (!counts) return "Semester " + i;
      const c = byNumber[i];
      return "Semester " + i + (c ? " \u00b7 " + c + (c === 1 ? " course" : " courses") : " \u00b7 none");
    });
  }

  // Phones: the filters fold away behind a "Filters" button so the sheet is the first thing you see
  // (kept open when there is only one filter, e.g. a student's term). Wide screens always show them.
  let userToggled = false;
  function syncFilterBox() {
    if (window.innerWidth >= 720) { els.box.open = true; return; }
    if (!userToggled) els.box.open = options ? options.allowed.length <= 2 : true;
  }
  els.box.addEventListener("toggle", function () { if (window.innerWidth < 720) userToggled = true; });
  window.addEventListener("resize", syncFilterBox);

  function activeFilterCount() {
    return [els.program.value, els.semester.value, els.section.value, els.teacher.value, els.room.value.trim()].filter(Boolean).length;
  }

  function anyFilterActive() {
    return !!(els.program.value || els.semester.value || els.section.value || els.teacher.value || els.room.value.trim());
  }

  function renderDayTabs(days) {
    els.dayTabs.innerHTML = "";
    days.forEach(function (d) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "tt-daytab" + (d === activeDay ? " active" : "");
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", d === activeDay ? "true" : "false");
      b.textContent = DAY_SHORT[d];
      b.addEventListener("click", function () { activeDay = d; applyActiveDay(); });
      els.dayTabs.appendChild(b);
    });
  }

  // On a phone only one day's column is shown next to the times; on wider screens all days show.
  function applyActiveDay() {
    Array.prototype.forEach.call(els.grid.querySelectorAll("[data-day]"), function (cell) {
      cell.classList.toggle("tt-active-day", cell.getAttribute("data-day") === activeDay);
    });
    // On a phone only the chosen day is shown, so hide the time rows that have no class on that day.
    let anyClass = false;
    Array.prototype.forEach.call(els.grid.tBodies[0].rows, function (row) {
      const cell = row.querySelector('td[data-day="' + activeDay + '"]');
      const has = !!(cell && cell.querySelector(".tt-entry"));
      anyClass = anyClass || has;
      row.classList.toggle("tt-row-empty-day", !has);
    });
    const dayMsg = document.getElementById("tt-day-empty");
    if (dayMsg) {
      dayMsg.textContent = lastData && lastData.entries_count && !anyClass ? "No classes on " + DAY_LABEL[activeDay] + "." : "";
      dayMsg.hidden = !dayMsg.textContent;
    }
    Array.prototype.forEach.call(els.dayTabs.children, function (b, i) {
      const isActive = lastData && lastData.days[i] === activeDay;
      b.classList.toggle("active", !!isActive);
      b.setAttribute("aria-selected", isActive ? "true" : "false");
    });
  }

  function entryHtml(e, scope) {
    const section = e.section_label && e.section_label !== "A" ? " \u00b7 Sec " + esc(e.section_label) : "";
    // Where this course sits in the curriculum (what the Semester filter matches), e.g. "BSN S3".
    const placed = (e.placements || []).map(function (p) { return p.program_code + " S" + p.semester; });
    const programs = scope !== "student" && placed.length ? '<span class="tt-chip">' + esc(placed.join(" \u00b7 ")) + "</span>" : "";
    const who = [e.room ? "Room " + esc(e.room) : "", scope !== "teacher" && e.teacher_name ? esc(e.teacher_name) : ""].filter(Boolean).join(" \u00b7 ");
    return '<div class="tt-entry" style="--h:' + hue(e.course_code) + '">' +
      '<div class="tt-code">' + esc(e.course_code) + section + programs + "</div>" +
      '<div class="tt-name">' + esc(e.course_title) + "</div>" +
      (who ? '<div class="tt-meta">' + who + "</div>" : "") + "</div>";
  }

  function render(data) {
    lastData = data;
    els.title.textContent = data.title;
    const noteEl = document.getElementById("tt-note");
    if (noteEl) { noteEl.textContent = data.note || ""; noteEl.hidden = !data.note; }
    const todayName = JS_DAY[new Date().getDay()];
    if (!activeDay || data.days.indexOf(activeDay) === -1) activeDay = data.days.indexOf(todayName) !== -1 ? todayName : data.days[0];

    const byCell = {};
    data.cells.forEach(function (c) { byCell[c.day + "|" + c.row_key] = c.entries; });

    let head = '<tr><th class="tt-time-h" scope="col">Time</th>';
    data.days.forEach(function (d) {
      head += '<th scope="col" data-day="' + d + '" class="' + (d === todayName ? "tt-today" : "") + '"><span class="tt-day-full">' + DAY_LABEL[d] + '</span></th>';
    });
    els.grid.tHead.innerHTML = head + "</tr>";

    let body = "";
    data.rows.forEach(function (row) {
      body += '<tr><th scope="row" class="tt-time">' + esc(row.label) +
        (row.period_number ? '<small>Period ' + row.period_number + "</small>" : "") + "</th>";
      data.days.forEach(function (d) {
        const entries = byCell[d + "|" + row.key] || [];
        body += '<td data-day="' + d + '" class="' + (d === todayName ? "tt-today" : "") + '">' +
          entries.map(function (e) { return entryHtml(e, data.scope); }).join("") + "</td>";
      });
      body += "</tr>";
    });
    els.grid.tBodies[0].innerHTML = body;

    renderDayTabs(data.days);
    applyActiveDay();

    showEmpty(data);
    els.reset.hidden = !anyFilterActive();
    const n = activeFilterCount();
    els.count.textContent = n ? "\u00b7 " + n + " applied" : "";
  }

  // When a chosen semester has no classes, say where this class's classes ARE, with one-tap buttons.
  function showEmpty(data) {
    els.empty.innerHTML = "";
    if (!data.rows.length) {
      els.empty.textContent = "No timetable has been set up yet.";
      els.empty.hidden = false;
      return;
    }
    if (data.entries_count) { els.empty.hidden = true; return; }
    const chosen = els.semester.value;
    const others = (data.semester_counts || []).filter(function (c) { return String(c.semester) !== chosen; });
    if (chosen && others.length) {
      const scope = els.program.selectedOptions[0] && els.program.value ? els.program.selectedOptions[0].textContent.split(" \u2014 ")[0] : "these filters";
      const p = document.createElement("span");
      p.textContent = "Nothing is scheduled for Semester " + chosen + ". Classes for " + scope + " are in: ";
      els.empty.appendChild(p);
      others.forEach(function (c) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "tt-jump";
        b.textContent = "Semester " + c.semester + " (" + c.courses + ")";
        b.addEventListener("click", function () { els.semester.value = String(c.semester); load(); });
        els.empty.appendChild(b);
      });
    } else {
      els.empty.textContent = anyFilterActive() ? "No classes match these filters." : "No classes are scheduled for this term yet.";
    }
    els.empty.hidden = false;
  }

  function showFailure(message) {
    els.sheet.classList.remove("is-loading");
    els.sheet.removeAttribute("aria-busy");
    els.error.innerHTML = "";
    els.error.appendChild(document.createTextNode(message + " "));
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "role-btn small-btn";
    retry.textContent = "Try again";
    retry.addEventListener("click", function () { if (options) load(); else init(); });
    els.error.appendChild(retry);
    els.error.hidden = false;
  }

  async function load() {
    els.error.hidden = true;
    els.sheet.classList.add("is-loading");
    els.sheet.setAttribute("aria-busy", "true");
    const p = params();
    els.pdf.href = API + "/weekly.pdf" + (p.toString() ? "?" + p.toString() : "");
    // Administrators: the whole term in one PDF, one page per class or per teacher.
    const bookClass = document.getElementById("tt-book-class"), bookTeacher = document.getElementById("tt-book-teacher");
    if (bookClass && options && options.allowed.indexOf("teacher") !== -1) {
      const t = els.term.value ? "&term_label=" + encodeURIComponent(els.term.value) : "";
      bookClass.href = API + "/book.pdf?by=class" + t; bookTeacher.href = API + "/book.pdf?by=teacher" + t;
      bookClass.hidden = bookTeacher.hidden = false;
    }
    // Never wait forever: after 25 seconds say so, and offer to try again.
    const controller = new AbortController();
    const timer = setTimeout(function () { controller.abort(); }, 25000);
    const result = await VA.fetchJson(API + "/weekly" + (p.toString() ? "?" + p.toString() : ""), { signal: controller.signal });
    clearTimeout(timer);
    if (!result.ok) {
      showFailure((result.body && result.body.error && result.body.error.message) || "Could not load the timetable.");
      return;
    }
    els.sheet.classList.remove("is-loading");
    els.sheet.removeAttribute("aria-busy");
    render(result.body);
    fillSemesters(result.body.semester_counts);
  }

  async function init() {
    const result = await VA.fetchJson(API + "/filters");
    if (!result.ok) {
      els.title.textContent = "";
      showFailure((result.body && result.body.error && result.body.error.message) || "Could not load the timetable.");
      return;
    }
    options = result.body;
    syncFilterBox();
    options.allowed.forEach(function (name) {
      const wrap = root.querySelector('[data-filter="' + name + '"]');
      if (wrap) wrap.hidden = false;
    });
    fillSelect(els.term, options.terms, "", function (t) { return t.label; }, function (t) { return t.label + (t.is_current ? " (now)" : ""); });
    if (options.default_term) els.term.value = options.default_term;
    if (!options.terms.length) {
      els.term.innerHTML = '<option value="">No terms yet</option>';
    }
    fillSelect(els.program, options.programs, "All classes", function (p) { return p.id; }, function (p) { return p.code + " \u2014 " + p.name; });
    fillSelect(els.section, options.sections, "All sections", String, function (s) { return "Section " + s; });
    fillSelect(els.teacher, options.teachers, "All teachers", function (t) { return t.id; }, function (t) { return t.name; });
    fillSemesters();
    load();
  }

  [els.term, els.semester, els.section, els.teacher].forEach(function (el) { el.addEventListener("change", load); });
  els.program.addEventListener("change", function () { fillSemesters(); load(); });
  els.room.addEventListener("input", function () { clearTimeout(roomTimer); roomTimer = setTimeout(load, 350); });
  els.reset.addEventListener("click", function () {
    els.program.value = ""; els.semester.value = ""; els.section.value = ""; els.teacher.value = ""; els.room.value = "";
    fillSemesters();
    load();
  });

  document.addEventListener("vistadmin:ready", init);
})();