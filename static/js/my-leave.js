(function () {
  "use strict";
  // My pay & attendance -> Leave: apply, see what is left, withdraw a waiting application. API: /api/v1/staff-attendance/leave*
  const VA = window.VistAdmin, esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const box = $("my-leave");
  if (!box) return;
  const STATUS = { REQUESTED: "Waiting", APPROVED: "Approved", REJECTED: "Not approved", CANCELLED: "Withdrawn" };
  const day = function (iso) { return new Date(String(iso).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" }); };

  function message(body, fallback) {
    const e = body && (body.error || body);
    return (e && (e.message || (typeof e.detail === "string" ? e.detail : ""))) || fallback;
  }

  async function load() {
    const r = await VA.fetchJson("/api/v1/staff-attendance/leave/me", { quiet: true });
    if (!r.ok) { box.hidden = true; return; }                // not a member of staff (e.g. an admin-only sign-in)
    box.hidden = false;
    const b = r.body.balances;
    $("ml-balances").innerHTML = [["Casual leave left this month", b.casual_left_month + " of " + b.casual_allowed_month],
      ["Sick leave left this year", b.sick_left_year + " of " + b.sick_allowed_year]].map(function (x) {
      return '<div class="kpi"><div class="kpi-label">' + esc(x[0]) + '</div><div class="kpi-value">' + esc(x[1]) + "</div></div>";
    }).join("");
    const rows = r.body.requests;
    $("ml-mine").innerHTML = rows.length ? rows.map(function (x) {
      return "<tr><td>" + esc(day(x.first_day)) + (x.last_day !== x.first_day ? " – " + esc(day(x.last_day)) : "") + " (" + x.days + ")</td><td>" + esc(x.kind_label) +
        "</td><td>" + esc(x.reason) + (x.note_url ? ' <a class="link-btn" href="' + esc(x.note_url) + '" target="_blank" rel="noopener">Medical note</a>' : "") +
        "</td><td>" + esc(STATUS[x.status] || x.status) + (x.decision_note ? '<div class="cc-meta">' + esc(x.decision_note) + "</div>" : "") +
        "</td><td>" + (x.status === "REQUESTED" ? '<button type="button" class="link-btn" data-withdraw="' + esc(x.id) + '">Withdraw</button>' : "") + "</td></tr>";
    }).join("") : '<tr><td colspan="5" class="empty-state">No applications yet.</td></tr>';
  }

  $("ml-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const err = $("ml-error");
    err.hidden = true;
    const from = $("ml-from").value, to = $("ml-to").value || $("ml-from").value, reason = $("ml-reason").value.trim();
    if (!from) { err.textContent = "Choose the first day."; err.hidden = false; return; }
    if (reason.length < 5) { err.textContent = "Write the reason (a few words)."; err.hidden = false; return; }
    const kind = $("ml-kind").value, file = $("ml-note").files[0];
    if (kind === "SICK" && !file) { err.textContent = "Sick leave needs a medical note: attach a PDF or a photo."; err.hidden = false; return; }
    $("ml-apply").disabled = true;
    const form = new FormData();
    form.append("kind", kind); form.append("first_day", from); form.append("last_day", to); form.append("reason", reason);
    if (kind === "SICK" && file) form.append("note", file);
    const r = await VA.fetchJson("/api/v1/staff-attendance/leave", { method: "POST", body: form });
    $("ml-apply").disabled = false;
    if (!r.ok) { err.textContent = message(r.body, "Could not send the application."); err.hidden = false; return; }
    VA.toast("Sent: " + r.body.days + " day(s) of " + r.body.kind_label.toLowerCase() + ". You will be told the decision.");
    $("ml-reason").value = ""; $("ml-from").value = ""; $("ml-to").value = ""; $("ml-note").value = "";
    load();
  });
  function noteBox() { $("ml-note-field").hidden = $("ml-kind").value !== "SICK"; }
  $("ml-kind").addEventListener("change", noteBox);
  noteBox();
  $("ml-from").addEventListener("change", function () { if (!$("ml-to").value || $("ml-to").value < $("ml-from").value) $("ml-to").value = $("ml-from").value; });
  $("ml-mine").addEventListener("click", async function (e) {
    const b = e.target.closest("[data-withdraw]");
    if (!b) return;
    if (!(await VA.confirm("Withdraw this leave application?", "Withdraw"))) return;
    const r = await VA.fetchJson("/api/v1/staff-attendance/leave/" + b.getAttribute("data-withdraw") + "/cancel", { method: "POST" });
    if (!r.ok) return VA.toast(message(r.body, "Could not withdraw it."), "error");
    load();
  });
  document.addEventListener("vistadmin:ready", load);
})();
