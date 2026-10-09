(function () {
  "use strict";

  // Exam office: /api/v1/exams/schedules ...
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/exams";
  let current = null;

  function err(body, fallback) {
    const box = document.getElementById("ds-error");
    box.textContent = (body && body.error && body.error.message) || fallback || "That did not work.";
    box.hidden = false;
    box.scrollIntoView({ block: "nearest" });
  }
  function clearErr() { document.getElementById("ds-error").hidden = true; }

  async function loadList() {
    const r = await VA.fetchJson(API + "/schedules");
    if (!r.ok) return err(r.body);
    document.getElementById("ds-list").innerHTML = r.body.map(function (s) {
      return "<tr><td>" + esc(s.term_label) + "</td><td>" + esc(s.name) + "</td><td>" + (s.status === "PUBLISHED" ? "Published" : "Draft") +
        '</td><td><button type="button" class="row-action" data-open="' + s.id + '">Open</button></td></tr>';
    }).join("") || '<tr><td colspan="4">No datesheets yet.</td></tr>';
    document.querySelectorAll("[data-open]").forEach(function (b) { b.addEventListener("click", function () { open(b.dataset.open); }); });
  }

  async function loadOfferings(term) {
    const r = await VA.fetchJson("/api/v1/academics/offerings?term_label=" + encodeURIComponent(term));
    const items = r.ok ? (Array.isArray(r.body) ? r.body : (r.body.items || [])) : [];
    document.getElementById("pp-offering").innerHTML = '<option value="">Course\u2026</option>' + items.filter(function (o) { return o.term_label === term; }).map(function (o) {
      return '<option value="' + o.id + '">' + esc((o.course_code || "") + " " + (o.course_title || "") + (o.section_label && o.section_label !== "A" ? " (" + o.section_label + ")" : "")) + "</option>";
    }).join("");
  }

  function render(d) {
    current = d;
    document.getElementById("ds-detail").hidden = false;
    document.getElementById("ds-title").textContent = d.name + " \u2014 " + d.term_label + (d.status === "PUBLISHED" ? " (published)" : " (draft)");
    document.getElementById("pp-list").innerHTML = d.papers.map(function (p) {
      return "<tr><td>" + esc(p.exam_date) + "</td><td>" + esc(p.start_time + "\u2013" + p.end_time) + "</td><td>" + esc(p.course_code + " " + p.course_title) +
        "</td><td>" + esc(p.room) + '</td><td><button type="button" class="row-action" data-del="' + p.id + '">Remove</button></td></tr>';
    }).join("") || '<tr><td colspan="5">No papers yet.</td></tr>';
    document.querySelectorAll("[data-del]").forEach(function (b) {
      b.addEventListener("click", async function () {
        if (!window.confirm("Remove this paper?")) return;
        const r = await VA.fetchJson(API + "/papers/" + b.dataset.del, { method: "DELETE" });
        if (!r.ok) return err(r.body);
        open(current.id);
      });
    });
    document.getElementById("ds-clashes").innerHTML = d.clashes.length
      ? '<p class="dash-alert error"><strong>' + d.clashes.length + " clash(es) to fix before publishing:</strong><br>" +
        d.clashes.map(function (c) { return esc(c.date + ": " + c.papers.join(" / ") + " \u2014 " + c.detail); }).join("<br>") + "</p>"
      : (d.papers.length ? '<p class="success-text">No clashes.</p>' : "");
    document.getElementById("ds-publish").hidden = d.status === "PUBLISHED";
    document.getElementById("ac-row").hidden = d.status !== "PUBLISHED";
  }

  async function open(id) {
    clearErr();
    const r = await VA.fetchJson(API + "/schedules/" + id);
    if (!r.ok) return err(r.body);
    await loadOfferings(r.body.term_label);
    render(r.body);
  }

  document.getElementById("ds-create").addEventListener("click", async function () {
    clearErr();
    const body = { term_label: document.getElementById("ds-term").value, name: document.getElementById("ds-name").value.trim() };
    if (!body.term_label || !body.name) return err(null, "Choose the term and give the datesheet a name.");
    const r = await VA.fetchJson(API + "/schedules", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) return err(r.body);
    document.getElementById("ds-name").value = "";
    await loadList();
    open(r.body.id);
  });

  document.getElementById("pp-add").addEventListener("click", async function () {
    clearErr();
    const body = { course_offering_id: document.getElementById("pp-offering").value, exam_date: document.getElementById("pp-date").value,
                   start_time: document.getElementById("pp-start").value, end_time: document.getElementById("pp-end").value,
                   room: document.getElementById("pp-room").value.trim() };
    if (!body.course_offering_id || !body.exam_date || !body.room) return err(null, "Choose the course, date and room.");
    const r = await VA.fetchJson(API + "/schedules/" + current.id + "/papers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) return err(r.body);
    render(r.body);
  });

  document.getElementById("ds-publish").addEventListener("click", async function () {
    clearErr();
    if (!window.confirm("Publish this datesheet? Students are notified and can download admit cards.")) return;
    const r = await VA.fetchJson(API + "/schedules/" + current.id + "/publish", { method: "POST" });
    if (!r.ok) return err(r.body);
    await loadList();
    open(current.id);
  });

  document.getElementById("ac-print").addEventListener("click", async function () {
    clearErr();
    const roll = document.getElementById("ac-roll").value.trim();
    if (!roll) return err(null, "Enter the roll number.");
    const r = await VA.fetchJson("/api/v1/students?q=" + encodeURIComponent(roll));
    const items = r.ok ? (r.body.items || r.body) : [];
    const st = items.find(function (s) { return s.roll_number === roll; });
    if (!st) return err(null, "No student with roll number " + roll + ".");
    window.open(API + "/schedules/" + current.id + "/admit-cards/" + st.id + "/pdf", "_blank");
  });

  document.addEventListener("vistadmin:ready", loadList);
})();   