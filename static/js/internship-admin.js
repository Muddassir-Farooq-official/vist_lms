(function () {
  "use strict";
  // Internship (administration): API /api/v1/internship; wards and supervisors from /api/v1/clinical/setup
  const VA = window.VistAdmin;
  const API = "/api/v1/internship";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const send = function (url, method, body) { return VA.fetchJson(url, { method: method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); };
  const day = function (v) { return v ? new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—"; };
  const STATUS = { SUBMITTED: "Waiting", VERIFIED: "Verified", REJECTED: "Rejected", REQUESTED: "Asked", APPROVED: "Approved" };
  let current = null;
  async function setup() {
    const r = await VA.fetchJson("/api/v1/clinical/setup", { quiet: true });
    if (!r.ok) return;
    const units = [];
    r.body.sites.forEach(function (s) { s.units.forEach(function (u) { units.push('<option value="' + u.id + '">' + esc(s.name + " — " + u.name) + "</option>"); }); });
    $("in-unit").innerHTML = units.join("");
    $("in-sup").insertAdjacentHTML("beforeend", r.body.supervisors.filter(function (x) { return x.is_active; }).map(function (x) { return '<option value="' + x.id + '">' + esc(x.name) + "</option>"; }).join(""));
  }
  async function load() {
    const r = await VA.fetchJson(API + "/interns?status=" + $("in-status").value);
    $("in-list").innerHTML = (r.ok ? r.body : []).map(function (i) {
      const pct = Math.min(100, Math.round(100 * i.verified_hours / i.required_hours));
      return '<tr data-id="' + i.id + '"><td><strong>' + esc(i.name) + "</strong><small>" + esc((i.roll_number || "") + " · " + day(i.start_date) + " to " + day(i.end_date)) + "</small></td><td>" + esc(i.placed_at || "—") +
        "</td><td>" + i.verified_hours + " / " + i.required_hours + " (" + pct + "%)" + (i.waiting_hours ? "<small>" + i.waiting_hours + " waiting</small>" : "") + "</td><td>" + i.evaluations + "</td><td>" + (i.leave_requests || "—") +
        '</td><td><button type="button" class="row-action in-open">Open</button></td></tr>';
    }).join("") || '<tr><td colspan="6" class="sd-dim">No interns.</td></tr>';
  }
  async function open(id) {
    const r = await VA.fetchJson(API + "/interns/" + id);
    if (!r.ok) return;
    const d = current = r.body;
    $("in-d-title").textContent = d.name + " — " + (d.roll_number || "");
    $("in-d-meta").textContent = day(d.start_date) + " to " + day(d.end_date) + " · " + d.verified_hours + " of " + d.required_hours + " hours verified" + (d.waiting_hours ? " (" + d.waiting_hours + " waiting)" : "") +
      " · " + d.leave_days + " day(s) of approved leave · " + d.status.toLowerCase() + (d.note ? " · " + d.note : "");
    $("in-places").innerHTML = d.placements.map(function (p) { return "<li>" + esc(day(p.start_date) + " to " + day(p.end_date) + ": " + p.site + " — " + p.unit + (p.supervisor ? " (" + p.supervisor + ")" : "")) + "</li>"; }).join("") || '<li class="sd-dim">Not placed yet.</li>';
    $("in-p-from").min = $("in-p-to").min = d.start_date; $("in-p-from").max = $("in-p-to").max = d.end_date;
    $("in-leaves").innerHTML = d.leaves.map(function (l) {
      return '<tr data-id="' + l.id + '"><td>' + esc(day(l.from_date) + " to " + day(l.to_date)) + " (" + l.days + " days)</td><td>" + esc(l.reason) + "</td><td>" + STATUS[l.status] + (l.decision_note ? "<small>" + esc(l.decision_note) + "</small>" : "") + "</td><td>" +
        (l.status === "REQUESTED" ? '<button type="button" class="row-action in-lv-yes">Approve</button> <button type="button" class="row-action in-lv-no">Refuse…</button>' : "") + "</td></tr>";
    }).join("") || '<tr><td class="sd-dim">No leave asked.</td></tr>';
    $("in-logs").innerHTML = d.logs.map(function (x) {
      return '<tr data-id="' + x.id + '"><td>' + esc(day(x.day)) + "</td><td>" + esc(x.shift_name) + "</td><td>" + x.hours + "</td><td>" + STATUS[x.status] + (x.reject_note ? "<small>" + esc(x.reject_note) + "</small>" : "") + "</td><td>" +
        (x.status === "SUBMITTED" ? '<button type="button" class="row-action in-ok">Verify</button> <button type="button" class="row-action in-no">Reject…</button>' : "") + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">No duties logged.</td></tr>';
    $("in-evals").innerHTML = d.evaluations.map(function (e) {
      return "<p><b>" + esc(day(e.created_at)) + "</b> · average " + e.average + " / 5 · " + d.criteria.map(function (c) { return esc(c.name) + " " + e.scores[c.key]; }).join(", ") + (e.comment ? "<br>" + esc(e.comment) : "") + "</p>";
    }).join("") || '<p class="sd-dim">No evaluations yet (the supervisor evaluates from their portal).</p>';
    const ongoing = d.status === "ONGOING";
    $("in-complete").hidden = $("in-withdraw").hidden = $("in-place-form").hidden = !ongoing;
    $("in-detail").hidden = false;
  }
  $("in-status").addEventListener("change", load);
  $("in-list").addEventListener("click", function (e) { if (e.target.closest(".in-open")) open(e.target.closest("tr").getAttribute("data-id")); });
  $("in-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const r = await send(API + "/start", "POST", { roll_numbers: $("in-rolls").value.split(/[\s,;]+/).filter(Boolean), start_date: $("in-from").value, end_date: $("in-to").value,
      required_hours: Number($("in-hours").value) });
    if (!r.ok) return VA.toast(err(r, "Not started."), "error");
    VA.toast(r.body.started + " started" + (r.body.skipped.length ? "; skipped: " + r.body.skipped.join("; ") : "") + ".");
    $("in-rolls").value = ""; load();
  });
  $("in-place-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const r = await send(API + "/interns/" + current.id + "/placements", "POST", { unit_id: $("in-unit").value, supervisor_id: $("in-sup").value || null, start_date: $("in-p-from").value, end_date: $("in-p-to").value });
    if (!r.ok) return VA.toast(err(r, "Not placed."), "error");
    open(current.id); load();
  });
  $("in-leaves").addEventListener("click", async function (e) {
    const yes = e.target.closest(".in-lv-yes"), no = e.target.closest(".in-lv-no");
    if (!yes && !no) return;
    let note = null;
    if (no) { note = await VA.ask({ title: "Refuse the leave", label: "Why (the intern sees this)", min: 3 }); if (!note) return; }
    const r = await send(API + "/leaves/" + e.target.closest("tr").getAttribute("data-id"), "POST", { approve: !!yes, note: note });
    if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
    open(current.id); load();
  });
  $("in-logs").addEventListener("click", async function (e) {
    const ok = e.target.closest(".in-ok"), no = e.target.closest(".in-no");
    if (!ok && !no) return;
    let note = null;
    if (no) { note = await VA.ask({ title: "Reject the duty", label: "Why (the intern sees this)", min: 3 }); if (!note) return; }
    const r = await send(API + "/duties/" + e.target.closest("tr").getAttribute("data-id"), "POST", { approve: !!ok, note: note });
    if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
    open(current.id); load();
  });
  async function finish(withdraw) {
    const short = !withdraw && (current.verified_hours < current.required_hours || !current.evaluations.length);
    let note = null;
    if (withdraw || short) {
      note = await VA.ask({ title: withdraw ? "Withdraw the internship" : "Complete although not everything is done", label: "Reason (at least 10 letters; recorded)", min: 10 });
      if (!note) return;
    } else if (!(await VA.confirm("Mark this internship complete?"))) return;
    const r = await send(API + "/interns/" + current.id + "/complete", "POST", { withdraw: withdraw, note: note });
    if (!r.ok) return VA.toast(err(r, "Not done."), "error");
    open(current.id); load();
  }
  $("in-complete").addEventListener("click", function () { finish(false); });
  $("in-withdraw").addEventListener("click", function () { finish(true); });
  setup(); load();
})();
