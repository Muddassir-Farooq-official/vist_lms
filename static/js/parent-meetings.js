/* Parent -> Parent-teacher meetings (School mode): /api/v1/school/children/{id}/ptm, /ptm/slots/{id}/book|release. */
(function () {
  "use strict";
  const P = window.ParentChild, VA = window.VistAdmin, el = P.el;
  const $ = function (id) { return document.getElementById(id); };
  let child = null;

  function draw(d) {
    const box = $("pm-view");
    box.hidden = false; box.textContent = "";
    if (!d.class_teacher) { box.appendChild(el("p", "pp-empty", "Your child's section has no class teacher yet.")); return; }
    box.appendChild(el("p", null, "Class teacher: " + d.class_teacher));
    if (!d.days.length) { box.appendChild(el("p", "pp-empty", "No meeting day is set. You will be told when the school sets one.")); return; }
    d.days.forEach(function (day) {
      const card = el("div", "pm-day");
      card.appendChild(el("h4", null, P.day(day.day, { weekday: "long", day: "numeric", month: "long" }) + (day.note ? " · " + day.note : "")));
      card.appendChild(el("p", "hw-meta", day.booked ? "Your time: " + day.booked + ". Choose another time to change it." : "Choose a time:"));
      const wrap = el("div", "crs-chips");
      day.slots.forEach(function (s) {
        const b = el("button", "mc-tab" + (s.mine ? " active" : ""), s.start_time); b.type = "button";
        b.disabled = !s.free && !s.mine;
        b.setAttribute("aria-pressed", String(!!s.mine));
        if (!s.free && !s.mine) b.title = "Taken";
        b.addEventListener("click", async function () {
          if (s.mine) {
            if (!(await VA.confirm("Cancel your " + s.start_time + " meeting?", "Cancel meeting"))) return;
            const r = await VA.fetchJson("/api/v1/school/ptm/slots/" + s.id + "/release", { method: "POST" });
            if (r.ok) { VA.toast("Cancelled.", "success"); draw(r.body); } else VA.toast("Could not cancel.", "error");
            return;
          }
          const r = await VA.fetchJson("/api/v1/school/ptm/slots/" + s.id + "/book", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ student_id: child }) });
          if (!r.ok) { VA.toast((r.body && r.body.error && r.body.error.message) || "Could not book.", "error"); load(); return; }
          VA.toast("Booked for " + s.start_time + ".", "success");
          draw(r.body);
        });
        wrap.appendChild(b);
      });
      card.appendChild(wrap);
      box.appendChild(card);
    });
  }

  async function load() {
    const r = await VA.fetchJson("/api/v1/school/children/" + encodeURIComponent(child) + "/ptm");
    if (!r.ok) { P.error("Could not load the meetings. Please refresh the page."); return; }
    draw(r.body);
  }

  P.onChange = function (id) { child = id; load(); };
})();
