// Settings -> Access control (/dashboard/admin/access, API /api/v1/access). Roles: what each role can do, by module
// (None / View / Edit, plus Approve) or right by right (Advanced). People: a staff member's extra roles and their own
// rights (given or taken away, optionally until a date). To confirm: another administrator's money/control changes.
// History: every change, with Undo. Every save shows what changes first. The server enforces every rule again.
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const API = "/api/v1/access";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  let data = null, role = null, draft = null, person = null, personRoles = null, ovDraft = null, onConfirm = null;

  function msg(text, tone) {
    const el = $("ac-msg");
    el.textContent = text || "";
    el.className = "dash-alert" + (tone === "error" ? " error" : tone === "ok" ? " success" : "");
    el.hidden = !text;
    if (text) el.scrollIntoView({ block: "nearest" });
  }
  function errOf(r, fallback) { return (r.body && r.body.error && r.body.error.message) || fallback; }
  function label(code) { return (data && data.labels[code]) || code; }
  function isSensitive(code) { return data && data.sensitive.indexOf(code) !== -1; }
  function tag(code) { return isSensitive(code) ? ' <span class="ac-tag" title="Giving this needs a second administrator">2nd admin</span>' : ""; }
  function done(r, what) {
    if (!r.ok) { msg(errOf(r, "Could not save."), "error"); return false; }
    if (r.body && r.body.waiting) { msg(what + " saved. It gives money or control rights, so another administrator must confirm it (To confirm tab).", "ok"); return true; }
    msg(r.body && r.body.changed === false ? "Nothing changed." : what + " saved.", "ok");
    return true;
  }

  // ------------------------------------------------------------------ preview dialog
  function preview(html, ok) {
    $("ac-preview-body").innerHTML = html;
    onConfirm = ok;
    $("ac-preview").showModal();
  }
  $("ac-preview-ok").addEventListener("click", async function () {
    const fn = onConfirm; onConfirm = null;
    $("ac-preview").close();
    if (fn) await fn();
  });
  document.querySelectorAll("dialog [data-close]").forEach(function (b) { b.addEventListener("click", function () { b.closest("dialog").close(); }); });

  function diffHtml(d, who) {
    const list = function (items, sign) {
      return items.map(function (x) { return "<li>" + sign + " " + esc(x.label) + (x.sensitive ? tag(x.code) : "") + "</li>"; }).join("");
    };
    let h = "";
    if (who) h += "<p>" + who + "</p>";
    if (!d.gained.length && !d.lost.length) h += "<p>Nothing changes.</p>";
    if (d.gained.length) h += '<h3 class="ac-h3">Will be able to</h3><ul class="ac-diff gain">' + list(d.gained, "+") + "</ul>";
    if (d.lost.length) h += '<h3 class="ac-h3">Will no longer be able to</h3><ul class="ac-diff lose">' + list(d.lost, "&minus;") + "</ul>";
    if (d.pages_gained && d.pages_gained.length) h += '<p class="panel-hint">New pages in their menu: ' + esc(d.pages_gained.join(", ")) + "</p>";
    if (d.pages_lost && d.pages_lost.length) h += '<p class="panel-hint">Pages they lose: ' + esc(d.pages_lost.join(", ")) + "</p>";
    if (d.gained.some(function (x) { return x.sensitive; })) h += '<p class="dash-alert">Money or control rights: this waits for another administrator to confirm (if there is one).</p>';
    return h;
  }

  // ------------------------------------------------------------------ roles
  async function load(keepRole) {
    const r = await VA.fetchJson(API);
    if (!r.ok) { msg(errOf(r, "Could not load access control."), "error"); return; }
    data = r.body;
    drawRoles();
    fillWho();
    const again = keepRole && data.roles.find(function (x) { return x.id === keepRole; });
    if (again) openRole(again.id);
    const copy = $("ac-new-copy");
    copy.innerHTML = '<option value="">Nothing (choose rights next)</option>' + data.roles.filter(function (x) { return !x.locked; })
      .map(function (x) { return '<option value="' + esc(x.id) + '">' + esc(x.name) + "</option>"; }).join("");
    loadWaiting();
  }

  function drawRoles() {
    $("ac-roles").innerHTML = data.roles.map(function (x) {
      const changed = x.builtin && (x.added.length || x.removed.length);
      return '<li><button type="button" class="ac-item' + (role && role.id === x.id ? " on" : "") + '" data-role="' + esc(x.id) + '"><strong>' + esc(x.name) + "</strong>" +
        "<small>" + (x.locked ? "Everything, always" : x.own_portal ? "Own portal" : x.builtin ? "Built-in" : "Your own role") + " &middot; " + x.people + " " + (x.people === 1 ? "person" : "people") +
        (changed ? ' &middot; <span class="ac-tag soft">changed</span>' : "") + "</small></button></li>";
    }).join("");
  }
  $("ac-roles").addEventListener("click", function (e) {
    const b = e.target.closest("[data-role]");
    if (b) openRole(b.getAttribute("data-role"));
  });

  function levelOf(m, set) {
    const has = function (list) { return list.length && list.every(function (c) { return set.has(c.code); }); };
    const any = function (list) { return list.some(function (c) { return set.has(c.code); }); };
    const lv = m.levels;
    let level = "none";
    if (has(lv.edit)) level = "edit";
    else if (has(lv.view)) level = "view";
    else if (!lv.view.length && !lv.edit.length && has(lv.approve)) level = "none";
    const partial = (any(lv.view) && !has(lv.view)) || (any(lv.edit) && !has(lv.edit)) || (any(lv.approve) && !has(lv.approve));
    return { level: level, approve: has(lv.approve), partial: partial };
  }

  function moduleRow(m, set, locked) {
    const st = levelOf(m, set), lv = m.levels, name = "lvl-" + m.key;
    const radio = function (val, text, enabled) {
      return '<label class="ac-lvl' + (enabled ? "" : " off") + '"><input type="radio" name="' + name + '" value="' + val + '"' + (st.level === val && !st.partial ? " checked" : "") +
        (enabled && !locked ? "" : " disabled") + "> " + text + "</label>";
    };
    const all = [].concat(lv.view, lv.edit, lv.approve);
    return '<div class="ac-mod" data-mod="' + m.key + '"><div class="ac-mod-head"><div><strong>' + esc(m.label) + "</strong><small>" + esc(m.about) + "</small></div>" +
      '<div class="ac-lvls">' + radio("none", "None", true) + radio("view", "View", lv.view.length > 0) + radio("edit", "Edit", lv.edit.length > 0) +
      '<label class="ac-lvl ac-appr' + (lv.approve.length ? "" : " off") + '"><input type="checkbox" data-approve' + (st.approve ? " checked" : "") + (lv.approve.length && !locked ? "" : " disabled") + "> Approve</label>" +
      (st.partial ? '<span class="ac-tag soft">custom</span>' : "") + "</div></div>" +
      '<details class="ac-adv"><summary>Advanced: single rights</summary><div class="ac-rights">' +
      all.map(function (c) {
        return '<label><input type="checkbox" data-right="' + esc(c.code) + '"' + (set.has(c.code) ? " checked" : "") + (locked ? " disabled" : "") + "> " + esc(c.label) + tag(c.code) + "</label>";
      }).join("") + "</div></details></div>";
  }

  function openRole(id) {
    role = data.roles.find(function (x) { return x.id === id; });
    if (!role) return;
    draft = new Set(role.rights);
    drawRoles();
    const box = $("ac-role-editor");
    const locked = role.locked || role.own_portal;
    let head = '<div class="ac-ed-head"><div><h2 class="panel-title">' + esc(role.name) + "</h2><p class=\"panel-hint\">" +
      esc((role.description || (role.builtin ? "A role that comes with the system" : "")).replace(/\.$/, "")) + ". " + role.people + " " + (role.people === 1 ? "person has" : "people have") + " it.</p></div>";
    if (!role.builtin) head += '<div class="ac-ed-acts"><button type="button" class="role-btn secondary-btn small-btn" id="ac-rename">Rename</button> <button type="button" class="role-btn danger-btn small-btn" id="ac-delete">Delete</button></div>';
    head += "</div>";
    if (role.locked) head += '<p class="dash-alert">The Administrator role always has every right, so nobody can lock the institute out. It cannot be changed here.</p>';
    else if (role.own_portal) head += '<p class="dash-alert">Students, parents and alumni have their own portal with their own records only. Office rights are given to staff, so this role is not changed here.</p>';
    if (role.builtin && (role.added.length || role.removed.length)) {
      head += '<p class="panel-hint">Changed from the standard: ' +
        (role.added.length ? "added " + esc(role.added.map(label).join(", ")) : "") + (role.added.length && role.removed.length ? "; " : "") +
        (role.removed.length ? "removed " + esc(role.removed.map(label).join(", ")) : "") +
        ' &middot; <button type="button" class="link-btn" id="ac-reset">Back to the standard</button></p>';
    }
    if (role.own_use.length) head += '<p class="panel-hint">Also comes with its own-use rights (not changed here): ' + esc(role.own_use.map(label).join(", ")) + ".</p>";
    head += '<details class="ac-members" id="ac-members"' + (role.people && role.people <= 12 ? " open" : "") + '><summary>People with this role (' + role.people + ')</summary><div id="ac-members-body"><p class="muted">Loading...</p></div></details>';
    box.innerHTML = head + '<div class="ac-mods">' + data.modules.map(function (m) { return moduleRow(m, draft, locked); }).join("") + "</div>" +
      (locked ? "" : '<div class="ac-save"><button type="button" class="role-btn secondary-btn" id="ac-undo-draft">Discard changes</button> <button type="button" class="role-btn" id="ac-save-role">Check and save</button></div>');
    loadMembers();
    if (locked) return;
    box.querySelectorAll(".ac-mod").forEach(function (row) { wireModule(row, draft, function () { openRoleKeep(); }); });
    $("ac-save-role").addEventListener("click", saveRole);
    $("ac-undo-draft").addEventListener("click", function () { openRole(role.id); });
    const reset = $("ac-reset");
    if (reset) reset.addEventListener("click", function () {
      role.added.forEach(function (c) { draft.delete(c); });
      role.removed.forEach(function (c) { draft.add(c); });
      redraw();
    });
    const del = $("ac-delete");
    if (del) del.addEventListener("click", async function () {
      if (!(await VA.confirm("Delete the role " + role.name + "? Nobody may still have it.", "Delete"))) return;
      const r = await VA.fetchJson(API + "/roles/" + role.id, { method: "DELETE" });
      if (done(r, "Role deleted")) { role = null; $("ac-role-editor").innerHTML = '<p class="empty-state">Choose a role on the left.</p>'; load(); }
    });
    const ren = $("ac-rename");
    if (ren) ren.addEventListener("click", async function () {
      const name = await VA.promptText("New name for " + role.name, role.name);
      if (!name) return;
      const r = await VA.fetchJson(API + "/roles/" + role.id, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name }) });
      if (done(r, "Name")) load(role.id);
    });
  }
  function openRoleKeep() { redraw(); }

  // ------------------------------------------------------------------ a role's people (give it to many at once)
  async function loadMembers() {
    const body = $("ac-members-body");
    if (!body || !role) return;
    const r = await VA.fetchJson(API + "/roles/" + role.id + "/members", { quiet: true });
    if (!r.ok) { body.innerHTML = ""; return; }
    const canGive = !role.own_portal;
    body.innerHTML = (r.body.length ? '<ul class="ac-member-list">' + r.body.map(function (m) {
      return "<li><span>" + esc(m.name) + ' <small class="muted">' + esc(m.code) + (m.department ? " &middot; " + esc(m.department) : "") + "</small></span>" +
        (m.from_job_title ? '<small class="muted">from their job title</small>' : (canGive ? '<button type="button" class="link-btn" data-take="' + esc(m.employee_id) + '">Take away</button>' : "")) + "</li>";
    }).join("") + "</ul>" : '<p class="muted">Nobody yet.</p>') +
      (canGive ? '<button type="button" class="role-btn secondary-btn small-btn" id="ac-add-people-btn">Give it to people...</button>' : "");
    const add = $("ac-add-people-btn");
    if (add) add.addEventListener("click", openAddPeople);
    body.querySelectorAll("[data-take]").forEach(function (b) {
      b.addEventListener("click", async function () {
        if (!(await VA.confirm("Take the " + role.name + " role from this person?", "Take it away"))) return;
        const s = await VA.fetchJson(API + "/roles/" + role.id + "/members", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ add: [], remove: [b.getAttribute("data-take")] }) });
        bulkDone(s);
      });
    });
  }
  let picked = new Set(), pickTimer = null;
  function openAddPeople() {
    picked = new Set();
    $("ac-add-title").textContent = "Give " + role.name + " to";
    $("ac-add-q").value = "";
    $("ac-add-count").textContent = "";
    $("ac-add-people").showModal();
    searchPick();
  }
  $("ac-add-q").addEventListener("input", function () { clearTimeout(pickTimer); pickTimer = setTimeout(searchPick, 250); });
  async function searchPick() {
    const q = $("ac-add-q").value.trim();
    const r = await VA.fetchJson(API + "/people" + (q ? "?q=" + encodeURIComponent(q) : ""), { quiet: true });
    if (!r.ok) return;
    $("ac-add-list").innerHTML = r.body.map(function (p) {
      const has = p.roles.some(function (x) { return x.code === role.code; });
      return '<label class="' + (has ? "muted" : "") + '"><input type="checkbox" data-pick="' + esc(p.employee_id) + '"' + (has ? " disabled checked" : picked.has(p.employee_id) ? " checked" : "") + "> " +
        esc(p.name) + ' <small class="muted">' + esc(p.code) + (p.department ? " &middot; " + esc(p.department) : "") + (has ? " &middot; already has it" : "") + "</small></label>";
    }).join("") || '<p class="muted">Nobody found.</p>';
  }
  $("ac-add-list").addEventListener("change", function (e) {
    const id = e.target.getAttribute("data-pick");
    if (!id) return;
    if (e.target.checked) picked.add(id); else picked.delete(id);
    $("ac-add-count").textContent = picked.size ? picked.size + " chosen" : "";
  });
  $("ac-add-ok").addEventListener("click", async function () {
    if (!picked.size) return;
    $("ac-add-people").close();
    const s = await VA.fetchJson(API + "/roles/" + role.id + "/members", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ add: Array.from(picked), remove: [] }) });
    bulkDone(s);
  });
  function bulkDone(s) {
    if (!s.ok) { msg(errOf(s, "Could not save."), "error"); return; }
    const b = s.body, bad = b.results.filter(function (x) { return !x.ok; });
    msg((b.done ? b.done + " saved. " : "") + (b.waiting ? b.waiting + " waiting for a second administrator. " : "") +
      (bad.length ? "Not changed: " + bad.map(function (x) { return x.name + " (" + x.message + ")"; }).join("; ") : ""), bad.length ? "error" : "ok");
    load(role.id);
    loadHistory();
  }
  function redraw() {
    // keep the draft, redraw the module rows
    const box = $("ac-role-editor");
    const mods = box.querySelector(".ac-mods");
    const open = Array.prototype.map.call(box.querySelectorAll("details[open]"), function (d) { return d.closest(".ac-mod").getAttribute("data-mod"); });
    mods.innerHTML = data.modules.map(function (m) { return moduleRow(m, draft, false); }).join("");
    open.forEach(function (k) { const d = mods.querySelector('[data-mod="' + k + '"] details'); if (d) d.open = true; });
    mods.querySelectorAll(".ac-mod").forEach(function (row) { wireModule(row, draft, redraw); });
  }

  function supportNeeded(set) {
    // a module's "support" rights stay while any module that needs them is above None
    const need = new Set();
    data.modules.forEach(function (m) {
      const lv = m.levels;
      const on = [].concat(lv.view, lv.edit, lv.approve).some(function (c) { return set.has(c.code); });
      if (on) (m.support || []).forEach(function (c) { need.add(c); });
    });
    return need;
  }
  function applySupport(set, before) {
    const now = supportNeeded(set);
    before.forEach(function (c) { if (!now.has(c) && !isInAnyLevel(c, set)) set.delete(c); });
    now.forEach(function (c) { set.add(c); });
  }
  function isInAnyLevel(code, set) {
    // a right that is also a level right of a module still chosen on its own (e.g. See programs for Programs & courses)
    return data.modules.some(function (m) {
      const lv = m.levels;
      return [].concat(lv.view, lv.edit).some(function (c) { return c.code === code; }) && levelOf(m, set).level !== "none";
    });
  }

  function wireModule(row, set, after) {
    const m = data.modules.find(function (x) { return x.key === row.getAttribute("data-mod"); });
    const lv = m.levels;
    row.querySelectorAll('input[type="radio"]').forEach(function (inp) {
      inp.addEventListener("change", function () {
        const before = supportNeeded(set);
        [].concat(lv.view, lv.edit).forEach(function (c) { set.delete(c.code); });
        if (inp.value === "view" || inp.value === "edit") lv.view.forEach(function (c) { set.add(c.code); });
        if (inp.value === "edit") lv.edit.forEach(function (c) { set.add(c.code); });
        if (inp.value === "none") lv.approve.forEach(function (c) { set.delete(c.code); });
        applySupport(set, before);
        after();
      });
    });
    const appr = row.querySelector("[data-approve]");
    if (appr) appr.addEventListener("change", function () {
      const before = supportNeeded(set);
      lv.approve.forEach(function (c) { if (appr.checked) set.add(c.code); else set.delete(c.code); });
      applySupport(set, before);
      after();
    });
    row.querySelectorAll("[data-right]").forEach(function (cb) {
      cb.addEventListener("change", function () {
        const before = supportNeeded(set);
        if (cb.checked) set.add(cb.getAttribute("data-right")); else set.delete(cb.getAttribute("data-right"));
        applySupport(set, before);
        after();
      });
    });
  }

  async function saveRole() {
    const rights = Array.from(draft);
    const r = await VA.fetchJson(API + "/roles/" + role.id + "/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rights: rights }) });
    if (!r.ok) { msg(errOf(r, "Could not check the change."), "error"); return; }
    const who = r.body.people ? esc(r.body.people + (r.body.people === 1 ? " person" : " people") + " with " + role.name + ": " + r.body.names.join(", ") + (r.body.people > r.body.names.length ? "..." : "")) : "Nobody has this role yet.";
    preview(diffHtml(r.body, who), async function () {
      const s = await VA.fetchJson(API + "/roles/" + role.id + "/rights", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rights: rights }) });
      if (done(s, role.name)) load(role.id);
    });
  }

  $("ac-new-role").addEventListener("click", function () {
    $("ac-new-form").reset();
    $("ac-new-err").hidden = true;
    $("ac-new-dialog").showModal();
    $("ac-new-name").focus();
  });
  $("ac-new-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const body = { name: $("ac-new-name").value.trim(), description: $("ac-new-about").value.trim() || null, copy_from: $("ac-new-copy").value || null, rights: [] };
    const r = await VA.fetchJson(API + "/roles", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { $("ac-new-err").textContent = errOf(r, "Could not make the role."); $("ac-new-err").hidden = false; return; }
    $("ac-new-dialog").close();
    done(r, "Role " + body.name);
    await load(r.body.id);
    if (!role || role.id !== r.body.id) openRole(r.body.id);
  });

  // ------------------------------------------------------------------ people
  let qTimer = null;
  $("ac-people-q").addEventListener("input", function () { clearTimeout(qTimer); qTimer = setTimeout(loadPeople, 250); });
  async function loadPeople() {
    const q = $("ac-people-q").value.trim();
    const r = await VA.fetchJson(API + "/people" + (q ? "?q=" + encodeURIComponent(q) : ""), { quiet: true });
    if (!r.ok) return;
    $("ac-people").innerHTML = r.body.map(function (p) {
      return '<li><button type="button" class="ac-item' + (person && person.user_id === p.user_id ? " on" : "") + '" data-user="' + esc(p.user_id) + '"><strong>' + esc(p.name) + "</strong><small>" +
        esc(p.code) + (p.department ? " &middot; " + esc(p.department) : "") + " &middot; " + esc(p.roles.map(function (x) { return x.name; }).join(", ")) +
        (p.own_rights ? ' &middot; <span class="ac-tag soft">own rights</span>' : "") + "</small></button></li>";
    }).join("") || '<li class="empty-state">Nobody found.</li>';
  }
  $("ac-people").addEventListener("click", function (e) {
    const b = e.target.closest("[data-user]");
    if (b) openPerson(b.getAttribute("data-user"));
  });

  async function openPerson(uid) {
    const r = await VA.fetchJson(API + "/people/" + uid);
    const box = $("ac-person");
    if (!r.ok) { box.innerHTML = '<p class="dash-alert error">' + esc(errOf(r, "Could not open.")) + "</p>"; return; }
    person = r.body;
    personRoles = null;
    if (person.employee_id) {
      const ar = await VA.fetchJson("/api/v1/employees/" + person.employee_id + "/access-roles", { quiet: true });
      if (ar.ok) personRoles = ar.body;
    }
    ovDraft = person.overrides.filter(function (o) { return !o.expired; }).map(function (o) { return Object.assign({}, o); });
    loadPeople();
    drawPerson();
  }

  function drawPerson() {
    const p = person, box = $("ac-person");
    let h = '<div class="ac-ed-head"><div><h2 class="panel-title">' + esc(p.name) + '</h2><p class="panel-hint">' + esc(p.username) + (p.department ? " &middot; " + esc(p.department) : "") +
      "</p></div>" + (p.is_admin ? "" : '<div class="ac-ed-acts"><button type="button" class="role-btn secondary-btn small-btn" id="ac-view-as">See the system as them</button></div>') + "</div>";
    if (p.is_admin) {
      h += '<p class="dash-alert">An Administrator has every right. To limit this person, take the Administrator role away (below), then give what they need.</p>';
    }
    // roles
    if (personRoles) {
      h += '<h3 class="ac-h3">Roles</h3><p class="panel-hint">Their job title gives <strong>' + esc((personRoles.names && personRoles.names[personRoles.main_role]) || personRoles.main_role || "no role") +
        "</strong> (change it on Employees). Extra roles:</p><div class=\"ac-rights\">" +
        personRoles.grantable.map(function (c) {
          return '<label><input type="checkbox" data-xrole="' + esc(c) + '"' + (personRoles.extra_roles.indexOf(c) !== -1 ? " checked" : "") + "> " + esc((personRoles.names && personRoles.names[c]) || c) +
            (c === "ADMIN" ? ' <span class="ac-tag" title="Needs a second administrator">2nd admin</span>' : "") + "</label>";
        }).join("") + '</div><div class="ac-save"><button type="button" class="role-btn" id="ac-save-roles">Check and save roles</button></div>';
    }
    if (!p.is_admin) {
      h += '<h3 class="ac-h3">Their own rights</h3><p class="panel-hint">On top of their roles. <em>Give</em> adds a right; <em>Take away</em> removes one their roles give (it wins over every role). Leave the date empty for no end.</p>' +
        '<div class="table-scroll"><table class="dash-table ac-ov"><thead><tr><th>Right</th><th>Give / take away</th><th>Until</th><th>Why</th><th></th></tr></thead><tbody id="ac-ov-rows"></tbody></table></div>' +
        '<div class="ac-add"><select id="ac-ov-add" class="select-input" aria-label="Add a right"></select> <button type="button" class="role-btn secondary-btn small-btn" id="ac-ov-add-btn">Add</button></div>' +
        '<div class="ac-save"><button type="button" class="role-btn" id="ac-save-ov">Check and save</button></div>';
    }
    h += '<details class="ac-adv"><summary>Everything they can do now (' + p.rights.length + ")</summary>" + whatTheyCan(p) + "</details>";
    if (!p.is_admin && p.pages.length) h += '<p class="panel-hint">Administration pages they can open: ' + esc(p.pages.join(", ")) + ".</p>";
    box.innerHTML = h;
    if (!p.is_admin) { drawOverrides(); wirePerson(); }
    if (personRoles) $("ac-save-roles").addEventListener("click", saveRoles);
    const va = $("ac-view-as");
    if (va) va.addEventListener("click", async function () {
      if (!(await VA.confirm("Open " + p.name + "'s screens, read-only, for 30 minutes? You can come back any time.", "Open their screens"))) return;
      const r = await VA.fetchJson("/api/v1/view-as/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: p.user_id }) });
      if (!r.ok) { msg(errOf(r, "Could not open their screens."), "error"); return; }
      window.location.href = r.body.redirect || "/";
    });
  }

  function whatTheyCan(p) {
    const by = {};
    p.rights.forEach(function (c) {
      const m = data.modules.find(function (x) { return [].concat(x.levels.view, x.levels.edit, x.levels.approve).some(function (y) { return y.code === c; }) || (x.support || []).indexOf(c) !== -1; });
      const k = m ? m.label : "Their own use";
      (by[k] = by[k] || []).push(c);
    });
    return '<dl class="ac-can">' + Object.keys(by).sort().map(function (k) {
      return "<dt>" + esc(k) + "</dt><dd>" + by[k].map(function (c) {
        const src = (p.from_roles[c] || []).join(", ");
        const own = p.overrides.find(function (o) { return o.code === c && o.effect === "GRANT" && !o.expired; });
        return esc(label(c)) + ' <small class="muted">(' + esc(own ? "own right" + (own.until ? " until " + own.until : "") : src) + ")</small>";
      }).join("<br>") + "</dd>";
    }).join("") + "</dl>";
  }

  function drawOverrides() {
    const today = new Date().toISOString().slice(0, 10);
    $("ac-ov-rows").innerHTML = ovDraft.map(function (o, i) {
      return "<tr><td>" + esc(label(o.code)) + tag(o.code) + '</td><td><select class="select-input small-input" data-i="' + i + '" data-k="effect"><option value="GRANT"' + (o.effect === "GRANT" ? " selected" : "") +
        '>Give</option><option value="DENY"' + (o.effect === "DENY" ? " selected" : "") + '>Take away</option></select></td><td><input type="date" class="text-input small-input" min="' + today + '" data-i="' + i +
        '" data-k="until" value="' + esc(o.until || "") + '"></td><td><input class="text-input small-input" maxlength="300" data-i="' + i + '" data-k="reason" value="' + esc(o.reason || "") +
        '" placeholder="e.g. covering exams"></td><td><button type="button" class="link-btn" data-del="' + i + '">Remove</button></td></tr>';
    }).join("") || '<tr><td colspan="5" class="muted">None: they have exactly what their roles give.</td></tr>';
    const add = $("ac-ov-add");
    const used = new Set(ovDraft.map(function (o) { return o.code; }));
    add.innerHTML = '<option value="">Add a right...</option>' + data.modules.map(function (m) {
      const rights = [].concat(m.levels.view, m.levels.edit, m.levels.approve).filter(function (c) { return !used.has(c.code); });
      return rights.length ? '<optgroup label="' + esc(m.label) + '">' + rights.map(function (c) { return '<option value="' + esc(c.code) + '">' + esc(c.label) + "</option>"; }).join("") + "</optgroup>" : "";
    }).join("");
  }

  function wirePerson() {
    $("ac-ov-rows").addEventListener("change", function (e) {
      const t = e.target, i = t.getAttribute("data-i");
      if (i == null) return;
      ovDraft[+i][t.getAttribute("data-k")] = t.value || null;
    });
    $("ac-ov-rows").addEventListener("click", function (e) {
      const b = e.target.closest("[data-del]");
      if (!b) return;
      ovDraft.splice(+b.getAttribute("data-del"), 1);
      drawOverrides();
    });
    $("ac-ov-add-btn").addEventListener("click", function () {
      const code = $("ac-ov-add").value;
      if (!code) return;
      ovDraft.push({ code: code, effect: person.rights.indexOf(code) === -1 ? "GRANT" : "DENY", until: null, reason: null });
      drawOverrides();
    });
    $("ac-save-ov").addEventListener("click", saveOverrides);
  }

  async function saveOverrides() {
    const body = { overrides: ovDraft.map(function (o) { return { code: o.code, effect: o.effect, until: o.until || null, reason: o.reason || null }; }) };
    const r = await VA.fetchJson(API + "/people/" + person.user_id + "/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { msg(errOf(r, "Could not check the change."), "error"); return; }
    preview(diffHtml(r.body, esc(person.name)), async function () {
      const s = await VA.fetchJson(API + "/people/" + person.user_id + "/rights", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (done(s, person.name + "'s rights")) openPerson(person.user_id);
    });
  }

  async function saveRoles() {
    const extra = Array.prototype.map.call(document.querySelectorAll("[data-xrole]:checked"), function (c) { return c.getAttribute("data-xrole"); });
    const r = await VA.fetchJson(API + "/people/" + person.user_id + "/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ extra_roles: extra }) });
    if (!r.ok) { msg(errOf(r, "Could not check the change."), "error"); return; }
    preview(diffHtml(r.body, esc(person.name) + ": roles " + esc(extra.map(function (c) { return (personRoles.names && personRoles.names[c]) || c; }).join(", ") || "only their job title's")), async function () {
      const s = await VA.fetchJson(API + "/employees/" + person.employee_id + "/roles", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ extra_roles: extra }) });
      if (done(s, person.name + "'s roles")) { openPerson(person.user_id); load(role && role.id); }
    });
  }

  // ------------------------------------------------------------------ who can do this?
  function fillWho() {
    const sel = $("ac-who-code");
    if (sel.options.length) return;
    sel.innerHTML = '<option value="">Choose a right...</option>' + data.modules.map(function (m) {
      const rights = [].concat(m.levels.view, m.levels.edit, m.levels.approve);
      return '<optgroup label="' + esc(m.label) + '">' + rights.map(function (c) { return '<option value="' + esc(c.code) + '">' + esc(c.label) + "</option>"; }).join("") + "</optgroup>";
    }).join("") + '<optgroup label="Administrator only">' + data.locked.map(function (c) { return '<option value="' + esc(c) + '">' + esc(label(c)) + "</option>"; }).join("") + "</optgroup>";
  }
  $("ac-who-code").addEventListener("change", async function () {
    const code = $("ac-who-code").value;
    if (!code) { $("ac-who").innerHTML = ""; $("ac-who-sum").textContent = ""; return; }
    const r = await VA.fetchJson(API + "/who?code=" + encodeURIComponent(code));
    if (!r.ok) { msg(errOf(r, "Could not look it up."), "error"); return; }
    $("ac-who-sum").textContent = r.body.count + (r.body.count === 1 ? " person can " : " people can ") + r.body.label.toLowerCase() + "." +
      (r.body.sensitive ? " (A money or control right.)" : "");
    $("ac-who").innerHTML = r.body.people.map(function (p) {
      const through = [].concat(p.roles.map(function (x) { return "role: " + x; }),
        p.own ? [(p.own.effect === "GRANT" ? "own right" : "taken away") + (p.own.until ? " until " + p.own.until : "")] : []);
      return "<tr" + (p.has_it ? "" : ' class="muted"') + '><td><button type="button" class="link-btn" data-open-user="' + esc(p.user_id) + '">' + esc(p.name) + "</button></td><td>" +
        esc(p.department || "") + "</td><td>" + (p.has_it ? "Yes" : "No") + "</td><td>" + esc(through.join("; ")) + "</td></tr>";
    }).join("") || '<tr><td colspan="4" class="muted">Nobody.</td></tr>';
  });
  $("ac-who").addEventListener("click", function (e) {
    const b = e.target.closest("[data-open-user]");
    if (!b) return;
    VA.showTab("people");
    openPerson(b.getAttribute("data-open-user"));
  });

  // ------------------------------------------------------------------ waiting and history
  async function loadWaiting() {
    const r = await VA.fetchJson(API + "/waiting", { quiet: true });
    if (!r.ok) return;
    $("ac-wait-count").textContent = r.body.length;
    $("ac-wait-count").hidden = !r.body.length;
    $("ac-waiting-empty").hidden = r.body.length > 0;
    $("ac-waiting").innerHTML = r.body.map(function (w) {
      return "<tr><td>" + esc(VA.formatDateTime(w.at)) + "<br><small>" + esc(w.asked_by || "") + "</small></td><td>" + esc(w.summary) + "</td><td>" + esc(w.reason || "") + "</td>" +
        '<td class="nowrap"><button type="button" class="role-btn small-btn" data-ok="' + esc(w.id) + '">Confirm</button> <button type="button" class="role-btn secondary-btn small-btn" data-no="' + esc(w.id) + '">Reject</button></td></tr>';
    }).join("");
  }
  $("ac-waiting").addEventListener("click", async function (e) {
    const ok = e.target.closest("[data-ok]"), no = e.target.closest("[data-no]");
    if (!ok && !no) return;
    let note = null;
    if (no) {
      note = await VA.promptText("Why is it rejected?", "");
      if (!note) return;
    } else if (!(await VA.confirm("Confirm this access change? It takes effect at once.", "Confirm"))) return;
    const id = (ok || no).getAttribute(ok ? "data-ok" : "data-no");
    const r = await VA.fetchJson(API + "/waiting/" + id + "/decide", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approve: !!ok, note: note }) });
    if (!r.ok) { msg(errOf(r, "Could not decide."), "error"); return; }
    msg(ok ? "Confirmed: the change is in effect." : "Rejected.", "ok");
    load(role && role.id);
    loadHistory();
  });

  async function loadHistory() {
    const r = await VA.fetchJson(API + "/history", { quiet: true });
    if (!r.ok) return;
    $("ac-history-empty").hidden = r.body.length > 0;
    $("ac-history").innerHTML = r.body.map(function (h) {
      return "<tr" + (h.undone_at ? ' class="muted"' : "") + "><td>" + esc(VA.formatDateTime(h.at)) + "</td><td>" + esc(h.by || "") + (h.confirmed_by_second_admin ? '<br><small>confirmed by a 2nd admin</small>' : "") +
        "</td><td>" + esc(h.summary) + (h.undo_of ? " <small>(undo)</small>" : "") + (h.undone_at ? "<br><small>Undone " + esc(VA.formatDateTime(h.undone_at)) + (h.undone_by ? " by " + esc(h.undone_by) : "") + "</small>" : "") +
        "</td><td>" + (h.can_undo ? '<button type="button" class="link-btn" data-undo="' + esc(h.id) + '">Undo</button>' : "") + "</td></tr>";
    }).join("");
  }
  $("ac-history").addEventListener("click", async function (e) {
    const b = e.target.closest("[data-undo]");
    if (!b) return;
    if (!(await VA.confirm("Put this change back as it was before?", "Undo"))) return;
    const r = await VA.fetchJson(API + "/history/" + b.getAttribute("data-undo") + "/undo", { method: "POST" });
    if (done(r, "Undo")) { load(role && role.id); loadHistory(); if (person) openPerson(person.user_id); }
  });

  document.addEventListener("vistadmin:ready", function () {
    load();
    loadPeople();
    loadHistory();
    const want = new URLSearchParams(location.search).get("user");
    if (want) { VA.showTab("people"); openPerson(want); }
  }, { once: true });
})();
