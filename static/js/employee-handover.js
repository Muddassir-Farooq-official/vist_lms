// Employees -> a person -> Hand over work (API /api/v1/employees/{id}/handover). AWAY: a cover for each class in the
// dates; LEFT: a new teacher for each running course. Both: covers they were going to take are given back, and exam
// duties, class-teacher roles and mentees go to whoever is chosen.
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const ID = document.currentScript.getAttribute("data-employee-id");
  const API = "/api/v1/employees/" + ID + "/handover";
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const day = function (v) { return new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }); };
  const iso = function (d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); };
  let plan = null;

  const params = new URLSearchParams(location.search);
  if (params.get("kind") === "LEFT") document.querySelector('input[name="ho-kind"][value="LEFT"]').checked = true;
  const today = new Date();
  $("ho-from").value = params.get("from") || iso(today);
  $("ho-until").value = params.get("until") || iso(new Date(today.getTime() + 13 * 86400000));
  const kind = function () { return document.querySelector('input[name="ho-kind"]:checked').value; };

  function people(selected, opts) {
    opts = opts || {};
    return '<option value="">' + esc(opts.none || "No one") + "</option>" + plan.staff.filter(function (s) { return !opts.teachers || s.teacher; }).map(function (s) {
      return '<option value="' + esc(s.id) + '"' + (s.id === selected ? " selected" : "") + ">" + esc(s.name) + (s.code ? " (" + esc(s.code) + ")" : "") + "</option>";
    }).join("");
  }

  async function load() {
    $("ho-error").hidden = true;
    $("ho-done").hidden = true;
    $("ho-until-field").hidden = kind() !== "AWAY";
    const q = "?kind=" + kind() + "&from_date=" + $("ho-from").value + (kind() === "AWAY" ? "&until_date=" + $("ho-until").value : "");
    const r = await VA.fetchJson(API + q);
    if (!r.ok) { $("ho-error").textContent = (r.body && r.body.error && r.body.error.message) || "Could not load."; $("ho-error").hidden = false; return; }
    plan = r.body;
    $("ho-name").textContent = plan.employee.name + "'s work";
    $("ho-status").textContent = "Status now: " + plan.employee.status.replace("_", " ").toLowerCase() + ".";
    draw();
  }

  function draw() {
    const left = plan.kind === "LEFT";
    // courses (LEFT)
    $("ho-courses-card").hidden = !left || !plan.courses.length;
    $("ho-courses").innerHTML = plan.courses.map(function (c) {
      return '<tr data-offering="' + esc(c.offering_id) + '"><td><strong>' + esc(c.code) + "</strong> " + esc(c.title) + (c.combined ? ' <span class="pt-comb">combined</span>' : "") +
        (c.still_theirs ? "" : ' <small class="list-sub">already without a teacher</small>') + "</td><td>" + esc(c.term) + " (" + esc(c.section) + ')</td><td class="num">' + c.students +
        '</td><td><select class="select-input ho-teacher" aria-label="New teacher for ' + esc(c.code) + '">' + people(null, { teachers: false, none: "No teacher yet" }) + "</select></td></tr>";
    }).join("");
    // classes (AWAY), grouped by course
    $("ho-classes-card").hidden = left || !plan.classes.length;
    const byCourse = {};
    plan.classes.forEach(function (x) { (byCourse[x.course + " · " + x.section] = byCourse[x.course + " · " + x.section] || []).push(x); });
    $("ho-classes").innerHTML = Object.keys(byCourse).map(function (k) {
      return '<div class="ho-course"><div class="ho-course-head"><strong>' + esc(k) + "</strong> <span class=\"cc-meta\">" + byCourse[k].length + " class" + (byCourse[k].length === 1 ? "" : "es") +
        '</span><label>Same for all <select class="select-input ho-all">' + people(null, { none: "—" }) + "</select></label></div>" +
        '<div class="table-scroll"><table class="tk-table"><tbody>' + byCourse[k].map(function (x) {
          return '<tr data-session="' + esc(x.class_session_id) + '"><td>' + esc(day(x.date)) + "</td><td>" + esc(x.time) + "</td><td>" + esc(x.room) + "</td><td>" +
            (x.covered_by ? '<span class="badge badge-covered">Covered: ' + esc(x.covered_by) + "</span> " : "") +
            '<select class="select-input ho-sub" aria-label="Cover for ' + esc(k) + " on " + esc(day(x.date)) + '">' + people(null, { none: x.covered_by ? "Keep as it is" : "Needs a teacher" }) + "</select></td></tr>";
        }).join("") + "</tbody></table></div></div>";
    }).join("");
    // everything else
    const other = [];
    if (plan.covers_taking.length) {
      other.push('<div class="ho-block"><h3>Covers they were going to take (' + plan.covers_taking.length + ")</h3><ul>" + plan.covers_taking.map(function (c) { return "<li>" + esc(c.description) + "</li>"; }).join("") +
        '</ul><label class="checkbox-label"><input type="checkbox" id="ho-release" checked /> Give these back, so the office can find someone else (shown under Attendance &rarr; Teachers)</label></div>');
    }
    if (plan.duties.length) {
      other.push('<div class="ho-block"><h3>Exam duties</h3><table class="tk-table"><tbody>' + plan.duties.map(function (d) {
        return '<tr data-duty="' + esc(d.duty_id) + '"><td>' + esc(day(d.date)) + " " + esc(d.time) + "</td><td>" + esc(d.paper) + " · room " + esc(d.room) + "</td><td>" + (d.role === "IN_CHARGE" ? "In charge" : "Assistant") +
          '</td><td><select class="select-input ho-duty">' + people(null, { none: "Take them off this duty" }) + "</select></td></tr>";
      }).join("") + "</tbody></table></div>");
    }
    if (plan.sections.length) {
      other.push('<div class="ho-block"><h3>Class teacher (school)</h3><table class="tk-table"><tbody>' + plan.sections.map(function (s) {
        return '<tr data-section="' + esc(s.section_id) + '"><td>' + esc(s.label) + " · " + esc(s.session) + '</td><td><select class="select-input ho-section">' + people(null, { teachers: false, none: "No class teacher" }) + "</select></td></tr>";
      }).join("") + "</tbody></table></div>");
    }
    if (plan.mentees) {
      other.push('<div class="ho-block"><h3>Mentees (' + plan.mentees + ')</h3><label>Give them to <select class="select-input" id="ho-mentor"><option value="keep">Keep with them</option>' +
        '<option value="none">No mentor for now</option>' + plan.staff.map(function (s) { return '<option value="' + esc(s.id) + '">' + esc(s.name) + "</option>"; }).join("") + "</select></label></div>");
    }
    if (plan.committees.length) {
      other.push('<div class="ho-block"><h3>Committees</h3><p class="cc-meta">' + plan.committees.map(function (c) { return esc(c.name) + " (" + esc(c.role.toLowerCase()) + ")"; }).join(", ") + "</p>" +
        (left ? '<label class="checkbox-label"><input type="checkbox" id="ho-committees" checked /> End these seats</label>' : '<p class="cc-meta">They keep their seats while away.</p>') + "</div>");
    }
    $("ho-other").innerHTML = other.join("");
    $("ho-other-card").hidden = !other.length;
    $("ho-leave").hidden = !left || plan.employee.status === "TERMINATED";
    const anything = (left && plan.courses.length) || (!left && plan.classes.length) || other.length;
    $("ho-finish").hidden = !anything && !(left && plan.employee.status !== "TERMINATED");
    $("ho-nothing").hidden = !!anything || !(left ? plan.employee.status === "TERMINATED" : true);
    $("ho-summary").textContent = left ? plan.courses.length + " course(s) to hand over." : plan.classes.length + " class(es) from " + day(plan.from_date) + " to " + day(plan.until_date) + ".";
  }

  document.addEventListener("change", function (e) {
    if (e.target.classList.contains("ho-all")) {
      e.target.closest(".ho-course").querySelectorAll(".ho-sub").forEach(function (s) { s.value = e.target.value; });
    }
    if (e.target.name === "ho-kind" || e.target.id === "ho-from" || e.target.id === "ho-until") load();
  });

  $("ho-save").addEventListener("click", async function () {
    const left = plan.kind === "LEFT";
    const body = { kind: plan.kind, from_date: plan.from_date, until_date: plan.until_date, courses: [], classes: [], duties: [], sections: [] };
    if (left) document.querySelectorAll("#ho-courses tr[data-offering]").forEach(function (tr) {
      body.courses.push({ offering_id: tr.dataset.offering, teacher_id: tr.querySelector(".ho-teacher").value || null });
    });
    else document.querySelectorAll("#ho-classes tr[data-session]").forEach(function (tr) {
      const v = tr.querySelector(".ho-sub").value;
      if (v) body.classes.push({ class_session_id: tr.dataset.session, substitute_id: v });
    });
    document.querySelectorAll("tr[data-duty]").forEach(function (tr) { body.duties.push({ duty_id: tr.dataset.duty, employee_id: tr.querySelector(".ho-duty").value || null }); });
    document.querySelectorAll("tr[data-section]").forEach(function (tr) { body.sections.push({ section_id: tr.dataset.section, employee_id: tr.querySelector(".ho-section").value || null }); });
    const m = $("ho-mentor");
    if (m && m.value !== "keep") body.mentors = m.value === "none" ? [] : [m.value];
    body.release_covers = $("ho-release") ? $("ho-release").checked : true;
    body.end_committees = $("ho-committees") ? $("ho-committees").checked : false;
    if (left && $("ho-terminate").checked) { body.terminate = true; body.reason = $("ho-reason").value.trim(); }
    const n = body.duties.filter(function (d) { return !d.employee_id; }).length;
    if (n && !window.confirm(n + " exam dut" + (n === 1 ? "y has" : "ies have") + " no replacement and will be removed. Go on?")) return;
    $("ho-save").disabled = true;
    const r = await VA.fetchJson(API, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    $("ho-save").disabled = false;
    if (!r.ok) { $("ho-error").textContent = (r.body && r.body.error && r.body.error.message) || "Not handed over."; $("ho-error").hidden = false; window.scrollTo({ top: 0, behavior: "smooth" }); return; }
    const d = r.body.done;
    const parts = [[d.courses, "course(s) to new teachers"], [d.covers, "class(es) covered"], [d.released, "cover(s) given back"], [d.duties, "exam dut(ies)"],
      [d.sections, "class-teacher role(s)"], [d.mentees, "mentee(s)"], [d.committees, "committee seat(s) ended"], [d.supervision, "clinical supervision ended"]]
      .filter(function (p) { return p[0]; }).map(function (p) { return p[0] + " " + p[1]; });
    $("ho-done").textContent = "Done: " + (parts.join(", ") || "nothing needed changing") + ". Everything is in the activity log.";
    $("ho-done").hidden = false;
    VA.toast("Handed over.", "success");
    plan = r.body.plan;
    draw();
    $("ho-done").hidden = false;
  });

  if (params.get("terminate") === "1") {
    const t = $("ho-terminate");
    if (t) t.checked = true;
  }
  load();
})();
