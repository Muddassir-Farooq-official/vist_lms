// Courses > Academic terms > Sessions (app/academics/sessions.py): group batch terms into sessions, and start the next one.
(function () {
  "use strict";

  const VA = window.VistAdmin;
  const API = "/api/v1/academics/sessions";
  const $ = function (id) { return document.getElementById(id); };
  if (!$("sessions-panel")) return;
  function esc(v) { return VA.escapeHtml(v == null ? "" : String(v)); }
  function msg(r, fb) { return (r && r.body && r.body.error && r.body.error.message) || fb; }
  function json(method, body) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }; }
  function err(text) { $("ses-error").textContent = text || ""; $("ses-error").hidden = !text; }
  function d(v) { return v ? VA.formatDateLong(v) : "—"; }
  const WHEN = { current: '<span class="status-pill active">Current</span>', coming: '<span class="status-pill pending">Coming</span>', past: '<span class="status-pill">Past</span>' };

  let groups = null, programs = [];

  async function load() {
    const [r, p] = await Promise.all([VA.fetchJson(API, { quiet: true }), programs.length ? Promise.resolve(null) : VA.fetchJson("/api/v1/programs", { quiet: true })]);
    if (p && p.ok) programs = Array.isArray(p.body) ? p.body : p.body.items || [];
    if (!r.ok) { err(msg(r, "Could not load the sessions.")); return; }
    groups = r.body;
    draw();
    if (window.TermGroups) window.TermGroups.reset();
  }

  function draw() {
    const box = $("ses-list");
    if (!groups.sessions.length) {
      box.innerHTML = '<p class="empty-state">No sessions yet. Press <strong>Suggest sessions from the term names</strong>.</p>' +
        (groups.ungrouped.length ? '<p class="sd-dim">' + groups.ungrouped.length + " term(s) in no session.</p>" : "");
      return;
    }
    box.innerHTML = '<table class="dash-table sd-table no-stack"><thead><tr><th>Session</th><th>Dates</th><th>Batch terms</th><th></th></tr></thead><tbody>' +
      groups.sessions.map(function (s) {
        return '<tr data-id="' + esc(s.id) + '"><td><strong>' + esc(s.name) + "</strong> " + (WHEN[s.when] || "") + "</td><td>" + d(s.start_date) + " – " + d(s.end_date) +
          "</td><td>" + (s.terms.length ? s.terms.map(esc).join(", ") : '<span class="sd-dim">none</span>') + "</td>" +
          '<td class="crs-actions"><button type="button" class="link-btn" data-act="report">Report</button> <button type="button" class="link-btn" data-act="next">Start next session…</button> ' +
          '<button type="button" class="link-btn" data-act="edit">Edit</button> <button type="button" class="link-btn" data-act="del">Remove</button></td></tr>';
      }).join("") + "</tbody></table>" +
      (groups.ungrouped.length ? '<p class="sd-dim">In no session: ' + groups.ungrouped.map(esc).join(", ") + "</p>" : "");
  }

  // ------------------------------------------------------------------ suggestions
  $("ses-suggest").addEventListener("click", async function () {
    err("");
    const r = await VA.fetchJson(API + "/suggest");
    if (!r.ok) { err(msg(r, "Could not make suggestions.")); return; }
    const s = r.body, box = $("ses-suggestions");
    if (!s.suggestions.length) {
      box.innerHTML = '<p class="dash-alert">No session could be read from the term names' + (s.not_matched.length ? " (" + s.not_matched.length + " term(s) in no session). Add a session and pick its terms with Edit." : ".") + "</p>";
      return;
    }
    const progOpts = function (sel) {
      return '<option value="">—</option>' + programs.map(function (p) { return '<option value="' + esc(p.id) + '"' + (p.id === sel ? " selected" : "") + ">" + esc(p.code) + "</option>"; }).join("");
    };
    box.innerHTML = '<h3 class="tt-sub">Suggested sessions</h3><p class="panel-hint">Untick any term that does not belong, fix names, check each batch (program + semester), then save. Nothing is saved before that.</p>' +
      s.suggestions.map(function (g, gi) {
        return '<div class="ses-sugg" data-g="' + gi + '"><label class="ses-name">Session <input type="text" class="text-input" maxlength="50" value="' + esc(g.name) + '" data-f="name" />' +
          (g.existing_session_id ? ' <span class="sd-dim">(adds to the existing session)</span>' : "") + "</label>" +
          '<table class="dash-table no-stack"><thead><tr><th></th><th>Term</th><th>Dates</th><th>Batch</th></tr></thead><tbody>' +
          g.terms.map(function (t) {
            const hasBatch = !!t.program_id;
            const guess = t.batch_guess || {};
            return '<tr data-term="' + esc(t.id) + '"><td><input type="checkbox"' + (t.by_dates ? "" : " checked") + ' aria-label="Include" /></td><td>' + esc(t.label) +
              (t.by_dates ? ' <span class="sd-dim">(by its dates; tick if it belongs)</span>' : "") + "</td><td>" + d(t.start_date) + " – " + d(t.end_date) + "</td><td>" +
              (hasBatch ? '<span class="sd-dim">already set</span>' :
                '<select class="select-input" data-f="program" aria-label="Program">' + progOpts(guess.program_id) + "</select> " +
                '<input type="number" class="text-input stt-num" min="1" max="20" data-f="semester" value="' + esc(guess.semester || "") + '" placeholder="sem" aria-label="Semester" />') + "</td></tr>";
          }).join("") + "</tbody></table></div>";
      }).join("") +
      (s.not_matched.length ? '<p class="sd-dim">Not matched (add them to a session with Edit): ' + s.not_matched.map(function (t) { return esc(t.label); }).join(", ") + "</p>" : "") +
      '<div class="stt-actions"><button type="button" class="role-btn" id="ses-save">Save these sessions</button></div>';
  });

  $("ses-suggestions").addEventListener("click", async function (e) {
    if (e.target.id !== "ses-save") return;
    const items = Array.prototype.map.call($("ses-suggestions").querySelectorAll(".ses-sugg"), function (g) {
      const item = { name: g.querySelector('[data-f="name"]').value.trim(), term_ids: [], batches: {} };
      g.querySelectorAll("tr[data-term]").forEach(function (tr) {
        if (!tr.querySelector("input[type=checkbox]").checked) return;
        item.term_ids.push(tr.dataset.term);
        const p = tr.querySelector('[data-f="program"]'), sem = tr.querySelector('[data-f="semester"]');
        if (p && p.value && sem && sem.value) item.batches[tr.dataset.term] = { program_id: p.value, semester: parseInt(sem.value, 10) };
      });
      return item;
    }).filter(function (i) { return i.name && i.term_ids.length; });
    if (!items.length) return;
    e.target.disabled = true;
    const r = await VA.fetchJson(API + "/suggest/save", json("POST", { items: items }));
    e.target.disabled = false;
    if (!r.ok) { err(msg(r, "Could not save.")); return; }
    VA.toast(r.body.sessions_created + " session(s) made, " + r.body.terms_linked + " term(s) grouped, " + r.body.batches_set + " batch(es) set.");
    $("ses-suggestions").innerHTML = "";
    load();
  });

  // ------------------------------------------------------------------ the session's report
  function money(n) { return VA.formatMoney ? VA.formatMoney(n) : "Rs. " + Number(n || 0).toLocaleString(); }
  async function showReport(s) {
    const r = await VA.fetchJson(API + "/" + s.id + "/report");
    if (!r.ok) { err(msg(r, "Could not make the report.")); return; }
    const d = r.body, t = d.totals;
    const res = function (x) { return x.PUBLISHED + " published, " + x.APPROVED + " approved, " + (x.SUBMITTED + x.RETURNED) + " with the approver, " + x.DRAFT + " not sent"; };
    const box = $("ses-report");
    box.hidden = false;
    box.innerHTML = '<h3 class="tt-sub">' + esc(d.session.name) + " report</h3>" +
      '<div class="pt-summary"><div class="pt-chip">' + t.terms + " batch terms · " + t.courses + " course sections · " + t.students + " students</div>" +
      '<div class="pt-chip">Attendance ' + (t.attendance_average == null ? "—" : t.attendance_average + "%") + " average · " + t.below_minimum + " below " + d.minimum_attendance + "%</div>" +
      '<div class="pt-chip">Fees: ' + money(t.fees.charged) + " charged · " + money(t.fees.paid) + " paid · " + money(t.fees.remaining) + " owed (" + money(t.fees.overdue) + " overdue)</div></div>" +
      '<div class="pt-wrap"><table class="dash-table no-stack" id="ses-report-table"><thead><tr><th>Batch term</th><th>Batch</th><th class="num">Sections</th><th class="num">Courses</th><th class="num">Students</th>' +
      '<th class="num">Attendance</th><th class="num">Below ' + d.minimum_attendance + '%</th><th>Results</th><th class="num">Fees charged</th><th class="num">Paid</th><th class="num">Owed</th><th class="num">Overdue</th></tr></thead><tbody>' +
      d.terms.map(function (x) {
        return "<tr><td><strong>" + esc(x.term) + "</strong></td><td>" + esc(x.program ? x.program + " · sem " + x.semester : "—") + '</td><td class="num">' + x.sections + '</td><td class="num">' + x.courses +
          '</td><td class="num">' + x.students + '</td><td class="num">' + (x.attendance.average == null ? "—" : x.attendance.average + "%") + '</td><td class="num">' + x.attendance.below_minimum +
          "</td><td><small>" + esc(res(x.results)) + '</small></td><td class="num">' + money(x.fees.charged) + '</td><td class="num">' + money(x.fees.paid) +
          '</td><td class="num">' + money(x.fees.remaining) + '</td><td class="num">' + money(x.fees.overdue) + "</td></tr>";
      }).join("") + "</tbody></table></div>" +
      '<div class="stt-actions"><button type="button" class="role-btn secondary-btn" id="ses-report-csv">Download as CSV</button></div>';
    $("ses-report-csv").addEventListener("click", function () {
      const head = ["Batch term", "Program", "Semester", "Sections", "Courses", "Students", "Attendance %", "Below minimum", "Results published", "Results approved",
        "Results with approver", "Results not sent", "Fees charged", "Concessions", "Paid", "Owed", "Overdue"];
      const lines = [head].concat(d.terms.map(function (x) {
        return [x.term, x.program || "", x.semester || "", x.sections, x.courses, x.students, x.attendance.average == null ? "" : x.attendance.average, x.attendance.below_minimum,
          x.results.PUBLISHED, x.results.APPROVED, x.results.SUBMITTED + x.results.RETURNED, x.results.DRAFT, x.fees.charged, x.fees.concession, x.fees.paid, x.fees.remaining, x.fees.overdue];
      }));
      const csv = lines.map(function (l) { return l.map(function (v) { v = String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }).join(","); }).join("\r\n");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv" }));
      a.download = "session-" + d.session.name.replace(/[^A-Za-z0-9-]+/g, "-") + ".csv";
      document.body.appendChild(a); a.click(); a.remove();
    });
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ------------------------------------------------------------------ add / edit / remove
  $("ses-add").addEventListener("click", async function () {
    const name = await VA.ask({ title: "Add a session", label: "Name, e.g. Spring 2027", ok: "Add", min: 2 });
    if (!name) return;
    const r = await VA.fetchJson(API, json("POST", { name: name }));
    if (!r.ok) { err(msg(r, "Could not add it.")); return; }
    load();
  });

  async function edit(s) {
    const terms = await VA.fetchJson("/api/v1/academics/academic-terms", { quiet: true });
    if (!terms.ok) return;
    const box = document.createElement("dialog");
    box.className = "modal modal-wide";
    const mine = new Set(s.term_details.map(function (t) { return t.id; }));
    box.innerHTML = '<form class="modal-body"><h2>Edit session</h2><div class="ar-form">' +
      '<label>Name<input type="text" class="text-input" maxlength="50" data-f="name" value="' + esc(s.name) + '" /></label>' +
      '<label>Starts<input type="date" class="text-input" data-f="start" value="' + esc(s.start_date || "") + '" /></label>' +
      '<label>Ends<input type="date" class="text-input" data-f="end" value="' + esc(s.end_date || "") + '" /></label></div>' +
      '<p class="panel-hint">Its batch terms (a term belongs to one session):</p><div class="ses-terms">' +
      terms.body.map(function (t) {
        const other = t.session_id && t.session_id !== s.id;
        return '<label><input type="checkbox" value="' + esc(t.id) + '"' + (mine.has(t.id) ? " checked" : "") + " /> " + esc(t.label) + (other ? ' <span class="sd-dim">(in another session)</span>' : "") + "</label>";
      }).join("") + '</div><p class="dash-alert error" hidden></p><div class="form-actions"><button type="submit" class="role-btn">Save</button></div>' +
      '<button type="button" class="back-link modal-close-btn">Cancel</button></form>';
    document.body.appendChild(box);
    box.querySelector(".modal-close-btn").addEventListener("click", function () { box.close(); box.remove(); });
    box.querySelector("form").addEventListener("submit", async function (e) {
      e.preventDefault();
      const v = function (f) { return box.querySelector('[data-f="' + f + '"]').value || null; };
      const body = { name: v("name"), start_date: v("start"), end_date: v("end"),
        term_ids: Array.prototype.map.call(box.querySelectorAll(".ses-terms input:checked"), function (c) { return c.value; }) };
      const r = await VA.fetchJson(API + "/" + s.id, json("PATCH", body));
      if (!r.ok) { const x = box.querySelector(".dash-alert"); x.textContent = msg(r, "Could not save."); x.hidden = false; return; }
      box.close(); box.remove();
      load();
    });
    box.showModal();
  }

  // ------------------------------------------------------------------ start the next session
  let nextOf = null, plan = null;
  function guessNext(name) {
    let m = /^(fall|autumn)\s+(\d{4})$/i.exec(name);
    if (m) return "Spring " + (parseInt(m[2], 10) + 1);
    m = /^(spring|summer)\s+(\d{4})$/i.exec(name);
    if (m) return "Fall " + m[2];
    m = /^(\d{4})-(\d{2})$/.exec(name);
    if (m) return (parseInt(m[1], 10) + 1) + "-" + String((parseInt(m[2], 10) + 1) % 100).padStart(2, "0");
    return "";
  }
  function openNext(s) {
    nextOf = s; plan = null;
    $("ses-next-from").textContent = "From " + s.name + ": " + s.terms.length + " batch term(s).";
    $("ses-next-name").value = guessNext(s.name);
    $("ses-next-start").value = ""; $("ses-next-end").value = "";
    $("ses-next-plan").innerHTML = '<p class="sd-dim">Press Preview to see what would be made. Nothing is saved before you press Start.</p>';
    $("ses-next-ok").disabled = true;
    $("ses-next-err").hidden = true;
    $("ses-next").showModal();
  }
  function labels() {
    const out = {};
    $("ses-next-plan").querySelectorAll("input[data-term]").forEach(function (i) { out[i.dataset.term] = i.value.trim(); });
    return out;
  }
  const STATUS = { NEW: "New term", EXISTS: "Term exists: sections added", LAST: "Last semester", NO_BATCH: "No batch set", NAME_TAKEN: "Name taken" };
  async function preview() {
    const body = { new_name: $("ses-next-name").value.trim(), labels: plan ? labels() : {} };
    if (!body.new_name) { $("ses-next-err").textContent = "Give the new session a name."; $("ses-next-err").hidden = false; return; }
    $("ses-next-err").hidden = true;
    const r = await VA.fetchJson(API + "/" + nextOf.id + "/next-preview", json("POST", body));
    if (!r.ok) { $("ses-next-err").textContent = msg(r, "Could not preview."); $("ses-next-err").hidden = false; return; }
    plan = r.body;
    $("ses-next-plan").innerHTML = '<p class="cc-meta">' + plan.will_create_terms + " new term(s) · about " + plan.will_open_sections + " course section(s) · up to " + plan.will_enroll_up_to + " enrolment(s).</p>" +
      '<table class="dash-table no-stack"><thead><tr><th>Now</th><th>Next term</th><th>Courses × sections</th><th class="num">Promoted</th><th class="num">Not yet</th><th></th></tr></thead><tbody>' +
      plan.rows.map(function (x) {
        const go = x.status === "NEW" || x.status === "EXISTS" || x.status === "NAME_TAKEN";
        return "<tr><td>" + esc(x.label) + "</td><td>" + (go ? '<input type="text" class="text-input" maxlength="50" data-term="' + esc(x.term_id) + '" value="' + esc(x.new_label) + '" aria-label="New term name" />' : "—") +
          "</td><td>" + (x.courses ? x.courses.length + " × " + x.sections.join(", ") : "") + '</td><td class="num">' + (x.promoted != null ? x.promoted : "") +
          '</td><td class="num">' + (x.not_promoted != null ? x.not_promoted : "") + "</td><td>" + esc(STATUS[x.status] || x.status) + (x.note ? "<small class=\"sd-dim\" style=\"display:block\">" + esc(x.note) + "</small>" : "") + "</td></tr>";
      }).join("") + "</tbody></table>" +
      '<p class="sd-dim">Change a new term\'s name and press Preview again to check it. Students still in their current semester ("Not yet") are not enrolled: promote them first (Result Approval), then enroll them from the course sections.</p>';
    $("ses-next-ok").disabled = plan.blocked.length > 0 || !plan.rows.some(function (x) { return x.status === "NEW" || x.status === "EXISTS"; });
  }
  $("ses-next-preview").addEventListener("click", preview);
  $("ses-next-name").addEventListener("change", function () { plan = null; $("ses-next-ok").disabled = true; });
  $("ses-next-cancel").addEventListener("click", function () { $("ses-next").close(); });
  $("ses-next-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const body = { new_name: $("ses-next-name").value.trim(), labels: labels(), start_date: $("ses-next-start").value || null,
      end_date: $("ses-next-end").value || null, enroll: $("ses-next-enroll").checked };
    if (!body.start_date || !body.end_date) { $("ses-next-err").textContent = "Give the start and end dates."; $("ses-next-err").hidden = false; return; }
    const ok = $("ses-next-ok");
    ok.disabled = true; ok.textContent = "Starting…";
    const r = await VA.fetchJson(API + "/" + nextOf.id + "/start-next", json("POST", body));
    ok.textContent = "Start the session";
    if (!r.ok) { ok.disabled = false; $("ses-next-err").textContent = msg(r, "Could not start it.") + " Nothing was changed."; $("ses-next-err").hidden = false; return; }
    $("ses-next").close();
    VA.toast(r.body.session + ": " + r.body.terms_created + " term(s), " + r.body.sections_opened + " section(s), " + r.body.enrolled + " enrolment(s)." +
      (r.body.enroll_failed.length ? " " + r.body.enroll_failed.length + " student(s) could not be enrolled: see the course sections." : ""));
    load();
  });

  $("ses-list").addEventListener("click", async function (e) {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const s = groups.sessions.find(function (x) { return x.id === b.closest("tr").dataset.id; });
    if (b.dataset.act === "report") showReport(s);
    if (b.dataset.act === "next") openNext(s);
    if (b.dataset.act === "edit") edit(s);
    if (b.dataset.act === "del") {
      const ok = await VA.ask({ title: "Remove " + s.name + "?", intro: "Only the grouping goes: its terms and everything recorded on them stay as they are.", ok: "Remove", input: "none" });
      if (!ok) return;
      const r = await VA.fetchJson(API + "/" + s.id, { method: "DELETE" });
      if (!r.ok) { err(msg(r, "Could not remove it.")); return; }
      load();
    }
  });

  load();
})();
