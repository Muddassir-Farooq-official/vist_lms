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
      const duty = p.on_duty || [];
      const short = duty.length < (p.needed || 1);
      const names = duty.length ? '<span class="dt-names">' + duty.map(function (a) {
        return '<span class="dt-name' + (a.role === "IN_CHARGE" ? " is-charge" : "") + '"' + (a.role === "IN_CHARGE" ? ' title="In charge"' : "") + ">" + esc(a.name) + "</span>";
      }).join("") + "</span>" : '<span class="sd-dim">Nobody yet</span>';
      const need = '<small class="dt-need' + (short ? " is-short" : "") + '">' + duty.length + " of " + (p.needed || 1) + " needed \u00b7 " + (p.students || 0) + " student" + (p.students === 1 ? "" : "s") + "</small>";
      return "<tr><td><strong>" + esc(VA.formatDateLong(p.exam_date)) + "</strong></td><td>" + esc(hm(p.start_time) + "\u2013" + hm(p.end_time)) +
        '</td><td><span class="crs-code">' + esc(p.course_code) + "</span> " + esc(p.course_title) +
        "</td><td>" + esc(p.room) + "</td><td>" + names + need + '</td><td class="crs-actions"><div class="dt-actions">' +
        (p.started ? "" : '<button type="button" class="sd-view" data-duty="' + p.id + '">Duty</button>') +
        (published ? '<button type="button" class="sd-view" data-roll="' + p.id + '">Exam roll</button>' : "") +
        (p.started ? "" : '<button type="button" class="sd-view tt-del" data-del="' + p.id + '">Remove</button>') + "</div></td></tr>";
    }).join("") || '<tr><td colspan="6" class="sd-dim">No papers yet. Add the first one above.</td></tr>';
    document.querySelectorAll("[data-duty]").forEach(function (b) { b.addEventListener("click", function () { openDuty(b.dataset.duty); }); });
    document.querySelectorAll("[data-roll]").forEach(function (b) {
      b.addEventListener("click", function () {
        document.getElementById("dt-box").hidden = true;
        window.VistExamRoll.open(b.dataset.roll, document.getElementById("er-box"), function () { refresh(); });
      });
    });
    renderCases(d.cases || []);
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

  // ---- exam duty: who is free, how many are needed, one in charge
  async function openDuty(paperId) {
    clearErr();
    const box = document.getElementById("dt-box");
    document.getElementById("er-box").hidden = true;
    const r = await VA.fetchJson(API + "/papers/" + paperId + "/duty");
    if (!r.ok) return err(r.body);
    const d = r.body, p = d.paper;
    let showBusy = false;
    const chosen = new Set(d.assigned.map(function (a) { return a.employee_id; }));
    let charge = (d.assigned.find(function (a) { return a.role === "IN_CHARGE"; }) || {}).employee_id || null;
    box.hidden = false;

    function summary() {
      const n = chosen.size;
      const ok = n >= d.needed;
      return '<p class="dt-summary' + (ok ? " is-ok" : "") + '">' + d.students + " student" + (d.students === 1 ? "" : "s") + " sit this paper, so it needs <b>" + d.needed +
        "</b> teacher" + (d.needed === 1 ? "" : "s") + " (one for every " + d.per_teacher + "). " + (ok ? "Chosen: " + n + "." : "Chosen so far: " + n + ".") + "</p>";
    }
    function row(t) {
      const on = chosen.has(t.employee_id);
      return '<li class="dt-row' + (t.free ? "" : " is-busy") + (on ? " is-on" : "") + '"><input type="checkbox" data-pick="' + t.employee_id + '"' + (on ? " checked" : "") +
        ' aria-label="Put ' + esc(t.name) + ' on duty"><div class="dt-who"><strong>' + esc(t.name) + "</strong><small>" + esc(t.employee_code || "") +
        " \u00b7 " + t.duties_in_datesheet + " dut" + (t.duties_in_datesheet === 1 ? "y" : "ies") + " in this datesheet</small> " +
        (t.free ? '<span class="dt-free">Free</span>' : '<span class="dt-busy">' + esc(t.busy_reason) + "</span>") +
        (t.teaches_this_course ? ' <span class="dt-own">\u00b7 teaches this course</span>' : "") + "</div>" +
        '<label class="dt-charge"><input type="radio" name="dt-charge" value="' + t.employee_id + '"' + (charge === t.employee_id ? " checked" : "") + (on ? "" : " disabled") + "> In charge</label></li>";
    }
    function draw() {
      const list = d.teachers.filter(function (t) { return showBusy || t.free || chosen.has(t.employee_id); });
      const busyCount = d.teachers.filter(function (t) { return !t.free; }).length;
      box.innerHTML = '<div class="dt-head"><h3 class="tt-sub">Exam duty \u00b7 ' + esc(p.label + " " + p.course_title) + '</h3><button type="button" class="sd-view dt-close" id="dt-close">Close</button>' +
        '<p class="sd-dim">' + esc(VA.formatDateLong(p.exam_date) + " \u00b7 " + p.start_time + "\u2013" + p.end_time + " \u00b7 room " + p.room) + "</p></div>" +
        summary() +
        (busyCount ? '<label class="dt-filter"><input type="checkbox" id="dt-busy"' + (showBusy ? " checked" : "") + "> Also show " + busyCount + (busyCount === 1 ? " teacher who is" : " teachers who are") + " busy then</label>" : "") +
        (list.length ? '<ul class="dt-list">' + list.map(row).join("") + "</ul>" : '<p class="sd-dim">No teacher is free at this time. Tick "Also show" to choose a busy one.</p>') +
        '<p id="dt-error" class="dash-alert error" hidden></p>' +
        '<div class="dt-foot">' + (d.suggested.length ? '<button type="button" class="role-btn secondary-btn" id="dt-suggest">Choose ' + d.suggested.length + " suggested</button>" : "") +
        '<button type="button" class="role-btn" id="dt-save">Save duty</button></div>' +
        (d.published ? '<p class="er-note">The teachers are told as soon as you save.</p>' : '<p class="er-note">The teachers are told when the datesheet is published.</p>');
      box.querySelector("#dt-close").addEventListener("click", function () { box.hidden = true; });
      const busyBox = box.querySelector("#dt-busy");
      if (busyBox) busyBox.addEventListener("change", function () { showBusy = busyBox.checked; draw(); });
      box.querySelectorAll("[data-pick]").forEach(function (c) {
        c.addEventListener("change", function () {
          const id = c.getAttribute("data-pick");
          if (c.checked) { chosen.add(id); if (!charge) charge = id; } else { chosen.delete(id); if (charge === id) charge = chosen.size ? Array.from(chosen)[0] : null; }
          draw();
        });
      });
      box.querySelectorAll('input[name="dt-charge"]').forEach(function (rd) { rd.addEventListener("change", function () { charge = rd.value; }); });
      const sug = box.querySelector("#dt-suggest");
      if (sug) sug.addEventListener("click", function () { d.suggested.forEach(function (id) { chosen.add(id); }); if (!charge && chosen.size) charge = Array.from(chosen)[0]; draw(); });
      box.querySelector("#dt-save").addEventListener("click", async function () {
        const b = this; b.disabled = true;
        const res = await VA.fetchJson(API + "/papers/" + paperId + "/duty", { method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ employee_ids: Array.from(chosen), in_charge_id: charge }) });
        b.disabled = false;
        if (!res.ok) { const e = box.querySelector("#dt-error"); e.textContent = (res.body && res.body.error && res.body.error.message) || "Could not save."; e.hidden = false; return; }
        VA.toast("Duty saved.", "success");
        box.hidden = true;
        refresh();
      });
    }
    draw();
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---- unfair means cases of this datesheet
  function renderCases(cases) {
    const holder = document.getElementById("ds-cases");
    if (!cases.length) { holder.innerHTML = ""; return; }
    const open = cases.filter(function (c) { return c.status === "OPEN"; }).length;
    holder.innerHTML = '<h3 class="tt-sub">Unfair means cases' + (open ? " \u00b7 " + open + " waiting for a decision" : "") + '</h3><ul class="dc-list">' + cases.map(function (c) {
      return '<li class="dc-item"><strong>' + esc(c.student + (c.roll_number ? " (" + c.roll_number + ")" : "")) + "</strong> \u00b7 " + esc(c.paper) + " \u00b7 " +
        esc(VA.formatDateLong(c.exam_date)) + " " + (c.status === "OPEN" ? '<span class="status-pill pending">Waiting for decision</span>' : '<span class="status-pill active">Decided</span>') +
        "<p>" + esc(c.note) + "</p>" + (c.decision ? '<p class="sd-dim">Decision: ' + esc(c.decision) + "</p>" : '<button type="button" class="role-btn small-btn" data-decide="' + c.id + '">Record decision</button>') + "</li>";
    }).join("") + "</ul>";
    holder.querySelectorAll("[data-decide]").forEach(function (b) {
      b.addEventListener("click", async function () {
        const text = await VA.ask({ title: "Decision on the case", label: "Decision (kept on record), e.g. paper cancelled, warning issued", input: "textarea", ok: "Save decision", min: 5 });
        if (!text) return;
        const r = await VA.fetchJson(API + "/cases/" + b.dataset.decide + "/decide", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision: text }) });
        if (!r.ok) return err(r.body);
        VA.toast("Decision saved.", "success");
        refresh();
      });
    });
  }

  async function refresh() {
    if (!current) return;
    const r = await VA.fetchJson(API + "/schedules/" + current.id);
    if (r.ok) render(r.body);
  }

  async function open(id) {
    clearErr();
    document.getElementById("dt-box").hidden = true;
    document.getElementById("er-box").hidden = true;
    const r = await VA.fetchJson(API + "/schedules/" + id);
    if (!r.ok) return err(r.body);
    await loadOfferings(r.body.term_label);
    render(r.body);
  }

  document.getElementById("ds-create").addEventListener("click", async function () {
    clearErr();
    const body = { term_label: document.getElementById("ds-term").value, name: document.getElementById("ds-name").value.trim(),
                   assessment_type_id: document.getElementById("ds-assess").value || null };
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

  async function loadResultTypes() {
    const r = await VA.fetchJson("/api/v1/academics/assessment-types");
    const items = r.ok ? (Array.isArray(r.body) ? r.body : (r.body.items || [])) : [];
    const sel = document.getElementById("ds-assess");
    items.forEach(function (t) { const o = document.createElement("option"); o.value = t.id; o.textContent = t.name + " (/" + Number(t.max_marks) + ")"; sel.appendChild(o); });
  }

  document.addEventListener("vistadmin:ready", function () { loadList(); loadResultTypes(); });
})();