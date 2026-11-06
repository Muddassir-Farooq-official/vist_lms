(function () {
  "use strict";

  // Tasks -> Classes I'm covering: classes given to me for an absent teacher; I take their attendance here.
  const VA = window.VistAdmin, esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/teaching/covers/me";
  if (!$("cv-list")) return;

  function hm(t) { return t ? String(t).slice(0, 5) : ""; }
  function msg(body, fallback) { return (body && body.error && body.error.message) || fallback; }
  const LABEL = { UPCOMING: "Not started", OPEN: "Take attendance now", ON_TIME: "Attendance taken", LATE: "Taken late", MISSED: "Waiting for the office" };

  async function load() {
    const r = await VA.fetchJson(API);
    if (!r.ok) return;
    const items = r.body;
    $("cv-tab").hidden = items.length === 0;
    const due = items.filter(function (c) { return c.can_mark && c.state === "OPEN"; }).length;
    $("cv-count").textContent = due ? String(due) : "";
    $("cv-empty").hidden = items.length > 0;
    $("cv-list").innerHTML = items.map(function (c) {
      return '<div class="cv-item" data-id="' + c.id + '"><div><b>' + esc(c.description) + '</b><small class="list-sub">' + hm(c.start_time) + "–" + hm(c.end_time) +
        " · room " + esc(c.room) + (c.amount > 0 ? " · cover pay " + (VA.formatMoney ? VA.formatMoney(c.amount) : c.amount) : "") + "</small></div>" +
        '<span class="sec-pill ' + (c.state === "ON_TIME" ? "tone-green" : c.state === "OPEN" ? "tone-teal" : c.state === "MISSED" ? "tone-red" : "tone-grey") + '">' + esc(LABEL[c.state] || c.state) + "</span>" +
        (c.can_mark ? '<button type="button" class="role-btn small-btn" data-open="' + c.id + '">' + (c.state === "ON_TIME" ? "Correct attendance" : "Take attendance") + "</button>" : "") +
        (c.state === "MISSED" ? '<small class="list-sub cv-note">The marking window has closed. The office records this class\u2019s attendance (Attendance \u2192 Teachers \u2192 Record attendance); your cover pay is added once it is recorded.</small>' : "") +
        '<div class="cv-register" hidden></div></div>';
    }).join("");
    if (location.hash === "#covers" && items.length && VA.showTab) VA.showTab("covers");
  }

  async function openRegister(id, box) {
    if (!box.hidden) { box.hidden = true; return; }
    const r = await VA.fetchJson(API + "/" + id + "/register");
    if (!r.ok) return VA.toast(msg(r.body, "Could not open the register."), "error");
    const s = r.body.students;
    box.innerHTML = (s.length ? '<div class="form-actions"><button type="button" class="role-btn small-btn" data-all="1">All present</button> <button type="button" class="role-btn small-btn" data-all="0">All absent</button></div>' +
      '<div class="table-scroll"><table class="dash-table"><thead><tr><th>Student</th><th>Present</th></tr></thead><tbody>' + s.map(function (x) {
        return "<tr><td>" + esc(x.name) + ' <small class="list-sub">' + esc(x.roll_number || "") + '</small></td><td><input type="checkbox" value="' + x.id + '" aria-label="' + esc(x.name) + ' present"' +
          (x.status === "PRESENT" || !x.status ? " checked" : "") + "></td></tr>";
      }).join("") + "</tbody></table></div>" : '<p class="empty-state">No students are enrolled in this class.</p>') +
      '<p class="dash-alert error" role="alert" hidden></p><div class="form-actions"><button type="button" class="role-btn" data-save="' + id + '">Save attendance</button></div>';
    box.hidden = false;
  }

  $("cv-list").addEventListener("click", async function (e) {
    const open = e.target.closest("[data-open]"), all = e.target.closest("[data-all]"), save = e.target.closest("[data-save]");
    const item = e.target.closest(".cv-item");
    if (open) return openRegister(open.getAttribute("data-open"), item.querySelector(".cv-register"));
    if (all) { item.querySelectorAll('.cv-register input[type="checkbox"]').forEach(function (b) { b.checked = all.getAttribute("data-all") === "1"; }); return; }
    if (save) {
      const ids = Array.prototype.map.call(item.querySelectorAll('.cv-register input[type="checkbox"]:checked'), function (b) { return b.value; });
      save.disabled = true;
      const r = await VA.fetchJson(API + "/" + save.getAttribute("data-save") + "/mark", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ present_student_ids: ids }) });
      save.disabled = false;
      if (!r.ok) { const err = item.querySelector(".cv-register .dash-alert"); err.textContent = msg(r.body, "Could not save the attendance."); err.hidden = false; return; }
      VA.toast("Attendance saved: " + r.body.present + " of " + r.body.marked + " present.");
      load();
    }
  });

  document.addEventListener("vistadmin:ready", load);
})();