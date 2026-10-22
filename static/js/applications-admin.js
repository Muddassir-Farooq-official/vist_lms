(function () {
  "use strict";

  const VA = window.VistAdmin;
  const SU = window.StudentUI;
  const esc = VA.escapeHtml;
  const API = "/api/v1/applications";
  const $ = function (id) { return document.getElementById(id); };
  const msg = function (body, fallback) { return body && body.error && body.error.message ? body.error.message : fallback; };

  const STATUS = {
    SUBMITTED: ["Waiting", "pending"], UNDER_REVIEW: ["Under review", "enrolled"], MORE_INFO_NEEDED: ["Waiting for the applicant", "pending"],
    APPROVED: ["Approved", "active"], REJECTED: ["Rejected", "dropped"], WITHDRAWN: ["Withdrawn", ""],
  };
  const pill = function (s) { const x = STATUS[s] || [s, ""]; return '<span class="status-pill ' + x[1] + '">' + esc(x[0]) + "</span>"; };
  const fmtDate = function (iso) { return iso ? new Date(iso + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : ""; };
  const range = function (a) { return a.from_date ? fmtDate(a.from_date) + (a.to_date !== a.from_date ? " \u2192 " + fmtDate(a.to_date) : "") + " \u00b7 " + a.days + " day" + (a.days > 1 ? "s" : "") : ""; };
  const age = function (iso) {
    const h = (Date.now() - new Date(iso).getTime()) / 3600000;
    return h < 1 ? "just now" : h < 24 ? Math.floor(h) + " h ago" : Math.floor(h / 24) + " day" + (Math.floor(h / 24) > 1 ? "s" : "") + " ago";
  };
  const size = function (b) { return b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB"; };
  const ext = function (n) { const m = /\.([a-z0-9]+)$/i.exec(n || ""); return m ? m[1].toUpperCase() : "FILE"; };
  const updateBadge = function () { VA.fetchJson(API + "/badge", { quiet: true }).then(function (r) { if (r.ok) window.dispatchEvent(new CustomEvent("applications:badge", { detail: r.body.count })); }); };

  let current = null;
  let allOffset = 0;
  const PAGE = 30;
  let timer = null;

  // ------------------------------------------------------------------ cards
  function card(a) {
    const who = a.applicant || { name: "Unknown", kind: "STUDENT" };
    const el = document.createElement("article");
    el.className = "asg-card " + (a.status === "SUBMITTED" ? "soon" : a.status === "APPROVED" ? "ok" : a.status === "REJECTED" || a.status === "WITHDRAWN" ? "missed" : "");
    el.tabIndex = 0; el.setAttribute("role", "button");
    el.innerHTML =
      '<div class="asg-top"><span class="pill">' + esc(a.reference) + "</span>" + pill(a.status) + '<span class="pill">' + (who.kind === "EMPLOYEE" ? "Employee" : "Student") + "</span></div>" +
      '<div class="rv-who">' + SU.avatar(who.name) + "<span><strong>" + esc(who.name) + "</strong><small>" + esc(who.identifier || "") + "</small></span></div>" +
      "<h3>" + esc(a.subject) + "</h3>" +
      '<p class="asg-snippet">' + esc(a.type_name) + (a.from_date ? " \u00b7 " + esc(range(a)) : "") + "</p>" +
      '<div class="asg-foot"><span class="cc-meta">' + esc(age(a.created_at)) + (a.attachments.length ? " \u00b7 \uD83D\uDCCE " + a.attachments.length : "") + (a.comment_count ? " \u00b7 \uD83D\uDCAC " + a.comment_count : "") +
      '</span><span class="role-btn small-btn' + (a.status === "SUBMITTED" ? "" : " secondary-btn") + '">' + (a.status === "SUBMITTED" ? "Review" : "Open") + "</span></div>";
    const open = function () { openModal(a.id); };
    el.addEventListener("click", open);
    el.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    return el;
  }

  async function loadQueue() {
    const r = await VA.fetchJson(API + "?needs_action=true&limit=200");
    const box = $("q-list");
    box.innerHTML = "";
    if (!r.ok) { box.innerHTML = '<p class="home-empty">' + esc(msg(r.body, "Could not load applications.")) + "</p>"; return; }
    $("q-empty").hidden = r.body.length > 0;
    r.body.forEach(function (a) { box.appendChild(card(a)); });
    $("tab-queue").textContent = "Needs action" + (r.body.length ? " \u00b7 " + r.body.length : "");
    updateBadge();
  }

  async function loadAll(more) {
    if (!more) allOffset = 0;
    const p = new URLSearchParams({ limit: String(PAGE), offset: String(allOffset) });
    [["f-q", "q"], ["f-status", "status"], ["f-type", "type_id"], ["f-kind", "kind"]].forEach(function (x) { const v = $(x[0]).value.trim(); if (v) p.set(x[1], v); });
    const r = await VA.fetchJson(API + "?" + p.toString());
    if (!r.ok) { VA.toast(msg(r.body, "Could not load applications."), "error"); return; }
    if (!more) $("a-list").innerHTML = "";
    r.body.forEach(function (a) { $("a-list").appendChild(card(a)); });
    allOffset += r.body.length;
    $("a-more").hidden = r.body.length < PAGE;
    $("a-empty").hidden = $("a-list").children.length > 0;
  }

  // ------------------------------------------------------------------ review dialog
  const modal = $("rv-modal");
  function showError(t) { $("rv-error").textContent = t || ""; $("rv-error").hidden = !t; }

  function fill(a) {
    current = a;
    const who = a.applicant;
    $("rv-badges").innerHTML = '<span class="pill">' + esc(a.reference) + "</span>" + pill(a.status);
    $("rv-title").textContent = a.subject;
    $("rv-meta").textContent = a.type_name + (a.from_date ? " \u00b7 " + range(a) : "") + " \u00b7 applied " + VA.formatDateTime(a.created_at);
    $("rv-applicant").innerHTML = who
      ? SU.avatar(who.name) + '<span><small>' + (who.kind === "EMPLOYEE" ? "Employee" : "Student") + "</small><strong>" + esc(who.name) + "</strong><em>" + esc([who.identifier, who.detail].filter(Boolean).join(" \u00b7 ")) + "</em></span>" +
        '<span class="ann-date">' + esc(who.contact_no || "") + "</span>" : "";
    const u = a.usage;
    $("rv-usage").innerHTML = u ? '<div class="rules-note"><strong>Yearly allowance (' + u.year + "):</strong> " + u.approved_days + " of " + u.limit_days + " days approved" + (u.pending_days ? ", " + u.pending_days + " more waiting for a decision" : "") + ".</div>" : "";
    $("rv-details").textContent = a.details;
    const url = function (f) { return API + "/attachments/" + f.id + "/file"; };
    $("rv-files").innerHTML = a.attachments.length ? '<ul class="ann-files">' + a.attachments.map(function (f) {
      return '<li><span class="ann-ext">' + esc(ext(f.original_name)) + '</span><span class="ann-fname"><strong>' + esc(f.original_name) + "</strong><small>" + size(f.size_bytes) + "</small></span>" +
        (f.inline ? '<a class="role-btn small-btn" href="' + url(f) + '" target="_blank" rel="noopener">Open</a>' : "") +
        '<a class="role-btn small-btn secondary-btn" href="' + url(f) + '" data-download="' + esc(f.original_name) + '" data-label="file">Download</a></li>';
    }).join("") + "</ul>" : "";
    $("rv-others").innerHTML = a.others && a.others.length ? '<h3 class="modal-subhead">Their other recent applications</h3><ul class="others">' + a.others.map(function (o) {
      return '<li><button type="button" class="linkish" data-open="' + o.id + '">' + esc(o.reference) + "</button> " + esc(o.type_name) + (o.from_date ? " (" + esc(fmtDate(o.from_date)) + ")" : "") + " " + pill(o.status) + "</li>";
    }).join("") + "</ul>" : "";
    $("rv-others").querySelectorAll("[data-open]").forEach(function (b) { b.addEventListener("click", function () { openModal(b.getAttribute("data-open")); }); });
    $("rv-thread").innerHTML = a.comments.length ? a.comments.map(function (c) {
      return '<div class="msg ' + c.side.toLowerCase() + '"><strong>' + esc(c.author_name) + "</strong><span>" + esc(c.body) + "</span><small>" + esc(VA.formatDateTime(c.created_at)) + "</small></div>";
    }).join("") : '<p class="home-empty">No messages yet.</p>';
    $("rv-note").value = "";
    const s = a.status, final = s === "APPROVED" || s === "REJECTED";
    $("rv-decided").hidden = !(final || s === "WITHDRAWN");
    $("rv-decided").textContent = s === "WITHDRAWN" ? "The applicant withdrew this application." : final ? (s === "APPROVED" ? "Approved" : "Rejected") + " on " + VA.formatDateTime(a.decided_at) + (a.decision_note ? ". Note: " + a.decision_note : ".") + " You can still change the decision." : "";
    const show = { START_REVIEW: s === "SUBMITTED" || s === "MORE_INFO_NEEDED", REQUEST_INFO: ["SUBMITTED", "UNDER_REVIEW", "MORE_INFO_NEEDED"].indexOf(s) !== -1,
                   APPROVE: s !== "WITHDRAWN" && s !== "APPROVED", REJECT: s !== "WITHDRAWN" && s !== "REJECTED" };
    $("rv-actions").querySelectorAll("[data-act]").forEach(function (b) { b.hidden = !show[b.getAttribute("data-act")]; });
    $("rv-message").hidden = false;
    $("rv-actions").querySelector('[data-act="APPROVE"]').textContent = final ? "Change to approved" : "Approve";
    $("rv-actions").querySelector('[data-act="REJECT"]').textContent = final ? "Change to rejected" : "Reject";
    showError("");
  }

  async function openModal(id) {
    const r = await VA.fetchJson(API + "/" + id);
    if (!r.ok) { VA.toast(msg(r.body, "Could not open it."), "error"); return; }
    fill(r.body);
    if (!modal.open) modal.showModal();
    const t = $("rv-title"); t.tabIndex = -1; t.focus({ preventScroll: true });
  }

  async function act(action) {
    showError("");
    const note = $("rv-note").value.trim();
    if ((action === "REQUEST_INFO" || action === "REJECT") && note.length < 3) return showError(action === "REJECT" ? "Give a reason for rejecting it." : "Say what you need to know.");
    document.querySelectorAll("#rv-actions button").forEach(function (b) { b.disabled = true; });
    const r = await VA.fetchJson(API + "/" + current.id + "/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: action, note: note || null }) });
    document.querySelectorAll("#rv-actions button").forEach(function (b) { b.disabled = false; });
    if (!r.ok) return showError(msg(r.body, "That did not work."));
    VA.toast({ START_REVIEW: "Marked as under review.", REQUEST_INFO: "Question sent to the applicant.", APPROVE: "Approved. The applicant has been told.", REJECT: "Rejected. The applicant has been told why." }[action]);
    fill(r.body);
    refresh();
  }
  $("rv-actions").querySelectorAll("[data-act]").forEach(function (b) { b.addEventListener("click", function () { act(b.getAttribute("data-act")); }); });
  $("rv-message").addEventListener("click", async function () {
    const note = $("rv-note").value.trim();
    if (!note) return showError("Write a message first.");
    const r = await VA.fetchJson(API + "/" + current.id + "/comments", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body: note }) });
    if (!r.ok) return showError(msg(r.body, "Could not send it."));
    VA.toast("Message sent."); fill(r.body); refresh();
  });
  $("rv-close").addEventListener("click", function () { modal.close(); });
  function refresh() { loadQueue(); loadAll(false); loadOverview(); }

  // ------------------------------------------------------------------ overview
  const set = function (id, v) { const e = $(id); if (e) e.textContent = v; };
  async function loadOverview() {
    const r = await VA.fetchJson(API + "/overview");
    if (!r.ok) return;
    const d = r.body, k = d.kpis, P = Charts.palette();
    set("k-wait", k.needs_action); set("k-wait-sub", k.oldest_waiting_days === null ? "nothing waiting" : "oldest " + k.oldest_waiting_days + " day" + (k.oldest_waiting_days === 1 ? "" : "s"));
    set("k-review", k.under_review); set("k-info-sub", k.more_info + " waiting for the applicant");
    set("k-approved", k.approved); set("k-rejected", k.rejected); set("k-rate-sub", k.approval_rate === null ? "" : k.approval_rate + "% approved");
    set("k-avg", k.avg_decision_hours === null ? "\u2014" : k.avg_decision_hours < 48 ? (Math.round(k.avg_decision_hours * 10) / 10) + " h" : (Math.round(k.avg_decision_hours / 24 * 10) / 10) + " days");
    set("k-total", k.total); set("k-withdrawn-sub", k.withdrawn + " withdrawn");
    const colour = { SUBMITTED: P.yellow, UNDER_REVIEW: P.blueSoft, MORE_INFO_NEEDED: P.amber, APPROVED: P.green, REJECTED: P.red, WITHDRAWN: P.grey };
    Charts.donut($("c-status"), d.by_status.map(function (s) { return { label: s.label, count: s.count, color: colour[s.key] }; }), { title: "Applications by status", centerLabel: "applications", empty: "No applications yet." });
    Charts.hbars($("c-type"), d.by_type.map(function (t) { return { label: t.label, count: t.count }; }), { empty: "No applications yet." });
    Charts.lineChart($("c-week"), d.by_week.map(function (w) {
      return { label: new Date(w.week_start + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" }), submitted: w.submitted, decided: w.decided };
    }), [{ key: "submitted", label: "Received", color: P.blue }, { key: "decided", label: "Decided", color: P.green }], { title: "Applications received and decided per week", empty: "Nothing in the last 12 weeks." });
    Charts.donut($("c-kind"), d.by_kind.map(function (x, i) { return { label: x.label, count: x.count, color: [P.blue, P.yellow][i % 2] }; }), { title: "Who applies", centerLabel: "applications", empty: "No applications yet." });
  }

  // ------------------------------------------------------------------ the list of things people can apply for
  async function loadTypes() {
    const r = await VA.fetchJson(API + "/types/all");
    if (!r.ok) return;
    $("f-type").innerHTML = '<option value="">Any type</option>' + r.body.map(function (t) { return '<option value="' + t.id + '">' + esc(t.name) + "</option>"; }).join("");
    const box = $("t-list");
    box.innerHTML = "";
    r.body.forEach(function (t) {
      const row = document.createElement("div");
      row.className = "auth-row" + (t.is_active ? "" : " off");
      const who = [t.for_students ? "students" : "", t.for_employees ? "employees" : ""].filter(Boolean).join(" & ");
      const bits = [who, t.requires_dates ? "needs dates" : "", t.requires_attachment ? "needs a document" : "", t.excuses_attendance ? "excuses attendance" : "", t.yearly_limit_days ? t.yearly_limit_days + " days a year" : "", t.is_active ? "" : "switched off"].filter(Boolean).join(" \u00b7 ");
      row.innerHTML = '<span class="grow"><strong>' + esc(t.name) + "</strong><small>" + esc(bits) + "</small></span>";
      const edit = document.createElement("button"); edit.type = "button"; edit.className = "role-btn small-btn secondary-btn"; edit.textContent = "Edit";
      edit.addEventListener("click", function () { editType(t, row); });
      const tog = document.createElement("button"); tog.type = "button"; tog.className = "role-btn small-btn secondary-btn"; tog.textContent = t.is_active ? "Switch off" : "Switch on";
      tog.addEventListener("click", async function () {
        const r2 = await VA.fetchJson(API + "/types/" + t.id, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ is_active: !t.is_active }) });
        if (r2.ok) loadTypes(); else VA.toast(msg(r2.body, "Could not change it."), "error");
      });
      row.appendChild(edit); row.appendChild(tog); box.appendChild(row);
    });
  }

  function editType(t, row) {
    row.innerHTML = '<div class="type-edit"><input class="text-input" value="' + esc(t.name) + '" maxlength="120" aria-label="Name" /><input class="text-input" value="' + esc(t.description || "") + '" maxlength="2000" aria-label="Help text" />' +
      '<div class="type-flags"><label><input type="checkbox" data-k="for_students"' + (t.for_students ? " checked" : "") + " /> Students</label><label><input type=\"checkbox\" data-k=\"for_employees\"" + (t.for_employees ? " checked" : "") + " /> Employees</label>" +
      '<label><input type="checkbox" data-k="requires_dates"' + (t.requires_dates ? " checked" : "") + ' /> Needs dates</label><label><input type="checkbox" data-k="requires_attachment"' + (t.requires_attachment ? " checked" : "") + " /> Needs a document</label>" +
      '<label><input type="checkbox" data-k="excuses_attendance"' + (t.excuses_attendance ? " checked" : "") + ' /> Approval excuses attendance</label>' +
      '<label>Yearly limit <input class="text-input tiny" type="number" min="1" max="366" value="' + (t.yearly_limit_days || "") + '" placeholder="days" /></label></div></div>';
    const save = document.createElement("button"); save.type = "button"; save.className = "role-btn small-btn"; save.textContent = "Save";
    const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "role-btn small-btn secondary-btn"; cancel.textContent = "Cancel"; cancel.addEventListener("click", loadTypes);
    save.addEventListener("click", async function () {
      const ins = row.querySelectorAll("input.text-input");
      const body = { name: ins[0].value, description: ins[1].value || null };
      row.querySelectorAll("input[data-k]").forEach(function (c) { body[c.getAttribute("data-k")] = c.checked; });
      const limit = row.querySelector("input[type=number]").value;
      if (limit) body.yearly_limit_days = parseInt(limit, 10); else body.clear_limit = true;
      const r2 = await VA.fetchJson(API + "/types/" + t.id, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (r2.ok) { VA.toast("Saved."); loadTypes(); } else VA.toast(msg(r2.body, "Could not save."), "error");
    });
    row.appendChild(save); row.appendChild(cancel);
  }

  $("t-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const err = $("t-error"); err.hidden = true;
    const limit = $("t-limit").value;
    const r = await VA.fetchJson(API + "/types", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      name: $("t-name").value, description: $("t-desc").value || null, for_students: $("t-students").checked, for_employees: $("t-employees").checked,
      requires_dates: $("t-dates").checked, requires_attachment: $("t-attach").checked, excuses_attendance: $("t-excuse").checked, yearly_limit_days: limit ? parseInt(limit, 10) : null }) });
    if (!r.ok) { err.textContent = msg(r.body, "Could not add it."); err.hidden = false; return; }
    ["t-name", "t-desc", "t-limit"].forEach(function (id) { $(id).value = ""; });
    VA.toast("Added."); loadTypes();
  });

  ["f-status", "f-type", "f-kind"].forEach(function (id) { $(id).addEventListener("change", function () { loadAll(false); }); });
  $("f-q").addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(function () { loadAll(false); }, 300); });
  $("a-more").addEventListener("click", function () { loadAll(true); });

  document.addEventListener("vistadmin:ready", async function () {
    await loadTypes();
    loadQueue(); loadAll(false); loadOverview();
    const wanted = new URLSearchParams(window.location.search).get("open");
    if (wanted) openModal(wanted);
  });
})();