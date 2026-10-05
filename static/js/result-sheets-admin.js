(function () {
  "use strict";

  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/results";
  const $ = function (id) { return document.getElementById(id); };
  const msg = function (body, fallback) { return body && body.error && body.error.message ? body.error.message : fallback; };
  const json = function (method, data) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }; };
  const STATUSES = ["SUBMITTED", "APPROVED", "PUBLISHED", "RETURNED"];
  const LABEL = { SUBMITTED: "Waiting for approval", APPROVED: "Ready to publish", PUBLISHED: "Published", RETURNED: "Sent back", DRAFT: "Draft" };
  const PILL = { SUBMITTED: "pending", APPROVED: "enrolled", PUBLISHED: "active", RETURNED: "dropped", DRAFT: "" };
  const when = function (iso) { return iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : ""; };
  const pill = function (s) { return '<span class="status-pill ' + PILL[s] + '">' + esc(LABEL[s] || s) + "</span>"; };

  let current = null;           // the sheet being reviewed

  // ------------------------------------------------------------------ the four lists
  function card(r) {
    const el = document.createElement("article");
    el.className = "asg-card " + (r.status === "SUBMITTED" ? "soon" : r.status === "PUBLISHED" ? "ok" : r.status === "RETURNED" ? "urgent" : "");
    el.tabIndex = 0; el.setAttribute("role", "button");
    const date = r.status === "PUBLISHED" ? "Published " + when(r.published_at) : r.status === "APPROVED" ? "Approved " + when(r.approved_at) : r.status === "RETURNED" ? "Sent back" : "Submitted " + when(r.submitted_at);
    el.innerHTML =
      '<div class="asg-top"><span class="pill">' + esc(r.course_code) + "</span>" + pill(r.status) + (r.correction_count ? '<span class="pill flag">' + r.correction_count + " correction" + (r.correction_count > 1 ? "s" : "") + "</span>" : "") + "</div>" +
      "<h3>" + esc(r.course_title) + '</h3><p class="tk-who">Section ' + esc(r.section_label) + " \u00b7 " + esc(r.term_label) + (r.teacher_name ? " \u00b7 " + esc(r.teacher_name) : "") + "</p>" +
      '<p class="asg-snippet">' + r.students + " student" + (r.students !== 1 ? "s" : "") + " \u00b7 " + r.assessments + " assessment" + (r.assessments !== 1 ? "s" : "") + (r.missing_students ? ' \u00b7 <b class="late">' + r.missing_students + " with marks missing</b>" : "") + "</p>" +
      (r.status === "RETURNED" && r.return_reason ? '<div class="cc-note rejected"><strong>Reason given</strong><span>' + esc(r.return_reason) + "</span></div>" : "") +
      '<div class="asg-foot"><span class="cc-meta">' + esc(date) + '</span><span class="role-btn small-btn' + (r.status === "SUBMITTED" ? "" : " secondary-btn") + '">' + (r.status === "SUBMITTED" ? "Review" : "Open") + "</span></div>";
    const open = function () { openSheet(r.course_offering_id); };
    el.addEventListener("click", open);
    el.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    return el;
  }

  async function loadLists() {
    for (const s of STATUSES) {
      const r = await VA.fetchJson(API + "/sheets?status=" + s, { quiet: true });
      const box = $("rs-list-" + s);
      box.innerHTML = "";
      if (!r.ok) continue;
      r.body.forEach(function (x) { box.appendChild(card(x)); });
      $("rs-empty-" + s).hidden = r.body.length > 0;
      $("rs-tab-" + s).textContent = LABEL[s] + (r.body.length && s !== "PUBLISHED" ? " \u00b7 " + r.body.length : "");
    }
  }

  // ------------------------------------------------------------------ one sheet
  const modal = $("rs-modal");
  const showError = function (t) { $("rs-error").textContent = t || ""; $("rs-error").hidden = !t; };
  const tile = function (a, b, c, tone) { return '<div class="kpi ' + (tone || "") + '"><span class="kpi-label">' + a + '</span><strong class="kpi-value">' + b + '</strong><span class="kpi-sub">' + (c || "") + "</span></div>"; };

  function fill(d) {
    current = d;
    $("rs-badges").innerHTML = '<span class="pill">' + esc(d.course_code) + "</span>" + pill(d.status);
    $("rs-title").textContent = d.course_title;
    $("rs-meta").textContent = "Section " + d.section_label + " \u00b7 " + d.term_label;
    $("rs-note").innerHTML = d.status === "RETURNED" && d.return_reason ? '<div class="cc-note rejected"><strong>Sent back</strong><span>' + esc(d.return_reason) + "</span></div>" : d.status === "PUBLISHED" && d.correction_count ? '<div class="cc-note"><strong>' + d.correction_count + " correction" + (d.correction_count > 1 ? "s" : "") + " since publication</strong><span>Each one is recorded with who made it and why.</span></div>" : "";
    const st = d.stats;
    $("rs-stats").innerHTML = tile("Students", st.students, "") + tile("Average", st.average === null ? "\u2014" : st.average + "%", "") + tile("Highest", st.highest === null ? "\u2014" : st.highest + "%", "") +
      tile("Lowest", st.lowest === null ? "\u2014" : st.lowest + "%", "") + tile(d.status === "PUBLISHED" ? "Passed" : "Would pass", st.passed + " of " + st.students, st.pass_rate === null ? "" : st.pass_rate + "%", "good") +
      tile("Below attendance limit", st.ineligible_for_attendance, "can only fail", st.ineligible_for_attendance ? "warn" : "");
    $("rs-projection").hidden = d.status === "PUBLISHED";
    const dist = d.stats.distribution || [];
    let strip = $("rs-dist");
    if (!strip) { strip = document.createElement("div"); strip.id = "rs-dist"; strip.className = "dist-strip"; $("rs-stats").after(strip); }
    strip.innerHTML = dist.length ? "<b>Grades:</b> " + dist.map(function (x) { return '<span class="dist-chip">' + esc(x.grade) + " \u00d7 " + x.count + "</span>"; }).join("") : "";
    $("rs-thead").innerHTML = "<tr><th>Student</th>" + d.assessments_list.map(function (a) { return "<th>" + esc(a.name) + " (/" + a.max_marks + ")</th>"; }).join("") + "<th>%</th><th>Attendance</th><th>Grade</th><th>" + (d.status === "PUBLISHED" ? "Result" : "Projected") + "</th></tr>";
    const fixable = d.status === "PUBLISHED";
    $("rs-tbody").innerHTML = "";
    d.rows.forEach(function (r) {
      const tr = document.createElement("tr");
      tr.innerHTML = '<td><strong>' + esc(r.student_name) + "</strong><small>" + esc(r.roll_number || "") + "</small></td>";
      r.marks.forEach(function (m, i) {
        const td = document.createElement("td");
        if (fixable) {
          const b = document.createElement("button"); b.type = "button"; b.className = "linkish markfix"; b.textContent = m === null ? "\u2014" : m; b.title = "Correct this mark";
          b.addEventListener("click", function () { correctMark(r, d.assessments_list[i], m); });
          td.appendChild(b);
        } else td.textContent = m === null ? "\u2014" : m;
        tr.appendChild(td);
      });
      tr.insertAdjacentHTML("beforeend", "<td>" + (r.percentage === null ? "\u2014" : r.percentage + "%") + "</td><td" + (r.attendance_eligible === false ? ' class="warn"' : "") + ">" + (r.attendance_percentage === null ? "\u2014" : Math.round(r.attendance_percentage) + "%") + "</td><td><b>" + esc(r.grade || "\u2014") + "</b></td>" +
        '<td class="' + (r.outcome === "PASSED" ? "ok" : r.outcome === "FAILED" ? "bad" : "") + '">' + (r.outcome ? esc(r.outcome.charAt(0) + r.outcome.slice(1).toLowerCase()) : "\u2014") + "</td>");
      $("rs-tbody").appendChild(tr);
    });
    const bar = $("rs-actions");
    bar.innerHTML = "";
    const add = function (label, cls, fn) { const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn " + cls; b.textContent = label; b.addEventListener("click", fn); bar.appendChild(b); };
    if (d.status === "SUBMITTED") add("Approve", "", function () { act("approve", "Approved."); });
    if (d.status === "APPROVED") add("Publish to students", "", publish);
    if (d.status === "SUBMITTED" || d.status === "APPROVED") add("Send back\u2026", "danger-btn", sendBack);
    showError("");
  }

  async function openSheet(id) {
    const r = await VA.fetchJson(API + "/sheets/" + id);
    if (!r.ok) { VA.toast(msg(r.body, "Could not open it."), "error"); return; }
    fill(r.body);
    if (!modal.open) modal.showModal();
    const h = $("rs-title"); h.tabIndex = -1; h.focus({ preventScroll: true });
  }
  async function act(what, done) {
    showError("");
    const r = await VA.fetchJson(API + "/sheets/" + current.course_offering_id + "/" + what, { method: "POST" });
    if (!r.ok) return showError(msg(r.body, "That did not work."));
    VA.toast(done); await openSheet(current.course_offering_id); loadLists();
  }
  async function publish() {
    if (!window.confirm("Publish the results for " + current.course_code + "? Students will see their marks and pass/fail straight away, and each one is notified.")) return;
    act("publish", "Published. " + current.stats.students + " students have been notified.");
  }

  // ------------------------------------------------------------------ small dialogs: a reason, or a corrected mark
  let pending = null;
  function ask(cfg) {
    return new Promise(function (resolve) {
      $("rs-act-title").textContent = cfg.title; $("rs-act-intro").textContent = cfg.intro || "";
      $("rs-act-num-wrap").hidden = !cfg.number; if (cfg.number) { $("rs-act-num-label").textContent = cfg.number.label; $("rs-act-num").value = cfg.number.value; $("rs-act-num").max = cfg.number.max; }
      $("rs-act-text-label").textContent = cfg.label; $("rs-act-text").value = ""; $("rs-act-error").hidden = true;
      $("rs-act-ok").textContent = cfg.ok; $("rs-act-ok").className = cfg.danger ? "danger-btn" : "role-btn";
      pending = { resolve: resolve, cfg: cfg }; $("rs-act").showModal();
    });
  }
  $("rs-act-ok").addEventListener("click", function () {
    const text = $("rs-act-text").value.trim(), cfg = pending.cfg, bad = function (t) { $("rs-act-error").textContent = t; $("rs-act-error").hidden = false; };
    if (text.length < 5) return bad("Give a real reason (at least a few words).");
    const out = { text: text, number: cfg.number ? parseFloat($("rs-act-num").value) : null };
    if (cfg.number && (isNaN(out.number) || out.number < 0 || out.number > cfg.number.max)) return bad("Enter a mark between 0 and " + cfg.number.max + ".");
    const done = pending.resolve; pending = null; $("rs-act").close(); done(out);
  });
  $("rs-act-cancel").addEventListener("click", function () { $("rs-act").close(); });
  $("rs-act").addEventListener("close", function () { if (pending) { const r = pending.resolve; pending = null; r(null); } });

  async function sendBack() {
    const a = await ask({ title: "Send the marks back", intro: "The teacher is told, and can correct the marks and submit them again.", label: "What needs fixing?", ok: "Send back", danger: true });
    if (!a) return;
    const r = await VA.fetchJson(API + "/sheets/" + current.course_offering_id + "/return", json("POST", { reason: a.text }));
    if (!r.ok) return showError(msg(r.body, "Could not send it back."));
    VA.toast("Sent back to the teacher."); modal.close(); loadLists();
  }
  async function correctMark(row, assessment, old) {
    const a = await ask({ title: "Correct a published mark", intro: row.student_name + " \u00b7 " + assessment.name + (old === null ? "" : " (now " + old + ")") + ". The student is told, their result is worked out again, and the old value, the new one and your reason are kept on record.",
      number: { label: "Correct mark (out of " + assessment.max_marks + ")", value: old === null ? "" : old, max: assessment.max_marks }, label: "Reason for the correction", ok: "Correct the mark" });
    if (!a) return;
    const r = await VA.fetchJson("/api/v1/academics/enrollments/" + row.enrollment_id + "/marks/" + assessment.id, json("POST", { obtained_marks: a.number, correction_reason: a.text }));
    if (!r.ok) return showError(msg(r.body, "Could not correct the mark."));
    VA.toast("Corrected. " + row.student_name + " has been told."); await openSheet(current.course_offering_id); loadLists();
  }
  $("rs-close").addEventListener("click", function () { modal.close(); });

  $("rs-publish-all").addEventListener("click", async function () {
    if (!window.confirm("Publish EVERY approved result now? Students will see their marks and pass/fail straight away, and are notified.")) return;
    const r = await VA.fetchJson(API + "/publish-approved", { method: "POST" });
    if (!r.ok) return VA.toast(msg(r.body, "Could not publish."), "error");
    const f = r.body.failed.length;
    VA.toast("Published " + r.body.published + " course" + (r.body.published !== 1 ? "s" : "") + "." + (f ? " " + f + " could not be published (see the list: marks are missing)." : ""), f ? "error" : undefined);
    loadLists();
  });

  // ------------------------------------------------------------------ the grading scale
  const fmt2 = function (v) { return v === null || v === undefined ? "\u2014" : Number(v).toFixed(2); };
  const STANDARD = [["A", 85, 4.0, true], ["A-", 80, 3.66, true], ["B+", 75, 3.33, true], ["B", 71, 3.0, true], ["B-", 68, 2.66, true], ["C+", 64, 2.33, true], ["C", 61, 2.0, true], ["C-", 58, 1.66, true], ["D+", 54, 1.33, true], ["D", 50, 1.0, true], ["F", 0, 0.0, false]];
  function scaleRow(letter, from, points, passing) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td><input class="text-input tiny" maxlength="4" value="' + esc(letter) + '" aria-label="Grade" /></td><td><input class="text-input tiny" type="number" min="0" max="100" step="0.01" value="' + from + '" aria-label="From percent" /></td>' +
      '<td><input class="text-input tiny" type="number" min="0" max="10" step="0.01" value="' + points + '" aria-label="Grade points" /></td><td><input type="checkbox"' + (passing ? " checked" : "") + ' aria-label="Passing" /></td><td><button type="button" class="linkish">Remove</button></td>';
    tr.querySelector("button").addEventListener("click", function () { tr.remove(); });
    $("gs-body").appendChild(tr);
  }
  function drawScale(rows) { $("gs-body").innerHTML = ""; rows.forEach(function (r) { scaleRow(r[0], r[1], r[2], r[3]); }); }
  async function loadScale() {
    const r = await VA.fetchJson(API + "/scale");
    if (r.ok) drawScale(r.body.map(function (e) { return [e.letter, e.min_percent, e.grade_points, e.is_passing]; }));
  }
  $("gs-add").addEventListener("click", function () { scaleRow("", "", "", true); });
  $("gs-reset").addEventListener("click", function () { if (window.confirm("Replace the rows below with the standard scale? Nothing is saved until you press Save.")) drawScale(STANDARD); });
  $("gs-save").addEventListener("click", async function () {
    $("gs-error").hidden = true; $("gs-warning").hidden = true;
    const entries = Array.prototype.map.call($("gs-body").querySelectorAll("tr"), function (tr) {
      const i = tr.querySelectorAll("input");
      return { letter: i[0].value.trim(), min_percent: parseFloat(i[1].value), grade_points: parseFloat(i[2].value), is_passing: i[3].checked };
    });
    if (entries.some(function (e) { return !e.letter || isNaN(e.min_percent) || isNaN(e.grade_points); })) { $("gs-error").textContent = "Fill in the grade, the percentage and the grade points on every row."; $("gs-error").hidden = false; return; }
    const r = await VA.fetchJson(API + "/scale", json("PUT", { entries: entries }));
    if (!r.ok) { $("gs-error").textContent = msg(r.body, "Could not save the scale."); $("gs-error").hidden = false; return; }
    VA.toast("Grading scale saved. Results already published keep their grade.");
    if (r.body.warning) { $("gs-warning").textContent = r.body.warning; $("gs-warning").hidden = false; }
    drawScale(r.body.entries.map(function (e) { return [e.letter, e.min_percent, e.grade_points, e.is_passing]; }));
  });

  // ------------------------------------------------------------------ result cards
  let cardTimer = null;
  async function searchStudents() {
    const q = $("rc-q").value.trim(), box = $("rc-list");
    box.innerHTML = ""; $("rc-empty").hidden = q.length > 0;
    if (!q) return;
    const r = await VA.fetchJson("/api/v1/students?q=" + encodeURIComponent(q) + "&page_size=20", { quiet: true });
    if (!r.ok) return;
    $("rc-empty").hidden = r.body.items.length > 0; $("rc-empty").textContent = r.body.items.length ? "" : "Nobody matches.";
    r.body.items.forEach(function (st) {
      const row = document.createElement("div");
      row.className = "auth-row";
      row.innerHTML = '<span class="grow"><strong>' + esc(st.first_name + " " + st.last_name) + "</strong><small>" + esc(st.roll_number || "") + "</small></span>";
      const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn"; b.textContent = "Open result card";
      b.addEventListener("click", function () { openCard(st.id); });
      row.appendChild(b); box.appendChild(row);
    });
  }
  async function openCard(studentId) {
    const r = await VA.fetchJson(API + "/students/" + studentId + "/transcript");
    if (!r.ok) return VA.toast(msg(r.body, "Could not open it."), "error");
    const t = r.body, s = t.student, sm = t.summary;
    $("rc-title").textContent = s.name;
    $("rc-meta").textContent = [s.roll_number, s.program_name, s.admission_session ? "session " + s.admission_session : "", s.guardian_name ? "guardian: " + s.guardian_name : ""].filter(Boolean).join(" \u00b7 ");
    const tile = function (a, b, c, tone) { return '<div class="kpi ' + (tone || "") + '"><span class="kpi-label">' + a + '</span><strong class="kpi-value">' + b + '</strong><span class="kpi-sub">' + (c || "") + "</span></div>"; };
    $("rc-summary").innerHTML = tile("CGPA", fmt2(sm.cgpa), "out of 4.00", "good") + tile("Credits earned", sm.credits_earned, "of " + sm.credits_attempted + " attempted") + tile("Terms", sm.terms, "") + tile("To clear", sm.courses_to_clear, "", sm.courses_to_clear ? "bad" : "");
    $("rc-terms").innerHTML = t.terms.length ? t.terms.map(function (term) {
      return '<h3 class="modal-subhead">' + esc(term.label) + ' <small class="cc-meta">GPA ' + fmt2(term.gpa) + " \u00b7 CGPA " + fmt2(term.cgpa_after) + "</small></h3>" +
        '<div class="table-scroll"><table class="tk-table rs-table"><thead><tr><th>Course</th><th>Cr</th><th>Marks</th><th>%</th><th>Grade</th><th>GP</th></tr></thead><tbody>' + term.courses.map(function (c) {
          return "<tr" + (c.replaced ? ' class="replaced"' : "") + "><td><strong>" + esc(c.course_code) + "</strong><small>" + esc(c.course_title) + (c.replaced ? " (repeated later)" : c.attempt > 1 ? " (attempt " + c.attempt + ")" : "") + "</small></td><td>" + c.credit_hours + "</td><td>" + c.total_obtained + "/" + c.total_max + "</td><td>" + c.percentage.toFixed(1) +
            '</td><td class="' + (c.outcome === "PASSED" ? "ok" : "bad") + '"><b>' + esc(c.letter_grade) + "</b></td><td>" + fmt2(c.grade_points) + "</td></tr>";
        }).join("") + "</tbody></table></div>";
    }).join("") : '<p class="home-empty">No results have been published for this student yet.</p>';
    $("rc-pdf").setAttribute("href", API + "/students/" + studentId + "/transcript.pdf");
    $("rc-pdf").setAttribute("data-download", "result-card-" + (s.roll_number || "student") + ".pdf");
    $("rc-modal").showModal();
  }
  $("rc-q").addEventListener("input", function () { clearTimeout(cardTimer); cardTimer = setTimeout(searchStudents, 300); });
  $("rc-close").addEventListener("click", function () { $("rc-modal").close(); });

  document.addEventListener("vistadmin:ready", function () {
    loadScale(); searchStudents();
    loadLists();
    const open = new URLSearchParams(window.location.search).get("open");
    if (open) openSheet(open);
  });
})();