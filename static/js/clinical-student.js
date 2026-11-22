(function () {
  "use strict";
  // My clinical (student): API /api/v1/clinical/me
  const VA = window.VistAdmin;
  const API = "/api/v1/clinical/me";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const day = function (v) { return new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }); };
  const NAMES = { SUBMITTED: "Waiting for signature", SIGNED: "Signed", REJECTED: "Not signed" };

  async function load() {
    const r = await VA.fetchJson(API);
    if (!r.ok) return;
    const d = r.body;
    $("mc-rots").innerHTML = d.rotations.map(function (x) {
      const a = x.attendance;
      return "<tr" + (x.now ? ' class="row-current"' : "") + "><td><strong>" + esc(day(x.start_date)) + "</strong><small>to " + esc(day(x.end_date)) + "</small></td><td>" + esc(x.unit) + "<small>" + esc(x.site) +
        "</small></td><td>" + esc(x.supervisor || "—") + "</td><td>" + esc(x.days_text) + "<small>" + x.start_time + "–" + x.end_time + "</small></td><td>" +
        (a.percentage == null ? "—" : a.percentage + "%") + "<small>" + a.PRESENT + " present · " + a.ABSENT + " absent · " + a.LEAVE + " leave</small></td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">No rotation yet.</td></tr>';
    $("mc-prog").innerHTML = d.progress.map(function (p) {
      return "<tr><td>" + esc(p.skill) + (p.complete ? " ✓" : "") + "</td><td>" + p.signed + "</td><td>" + p.required + "</td><td>" + (p.waiting || "—") + "</td></tr>";
    }).join("") || '<tr><td colspan="4" class="sd-dim">Your program has no logbook skills set up.</td></tr>';
    $("mc-skill").innerHTML = d.progress.map(function (p) { return '<option value="' + p.skill_id + '">' + esc(p.skill) + "</option>"; }).join("");
    $("mc-rot").innerHTML = d.rotations.map(function (x) { return '<option value="' + x.id + '"' + (x.now ? " selected" : "") + ">" + esc(x.unit + " · " + day(x.start_date)) + "</option>"; }).join("");
    $("mc-entries").innerHTML = d.entries.map(function (e) {
      return '<tr data-id="' + e.id + '"><td>' + esc(day(e.day)) + "</td><td>" + esc(e.skill) + (e.note ? "<small>" + esc(e.note) + "</small>" : "") + "</td><td>" + e.times + "</td><td>" +
        NAMES[e.status] + (e.reject_note ? "<small>" + esc(e.reject_note) + "</small>" : "") + "</td><td>" + (e.status === "SUBMITTED" ? '<button type="button" class="row-action mc-del">Withdraw</button>' : "") + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">No entries yet.</td></tr>';
  }
  $("mc-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const r = await VA.fetchJson(API + "/logbook", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      skill_id: $("mc-skill").value, rotation_id: $("mc-rot").value, day: $("mc-day").value, times: Number($("mc-times").value), note: $("mc-note").value || null }) });
    if (!r.ok) { VA.toast((r.body && r.body.error && r.body.error.message) || "Not saved.", "error"); return; }
    VA.toast("Sent to your supervisor.");
    $("mc-note").value = "";
    load();
  });
  $("mc-entries").addEventListener("click", async function (e) {
    const b = e.target.closest(".mc-del");
    if (!b) return;
    const r = await VA.fetchJson(API + "/logbook/" + b.closest("tr").getAttribute("data-id"), { method: "DELETE" });
    if (r.ok) load();
  });
  load();
})();
