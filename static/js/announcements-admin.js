(function () {
  "use strict";

  const VA = window.VistAdmin;
  const AU = window.AnnouncementUI;
  const esc = VA.escapeHtml;
  const API = "/api/v1/announcements";

  const $ = function (id) { return document.getElementById(id); };
  const listEl = $("adm-list"), emptyEl = $("adm-empty");
  const form = $("ann-form"), errorEl = $("ann-form-error");
  let items = [];
  let editing = null;          // the announcement being edited, or null when writing a new one
  let picked = [];             // files chosen but not yet uploaded
  let authorities = [];

  const msg = function (body, fallback) { return body && body.error && body.error.message ? body.error.message : fallback; };

  // ------------------------------------------------------------------ the list
  function statusPill(a) {
    return '<span class="status-pill ' + (a.status === "PUBLISHED" ? "active" : a.status === "DRAFT" ? "pending" : "dropped") + '">' +
      (a.status === "PUBLISHED" ? "Published" : a.status === "DRAFT" ? "Draft" : "Taken down") + "</span>";
  }
  function audience(a) {
    const who = [];
    if (a.audience_students) who.push(a.program_names && a.program_names.length ? "students (" + a.program_names.join(", ") + ")" : "all students");
    if (a.audience_teachers) who.push("teachers");
    if (a.audience_staff) who.push("other staff");
    return who.join(" \u00b7 ");
  }

  function card(a) {
    const el = document.createElement("article");
    el.className = "adm-card " + a.priority.toLowerCase() + " " + a.status;
    const pct = a.recipients ? Math.round((a.read_count / a.recipients) * 100) : 0;
    el.innerHTML =
      '<div class="ann-card-top"><div class="ann-badges">' + statusPill(a) + AU.badges(a) + "</div></div>" +
      "<h3>" + esc(a.title) + "</h3>" +
      '<p class="adm-line"><strong>' + esc(a.issuer_name) + "</strong>, " + esc(a.issuer_designation) + "</p>" +
      '<p class="adm-line">For: ' + esc(audience(a)) + (a.attachments.length ? " \u00b7 \uD83D\uDCCE " + a.attachments.length + " file" + (a.attachments.length > 1 ? "s" : "") : "") +
      (a.expires_at ? " \u00b7 hidden after " + esc(new Date(a.expires_at).toLocaleDateString()) : "") + "</p>" +
      (a.status === "DRAFT"
        ? '<p class="adm-line">Not published yet \u2014 nobody can see it.</p>'
        : '<div class="adm-reach"><span><strong>' + a.read_count + "</strong> of <strong>" + a.recipients + "</strong> have opened it (" + pct + '%)</span><div class="meter" role="progressbar" aria-valuenow="' + pct +
          '" aria-valuemin="0" aria-valuemax="100"><span class="meter-fill" style="width:' + Math.max(pct, a.read_count ? 3 : 0) + '%"></span></div></div>' +
          '<p class="adm-line">Published ' + esc(AU.when(a.published_at)) + "</p>") +
      '<div class="adm-actions"></div>';
    const actions = el.querySelector(".adm-actions");
    const add = function (label, cls, fn) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "role-btn small-btn " + cls; b.textContent = label; b.addEventListener("click", fn); actions.appendChild(b);
    };
    add("View", "secondary-btn", function () { AU.openDetail(a); });
    add("Edit", "secondary-btn", function () { startEdit(a); });
    if (a.status === "PUBLISHED") add("Take down", "secondary-btn", function () { act(a, "archive", "Take this announcement down? People will no longer see it (it is kept for the record)."); });
    else add(a.status === "DRAFT" ? "Publish" : "Publish again", "", function () { act(a, "publish", "Publish this announcement now?" + (a.published_at ? "" : " Everyone it is for will be notified.")); });
    add("Delete", "danger-btn", function () { remove(a); });
    return el;
  }

  async function act(a, action, question) {
    if (!window.confirm(question)) return;
    const r = await VA.fetchJson(API + "/" + a.id + "/" + action, { method: "POST" });
    if (!r.ok) { VA.toast(msg(r.body, "That did not work."), "error"); return; }
    VA.toast(action === "publish" ? "Published." : "Taken down.");
    loadList();
  }
  async function remove(a) {
    if (!window.confirm("Delete \u201c" + a.title + "\u201d and its attachments for good? This cannot be undone.")) return;
    const r = await VA.fetchJson(API + "/" + a.id, { method: "DELETE" });
    if (r.ok) { VA.toast("Deleted."); loadList(); } else VA.toast(msg(r.body, "Could not delete it."), "error");
  }

  async function loadList() {
    const status = $("adm-status").value;
    const r = await VA.fetchJson(API + "/manage" + (status ? "?status=" + status : ""));
    listEl.innerHTML = "";
    if (!r.ok) { listEl.innerHTML = '<p class="home-empty">' + esc(msg(r.body, "Could not load announcements.")) + "</p>"; return; }
    items = r.body;
    emptyEl.hidden = items.length > 0;
    items.forEach(function (a) { listEl.appendChild(card(a)); });
  }

  // ------------------------------------------------------------------ compose / edit
  function resetForm() {
    editing = null; picked = [];
    form.reset();
    $("compose-title").textContent = "Write an announcement";
    $("compose-tab-label").textContent = "New announcement";
    $("ann-cancel").hidden = true;
    $("ann-publish").textContent = "Publish now";
    $("ann-existing").innerHTML = ""; $("ann-picked").innerHTML = ""; errorEl.hidden = true; $("ann-count").textContent = "0";
    document.querySelectorAll("#ann-programs input").forEach(function (c) { c.checked = false; });
    updateProgramsVisibility();
  }

  function startEdit(a) {
    resetForm();
    editing = a;
    $("compose-title").textContent = "Edit announcement";
    $("compose-tab-label").textContent = "Edit announcement";
    $("ann-cancel").hidden = false;
    $("ann-title").value = a.title; $("ann-body").value = a.body; $("ann-count").textContent = a.body.length;
    $("ann-category").value = a.category; $("ann-priority").value = a.priority;
    $("ann-aud-students").checked = a.audience_students; $("ann-aud-teachers").checked = a.audience_teachers; $("ann-aud-staff").checked = a.audience_staff;
    $("ann-pinned").checked = a.pinned;
    $("ann-expires").value = a.expires_at ? a.expires_at.slice(0, 10) : "";
    (a.program_ids || []).forEach(function (id) { const c = document.querySelector('#ann-programs input[value="' + id + '"]'); if (c) c.checked = true; });
    const match = authorities.find(function (x) { return x.is_active && x.name === a.issuer_name && x.designation === a.issuer_designation; });
    if (match) $("ann-issuer").value = match.id;
    $("ann-publish").textContent = a.status === "PUBLISHED" ? "Save changes" : "Publish now";
    $("ann-draft").hidden = a.status === "PUBLISHED";
    renderExisting();
    updateProgramsVisibility();
    VA.showTab("compose");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function renderExisting() {
    const box = $("ann-existing");
    box.innerHTML = "";
    if (!editing || !editing.attachments.length) return;
    const ul = document.createElement("ul");
    ul.className = "ann-files";
    editing.attachments.forEach(function (f) {
      const li = document.createElement("li");
      li.innerHTML = '<span class="ann-ext">' + esc((/\.([a-z0-9]+)$/i.exec(f.original_name) || [0, "FILE"])[1].toUpperCase()) + '</span><span class="ann-fname"><strong>' + esc(f.original_name) + "</strong><small>" + AU.size(f.size_bytes) + "</small></span>";
      const rm = document.createElement("button");
      rm.type = "button"; rm.className = "linkish"; rm.textContent = "Remove";
      rm.addEventListener("click", async function () {
        const r = await VA.fetchJson(API + "/attachments/" + f.id, { method: "DELETE" });
        if (r.ok) { editing.attachments = editing.attachments.filter(function (x) { return x.id !== f.id; }); renderExisting(); } else VA.toast(msg(r.body, "Could not remove it."), "error");
      });
      li.appendChild(rm); ul.appendChild(li);
    });
    box.appendChild(ul);
  }

  function renderPicked() {
    const ul = $("ann-picked");
    ul.innerHTML = "";
    picked.forEach(function (file, i) {
      const li = document.createElement("li");
      li.innerHTML = '<span class="ann-ext">' + esc((/\.([a-z0-9]+)$/i.exec(file.name) || [0, "FILE"])[1].toUpperCase()) + '</span><span class="ann-fname"><strong>' + esc(file.name) + "</strong><small>" + AU.size(file.size) + (file.size > 15 * 1048576 ? " \u2014 too large (15 MB max)" : "") + "</small></span>";
      const rm = document.createElement("button");
      rm.type = "button"; rm.className = "linkish"; rm.textContent = "Remove";
      rm.addEventListener("click", function () { picked.splice(i, 1); renderPicked(); });
      li.appendChild(rm); ul.appendChild(li);
    });
  }
  function addFiles(list) { Array.prototype.forEach.call(list, function (f) { picked.push(f); }); renderPicked(); }

  function updateProgramsVisibility() { $("ann-programs-wrap").hidden = !$("ann-aud-students").checked; }

  async function submit(publish) {
    errorEl.hidden = true;
    const fail = function (text) { errorEl.textContent = text; errorEl.hidden = false; errorEl.scrollIntoView({ block: "center" }); };
    const title = $("ann-title").value.trim(), body = $("ann-body").value.trim();
    if (!authorities.some(function (a) { return a.is_active; })) return fail("Add an issuing official first (the Issuing officials tab).");
    if (title.length < 3) return fail("Give the announcement a title.");
    if (!body) return fail("Write the message.");
    if (!$("ann-aud-students").checked && !$("ann-aud-teachers").checked && !$("ann-aud-staff").checked) return fail("Choose who this announcement is for.");
    if (picked.some(function (f) { return f.size > 15 * 1048576; })) return fail("A file is over 15 MB. Remove it or use a smaller one.");
    const total = (editing ? editing.attachments.length : 0) + picked.length;
    if (total > 8) return fail("An announcement can have at most 8 attachments.");

    const programs = Array.prototype.map.call(document.querySelectorAll("#ann-programs input:checked"), function (c) { return c.value; });
    const payload = {
      title: title, body: body, category: $("ann-category").value, priority: $("ann-priority").value, issuer_id: $("ann-issuer").value,
      audience_students: $("ann-aud-students").checked, audience_teachers: $("ann-aud-teachers").checked, audience_staff: $("ann-aud-staff").checked,
      pinned: $("ann-pinned").checked,
    };
    const expires = $("ann-expires").value;
    const buttons = [$("ann-publish"), $("ann-draft")];
    buttons.forEach(function (b) { b.disabled = true; });
    try {
      let r;
      if (editing) {
        Object.assign(payload, { program_ids: $("ann-aud-students").checked && programs.length ? programs : null, clear_program_filter: !programs.length || !$("ann-aud-students").checked,
          expires_at: expires ? new Date(expires + "T23:59:59").toISOString() : null, clear_expiry: !expires });
        if (!payload.program_ids) delete payload.program_ids;
        if (!payload.expires_at) delete payload.expires_at;
        r = await VA.fetchJson(API + "/" + editing.id, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      } else {
        Object.assign(payload, { program_ids: $("ann-aud-students").checked && programs.length ? programs : null, expires_at: expires ? new Date(expires + "T23:59:59").toISOString() : null, publish: false });
        r = await VA.fetchJson(API, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      }
      if (!r.ok) return fail(msg(r.body, "Could not save the announcement."));
      const saved = r.body;

      // Files go up before it is published, so nobody ever sees an announcement missing its attachments.
      for (let i = 0; i < picked.length; i++) {
        $("ann-publish").textContent = "Uploading file " + (i + 1) + " of " + picked.length + "\u2026";
        const fd = new FormData();
        fd.append("file", picked[i]);
        const up = await VA.fetchJson(API + "/" + saved.id + "/attachments", { method: "POST", body: fd });
        if (!up.ok) {
          editing = saved; picked = picked.slice(i);
          await loadList();
          return fail("Saved as a draft, but \u201c" + picked[0].name + "\u201d could not be attached: " + msg(up.body, "upload failed") + " Fix it and press the button again.");
        }
      }
      if (publish && saved.status !== "PUBLISHED") {
        const pub = await VA.fetchJson(API + "/" + saved.id + "/publish", { method: "POST" });
        if (!pub.ok) return fail("Saved, but it could not be published: " + msg(pub.body, "error"));
      }
      VA.toast(publish ? (editing && saved.status === "PUBLISHED" ? "Changes saved." : "Published. Everyone it is for has been notified.") : "Saved as a draft.");
      resetForm();
      await loadList();
      VA.showTab("all");
    } finally {
      buttons.forEach(function (b) { b.disabled = false; });
      $("ann-publish").textContent = editing ? (editing.status === "PUBLISHED" ? "Save changes" : "Publish now") : "Publish now";
    }
  }

  // ------------------------------------------------------------------ issuing officials
  async function loadAuthorities() {
    const r = await VA.fetchJson(API + "/authorities");
    if (!r.ok) return;
    authorities = r.body;
    const sel = $("ann-issuer");
    const keep = sel.value;
    sel.innerHTML = authorities.filter(function (a) { return a.is_active; }).map(function (a) {
      return '<option value="' + a.id + '">' + esc(a.name) + " \u2014 " + esc(a.designation) + "</option>";
    }).join("") || '<option value="">No official yet \u2014 add one first</option>';
    if (keep) sel.value = keep;
    $("ann-issuer-hint").innerHTML = authorities.some(function (a) { return a.is_active; })
      ? "The official this notice comes from, shown to everyone who reads it."
      : '<strong>Add an issuing official first</strong> (the &ldquo;Issuing officials&rdquo; tab), for example the Managing Director.';
    const box = $("auth-list");
    box.innerHTML = "";
    authorities.forEach(function (a) {
      const row = document.createElement("div");
      row.className = "auth-row" + (a.is_active ? "" : " off");
      const view = function () {
        row.innerHTML = '<span class="sh-avatar" style="--h:' + AU.hueOf(a.name) + '">' + esc(AU.initials(a.name)) + '</span><span class="grow"><strong>' + esc(a.name) + "</strong><small>" + esc(a.designation) + (a.is_active ? "" : " \u00b7 switched off") + "</small></span>";
        const edit = document.createElement("button"); edit.type = "button"; edit.className = "role-btn small-btn secondary-btn"; edit.textContent = "Edit"; edit.addEventListener("click", editRow);
        const tog = document.createElement("button"); tog.type = "button"; tog.className = "role-btn small-btn secondary-btn"; tog.textContent = a.is_active ? "Switch off" : "Switch on";
        tog.addEventListener("click", async function () {
          const r2 = await VA.fetchJson(API + "/authorities/" + a.id, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ is_active: !a.is_active }) });
          if (r2.ok) loadAuthorities(); else VA.toast(msg(r2.body, "Could not change it."), "error");
        });
        row.appendChild(edit); row.appendChild(tog);
      };
      const editRow = function () {
        row.innerHTML = '<input class="text-input" value="' + esc(a.name) + '" aria-label="Name" maxlength="120" /><input class="text-input" value="' + esc(a.designation) + '" aria-label="Designation" maxlength="120" />';
        const save = document.createElement("button"); save.type = "button"; save.className = "role-btn small-btn"; save.textContent = "Save";
        const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "role-btn small-btn secondary-btn"; cancel.textContent = "Cancel"; cancel.addEventListener("click", view);
        save.addEventListener("click", async function () {
          const inputs = row.querySelectorAll("input");
          const r2 = await VA.fetchJson(API + "/authorities/" + a.id, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: inputs[0].value, designation: inputs[1].value }) });
          if (r2.ok) loadAuthorities(); else VA.toast(msg(r2.body, "Could not save."), "error");
        });
        row.appendChild(save); row.appendChild(cancel);
      };
      view();
      box.appendChild(row);
    });
  }

  $("auth-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const err = $("auth-error");
    err.hidden = true;
    const r = await VA.fetchJson(API + "/authorities", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("auth-name").value, designation: $("auth-designation").value }) });
    if (!r.ok) { err.textContent = msg(r.body, "Could not add the official."); err.hidden = false; return; }
    $("auth-name").value = ""; $("auth-designation").value = "";
    VA.toast("Added.");
    loadAuthorities();
  });

  // ------------------------------------------------------------------ wiring
  $("ann-publish").addEventListener("click", function () { submit(true); });
  $("ann-draft").addEventListener("click", function () { submit(false); });
  $("ann-cancel").addEventListener("click", function () { resetForm(); VA.showTab("all"); });
  $("adm-new").addEventListener("click", function () { resetForm(); VA.showTab("compose"); });
  $("adm-status").addEventListener("change", loadList);
  $("ann-modal-close").addEventListener("click", function () { $("ann-modal").close(); });
  $("ann-body").addEventListener("input", function () { $("ann-count").textContent = $("ann-body").value.length; });
  $("ann-aud-students").addEventListener("change", updateProgramsVisibility);
  $("ann-files").addEventListener("change", function (e) { addFiles(e.target.files); e.target.value = ""; });
  const zone = document.querySelector(".drop-zone");
  ["dragenter", "dragover"].forEach(function (ev) { zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.add("over"); }); });
  ["dragleave", "drop"].forEach(function (ev) { zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.remove("over"); }); });
  zone.addEventListener("drop", function (e) { addFiles(e.dataTransfer.files); });
  form.addEventListener("submit", function (e) { e.preventDefault(); });

  document.addEventListener("vistadmin:ready", async function () {
    const progs = await VA.fetchJson("/api/v1/programs");
    if (progs.ok) {
      $("ann-programs").innerHTML = progs.body.filter(function (p) { return p.is_active !== false; }).map(function (p) {
        return '<label><input type="checkbox" value="' + p.id + '" /> ' + esc(p.code) + "</label>";
      }).join("");
    }
    await loadAuthorities();
    loadList();
  });
})();