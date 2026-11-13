/* Admin -> Classes & sections -> Exams & results (School mode): set up exams, subjects' totals, follow marks entry,
   publish report cards class by class, promote students, and the skills list.  API: /api/v1/school/exams, /skills, /promotion. */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const API = "/api/v1/school";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (s) { return VA.escapeHtml(s == null ? "" : String(s)); };
  function msg(r, f) { return (r.body && r.body.error && r.body.error.message) || f; }
  function json(method, body) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }; }
  function session() { const s = $("sc-session"); return s && s.value ? s.value : ""; }
  let data = null;

  async function load() {
    const r = await VA.fetchJson(API + "/exams" + (session() ? "?session_label=" + encodeURIComponent(session()) : ""));
    if (!r.ok) { $("rs-exams").innerHTML = '<p class="dash-alert error">' + esc(msg(r, "Could not load.")) + "</p>"; return; }
    data = r.body;
    const w = data.weights;
    $("rs-rule").textContent = "Session " + data.session + ". The year's result: 1st Term " + w.FIRST_TERM + "%, Mid-Term " + w.MID_TERM + "%, Final " + w.FINAL +
      "% (Settings). Pass: " + data.pass_percent + "% overall and " + data.subject_pass_percent + "% in every subject.";
    $("rs-create").innerHTML = data.can_create.map(function (k) {
      return '<button type="button" class="role-btn secondary-btn small-btn" data-create="' + k.kind + '">+ Set up the ' + esc(k.name) + " exam</button>";
    }).join("");
    $("rs-create").querySelectorAll("[data-create]").forEach(function (b) {
      b.addEventListener("click", async function () {
        const x = await VA.fetchJson(API + "/exams", json("POST", { kind: b.dataset.create, session_label: data.session }));
        if (!x.ok) { VA.toast(msg(x, "Could not set it up."), "error"); return; }
        VA.toast("Exam set up. Teachers can enter marks now.", "success");
        load();
      });
    });
    $("rs-exams").innerHTML = data.exams.length ? data.exams.map(examCard).join("") : '<p class="empty-state">No exam yet. Set up the 1st Term exam to start.</p>';
    bind();
    skills();
  }

  function examCard(e) {
    return '<div class="rs-exam"><div class="rs-exam-head"><h3>' + esc(e.name) + " exam</h3>" +
      '<span class="status-pill ' + (e.marks_open ? "active" : "pending") + '">' + (e.marks_open ? "Marks entry open" : "Marks entry closed") + "</span>" +
      '<button type="button" class="sd-view" data-open="' + e.id + '" data-to="' + (!e.marks_open) + '">' + (e.marks_open ? "Close marks entry" : "Open marks entry") + "</button></div>" +
      '<table class="dash-table"><thead><tr><th>Class</th><th class="num">Students</th><th>Marks entered</th><th class="num">Remarks</th><th>Report cards</th><th class="crs-actions"></th></tr></thead><tbody>' +
      e.classes.map(function (c) {
        const pct = c.marks_needed ? Math.round(c.marks_entered / c.marks_needed * 100) : 0;
        return "<tr><td><strong>" + esc(c.class) + '</strong></td><td class="num">' + c.students + '</td><td><div class="rs-bar" title="' + c.marks_entered + " of " + c.marks_needed +
          '"><span style="width:' + pct + '%"></span></div><small class="sc-code">' + c.marks_entered + " of " + c.marks_needed + '</small></td><td class="num">' + c.remarks +
          "</td><td>" + (c.published ? '<span class="status-pill active">Published</span>' : '<span class="status-pill">Not published</span>') +
          '</td><td class="crs-actions"><div class="dt-actions">' +
          (c.published ? "" : '<button type="button" class="sd-view" data-totals="' + e.id + '" data-pid="' + c.program_id + '" data-name="' + esc(c.class) + '">Totals</button>') +
          (c.published ? '<button type="button" class="sd-view tt-del" data-withdraw="' + e.id + '" data-pid="' + c.program_id + '">Withdraw</button>'
            : (c.students ? '<button type="button" class="sd-view" data-publish="' + e.id + '" data-pid="' + c.program_id + '" data-name="' + esc(c.class) + '">Publish</button>' : "")) +
          (e.kind === "FINAL" && c.published ? '<button type="button" class="sd-view" data-promote="' + c.program_id + '">Promote</button>' : "") +
          "</div></td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  function bind() {
    document.querySelectorAll("#rs-exams [data-open]").forEach(function (b) {
      b.addEventListener("click", async function () {
        const x = await VA.fetchJson(API + "/exams/" + b.dataset.open + "/marks-open", json("POST", { marks_open: b.dataset.to === "true" }));
        if (!x.ok) VA.toast(msg(x, "Could not change it."), "error"); else load();
      });
    });
    document.querySelectorAll("#rs-exams [data-publish]").forEach(function (b) {
      b.addEventListener("click", async function () {
        if (!(await VA.confirm("Publish the report cards of " + b.dataset.name + "? Students and parents are told and can open them.", "Publish"))) return;
        const x = await VA.fetchJson(API + "/exams/" + b.dataset.publish + "/classes/" + b.dataset.pid + "/publish", { method: "POST" });
        if (!x.ok) { VA.toast(msg(x, "Could not publish."), "error"); return; }
        VA.toast("Published. Students and parents have been told.", "success");
        load();
      });
    });
    document.querySelectorAll("#rs-exams [data-withdraw]").forEach(function (b) {
      b.addEventListener("click", async function () {
        const why = await VA.ask({ title: "Withdraw report cards", intro: "Students and parents will no longer see them until you publish again.", label: "Reason (kept on record)", ok: "Withdraw", danger: true });
        if (!why) return;
        const x = await VA.fetchJson(API + "/exams/" + b.dataset.withdraw + "/classes/" + b.dataset.pid + "/withdraw", json("POST", { reason: why }));
        if (!x.ok) VA.toast(msg(x, "Could not withdraw."), "error"); else load();
      });
    });
    document.querySelectorAll("#rs-exams [data-totals]").forEach(function (b) { b.addEventListener("click", function () { totals(b.dataset.totals, b.dataset.pid, b.dataset.name); }); });
    document.querySelectorAll("#rs-exams [data-promote]").forEach(function (b) { b.addEventListener("click", function () { promote(b.dataset.promote); }); });
  }

  async function totals(examId, pid, name) {
    const box = $("rs-totals");
    const r = await VA.fetchJson(API + "/exams/" + examId + "/classes/" + pid + "/totals");
    if (!r.ok) { VA.toast(msg(r, "Could not load."), "error"); return; }
    box.hidden = false;
    box.innerHTML = '<div class="sc-detail-head"><h2 class="panel-title">Total marks — ' + esc(name) + '</h2><button type="button" class="sd-view" id="rt-close">Close</button></div>' +
      '<div class="rs-totals">' + r.body.map(function (t) {
        return "<label>" + esc(t.subject) + '<input type="number" class="text-input" min="1" max="1000" data-course="' + t.course_id + '" value="' + t.total_marks + '"></label>';
      }).join("") + '</div><div class="toolbar"><button type="button" class="role-btn" id="rt-save">Save totals</button></div>';
    $("rt-close").addEventListener("click", function () { box.hidden = true; });
    $("rt-save").addEventListener("click", async function () {
      const map = {};
      box.querySelectorAll("[data-course]").forEach(function (i) { map[i.dataset.course] = Number(i.value); });
      const x = await VA.fetchJson(API + "/exams/" + examId + "/classes/" + pid + "/totals", json("PUT", { totals: map }));
      if (!x.ok) { VA.toast(msg(x, "Could not save."), "error"); return; }
      VA.toast("Totals saved.", "success");
      box.hidden = true;
    });
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function promote(pid) {
    const box = $("rs-promo");
    const r = await VA.fetchJson(API + "/promotion/" + pid + "?session_label=" + encodeURIComponent(data.session));
    if (!r.ok) { VA.toast(msg(r, "Could not load."), "error"); return; }
    const d = r.body;
    box.hidden = false;
    const classes = d.next_classes.map(function (c) { return '<option value="' + c.id + '">' + esc(c.name) + "</option>"; }).join("");
    box.innerHTML = '<div class="sc-detail-head"><h2 class="panel-title">Promotion — ' + esc(d.class.name) + " (" + esc(d.session) + ')</h2><button type="button" class="sd-view" id="pr-close">Close</button></div>' +
      (d.to_sessions.length ? '<label class="rc-day">Move to session <select id="pr-to" class="select-input">' + d.to_sessions.map(function (t) { return "<option>" + esc(t) + "</option>"; }).join("") + "</select></label>"
        : '<p class="rc-info">Set up the next session first (Classes tab → Set up more classes, with the new session name).</p>') +
      (d.next_classes.length ? "" : '<p class="rc-info">This is the last class; there is no next class to move to.</p>') +
      '<table class="dash-table"><thead><tr><th>Student</th><th class="num">Year %</th><th>Result</th><th>Decision</th><th>Next class</th><th>Note</th></tr></thead><tbody>' +
      d.students.map(function (s) {
        if (s.done) return '<tr><td>' + esc(s.name) + " <small class=\"sc-code\">" + esc(s.roll_number + " · " + s.section) + '</small></td><td class="num">' + (s.percentage == null ? "—" : s.percentage.toFixed(1)) +
          "</td><td colspan=\"4\"><span class=\"status-pill active\">" + esc(s.done.replace(/_/g, " ").toLowerCase()) + "</span></td></tr>";
        const def = s.passed ? "PROMOTED" : "KEPT_BACK";
        return '<tr class="pr-row" data-sid="' + s.student_id + '"><td>' + esc(s.name) + ' <small class="sc-code">' + esc(s.roll_number + " · " + s.section) + "</small></td>" +
          '<td class="num">' + (s.percentage == null ? "—" : s.percentage.toFixed(1)) + "</td><td>" + (s.passed ? '<span class="status-pill active">Pass</span>' :
          '<span class="status-pill dropped">Fail</span>' + (s.failed_subjects.length ? '<small class="sc-code">' + esc(s.failed_subjects.join(", ")) + "</small>" : "")) + "</td>" +
          '<td><select class="select-input pr-dec">' + (s.passed ? '<option value="PROMOTED">Promote</option>' : '<option value="PROMOTED_ON_APPROVAL">Promote on approval</option>') +
          '<option value="KEPT_BACK"' + (def === "KEPT_BACK" ? " selected" : "") + ">Keep back</option><option value=\"\">Decide later</option></select></td>" +
          '<td><select class="select-input pr-to">' + classes + '</select></td><td><input type="text" class="text-input pr-note" maxlength="300" placeholder="' + (s.passed ? "" : "Why promoted (if so)") + '"></td></tr>';
      }).join("") + '</tbody></table><p class="dash-alert error" id="pr-error" hidden></p>' +
      (d.to_sessions.length ? '<div class="toolbar"><button type="button" class="role-btn" id="pr-go">Move the students</button></div>' : "");
    $("pr-close").addEventListener("click", function () { box.hidden = true; });
    const go = $("pr-go");
    if (go) go.addEventListener("click", async function () {
      const err = $("pr-error"); err.hidden = true;
      const items = [];
      box.querySelectorAll("tr[data-sid]").forEach(function (tr) {
        const dec = tr.querySelector(".pr-dec").value;
        if (!dec) return;
        items.push({ student_id: tr.dataset.sid, decision: dec, to_program_id: dec === "KEPT_BACK" ? null : (tr.querySelector(".pr-to").value || null),
          note: tr.querySelector(".pr-note").value.trim() || null });
      });
      if (!items.length) { err.textContent = "Nothing to do: every student is set to Decide later."; err.hidden = false; return; }
      if (!(await VA.confirm("Move " + items.length + " student(s) to " + $("pr-to").value + "? They are put in the same section letter and enrolled in its subjects.", "Move"))) return;
      go.disabled = true;
      const x = await VA.fetchJson(API + "/promotion/" + pid, json("POST", { session_label: data.session, to_session: $("pr-to").value, items: items }));
      go.disabled = false;
      if (!x.ok) { err.textContent = msg(x, "Could not move them."); err.hidden = false; return; }
      VA.toast(x.body.moved.length + " moved." + (x.body.failed.length ? " " + x.body.failed.length + " not moved: " + x.body.failed.map(function (f) { return f.name + " (" + f.reason + ")"; }).join("; ") : ""),
        x.body.failed.length ? "error" : "success");
      promote(pid);
    });
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function skills() {
    const r = await VA.fetchJson(API + "/skills?all=true");
    const box = $("rs-skills");
    if (!r.ok) { box.textContent = "Could not load."; return; }
    function row(s) {
      return '<div class="rs-skill" data-id="' + (s.id || "") + '"><input class="text-input" maxlength="60" value="' + esc(s.name) + '" aria-label="Skill">' +
        '<label class="rc-day"><input type="checkbox"' + (s.active !== false ? " checked" : "") + "> On</label></div>";
    }
    box.innerHTML = '<div id="rs-sk-list">' + r.body.map(row).join("") + '</div><div class="toolbar"><button type="button" class="role-btn secondary-btn small-btn" id="rs-sk-add">+ Add</button>' +
      '<button type="button" class="role-btn small-btn" id="rs-sk-save">Save list</button></div>';
    $("rs-sk-add").addEventListener("click", function () { $("rs-sk-list").insertAdjacentHTML("beforeend", row({ name: "", active: true })); });
    $("rs-sk-save").addEventListener("click", async function () {
      const items = [];
      box.querySelectorAll(".rs-skill").forEach(function (d) {
        const name = d.querySelector("input.text-input").value.trim();
        if (name) items.push({ id: d.dataset.id || null, name: name, active: d.querySelector("input[type=checkbox]").checked });
      });
      const x = await VA.fetchJson(API + "/skills", json("PUT", { skills: items }));
      VA.toast(x.ok ? "Saved." : msg(x, "Could not save."), x.ok ? "success" : "error");
      if (x.ok) skills();
    });
  }

  window.VistResults = { load: load };
})();
