(function () {
  "use strict";
  // Substitute teachers (administration): API /api/v1/substitutes
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const day = function (v) { return new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }); };
  const API = "/api/v1/substitutes";
  async function load() {
    const r = await VA.fetchJson(API + ($("sb-status").value ? "?status=" + $("sb-status").value : ""));
    const names = { PROPOSED: "To confirm", CONFIRMED: "Confirmed", CANCELLED: "Class cancelled" };
    $("sb-body").innerHTML = (r.ok ? r.body : []).map(function (x) {
      const open = x.status !== "CANCELLED";
      return '<tr data-id="' + x.class_session_id + '"><td><strong>' + esc(day(x.date)) + "</strong><small>" + x.start + "–" + x.end + " · " + esc(x.room) + "</small></td>" +
        "<td>" + esc(x.course) + "<small>" + esc(x.term_label + " · " + x.section) + "</small></td><td>" + esc(x.absent) + "</td>" +
        "<td>" + (open ? '<select class="text-input sb-pick" aria-label="Substitute"><option value="' + (x.substitute_id || "") + '">' + esc(x.substitute || "— nobody free —") + "</option></select>" : "—") + "</td>" +
        "<td>" + names[x.status] + (x.note ? "<small>" + esc(x.note) + "</small>" : "") + "</td><td>" +
        (open ? '<button type="button" class="row-action sb-ok">Confirm</button> <button type="button" class="row-action sb-cancel">Cancel class</button>' : "") + "</td></tr>";
    }).join("") || '<tr><td colspan="6" class="sd-dim">Nothing here.</td></tr>';
  }
  $("sb-body").addEventListener("focusin", async function (e) {
    const sel = e.target.closest(".sb-pick");
    if (!sel || sel.dataset.loaded) return;
    sel.dataset.loaded = "1";
    const id = sel.closest("tr").getAttribute("data-id"), cur = sel.value;
    const r = await VA.fetchJson(API + "/" + id + "/candidates");
    if (!r.ok) return;
    sel.innerHTML = r.body.map(function (c) {
      return '<option value="' + c.employee_id + '"' + (c.employee_id === cur ? " selected" : "") + ">" + esc(c.name) + (c.free ? " (free, " + c.substitutions_this_week + " this week)" : " — " + (c.busy_reason || "busy")) + "</option>";
    }).join("");
  });
  $("sb-body").addEventListener("click", async function (e) {
    const tr = e.target.closest("tr");
    if (!tr) return;
    const id = tr.getAttribute("data-id");
    let body = null;
    if (e.target.classList.contains("sb-ok")) {
      body = { action: "CONFIRM", employee_id: tr.querySelector(".sb-pick").value || null };
    } else if (e.target.classList.contains("sb-cancel")) {
      if (!window.confirm("Cancel this class? Students see it as cancelled.")) return;
      body = { action: "CANCEL" };
    } else return;
    let r = await VA.fetchJson(API + "/" + id, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok && body.action === "CONFIRM" && /Write a note/.test(err(r, ""))) {
      const note = window.prompt(err(r, "") + "\nWhy choose them anyway?");
      if (!note) return;
      body.note = note;
      r = await VA.fetchJson(API + "/" + id, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    }
    if (!r.ok) { VA.toast(err(r, "Not saved."), "error"); return; }
    VA.toast(body.action === "CANCEL" ? "Class cancelled." : "Confirmed: the teacher is told.");
    load();
  });
  $("sb-status").addEventListener("change", load);
  load();
})();
