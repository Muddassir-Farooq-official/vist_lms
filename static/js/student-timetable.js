(function () {
  "use strict";

  const VA = window.VistAdmin;
  const SU = window.StudentUI;

  const termSelect = document.getElementById("term-label-input");
  const grid = document.getElementById("upcoming-grid");
  const empty = document.getElementById("my-schedule-empty");
  let scheduleCache = [];

  function render() {
    const termFilter = termSelect.value;
    const upcoming = SU.fromToday(scheduleCache).filter(function (e) { return !termFilter || e.term_label === termFilter; });
    grid.innerHTML = "";
    empty.hidden = upcoming.length > 0;
    upcoming.slice(0, 60).forEach(function (e, i) { grid.appendChild(SU.classCard(e, i === 0)); });
  }

  async function loadMySchedule() {
    const result = await VA.fetchJson("/api/v1/academics/enrollments/me/schedule");
    grid.innerHTML = "";
    if (!result.ok) {
      grid.innerHTML = '<p class="home-empty">' + VA.escapeHtml((result.body && result.body.error && result.body.error.message) || "Could not load your classes.") + "</p>";
      return;
    }
    scheduleCache = result.body;
    const terms = Array.from(new Set(scheduleCache.map(function (e) { return e.term_label; })));
    const previous = termSelect.value;
    termSelect.innerHTML = '<option value="">All terms</option>';
    terms.forEach(function (t) {
      const opt = document.createElement("option");
      opt.value = t;
      opt.textContent = t;
      termSelect.appendChild(opt);
    });
    if (previous) termSelect.value = previous;
    render();
  }

  termSelect.addEventListener("change", render);
  document.addEventListener("vistadmin:ready", loadMySchedule);
})();