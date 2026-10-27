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
    const pub = r.body.filter(function (s) { return s.status === "PUBLISHED"; }).length;
    document.getElementById("ds-summary").innerHTML = r.body.length
      ? '<span class="crs-chip"><b>' + pub + "</b> published</span>" + '<span class="crs-chip"><b>' + (r.body.length - pub) + "</b> draft</span>" : "";
    document.getElementById("ds-list").innerHTML = r.body.map(function (s) {
      return '<tr data-row="' + s.id + '"' + (current && current.id === s.id ? ' class="is-open"' : "") + "><td><strong>" + esc(s.name) + "</strong>" +
        (s.published_at ? '<small class="sd-dim ds-when">Published ' + esc(VA.formatDateLong(String(s.published_at).slice(0, 10))) + "</small>" : "") +
        "</td><td>" + esc(s.term_label) + "</td><td>" + pill(s.status) +
        '</td><td class="crs-actions"><button type="button" class="sd-view" data-open="' + s.id + '">Open</button></td></tr>';
    }).join("") || '<tr><td colspan="4" class="sd-dim">No datesheets yet. Create one above.</td></tr>';
    document.querySelectorAll("[data-open]").forEach(function (b) { b.addEventListener("click", function () { open(b.dataset.open); }); });
  }

  function pill(status) {
    return status === "PUBLISHED" ? '<span class="status-pill active">Published</span>' : '<span class="status-pill pending">Draft</span>';
  }
  function hm(t) { return String(t || "").slice(0, 5); }

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
    const published = d.status === "PUBLISHED";
    document.getElementById("ds-title").textContent = d.name + " \u00b7 " + d.term_label;
    document.getElementById("ds-status").innerHTML = pill(d.status);
    const days = {};
    d.papers.forEach(function (p) { days[p.exam_date] = 1; });
    document.getElementById("ds-detail-summary").innerHTML =
      '<span class="crs-chip"><b>' + d.papers.length + "</b> paper" + (d.papers.length === 1 ? "" : "s") + "</span>" +
      '<span class="crs-chip"><b>' + Object.keys(days).length + "</b> day" + (Object.keys(days).length === 1 ? "" : "s") + "</span>";
    document.querySelectorAll("#ds-list tr[data-row]").forEach(function (tr) { tr.classList.toggle("is-open", tr.dataset.row === d.id); });
    document.getElementById("pp-list").innerHTML = d.papers.map(function (p) {
      return "<tr><td><strong>" + esc(VA.formatDateLong(p.exam_date)) + "</strong></td><td>" + esc(hm(p.start_time) + "\u2013" + hm(p.end_time)) +
        '</td><td><span class="crs-code">' + esc(p.course_code) + "</span> " + esc(p.course_title) +
        "</td><td>" + esc(p.room) + '</td><td class="crs-actions">' +
        '<button type="button" class="sd-view tt-del" data-del="' + p.id + '">Remove</button></td></tr>';
    }).join("") || '<tr><td colspan="5" class="sd-dim">No papers yet. Add the first one above.</td></tr>';
    document.querySelectorAll("[data-del]").forEach(function (b) {
      b.addEventListener("click", async function () {
        if (!(await VA.confirm("Remove this paper?", "Remove"))) return;
        const r = await VA.fetchJson(API + "/papers/" + b.dataset.del, { method: "DELETE" });
        if (!r.ok) return err(r.body);
        VA.toast("Paper removed.");
        open(current.id);
      });
    });
    document.getElementById("ds-clashes").innerHTML = d.clashes.length
      ? '<div class="dash-alert error ds-clash"><strong>' + d.clashes.length + " clash" + (d.clashes.length === 1 ? "" : "es") + " to fix before publishing</strong><ul>" +
        d.clashes.map(function (c) { return "<li><b>" + esc(VA.formatDateLong(c.date)) + "</b>: " + esc(c.papers.join(" / ")) + " \u2014 " + esc(c.detail) + "</li>"; }).join("") + "</ul></div>"
      : (d.papers.length ? '<p class="ds-ok">No clashes: every student, teacher and room has one paper at a time.</p>' : "");
    document.getElementById("ds-publish").hidden = published;
    document.getElementById("ds-publish").disabled = !d.papers.length || d.clashes.length > 0;
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
    VA.toast("Datesheet created.");
    await loadList();
    await open(r.body.id);
    document.getElementById("ds-detail").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  document.getElementById("pp-add").addEventListener("click", async function () {
    clearErr();
    const body = { course_offering_id: document.getElementById("pp-offering").value, exam_date: document.getElementById("pp-date").value,
                   start_time: document.getElementById("pp-start").value, end_time: document.getElementById("pp-end").value,
                   room: document.getElementById("pp-room").value.trim() };
    if (!body.course_offering_id || !body.exam_date || !body.room) return err(null, "Choose the course, date and room.");
    const r = await VA.fetchJson(API + "/schedules/" + current.id + "/papers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) return err(r.body);
    VA.toast("Paper added.");
    render(r.body);
  });

  document.getElementById("ds-publish").addEventListener("click", async function () {
    clearErr();
    if (!(await VA.confirm("Publish this datesheet? Students are notified and can download admit cards.", "Publish"))) return;
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