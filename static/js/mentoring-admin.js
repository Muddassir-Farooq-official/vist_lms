(function () {
  "use strict";
  // Mentors (administration): API /api/v1/mentoring
  const VA = window.VistAdmin;
  const API = "/api/v1/mentoring";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const send = function (url, method, body) { return VA.fetchJson(url, { method: method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); };
  const day = function (v) { return new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }); };
  let timer = null;
  async function programs() {
    const r = await VA.fetchJson("/api/v1/programs", { quiet: true });
    const rows = r.ok ? (r.body.items || r.body) : [];
    $("mt-program").insertAdjacentHTML("beforeend", rows.map(function (p) { return '<option value="' + p.id + '">' + esc(p.code) + "</option>"; }).join(""));
  }
  async function load() {
    const qs = new URLSearchParams();
    if ($("mt-program").value) qs.set("program_id", $("mt-program").value);
    if ($("mt-sem").value) qs.set("semester", $("mt-sem").value);
    if ($("mt-unassigned").checked) qs.set("unassigned", "true");
    const r = await VA.fetchJson(API + "/overview?" + qs.toString());
    if (!r.ok) return;
    const d = r.body;
    $("mt-refcount").textContent = d.open_referrals ? "(" + d.open_referrals + ")" : "";
    $("mt-mentors").textContent = d.mentors.length ? "Mentors: " + d.mentors.map(function (m) { return m.name + " (" + m.mentees + ")"; }).join(", ") : "No mentors yet.";
    $("mt-all").checked = false;
    $("mt-list").innerHTML = d.students.map(function (s) {
      return '<tr data-id="' + s.id + '"><td><input type="checkbox" class="mt-pick" aria-label="Choose"></td><td>' + esc(s.roll_number) + "</td><td>" + esc(s.name) + "</td><td>" + esc((s.program || "") + (s.semester ? " · sem " + s.semester : "")) +
        "</td><td>" + (s.mentor ? esc(s.mentor) : '<span class="sd-dim">—</span>') + "</td><td>" + (s.mentor ? '<button type="button" class="row-action mt-end">End</button>' : "") + "</td></tr>";
    }).join("") || '<tr><td colspan="6" class="sd-dim">No students.</td></tr>';
  }
  ["mt-program", "mt-unassigned"].forEach(function (id) { $(id).addEventListener("change", load); });
  $("mt-sem").addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(load, 300); });
  $("mt-all").addEventListener("change", function () { $("mt-list").querySelectorAll(".mt-pick").forEach(function (c) { c.checked = $("mt-all").checked; }); });
  $("mt-list").addEventListener("click", async function (e) {
    if (!e.target.closest(".mt-end")) return;
    if (!(await VA.confirm("End this student's mentor? The meeting notes stay."))) return;
    const r = await send(API + "/students/" + e.target.closest("tr").getAttribute("data-id") + "/mentor", "DELETE");
    if (!r.ok) return VA.toast(err(r, "Not done."), "error");
    load();
  });
  $("mt-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const ids = Array.prototype.map.call($("mt-list").querySelectorAll(".mt-pick:checked"), function (c) { return c.closest("tr").getAttribute("data-id"); });
    if (!ids.length) return VA.toast("Choose the students first (tick them in the list).", "error");
    const r = await send(API + "/assign", "POST", { student_ids: ids, mentor_codes: $("mt-codes").value.split(/[\s,;]+/).filter(Boolean), only_unassigned: $("mt-keep").checked });
    if (!r.ok) return VA.toast(err(r, "Not assigned."), "error");
    VA.toast(r.body.assigned + " assigned" + (r.body.unchanged ? ", " + r.body.unchanged + " unchanged" : "") + ".");
    load();
  });
  async function referrals() {
    const r = await VA.fetchJson(API + "/referrals?status=" + $("rf-status").value);
    $("rf-list").innerHTML = (r.ok ? r.body : []).map(function (m) {
      return '<article class="sh-card" data-id="' + m.id + '"><h3>' + esc(m.student) + " <small>" + esc(m.roll_number || "") + "</small></h3><p><b>" + esc(day(m.met_on)) + "</b> · referred by " + esc(m.mentor) + ": " + esc(m.referral_reason) +
        "</p><p class=\"panel-hint\">" + esc(m.notes) + "</p>" + (m.actions ? "<p>Agreed: " + esc(m.actions) + "</p>" : "") +
        (m.resolution ? "<p><b>Answer:</b> " + esc(m.resolution) + "</p>" : '<div class="decision-bar"><button type="button" class="role-btn rf-resolve">Answer…</button></div>') + "</article>";
    }).join("") || '<p class="sd-dim">Nothing here.</p>';
  }
  $("rf-status").addEventListener("change", referrals);
  $("rf-list").addEventListener("click", async function (e) {
    if (!e.target.closest(".rf-resolve")) return;
    const text = await VA.ask({ title: "Answer the referral", label: "What was decided or done (the mentor sees this)", min: 5 });
    if (!text) return;
    const r = await send(API + "/referrals/" + e.target.closest("[data-id]").getAttribute("data-id") + "/resolve", "POST", { resolution: text });
    if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
    referrals(); load();
  });
  programs(); load(); referrals();
})();
