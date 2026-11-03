(function () {
  "use strict";

  // Admin → Top bars. API: /api/v1/banners
  const VA = window.VistAdmin, esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/banners";
  const TONES = [["blue", "#1d4ed8", "Blue"], ["green", "#047857", "Green"], ["orange", "#c2410c", "Orange"], ["red", "#be123c", "Red"],
    ["purple", "#6d28d9", "Purple"], ["teal", "#0e7490", "Teal"], ["ink", "#0b1630", "Dark"], ["yellow", "#f5b700", "Yellow"]];
  const STATE = { SHOWING: "Showing", SCHEDULED: "Scheduled", ENDED: "Ended", HIDDEN: "Hidden" };
  let bars = [], roles = [], editing = null, form = null;

  function msg(r, fallback) { return (r.body && r.body.error && r.body.error.message) || fallback; }
  function json(method, body) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }; }
  function when(iso) { return iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : ""; }
  function localInput(iso) { if (!iso) return ""; const d = new Date(iso); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); }
  function barHtml(b) {
    const btn = b.button_url ? '<a class="topbar-btn" href="' + esc(b.button_url) + '" target="_blank" rel="noopener noreferrer">' + esc(b.button_label || "Open") + (b.new_tab ? ' <span aria-hidden="true">↗</span>' : "") + "</a>" : "";
    return '<div class="topbar tone-' + esc(b.tone || "blue") + '"><span class="topbar-dot" aria-hidden="true"></span><span class="topbar-text">' +
      esc(b.message || "Your message appears here.") + "</span>" + btn + (b.dismissible ? '<span class="topbar-close" aria-hidden="true">×</span>' : "") + "</div>";
  }

  // ------------------------------------------------------------------ list
  async function load() {
    const r = await VA.fetchJson(API);
    if (!r.ok) return VA.toast(msg(r, "Could not load the top bars."), "error");
    bars = r.body.bars; roles = r.body.roles;
    $("tb-empty").hidden = bars.length > 0;
    $("tb-list").innerHTML = bars.map(function (b) {
      const dates = [b.starts_at ? "from " + when(b.starts_at) : "", b.ends_at ? "until " + when(b.ends_at) : ""].filter(Boolean).join(" ");
      return '<article class="tb-item">' + barHtml(b) + '<div class="tb-meta"><span class="tb-state s-' + esc(b.status) + '">' + esc(STATE[b.status] || b.status) + "</span>" +
        "<span><b>" + esc(b.name) + "</b></span><span>" + esc(b.audience_label) + " · reaches " + b.reach.toLocaleString() + "</span>" + (dates ? "<span>" + esc(dates) + "</span>" : "") +
        '<span><button type="button" class="link-btn" data-resp="' + esc(b.id) + '">' + b.clicked + " pressed · " + b.closed + " closed</button></span>" +
        '<span class="tb-actions"><button type="button" class="role-btn small-btn ' + (b.is_active ? "secondary-btn" : "") + '" data-toggle="' + esc(b.id) + '">' + (b.is_active ? "Hide" : "Show") + "</button>" +
        '<button type="button" class="role-btn small-btn secondary-btn" data-edit="' + esc(b.id) + '">Edit</button>' +
        '<button type="button" class="role-btn small-btn secondary-btn danger-btn" data-del="' + esc(b.id) + '">Delete</button></span></div></article>';
    }).join("");
  }

  $("tb-list").addEventListener("click", async function (e) {
    const t = e.target.closest("[data-toggle]"), ed = e.target.closest("[data-edit]"), del = e.target.closest("[data-del]"), resp = e.target.closest("[data-resp]");
    if (t) {
      const b = bars.find(function (x) { return x.id === t.getAttribute("data-toggle"); });
      t.disabled = true;
      const r = await VA.fetchJson(API + "/" + b.id, json("PATCH", { is_active: !b.is_active }));
      if (!r.ok) { t.disabled = false; return VA.toast(msg(r, "Could not change it."), "error"); }
      VA.toast(r.body.is_active ? "Showing again." : "Hidden. Nobody sees it until you show it again.");
      load();
    } else if (ed) {
      open(bars.find(function (x) { return x.id === ed.getAttribute("data-edit"); }));
    } else if (del) {
      const b = bars.find(function (x) { return x.id === del.getAttribute("data-del"); });
      const ok = await VA.ask({ title: "Delete “" + b.name + "”?", intro: "The bar and the list of who pressed or closed it are removed. To stop it for now, use Hide instead.", danger: true, ok: "Delete", input: "none" });
      if (!ok) return;
      const r = await VA.fetchJson(API + "/" + b.id, { method: "DELETE" });
      if (!r.ok) return VA.toast(msg(r, "Could not delete it."), "error");
      load();
    } else if (resp) {
      showResponses(bars.find(function (x) { return x.id === resp.getAttribute("data-resp"); }));
    }
  });

  async function showResponses(b) {
    const r = await VA.fetchJson(API + "/" + b.id + "/responses");
    if (!r.ok) return VA.toast(msg(r, "Could not load the responses."), "error");
    $("tb-resp-title").textContent = b.name;
    $("tb-resp-meta").textContent = b.clicked + " pressed the button · " + b.closed + " closed the bar · reaches " + b.reach.toLocaleString();
    $("tb-resp-csv").href = API + "/" + b.id + "/responses.csv";
    $("tb-resp-body").innerHTML = r.body.map(function (x) {
      return "<tr><td>" + esc(x.person) + "</td><td>" + (x.clicked_at ? esc(when(x.clicked_at)) : "No") + '</td><td class="num">' + x.clicks + "</td><td>" + (x.dismissed_at ? esc(when(x.dismissed_at)) : "") + "</td></tr>";
    }).join("");
    $("tb-resp-empty").hidden = r.body.length > 0;
    $("tb-resp").showModal();
  }
  $("tb-resp-close").addEventListener("click", function () { $("tb-resp").close(); });

  // ------------------------------------------------------------------ form
  function blank() { return { message: "", button_label: "", button_url: "", new_tab: true, tone: "blue", dismissible: true, is_active: true, position: 0, name: "",
    starts_at: null, ends_at: null, audience: { everyone: false, roles: [], program_ids: [], semesters: [], user_ids: [] } }; }

  async function open(b) {
    editing = b ? b.id : null;
    form = JSON.parse(JSON.stringify(b || blank()));
    form.audience = Object.assign({ everyone: false, roles: [], program_ids: [], semesters: [], user_ids: [] }, form.audience || {});
    $("tb-title").textContent = b ? "Change top bar" : "New top bar";
    $("tb-message").value = form.message; $("tb-label").value = form.button_label || ""; $("tb-url").value = form.button_url || "";
    $("tb-newtab").checked = form.new_tab; $("tb-dismiss").checked = form.dismissible; $("tb-active").checked = form.is_active;
    $("tb-name").value = b ? form.name : ""; $("tb-pos").value = form.position || 0;
    $("tb-start").value = localInput(form.starts_at); $("tb-end").value = localInput(form.ends_at);
    $("tb-everyone").checked = !!form.audience.everyone;
    $("tb-error").hidden = true;
    renderAudience();
    people = {};
    if (form.audience.user_ids.length) {
      const r = await VA.fetchJson(API + "/people/names", json("POST", { user_ids: form.audience.user_ids }), { quiet: true });
      if (r.ok) r.body.forEach(function (p) { people[p.user_id] = p.name; });
    }
    renderPeople(); preview(); reach();
    $("tb-modal").showModal();
    $("tb-message").focus();
  }

  let programs = [], people = {};
  async function loadPrograms() {
    const r = await VA.fetchJson("/api/v1/programs", { quiet: true });
    programs = r.ok ? r.body : [];
    $("tb-programs").innerHTML = programs.map(function (p) { return '<option value="' + esc(p.id) + '">' + esc(p.code + " — " + p.name) + "</option>"; }).join("");
  }

  function renderAudience() {
    $("tb-groups").hidden = $("tb-everyone").checked;
    $("tb-roles").innerHTML = roles.map(function (r) {
      return '<button type="button" class="tb-chip" data-role="' + esc(r.code) + '" aria-pressed="' + (form.audience.roles.indexOf(r.code) !== -1) + '">' + esc(r.label) + "</button>";
    }).join("");
    $("tb-semesters").innerHTML = [1, 2, 3, 4, 5, 6, 7, 8].map(function (s) {
      return '<button type="button" class="tb-chip" data-sem="' + s + '" aria-pressed="' + (form.audience.semesters.indexOf(s) !== -1) + '">' + s + "</button>";
    }).join("");
    Array.prototype.forEach.call($("tb-programs").options, function (o) { o.selected = form.audience.program_ids.indexOf(o.value) !== -1; });
    $("tb-tones").innerHTML = TONES.map(function (t) {
      return '<button type="button" class="tb-swatch' + (form.tone === t[0] ? " on" : "") + '" style="background:' + t[1] + '" data-tone="' + t[0] + '" role="radio" aria-checked="' + (form.tone === t[0]) + '" aria-label="' + t[2] + '" title="' + t[2] + '"></button>';
    }).join("");
  }
  function renderPeople() {
    const ids = form.audience.user_ids;
    $("tb-people").innerHTML = ids.slice(0, 60).map(function (id) {
      return '<span class="tb-person">' + esc(people[id] || "Someone") + ' <button type="button" data-unpick="' + esc(id) + '" aria-label="Remove ' + esc(people[id] || "") + '">×</button></span>';
    }).join("") + (ids.length > 60 ? '<span class="cc-meta">and ' + (ids.length - 60) + " more</span>" : "");
  }
  function preview() {
    form.message = $("tb-message").value; form.button_label = $("tb-label").value.trim(); form.button_url = $("tb-url").value.trim();
    form.new_tab = $("tb-newtab").checked; form.dismissible = $("tb-dismiss").checked;
    $("tb-preview").innerHTML = barHtml({ message: form.message.trim(), button_label: form.button_label || (form.button_url ? "Open" : ""), button_url: form.button_url ? "#" : "",
      new_tab: form.new_tab, tone: form.tone, dismissible: form.dismissible });
  }
  let reachTimer = null;
  function reach() {
    clearTimeout(reachTimer);
    reachTimer = setTimeout(async function () {
      form.audience.everyone = $("tb-everyone").checked;
      const r = await VA.fetchJson(API + "/reach", json("POST", { audience: form.audience }), { quiet: true });
      $("tb-reach").textContent = r.ok ? r.body.reach.toLocaleString() + " account" + (r.body.reach === 1 ? "" : "s") + " (" + r.body.label + ")" : "—";
    }, 250);
  }

  ["tb-message", "tb-label", "tb-url"].forEach(function (id) { $(id).addEventListener("input", preview); });
  ["tb-newtab", "tb-dismiss"].forEach(function (id) { $(id).addEventListener("change", preview); });
  $("tb-everyone").addEventListener("change", function () { $("tb-groups").hidden = $("tb-everyone").checked; reach(); });
  $("tb-tones").addEventListener("click", function (e) { const b = e.target.closest("[data-tone]"); if (!b) return; form.tone = b.getAttribute("data-tone"); renderAudience(); preview(); });
  $("tb-roles").addEventListener("click", function (e) {
    const b = e.target.closest("[data-role]"); if (!b) return;
    const code = b.getAttribute("data-role"), list = form.audience.roles, i = list.indexOf(code);
    if (i === -1) list.push(code); else list.splice(i, 1);
    b.setAttribute("aria-pressed", String(i === -1)); reach();
  });
  $("tb-semesters").addEventListener("click", function (e) {
    const b = e.target.closest("[data-sem]"); if (!b) return;
    const s = Number(b.getAttribute("data-sem")), list = form.audience.semesters, i = list.indexOf(s);
    if (i === -1) list.push(s); else list.splice(i, 1);
    b.setAttribute("aria-pressed", String(i === -1)); reach();
  });
  $("tb-programs").addEventListener("change", function () {
    form.audience.program_ids = Array.prototype.filter.call($("tb-programs").options, function (o) { return o.selected; }).map(function (o) { return o.value; });
    reach();
  });
  let findTimer = null;
  $("tb-person-q").addEventListener("input", function () {
    clearTimeout(findTimer);
    const q = $("tb-person-q").value.trim();
    if (q.length < 2) { $("tb-person-list").innerHTML = ""; return; }
    findTimer = setTimeout(async function () {
      const r = await VA.fetchJson(API + "/people?q=" + encodeURIComponent(q), { quiet: true });
      if (!r.ok) return;
      $("tb-person-list").innerHTML = r.body.map(function (p) {
        return '<button type="button" class="picker-item" data-pick="' + esc(p.user_id) + '" data-name="' + esc(p.name + " (" + p.detail + ")") + '"><span>' + esc(p.name) + "</span><small>" + esc(p.detail) + "</small></button>";
      }).join("") || '<p class="cc-meta">Nobody found.</p>';
    }, 250);
  });
  $("tb-person-list").addEventListener("click", function (e) {
    const b = e.target.closest("[data-pick]"); if (!b) return;
    const id = b.getAttribute("data-pick");
    if (form.audience.user_ids.indexOf(id) === -1) form.audience.user_ids.push(id);
    people[id] = b.getAttribute("data-name");
    $("tb-person-q").value = ""; $("tb-person-list").innerHTML = ""; renderPeople(); reach(); $("tb-person-q").focus();
  });
  $("tb-people").addEventListener("click", function (e) {
    const b = e.target.closest("[data-unpick]"); if (!b) return;
    form.audience.user_ids = form.audience.user_ids.filter(function (x) { return x !== b.getAttribute("data-unpick"); });
    renderPeople(); reach();
  });

  $("tb-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    preview();
    form.audience.everyone = $("tb-everyone").checked;
    const start = $("tb-start").value, end = $("tb-end").value;
    const body = { message: form.message.trim(), button_label: form.button_label || null, button_url: form.button_url || null, new_tab: form.new_tab, tone: form.tone,
      dismissible: form.dismissible, is_active: $("tb-active").checked, position: Number($("tb-pos").value || 0), name: $("tb-name").value.trim(),
      starts_at: start ? new Date(start).toISOString() : null, ends_at: end ? new Date(end).toISOString() : null, audience: form.audience };
    $("tb-save").disabled = true;
    const r = await VA.fetchJson(editing ? API + "/" + editing : API, json(editing ? "PATCH" : "POST", body));
    $("tb-save").disabled = false;
    if (!r.ok) { $("tb-error").textContent = msg(r, "Could not save it."); $("tb-error").hidden = false; return; }
    $("tb-modal").close();
    VA.toast(r.body.status === "SHOWING" ? "Saved. It is showing now to " + r.body.reach.toLocaleString() + " account(s)." : "Saved (" + (STATE[r.body.status] || r.body.status).toLowerCase() + ").");
    load();
  });
  $("tb-cancel").addEventListener("click", function () { $("tb-modal").close(); });
  $("tb-new").addEventListener("click", function () { open(null); });

  document.addEventListener("vistadmin:ready", function () { loadPrograms(); load(); });
})();