(function () {
  "use strict";

  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/applications";
  const $ = function (id) { return document.getElementById(id); };
  const msg = function (body, fallback) { return body && body.error && body.error.message ? body.error.message : fallback; };

  const STATUS = {
    SUBMITTED: ["Waiting for the administration", "pending"], UNDER_REVIEW: ["Under review", "enrolled"], MORE_INFO_NEEDED: ["Needs your reply", "pending"],
    APPROVED: ["Approved", "active"], REJECTED: ["Rejected", "dropped"], WITHDRAWN: ["Withdrawn", ""],
  };
  const pill = function (s) { const x = STATUS[s] || [s, ""]; return '<span class="status-pill ' + x[1] + '">' + esc(x[0]) + "</span>"; };
  const OPEN = ["SUBMITTED", "UNDER_REVIEW", "MORE_INFO_NEEDED"];

  let apps = [];
  let types = [];
  let current = null;
  let picked = [];
  let replyFiles = [];

  const fmtDate = function (iso) { return iso ? new Date(iso + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" }) : ""; };
  const range = function (a) { return a.from_date ? fmtDate(a.from_date) + (a.to_date && a.to_date !== a.from_date ? " \u2192 " + fmtDate(a.to_date) : "") + " \u00b7 " + a.days + " day" + (a.days > 1 ? "s" : "") : ""; };
  const size = function (b) { return b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB"; };
  const ext = function (n) { const m = /\.([a-z0-9]+)$/i.exec(n || ""); return m ? m[1].toUpperCase() : "FILE"; };
  const updateBadge = function () { VA.fetchJson(API + "/badge", { quiet: true }).then(function (r) { if (r.ok) window.dispatchEvent(new CustomEvent("applications:badge", { detail: r.body.count })); }); };

  // ------------------------------------------------------------------ the list
  function card(a) {
    const mineNow = a.awaiting === "APPLICANT";
    const el = document.createElement("article");
    el.className = "asg-card " + (mineNow ? "urgent" : a.status === "APPROVED" ? "ok" : a.status === "REJECTED" || a.status === "WITHDRAWN" ? "missed" : "");
    el.tabIndex = 0; el.setAttribute("role", "button");
    el.innerHTML =
      '<div class="asg-top"><span class="pill">' + esc(a.reference) + "</span>" + pill(a.status) + "</div>" +
      "<h3>" + esc(a.subject) + "</h3>" +
      '<p class="asg-snippet">' + esc(a.type_name) + (a.from_date ? " \u00b7 " + esc(range(a)) : "") + "</p>" +
      (mineNow ? '<p class="asg-due urgent"><strong>The administration asked you a question. Open it to reply.</strong></p>'
        : a.decision_note && a.status !== "WITHDRAWN" ? '<div class="cc-note"><strong>' + (a.status === "APPROVED" ? "Note" : "Reason") + "</strong><span>" + esc(a.decision_note) + "</span></div>" : "") +
      '<div class="asg-foot"><span class="cc-meta">Applied ' + esc(VA.formatDateTime(a.created_at)) + (a.attachments.length ? " \u00b7 \uD83D\uDCCE " + a.attachments.length : "") +
      (a.comment_count ? " \u00b7 \uD83D\uDCAC " + a.comment_count : "") + '</span><span class="role-btn small-btn secondary-btn">Open</span></div>';
    const open = function () { openModal(a.id); };
    el.addEventListener("click", open);
    el.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    return el;
  }

  function render() {
    const f = $("ap-filter").value;
    const shown = apps.filter(function (a) { return !f || (f === "OPEN" ? OPEN.indexOf(a.status) !== -1 : a.status === f); });
    $("ap-list").innerHTML = "";
    $("ap-empty").hidden = shown.length > 0;
    $("ap-empty").textContent = apps.length ? "Nothing matches." : "You have not applied for anything yet.";
    shown.forEach(function (a) { $("ap-list").appendChild(card(a)); });
    const reply = apps.filter(function (a) { return a.awaiting === "APPLICANT"; }).length;
    $("ap-tab-mine").textContent = "My applications" + (reply ? " \u00b7 " + reply + " to answer" : "");
  }

  async function load() {
    const r = await VA.fetchJson(API + "/mine");
    if (!r.ok) { $("ap-list").innerHTML = '<p class="home-empty">' + esc(msg(r.body, "Could not load your applications.")) + "</p>"; return; }
    apps = r.body;
    render();
    updateBadge();
  }

  // ------------------------------------------------------------------ one application
  const modal = $("ap-modal");
  function showModalError(t) { $("ap-modal-error").textContent = t || ""; $("ap-modal-error").hidden = !t; }

  function filesHtml(a) {
    if (!a.attachments.length) return "";
    const url = function (f) { return API + "/attachments/" + f.id + "/file"; };
    return '<ul class="ann-files">' + a.attachments.map(function (f) {
      return '<li><span class="ann-ext">' + esc(ext(f.original_name)) + '</span><span class="ann-fname"><strong>' + esc(f.original_name) + "</strong><small>" + size(f.size_bytes) + "</small></span>" +
        (f.inline ? '<a class="role-btn small-btn" href="' + url(f) + '" target="_blank" rel="noopener">Open</a>' : "") +
        '<a class="role-btn small-btn secondary-btn" href="' + url(f) + '" data-download="' + esc(f.original_name) + '" data-label="file">Download</a></li>';
    }).join("") + "</ul>";
  }

  function fill(a) {
    current = a; replyFiles = [];
    $("ap-badges").innerHTML = '<span class="pill">' + esc(a.reference) + "</span>" + pill(a.status);
    $("ap-title").textContent = a.subject;
    $("ap-meta").textContent = a.type_name + (a.from_date ? " \u00b7 " + range(a) : "");
    const reviewed = ["UNDER_REVIEW", "MORE_INFO_NEEDED", "APPROVED", "REJECTED"].indexOf(a.status) !== -1;
    const final = a.status === "APPROVED" || a.status === "REJECTED";
    $("ap-timeline").innerHTML =
      '<li class="done"><strong>Submitted</strong><span>' + esc(VA.formatDateTime(a.created_at)) + "</span></li>" +
      '<li class="' + (reviewed ? "done" : "") + (a.status === "UNDER_REVIEW" || a.status === "MORE_INFO_NEEDED" ? " current" : "") + '"><strong>Reviewed</strong><span>' +
        esc(a.reviewed_at ? VA.formatDateTime(a.reviewed_at) : (a.status === "WITHDRAWN" ? "\u2014" : "Waiting for the administration")) + "</span></li>" +
      '<li class="' + (final ? "done " + a.status.toLowerCase() : a.status === "WITHDRAWN" ? "done withdrawn" : "") + '"><strong>' + (a.status === "WITHDRAWN" ? "Withdrawn" : final ? STATUS[a.status][0] : "Decision") +
        "</strong><span>" + esc(a.decided_at ? VA.formatDateTime(a.decided_at) : "Not decided yet") + "</span></li>";
    $("ap-decision").innerHTML = a.decision_note && a.status !== "WITHDRAWN"
      ? '<div class="cc-note ' + (a.status === "APPROVED" ? "good" : "") + '"><strong>' + (a.status === "APPROVED" ? "Note from the administration" : "Why it was not approved") + "</strong><span>" + esc(a.decision_note) + "</span></div>" : "";
    $("ap-details-view").textContent = a.details;
    $("ap-files-view").innerHTML = filesHtml(a);
    $("ap-thread").innerHTML = a.comments.length ? a.comments.map(function (c) {
      return '<div class="msg ' + c.side.toLowerCase() + '"><strong>' + esc(c.author_name) + "</strong><span>" + esc(c.body) + "</span><small>" + esc(VA.formatDateTime(c.created_at)) + "</small></div>";
    }).join("") : '<p class="home-empty">No messages yet.</p>';
    const open = OPEN.indexOf(a.status) !== -1;
    $("ap-reply-wrap").hidden = !open;
    $("ap-reply").value = "";
    $("ap-reply").placeholder = a.awaiting === "APPLICANT" ? "Answer the administration's question\u2026" : "Add a message, or attach more documents\u2026";
    showModalError("");
  }

  async function openModal(id) {
    const r = await VA.fetchJson(API + "/" + id);
    if (!r.ok) { VA.toast(msg(r.body, "Could not open it."), "error"); return; }
    fill(r.body);
    if (!modal.open) modal.showModal();
    const t = $("ap-title"); t.tabIndex = -1; t.focus({ preventScroll: true });
  }

  $("ap-send").addEventListener("click", async function () {
    showModalError("");
    const body = $("ap-reply").value.trim();
    if (!body && !replyFiles.length) return showModalError("Write a message or add a document first.");
    $("ap-send").disabled = true;
    let r;
    if (replyFiles.length) {
      const fd = new FormData();
      replyFiles.forEach(function (f) { fd.append("files", f); });
      r = await VA.fetchJson(API + "/" + current.id + "/attachments", { method: "POST", body: fd });
      if (r.ok && body) r = await VA.fetchJson(API + "/" + current.id + "/comments", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body: body }) });
    } else {
      r = await VA.fetchJson(API + "/" + current.id + "/comments", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body: body }) });
    }
    $("ap-send").disabled = false;
    if (!r.ok) return showModalError(msg(r.body, "Could not send it."));
    VA.toast("Sent to the administration.");
    fill(r.body);
    load();
  });
  $("ap-reply-file").addEventListener("change", function (e) {
    replyFiles = replyFiles.concat(Array.prototype.slice.call(e.target.files)); e.target.value = "";
    $("ap-reply").placeholder = replyFiles.length + " document" + (replyFiles.length > 1 ? "s" : "") + " ready to send: " + replyFiles.map(function (f) { return f.name; }).join(", ");
  });
  $("ap-withdraw").addEventListener("click", async function () {
    if (!window.confirm("Withdraw this application? The administration will no longer review it.")) return;
    const r = await VA.fetchJson(API + "/" + current.id + "/withdraw", { method: "POST" });
    if (!r.ok) return showModalError(msg(r.body, "Could not withdraw it."));
    VA.toast("Withdrawn."); fill(r.body); load();
  });
  $("ap-close").addEventListener("click", function () { modal.close(); });

  // ------------------------------------------------------------------ the form
  function selectedType() { return types.find(function (t) { return t.id === $("ap-type").value; }); }

  function updateForm() {
    const t = selectedType();
    if (!t) return;
    const hint = $("ap-type-hint");
    const u = t.usage;
    hint.hidden = !(t.description || u);
    hint.innerHTML = '<div class="rules-note">' + (t.description ? "<p>" + esc(t.description) + "</p>" : "") +
      (t.requires_attachment ? "<p><strong>A document is required</strong> for this kind of application.</p>" : "") +
      (u ? "<p><strong>Your yearly allowance:</strong> " + u.approved_days + " of " + u.limit_days + " days approved in " + u.year + (u.pending_days ? " (+" + u.pending_days + " waiting)" : "") + ".</p>" : "") + "</div>";
    $("ap-dates").hidden = !t.requires_dates;
    $("ap-files-label").textContent = t.requires_attachment ? "Documents (required)" : "Documents (optional)";
    updateDays();
  }
  function updateDays() {
    const t = selectedType(), out = $("ap-days");
    out.textContent = ""; out.className = "field-hint";
    const f = $("ap-from").value, to = $("ap-to").value;
    if (!t || !t.requires_dates || !f || !to) return;
    const days = Math.round((new Date(to) - new Date(f)) / 86400000) + 1;
    if (days < 1) { out.textContent = "The last day cannot be before the first day."; out.className = "field-hint warn"; return; }
    out.textContent = days + " day" + (days > 1 ? "s" : "") + " in total.";
    const u = t.usage;
    if (u && u.approved_days + u.pending_days + days > u.limit_days) {
      out.textContent += " This would take you over your yearly allowance of " + u.limit_days + " days. You can still apply: the administration decides.";
      out.className = "field-hint warn";
    }
  }
  function renderPicked() {
    $("ap-picked").innerHTML = "";
    picked.forEach(function (file, i) {
      const li = document.createElement("li");
      li.innerHTML = '<span class="ann-ext">' + esc(ext(file.name)) + '</span><span class="ann-fname"><strong>' + esc(file.name) + "</strong><small>" + size(file.size) + (file.size > 15 * 1048576 ? " \u2014 too large (15 MB max)" : "") + "</small></span>";
      const rm = document.createElement("button"); rm.type = "button"; rm.className = "linkish"; rm.textContent = "Remove";
      rm.addEventListener("click", function () { picked.splice(i, 1); renderPicked(); });
      li.appendChild(rm); $("ap-picked").appendChild(li);
    });
  }
  function addPicked(list) { picked = picked.concat(Array.prototype.slice.call(list)); renderPicked(); }

  $("ap-submit").addEventListener("click", async function () {
    const err = $("ap-error"); err.hidden = true;
    const fail = function (t) { err.textContent = t; err.hidden = false; err.scrollIntoView({ block: "center" }); };
    const t = selectedType();
    if (!t) return fail("Choose what you are applying for.");
    if (t.requires_dates && (!$("ap-from").value || !$("ap-to").value)) return fail("Choose the first and last date.");
    if ($("ap-subject").value.trim().length < 3) return fail("Write a short subject.");
    if ($("ap-details").value.trim().length < 3) return fail("Explain what you are asking for.");
    if (t.requires_attachment && !picked.length) return fail("This kind of application needs a document. Choose a file below.");
    if (picked.length > 5) return fail("You can attach at most 5 files.");
    if (picked.some(function (f) { return f.size > 15 * 1048576; })) return fail("A file is over 15 MB. Remove it or use a smaller one.");
    const fd = new FormData();
    fd.append("type_id", t.id); fd.append("subject", $("ap-subject").value.trim()); fd.append("details", $("ap-details").value.trim());
    if (t.requires_dates) { fd.append("from_date", $("ap-from").value); fd.append("to_date", $("ap-to").value); }
    picked.forEach(function (f) { fd.append("files", f); });
    $("ap-submit").disabled = true;
    const r = await VA.fetchJson(API, { method: "POST", body: fd });
    $("ap-submit").disabled = false;
    if (!r.ok) return fail(msg(r.body, "Could not submit your application."));
    VA.toast("Submitted as " + r.body.reference + ". You will be notified when it is decided.");
    $("ap-subject").value = ""; $("ap-details").value = ""; $("ap-from").value = ""; $("ap-to").value = ""; picked = []; renderPicked();
    await load();
    VA.showTab("mine");
  });

  $("ap-type").addEventListener("change", updateForm);
  $("ap-from").addEventListener("change", function () { if ($("ap-from").value && (!$("ap-to").value || $("ap-to").value < $("ap-from").value)) $("ap-to").value = $("ap-from").value; updateDays(); });
  $("ap-to").addEventListener("change", updateDays);
  $("ap-files").addEventListener("change", function (e) { addPicked(e.target.files); e.target.value = ""; });
  const zone = document.querySelector("#ap-form .drop-zone");
  ["dragenter", "dragover"].forEach(function (ev) { zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.add("over"); }); });
  ["dragleave", "drop"].forEach(function (ev) { zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.remove("over"); }); });
  zone.addEventListener("drop", function (e) { addPicked(e.dataTransfer.files); });
  $("ap-filter").addEventListener("change", render);
  $("ap-new").addEventListener("click", function () { VA.showTab("new"); });
  $("ap-form").addEventListener("submit", function (e) { e.preventDefault(); });

  document.addEventListener("vistadmin:ready", async function () {
    const r = await VA.fetchJson(API + "/types");
    types = r.ok ? r.body : [];
    $("ap-type").innerHTML = types.map(function (t) { return '<option value="' + t.id + '">' + esc(t.name) + "</option>"; }).join("") || '<option value="">Nothing can be applied for right now</option>';
    updateForm();
    await load();
    const wanted = new URLSearchParams(window.location.search).get("open");
    if (wanted) openModal(wanted);
  });
})();