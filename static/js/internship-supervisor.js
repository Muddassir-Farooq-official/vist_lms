(function () {
  "use strict";
  // Interns placed with a clinical supervisor: API /api/v1/internship/supervise
  const VA = window.VistAdmin;
  const API = "/api/v1/internship";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const send = function (url, method, body) { return VA.fetchJson(url, { method: method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); };
  const day = function (v) { return new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }); };
  const STATUS = { SUBMITTED: "Waiting", VERIFIED: "Verified", REJECTED: "Rejected" };
  async function load() {
    const r = await VA.fetchJson(API + "/supervise", { quiet: true });
    const rows = r.ok ? r.body : [];
    $("iv-card").hidden = !rows.length;
    $("iv-list").innerHTML = rows.map(function (p) {
      const logs = p.logs.map(function (x) {
        return '<tr data-id="' + x.id + '"><td>' + esc(day(x.day)) + "</td><td>" + esc(x.shift_name) + "</td><td>" + x.hours + "</td><td>" + STATUS[x.status] + (x.note ? "<small>" + esc(x.note) + "</small>" : "") + "</td><td>" +
          (x.status === "SUBMITTED" ? '<button type="button" class="row-action iv-ok">Verify</button> <button type="button" class="row-action iv-no">Reject…</button>' : "") + "</td></tr>";
      }).join("") || '<tr><td colspan="5" class="sd-dim">No duties logged.</td></tr>';
      const crit = p.criteria.map(function (c) {
        return '<label>' + esc(c.name) + '<select name="' + c.key + '" class="text-input">' + [5, 4, 3, 2, 1].map(function (n) { return "<option>" + n + "</option>"; }).join("") + "</select></label>";
      }).join("");
      return '<article class="sh-card" data-placement="' + p.placement_id + '"><h3>' + esc(p.name) + " <small>" + esc(p.roll_number || "") + "</small></h3><p class=\"panel-hint\">" + esc(p.where) + " · " + esc(day(p.start_date)) + " to " + esc(day(p.end_date)) +
        '</p><div class="table-scroll"><table class="dash-table"><thead><tr><th>Day</th><th>Shift</th><th>Hours</th><th>Status</th><th></th></tr></thead><tbody>' + logs + "</tbody></table></div>" +
        '<form class="form-grid iv-eval">' + crit + '<label class="cl-wide">Comment<input name="comment" class="text-input" maxlength="1000"></label><div class="decision-bar"><button type="submit" class="role-btn secondary-btn">Save evaluation</button></div></form></article>';
    }).join("");
  }
  $("iv-list").addEventListener("click", async function (e) {
    const ok = e.target.closest(".iv-ok"), no = e.target.closest(".iv-no");
    if (!ok && !no) return;
    let note = null;
    if (no) { note = await VA.ask({ title: "Reject the duty", label: "Why (the intern sees this)", min: 3 }); if (!note) return; }
    const r = await send(API + "/duties/" + e.target.closest("tr").getAttribute("data-id"), "POST", { approve: !!ok, note: note });
    if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
    load();
  });
  $("iv-list").addEventListener("submit", async function (e) {
    const f = e.target.closest(".iv-eval");
    if (!f) return;
    e.preventDefault();
    const scores = {};
    f.querySelectorAll("select").forEach(function (s) { scores[s.name] = Number(s.value); });
    const r = await send(API + "/placements/" + f.closest("[data-placement]").getAttribute("data-placement") + "/evaluate", "POST", { scores: scores, comment: f.elements.comment.value || null });
    if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
    VA.toast("Evaluation saved (average " + r.body.average + ")."); f.reset();
  });
  load();
})();
