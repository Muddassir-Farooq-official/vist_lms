(function () {
  "use strict";
  // My internship (intern): API /api/v1/internship/me
  const VA = window.VistAdmin;
  const API = "/api/v1/internship/me";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const send = function (url, method, body) { return VA.fetchJson(url, { method: method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); };
  const day = function (v) { return new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }); };
  const STATUS = { SUBMITTED: "Waiting for verification", VERIFIED: "Verified", REJECTED: "Rejected", REQUESTED: "Asked", APPROVED: "Approved" };
  async function load() {
    const r = await VA.fetchJson(API, { quiet: true });
    const d = r.ok ? r.body : null;
    $("internship").hidden = !d;
    if (!d) return;
    const pct = Math.min(100, Math.round(100 * d.verified_hours / d.required_hours));
    $("ip-meta").textContent = day(d.start_date) + " to " + day(d.end_date) + " · " + d.verified_hours + " of " + d.required_hours + " hours verified (" + pct + "%)" +
      (d.waiting_hours ? " · " + d.waiting_hours + " waiting" : "") + (d.status !== "ONGOING" ? " · " + d.status.toLowerCase() : "");
    $("ip-places").innerHTML = d.placements.map(function (p) { return "<li>" + esc(day(p.start_date) + " to " + day(p.end_date) + ": " + p.site + " — " + p.unit + (p.supervisor ? " (" + p.supervisor + ")" : "")) + "</li>"; }).join("") || '<li class="sd-dim">Not placed yet.</li>';
    $("ip-form").hidden = $("ip-leave").hidden = d.status !== "ONGOING";
    $("ip-logs").innerHTML = d.logs.map(function (x) {
      return '<tr data-id="' + x.id + '"><td>' + esc(day(x.day)) + "</td><td>" + esc(x.shift_name) + "</td><td>" + x.hours + "</td><td>" + STATUS[x.status] + (x.reject_note ? "<small>" + esc(x.reject_note) + "</small>" : "") + "</td><td>" +
        (x.status === "SUBMITTED" ? '<button type="button" class="row-action ip-del">Withdraw</button>' : "") + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">No duties logged yet.</td></tr>';
    $("ip-leaves").innerHTML = d.leaves.map(function (l) { return "<li>" + esc(day(l.from_date) + " to " + day(l.to_date) + ": " + l.reason) + " — " + STATUS[l.status] + (l.decision_note ? " (" + esc(l.decision_note) + ")" : "") + "</li>"; }).join("");
    $("ip-evals").innerHTML = d.evaluations.map(function (e) { return "<p>" + esc(day(e.created_at)) + ": average " + e.average + " / 5" + (e.comment ? " — " + esc(e.comment) : "") + "</p>"; }).join("") || '<p class="sd-dim">No evaluations yet.</p>';
    if (location.hash === "#internship") $("internship").scrollIntoView();
  }
  $("ip-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const r = await send(API + "/duties", "POST", { day: $("ip-day").value, shift: $("ip-shift").value, hours: Number($("ip-hours").value), note: $("ip-note").value || null });
    if (!r.ok) return VA.toast(err(r, "Not sent."), "error");
    VA.toast("Sent to your supervisor."); $("ip-note").value = ""; load();
  });
  $("ip-logs").addEventListener("click", async function (e) {
    if (!e.target.closest(".ip-del")) return;
    const r = await send(API + "/duties/" + e.target.closest("tr").getAttribute("data-id"), "DELETE");
    if (r.ok) load();
  });
  $("ip-leave").addEventListener("submit", async function (e) {
    e.preventDefault();
    const r = await send(API + "/leaves", "POST", { from_date: $("ip-l-from").value, to_date: $("ip-l-to").value, reason: $("ip-l-reason").value });
    if (!r.ok) return VA.toast(err(r, "Not sent."), "error");
    VA.toast("Asked. You are told the answer."); $("ip-leave").reset(); load();
  });
  load();
})();
