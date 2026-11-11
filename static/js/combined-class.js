/* Admin -> a course section -> Combined class (/api/v1/academics/offerings/{id}/combined|combine|separate). */
(function () {
  "use strict";
  const OFFERING_ID = document.currentScript && document.currentScript.dataset.offeringId;
  const API = "/api/v1/academics/offerings/" + OFFERING_ID;
  const box = document.getElementById("combined-box");
  let VA = null;

  function esc(s) { return VA.escapeHtml(s == null ? "" : String(s)); }
  function msg(r, f) { return (r.body && r.body.error && r.body.error.message) || f; }
  function who(m) {
    return "Section " + esc(m.section_label) + (m.programs.length ? " · " + esc(m.programs.join(", ")) : "") + " · " + m.students + " student" + (m.students === 1 ? "" : "s");
  }

  async function load() {
    const r = await VA.fetchJson(API + "/combined");
    if (!r.ok) { box.innerHTML = '<p class="dash-alert error">' + esc(msg(r, "Could not load.")) + "</p>"; return; }
    draw(r.body);
  }

  function draw(d) {
    let html = "";
    if (d.combined) {
      html += '<p class="cb-on"><strong>Combined class</strong> of ' + d.members.length + " sections, " + d.students + " students in all" +
        (d.members[0].teacher ? ", taught by " + esc(d.members[0].teacher) : "") + ".</p><ul class=\"cb-list\">" +
        d.members.map(function (m) {
          return "<li>" + who(m) + (m.id === OFFERING_ID ? ' <span class="status-pill active">this section</span>' : ' <a href="/dashboard/admin/offerings/' + esc(m.id) + '">Open</a>') + "</li>";
        }).join("") + "</ul>" +
        '<button type="button" class="role-btn secondary-btn small-btn" id="cb-separate">Take this section out of the combined class</button>';
    } else {
      html += '<p class="sd-dim">This section is taught on its own.</p>';
    }
    if (d.can_join.length) {
      const teachers = {};
      d.members.concat(d.can_join).forEach(function (m) { if (m.teacher_id) teachers[m.teacher_id] = m.teacher; });
      const tIds = Object.keys(teachers);
      html += '<div class="cb-join"><p><strong>' + (d.combined ? "Add sections" : "Combine with") + "</strong> (other sections of this course this term):</p>" +
        d.can_join.map(function (m) {
          return '<label class="cb-opt"><input type="checkbox" value="' + esc(m.id) + '"> ' + who(m) + (m.teacher ? ' <small>' + esc(m.teacher) + "</small>" : "") + "</label>";
        }).join("") +
        (tIds.length > 1 ? '<label class="cb-teacher">Teacher of the combined class <select id="cb-teacher" class="select-input">' +
          tIds.map(function (id) { return '<option value="' + esc(id) + '">' + esc(teachers[id]) + "</option>"; }).join("") + "</select></label>" : "") +
        '<p class="dash-alert error" id="cb-error" hidden></p><button type="button" class="role-btn small-btn" id="cb-go">Combine</button>' +
        '<p class="panel-hint">A section without a timetable gets this one’s; a section with a different timetable or marks scheme must be made the same first.</p></div>';
    } else if (!d.combined) {
      html += '<p class="sd-dim">There is no other section of this course this term to combine with.</p>';
    }
    box.innerHTML = html;
    const sep = document.getElementById("cb-separate");
    if (sep) sep.addEventListener("click", async function () {
      if (!(await VA.confirm("Take this section out of the combined class? It keeps its timetable and teacher; change them afterwards if needed.", "Take out"))) return;
      const r = await VA.fetchJson(API + "/separate", { method: "POST" });
      if (!r.ok) { VA.toast(msg(r, "Could not do that."), "error"); return; }
      VA.toast("This section is now taught on its own.", "success");
      draw(r.body);
    });
    const go = document.getElementById("cb-go");
    if (go) go.addEventListener("click", async function () {
      const err = document.getElementById("cb-error"); err.hidden = true;
      const ids = Array.prototype.map.call(box.querySelectorAll(".cb-opt input:checked"), function (c) { return c.value; });
      if (!ids.length) { err.textContent = "Tick the sections to combine with."; err.hidden = false; return; }
      const t = document.getElementById("cb-teacher");
      go.disabled = true;
      const r = await VA.fetchJson(API + "/combine", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ offering_ids: ids, teacher_id: t ? t.value : null }) });
      go.disabled = false;
      if (!r.ok) { err.textContent = msg(r, "Could not combine."); err.hidden = false; return; }
      VA.toast("Combined. The teacher now sees one class.", "success");
      draw(r.body);
    });
  }

  document.addEventListener("vistadmin:ready", function () { VA = window.VistAdmin; load(); });
})();
