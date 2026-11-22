(function () {
  "use strict";
  // My mentees (mentor): API /api/v1/mentoring
  const VA = window.VistAdmin;
  const API = "/api/v1/mentoring";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const day = function (v) { return v ? new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—"; };
  const iso = function (d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); };
  let current = null;
  async function load() {
    const r = await VA.fetchJson(API + "/mentees");
    $("me-list").innerHTML = (r.ok ? r.body : []).map(function (s) {
      return '<tr id="s' + s.id + '" data-id="' + s.id + '"><td><strong>' + esc(s.name) + "</strong><small>" + esc((s.roll_number || "") + (s.program ? " · " + s.program : "") + (s.semester ? " · sem " + s.semester : "")) + "</small></td><td>" +
        (s.signs.length ? s.signs.map(esc).join("<br>") : '<span class="sd-dim">none</span>') + "</td><td>" + esc(day(s.last_met)) + "</td><td>" + (s.follow_up_on ? (s.follow_up_due ? "<b>Due " + esc(day(s.follow_up_on)) + "</b>" : esc(day(s.follow_up_on))) : "—") +
        '</td><td><button type="button" class="row-action me-open">Open</button></td></tr>';
    }).join("") || '<tr><td colspan="5" class="sd-dim">No mentees are given to you.</td></tr>';
    const hash = location.hash.replace("#s", "");
    if (hash && document.getElementById("s" + hash)) open(hash);
  }
  async function open(id) {
    const r = await VA.fetchJson(API + "/students/" + id);
    if (!r.ok) return VA.toast(err(r, "Could not open."), "error");
    const d = r.body;
    current = id;
    $("me-name").textContent = d.name + " — " + (d.roll_number || "");
    $("me-meta").textContent = [d.program, d.semester ? "semester " + d.semester : "", d.contact_no ? "phone " + d.contact_no : "", d.guardian_name ? "guardian " + d.guardian_name : ""].filter(Boolean).join(" · ");
    $("me-signs").innerHTML = d.signs.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") || '<li class="sd-dim">No warning signs.</li>';
    $("me-meetings").innerHTML = d.meetings.map(function (m) {
      return '<article class="sh-card"><h3>' + esc(day(m.met_on)) + " · " + esc(m.kind_name) + "</h3><p>" + esc(m.notes).replace(/\n/g, "<br>") + "</p>" + (m.actions ? "<p><b>Agreed:</b> " + esc(m.actions) + "</p>" : "") +
        (m.follow_up_on ? "<p>Follow up on " + esc(day(m.follow_up_on)) + "</p>" : "") + (m.referral_status ? "<p><b>Referred to the principal</b> (" + (m.referral_status === "OPEN" ? "waiting" : "answered") + "): " + esc(m.referral_reason) +
        (m.resolution ? "<br><b>Answer:</b> " + esc(m.resolution) : "") + "</p>" : "") + "</article>";
    }).join("") || '<p class="sd-dim">No meetings yet.</p>';
    $("me-date").value = iso(new Date()); $("me-date").max = iso(new Date());
    $("me-file").hidden = false;
    $("me-file").scrollIntoView({ behavior: "smooth" });
  }
  $("me-list").addEventListener("click", function (e) { if (e.target.closest(".me-open")) open(e.target.closest("tr").getAttribute("data-id")); });
  $("me-refer").addEventListener("change", function () { $("me-reason-l").hidden = !$("me-refer").checked; });
  $("me-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const r = await VA.fetchJson(API + "/students/" + current + "/meetings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      met_on: $("me-date").value, kind: $("me-kind").value, notes: $("me-notes").value, actions: $("me-actions").value || null, follow_up_on: $("me-fu").value || null,
      refer: $("me-refer").checked, referral_reason: $("me-refer").checked ? $("me-reason").value : null }) });
    if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
    VA.toast("Saved."); $("me-form").reset(); $("me-reason-l").hidden = true; open(current); load();
  });
  load();
})();
