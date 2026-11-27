// When a class cannot be added because the SAME course of another batch is already there at that time (one teacher, one
// room), the server says so with error.details.combine. VistCombine.offer() turns that into a question with a Combine
// button; VistCombine.combine() is also used by the Timetable wizard's suggestions and its "Classes & labs" step.
// Nothing is combined without the person pressing the button.
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const msg = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  let dlg = null;

  function dialog() {
    if (dlg) return dlg;
    dlg = document.createElement("dialog");
    dlg.className = "modal";
    dlg.id = "combine-offer-dlg";
    dlg.setAttribute("aria-labelledby", "co-title");
    dlg.innerHTML = '<form class="modal-body" id="co-form"><h2 id="co-title">Teach them together?</h2><div id="co-text"></div>' +
      '<p class="dash-alert error" id="co-err" role="alert" hidden></p>' +
      '<div class="form-actions"><button type="submit" class="role-btn" id="co-ok">Combine</button></div>' +
      '<button type="button" class="back-link modal-close-btn" id="co-cancel">Not now</button></form>';
    document.body.appendChild(dlg);
    dlg.querySelector("#co-cancel").addEventListener("click", function () { dlg.close(); });
    return dlg;
  }

  // POST .../offerings/{with}/combine with the other section(s). Resolves to the combined view, or null (error shown in errEl / toast).
  async function combine(withId, ids, errEl, teacherId) {
    const body = { offering_ids: ids };
    if (teacherId) body.teacher_id = teacherId;
    const r = await VA.fetchJson("/api/v1/academics/offerings/" + withId + "/combine", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    });
    if (!r.ok) {
      const m = msg(r, "Could not combine these sections.");
      if (errEl) { errEl.textContent = m; errEl.hidden = false; } else VA.toast(m, "error");
      return null;
    }
    return r.body;
  }

  // body: the failed response's body. Returns true when it was a "same course of another batch" clash (and the question was asked).
  function offer(body, opts) {
    const c = body && body.error && body.error.details && body.error.details.combine;
    if (!c) return false;
    const d = dialog();
    d.querySelector("#co-text").innerHTML = "<p>" + esc(body.error.message) + "</p>" +
      "<p><strong>Combine</strong> makes " + esc(c.course) + " of <strong>" + esc(c.term) + "</strong> and <strong>" + esc(c.with_term) + "</strong> one class: " +
      "one teacher, one room, one timetable and one exam paper. Each batch keeps its own students, attendance, marks and result. " +
      "You can separate them again later from the section's page.</p>";
    const err = d.querySelector("#co-err");
    err.hidden = true;
    const ok = d.querySelector("#co-ok");
    ok.disabled = false;
    d.querySelector("#co-form").onsubmit = async function (e) {
      e.preventDefault();
      ok.disabled = true;
      const done = await combine(c.with_offering_id, [c.offering_id], err);
      ok.disabled = false;
      if (!done) return;
      d.close();
      VA.toast(c.course + ": " + c.term + " and " + c.with_term + " are now one class.", "success");
      if (opts && opts.onDone) opts.onDone(done);
    };
    d.showModal();
    return true;
  }

  async function separate(id) {
    const r = await VA.fetchJson("/api/v1/academics/offerings/" + id + "/separate", { method: "POST" });
    if (!r.ok) { VA.toast(msg(r, "Could not separate this section."), "error"); return null; }
    return r.body;
  }

  // A suggestion row (from .../smart/readiness or /sections): a Combine button, and a teacher choice when they differ.
  function controls(s) {
    const pick = !s.same_teacher && s.teachers && s.teachers.length > 1
      ? '<select class="select-input co-teacher" aria-label="Teacher of the combined class"><option value="">Teacher of the combined class…</option>' +
        s.teachers.map(function (t) { return '<option value="' + esc(t.id) + '">' + esc(t.name) + "</option>"; }).join("") + "</select> " : "";
    return pick + '<button type="button" class="role-btn small-btn co-go" data-lead="' + esc(s.lead_id) + '" data-join="' + esc((s.join_ids || []).join(",")) + '">Combine</button>';
  }

  // Wire every .co-go button inside root; onDone runs after a successful combine.
  function wire(root, onDone) {
    root.addEventListener("click", async function (e) {
      const b = e.target.closest(".co-go");
      if (!b || !root.contains(b)) return;
      const sel = b.parentElement.querySelector(".co-teacher");
      if (sel && !sel.value) { VA.toast("Choose the teacher of the combined class first.", "error"); sel.focus(); return; }
      b.disabled = true;
      const done = await combine(b.getAttribute("data-lead"), b.getAttribute("data-join").split(",").filter(Boolean), null, sel ? sel.value : null);
      b.disabled = false;
      if (done) { VA.toast("Combined: one class, one teacher, one timetable.", "success"); if (onDone) onDone(done); }
    });
  }

  window.VistCombine = { offer: offer, combine: combine, separate: separate, controls: controls, wire: wire };
})();
