(function () {
  "use strict";
  // Clinical rotations (administration): API /api/v1/clinical
  const VA = window.VistAdmin;
  const API = "/api/v1/clinical";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const day = function (v) { return new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }); };
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  let S = { sites: [], groups: [], supervisors: [], skills: [], programs: [] };

  async function send(url, method, body, ok) {
    const r = await VA.fetchJson(url, { method: method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) { VA.toast(err(r, "Not saved."), "error"); return null; }
    if (ok) VA.toast(ok);
    return r.body;
  }
  const opts = function (rows, label, blank) {
    return (blank ? '<option value="">' + blank + "</option>" : "") + rows.map(function (x) { return '<option value="' + x.id + '">' + esc(label(x)) + "</option>"; }).join("");
  };

  async function setup() {
    const r = await VA.fetchJson(API + "/setup");
    if (!r.ok) return;
    S = r.body;
    const units = [];
    S.sites.forEach(function (s) { s.units.forEach(function (u) { units.push({ id: u.id, name: s.name + " — " + u.name + " (" + u.capacity + ")" }); }); });
    $("cr-group").innerHTML = opts(S.groups, function (g) { return g.name + " (" + g.students + ")"; });
    $("cr-unit").innerHTML = opts(units, function (u) { return u.name; });
    $("cr-sup").innerHTML = opts(S.supervisors.filter(function (x) { return x.is_active; }), function (x) { return x.name; }, "— later —");
    $("cg-program").innerHTML = opts(S.programs, function (p) { return p.code; }, "—");
    $("ck-program").innerHTML = opts(S.programs, function (p) { return p.code; });
    $("cv-site").innerHTML = opts(S.sites, function (s) { return s.name; }, "—");
    $("cg-body").innerHTML = S.groups.map(function (g) {
      return '<tr data-id="' + g.id + '"><td><strong>' + esc(g.name) + "</strong></td><td>" + esc((g.program || "—") + (g.semester ? " · sem " + g.semester : "")) + "</td><td>" + g.students +
        '</td><td><button type="button" class="row-action cg-report" data-name="' + esc(g.name) + '">Progress</button> <button type="button" class="row-action cg-edit">Change students</button></td></tr>';
    }).join("") || '<tr><td colspan="4" class="sd-dim">No groups yet.</td></tr>';
    $("cs-list").innerHTML = S.sites.map(function (s) {
      return '<section class="sh-card" data-site="' + s.id + '"><h2 class="panel-title">' + esc(s.name) + "</h2><p class=\"panel-hint\">" + esc([s.address, s.contact_person, s.contact_phone].filter(Boolean).join(" · ")) + "</p>" +
        '<table class="dash-table"><thead><tr><th>Ward</th><th>Takes</th></tr></thead><tbody>' +
        (s.units.map(function (u) { return "<tr><td>" + esc(u.name) + "</td><td>" + u.capacity + " students</td></tr>"; }).join("") || '<tr><td colspan="2" class="sd-dim">No wards yet.</td></tr>') +
        '</tbody></table><form class="form-grid cs-unit"><label>Ward<input name="n" class="text-input" maxlength="120" required></label><label>Takes<input name="c" type="number" min="1" max="200" value="10" class="text-input" required></label>' +
        '<div class="decision-bar"><button class="role-btn" type="submit">Add ward</button></div></form></section>';
    }).join("");
    $("cv-body").innerHTML = S.supervisors.map(function (x) {
      return '<tr data-id="' + x.id + '"><td><strong>' + esc(x.name) + "</strong></td><td>" + (x.internal ? "Our staff" : "Hospital login") + "</td><td>" + esc(x.phone || "") + "</td><td>" +
        (x.is_active ? "Active" : "Switched off") + '</td><td><button type="button" class="row-action cv-toggle" data-on="' + (x.is_active ? "0" : "1") + '">' + (x.is_active ? "Switch off" : "Switch on") + "</button></td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">No supervisors yet.</td></tr>';
    $("ck-body").innerHTML = S.skills.map(function (k) {
      return "<tr><td>" + esc(k.program) + "</td><td>" + esc(k.name) + "</td><td>" + k.required + "</td><td>" + (k.semester || "—") + "</td><td>" + (k.is_active ? "" : "retired") + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">No skills yet.</td></tr>';
  }

  async function rotations() {
    const r = await VA.fetchJson(API + "/rotations" + ($("cr-current").checked ? "?current=true" : ""));
    $("cr-body").innerHTML = (r.ok ? r.body : []).map(function (x) {
      return '<tr data-id="' + x.id + '"><td><strong>' + esc(day(x.start_date)) + "</strong><small>to " + esc(day(x.end_date)) + "</small></td><td>" + esc(x.group) + "<small>" + x.students + " students</small></td><td>" +
        esc(x.unit) + "<small>" + esc(x.site) + "</small></td><td>" + esc(x.supervisor || "—") + "</td><td>" + esc(x.days_text) + "<small>" + x.start_time + "–" + x.end_time + "</small></td>" +
        '<td><a class="row-action" href="' + API + "/rotations/" + x.id + '/letter.pdf">Placement letter</a> <button type="button" class="row-action cr-del">Remove</button></td></tr>';
    }).join("") || '<tr><td colspan="6" class="sd-dim">No rotations.</td></tr>';
  }

  $("cr-days").insertAdjacentHTML("beforeend", DAYS.map(function (d, i) { return '<label><input type="checkbox" value="' + i + '"' + (i < 5 ? " checked" : "") + "> " + d + "</label>"; }).join(""));
  $("cr-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const days = Array.prototype.map.call($("cr-days").querySelectorAll("input:checked"), function (c) { return c.value; }).join(",");
    const ok = await send(API + "/rotations", "POST", { group_id: $("cr-group").value, unit_id: $("cr-unit").value, supervisor_id: $("cr-sup").value || null,
      start_date: $("cr-from").value, end_date: $("cr-to").value, days: days, start_time: $("cr-st").value, end_time: $("cr-en").value, note: $("cr-note").value || null }, "Rotation saved.");
    if (ok) rotations();
  });
  $("cr-current").addEventListener("change", rotations);
  $("cr-body").addEventListener("click", async function (e) {
    const b = e.target.closest(".cr-del");
    if (!b || !confirm("Remove this rotation?")) return;
    if (await send(API + "/rotations/" + b.closest("tr").getAttribute("data-id"), "DELETE", null, "Removed.")) rotations();
  });

  const rolls = function () { return $("cg-rolls").value.split(/[\s,;]+/).filter(Boolean); };
  let editing = null;
  $("cg-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const body = { name: $("cg-name").value, program_id: $("cg-program").value || null, semester: $("cg-sem").value ? Number($("cg-sem").value) : null, roll_numbers: rolls() };
    if (await send(API + "/groups" + (editing ? "/" + editing : ""), editing ? "PUT" : "POST", body, "Group saved.")) { editing = null; $("cg-form").reset(); setup(); }
  });
  $("cg-body").addEventListener("click", async function (e) {
    const tr = e.target.closest("tr"), id = tr && tr.getAttribute("data-id");
    if (e.target.closest(".cg-edit")) {
      const g = S.groups.find(function (x) { return x.id === id; });
      const r = await VA.fetchJson(API + "/groups/" + id + "/students");
      editing = id;
      $("cg-name").value = g.name; $("cg-program").value = g.program_id || ""; $("cg-sem").value = g.semester || "";
      $("cg-rolls").value = (r.ok ? r.body : []).map(function (s) { return s.roll_number; }).join("\n");
      $("cg-name").focus();
    } else if (e.target.closest(".cg-report")) {
      const r = await VA.fetchJson(API + "/groups/" + id + "/report");
      if (!r.ok) return;
      $("cg-report-title").textContent = r.body.group;
      $("cg-report-body").innerHTML = r.body.students.map(function (s) {
        return "<tr><td>" + esc(s.roll_number) + "</td><td>" + esc(s.name) + "</td><td>" + (s.attendance == null ? "—" : s.attendance + "%") + "<small>" + s.days + " days</small></td><td>" + s.skills_complete + " / " + s.skills + "</td></tr>";
      }).join("") || '<tr><td colspan="4" class="sd-dim">No students.</td></tr>';
      $("cg-report").hidden = false;
    }
  });

  $("cs-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    if (await send(API + "/sites", "POST", { name: $("cs-name").value, address: $("cs-addr").value || null, contact_person: $("cs-person").value || null, contact_phone: $("cs-phone").value || null }, "Hospital added.")) {
      $("cs-form").reset(); setup();
    }
  });
  $("cs-list").addEventListener("submit", async function (e) {
    const f = e.target.closest(".cs-unit");
    if (!f) return;
    e.preventDefault();
    if (await send(API + "/sites/" + f.closest("[data-site]").getAttribute("data-site") + "/units", "POST", { name: f.n.value, capacity: Number(f.c.value) }, "Ward added.")) setup();
  });

  $("cv-kind").addEventListener("change", function () {
    const ext = $("cv-kind").value === "ext";
    $("cv-code-l").hidden = ext; $("cv-name-l").hidden = !ext; $("cv-user-l").hidden = !ext;
  });
  $("cv-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const ext = $("cv-kind").value === "ext";
    const out = await send(API + "/supervisors", "POST", { employee_code: ext ? null : $("cv-code").value, name: ext ? $("cv-name").value : null, username: ext ? $("cv-user").value : null,
      phone: $("cv-phone").value || null, site_id: $("cv-site").value || null }, "Supervisor added.");
    if (!out) return;
    $("cv-pass").hidden = !out.temporary_password;
    if (out.temporary_password) $("cv-pass").textContent = "Login: " + out.username + " — temporary password: " + out.temporary_password + " (shown once; they choose their own at first sign-in).";
    $("cv-form").reset(); $("cv-kind").dispatchEvent(new Event("change")); setup();
  });
  $("cv-body").addEventListener("click", async function (e) {
    const b = e.target.closest(".cv-toggle");
    if (!b) return;
    if (await send(API + "/supervisors/" + b.closest("tr").getAttribute("data-id") + "/active", "POST", { is_active: b.getAttribute("data-on") === "1" }, "Saved.")) setup();
  });

  $("ck-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    if (await send(API + "/skills", "POST", { program_id: $("ck-program").value, name: $("ck-name").value, required: Number($("ck-req").value),
      semester: $("ck-sem").value ? Number($("ck-sem").value) : null }, "Skill added.")) { $("ck-form").reset(); setup(); }
  });

  setup().then(rotations);
})();
