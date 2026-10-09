(function () {
  "use strict";

  const VA = window.VistAdmin;
  const SU = window.StudentUI;
  const esc = VA.escapeHtml;
  const API = "/api/v1/tasks";
  const $ = function (id) { return document.getElementById(id); };
  const msg = function (body, fallback) { return body && body.error && body.error.message ? body.error.message : fallback; };
  const json = function (method, data) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }; };
  const IS_ADMIN = !!$("tk-staff-body");
  const MIN_HOURS = 3;

  const STATUS = { PENDING: ["Waiting for an answer", "pending"], ACCEPTED: ["In progress", "enrolled"], REJECTED: ["Rejected", "dropped"], COMPLETED: ["Completed", "active"], CANCELLED: ["Cancelled", ""] };
  const PRIORITY = { LOW: "Low", NORMAL: "Normal", HIGH: "High", URGENT: "Urgent" };
  const REASONS = [["NO_CAPACITY", "I do not have the time or capacity"], ["NOT_MY_RESPONSIBILITY", "This is not my responsibility"], ["DEADLINE_TOO_SHORT", "The deadline is too short"],
                   ["MISSING_INFORMATION", "I am missing information or resources"], ["CONFLICTS_WITH_OTHER_WORK", "It conflicts with other work"], ["OTHER", "Another reason"]];

  let inbox = [], sent = [], staff = [], current = null, editing = null, followUp = null;
  let inboxFilter = "ALL", sentFilter = "ALL", allOffset = 0;

  // ------------------------------------------------------------------ small helpers
  const pill = function (s) { const x = STATUS[s] || [s, ""]; return '<span class="status-pill ' + x[1] + '">' + esc(x[0]) + "</span>"; };
  const when = function (iso) { const d = new Date(iso); return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) + ", " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }); };
  const toLocalInput = function (d) { const p = function (n) { return String(n).padStart(2, "0"); }; return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" + p(d.getHours()) + ":" + p(d.getMinutes()); };
  const span = function (ms) {
    const m = Math.floor(Math.abs(ms) / 60000), h = Math.floor(m / 60), d = Math.floor(h / 24);
    return d >= 2 ? d + " days" : h >= 1 ? h + " h" + (h < 10 ? " " + (m % 60) + " min" : "") : Math.max(m, 1) + " min";
  };
  const hoursText = function (h) { return h === null || h === undefined ? "\u2014" : h < 48 ? (Math.round(h * 10) / 10) + " h" : (Math.round(h / 24 * 10) / 10) + " days"; };
  const pct = function (v) { return v === null || v === undefined ? "\u2014" : v + "%"; };
  function dueLine(t) {
    if (t.status === "COMPLETED") return t.timeliness === "LATE" ? '<span class="late">Completed ' + span(t.late_by_hours * 3600000) + " after the deadline</span>" : "Completed on time";
    if (t.status === "REJECTED" || t.status === "CANCELLED") return "Was due " + esc(when(t.deadline));
    const ms = new Date(t.deadline).getTime() - Date.now();
    return "Due " + esc(when(t.deadline)) + (ms > 0 ? " \u00b7 in " + span(ms) : ' \u00b7 <span class="late">overdue by ' + span(ms) + "</span>");
  }
  const prioClass = function (t) { return t.priority === "URGENT" ? "p-urgent" : t.priority === "HIGH" ? "p-high" : t.priority === "LOW" ? "p-low" : ""; };
  const updateBadge = function () { VA.fetchJson(API + "/summary", { quiet: true }).then(function (r) { if (r.ok) window.dispatchEvent(new CustomEvent("tasks:badge", { detail: r.body.badge })); }); };
  const person = function (p) { return '<span class="tk-person">' + SU.avatar(p.name) + "<span><strong>" + esc(p.name) + "</strong><small>" + esc(p.detail || "") + "</small></span></span>"; };

  // ------------------------------------------------------------------ cards and lists
  function card(t, side) {
    const el = document.createElement("article");
    el.className = "asg-card tk " + prioClass(t) + (t.is_overdue ? " urgent" : t.status === "COMPLETED" ? " ok" : t.status === "REJECTED" || t.status === "CANCELLED" ? " missed" : "");
    el.tabIndex = 0; el.setAttribute("role", "button");
    const who = side === "inbox" ? "From " + esc(t.assigner.name) : side === "sent" ? "To " + esc(t.assignee.name) : esc(t.assigner.name) + " \u2192 " + esc(t.assignee.name);
    el.innerHTML =
      '<div class="asg-top"><span class="pill">' + esc(t.reference) + "</span>" + pill(t.status) + (t.is_overdue ? '<span class="status-pill dropped">Overdue</span>' : "") +
      (t.priority !== "NORMAL" ? '<span class="pill pr-' + t.priority.toLowerCase() + '">' + esc(PRIORITY[t.priority]) + "</span>" : "") + (t.is_flagged ? '<span class="pill flag">\uD83D\uDEA9 Flagged</span>' : "") + "</div>" +
      "<h3>" + esc(t.title) + "</h3>" +
      '<p class="tk-who">' + who + "</p>" +
      '<p class="asg-due">' + dueLine(t) + "</p>" +
      (t.status === "REJECTED" ? '<div class="cc-note"><strong>' + esc(t.reject_reason_label || "Rejected") + "</strong><span>" + esc((t.reject_explanation || "").slice(0, 140)) + ((t.reject_explanation || "").length > 140 ? "\u2026" : "") + "</span></div>" : "") +
      '<div class="asg-foot"><span class="cc-meta">' + esc(VA.formatDateTime(t.created_at)) + (t.comment_count ? " \u00b7 \uD83D\uDCAC " + t.comment_count : "") + '</span><span class="tk-quick"></span></div>';
    const quick = el.querySelector(".tk-quick");
    const add = function (label, cls, fn) { const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn " + cls; b.textContent = label; b.addEventListener("click", function (e) { e.stopPropagation(); fn(); }); quick.appendChild(b); };
    if (t.can.accept) { add("Accept", "", function () { respond(t, true); }); add("Reject", "danger-btn", function () { reject(t); }); }
    else add("Open", "secondary-btn", function () { openTask(t.id); });
    el.addEventListener("click", function () { openTask(t.id); });
    el.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openTask(t.id); } });
    return el;
  }

  const FILTERS = {
    inbox: [["ALL", "Everything"], ["PENDING", "Needs my answer"], ["ACCEPTED", "In progress"], ["OVERDUE", "Overdue"], ["COMPLETED", "Done"], ["CLOSED", "Rejected / cancelled"]],
    sent: [["ALL", "Everything"], ["PENDING", "Waiting for an answer"], ["ACCEPTED", "In progress"], ["OVERDUE", "Overdue"], ["REJECTED", "Rejected"], ["COMPLETED", "Done"], ["FLAGGED", "Flagged"]],
  };
  const keep = function (t, f) {
    return f === "ALL" || (f === "OVERDUE" ? t.is_overdue : f === "CLOSED" ? t.status === "REJECTED" || t.status === "CANCELLED" : f === "FLAGGED" ? t.is_flagged : t.status === f);
  };
  function drawList(kind) {
    const rows = kind === "inbox" ? inbox : sent, f = kind === "inbox" ? inboxFilter : sentFilter, box = $("tk-" + kind), chips = $("tk-" + kind + "-chips");
    chips.innerHTML = FILTERS[kind].map(function (c) {
      const n = rows.filter(function (t) { return keep(t, c[0]); }).length;
      return '<button type="button" class="chip' + (f === c[0] ? " active" : "") + '" data-f="' + c[0] + '">' + c[1] + " \u00b7 " + n + "</button>";
    }).join("");
    chips.querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { if (kind === "inbox") inboxFilter = b.getAttribute("data-f"); else sentFilter = b.getAttribute("data-f"); drawList(kind); }); });
    const shown = rows.filter(function (t) { return keep(t, f); });
    box.innerHTML = "";
    $("tk-" + kind + "-empty").hidden = shown.length > 0;
    $("tk-" + kind + "-empty").textContent = rows.length ? "Nothing in this group." : kind === "inbox" ? "Nobody has assigned you anything yet." : "You have not assigned any tasks yet.";
    shown.forEach(function (t) { box.appendChild(card(t, kind)); });
    if (kind === "inbox") {
      const n = rows.filter(function (t) { return t.status === "PENDING"; }).length;
      $("tk-tab-inbox").textContent = "Assigned to me" + (n ? " \u00b7 " + n + " to answer" : "");
    }
  }
  async function loadInbox() { const r = await VA.fetchJson(API + "/inbox"); if (r.ok) { inbox = r.body; drawList("inbox"); } else $("tk-inbox").innerHTML = '<p class="home-empty">' + esc(msg(r.body, "Could not load your tasks.")) + "</p>"; updateBadge(); }
  async function loadSent() { const r = await VA.fetchJson(API + "/sent"); if (r.ok) { sent = r.body; drawList("sent"); } }
  function refresh() { loadInbox(); loadSent(); if (IS_ADMIN) { loadFlags(); loadAll(false); } }

  // ------------------------------------------------------------------ asking for a short answer (reject, flag, cancel...)
  const act = { resolve: null };
  function ask(cfg) {
    return new Promise(function (resolve) {
      $("tk-act-title").textContent = cfg.title;
      $("tk-act-intro").textContent = cfg.intro || "";
      $("tk-act-select-wrap").hidden = !cfg.options;
      if (cfg.options) { $("tk-act-select-label").textContent = cfg.selectLabel; $("tk-act-select").innerHTML = cfg.options.map(function (o) { return '<option value="' + o[0] + '">' + esc(o[1]) + "</option>"; }).join(""); }
      $("tk-act-text-wrap").hidden = cfg.noText === true;
      $("tk-act-text-label").textContent = cfg.textLabel || "";
      $("tk-act-text").value = ""; $("tk-act-text").placeholder = cfg.placeholder || "";
      const dateWrap = $("tk-act-date-wrap");
      const syncDate = function () { dateWrap.hidden = !(cfg.dateWhen && $("tk-act-select").value === cfg.dateWhen); };
      dateWrap.hidden = true; $("tk-act-date-label").textContent = cfg.dateLabel || "";
      const min = new Date(Date.now() + MIN_HOURS * 3600000); const dflt = new Date(Date.now() + 48 * 3600000);
      $("tk-act-date").value = toLocalInput(dflt); $("tk-act-date").min = toLocalInput(min);
      $("tk-act-select").onchange = syncDate; syncDate();
      $("tk-act-error").hidden = true;
      $("tk-act-ok").textContent = cfg.ok || "OK"; $("tk-act-ok").className = cfg.danger ? "danger-btn" : "role-btn";
      act.resolve = resolve; act.cfg = cfg;
      $("tk-act").showModal();
    });
  }
  $("tk-act-ok").addEventListener("click", function () {
    const cfg = act.cfg, text = $("tk-act-text").value.trim(), error = function (t) { $("tk-act-error").textContent = t; $("tk-act-error").hidden = false; };
    if (!cfg.noText && cfg.minText && text.length < cfg.minText) return error(cfg.minMessage);
    const out = { text: text, choice: cfg.options ? $("tk-act-select").value : null, date: null };
    if (cfg.dateWhen && out.choice === cfg.dateWhen) {
      if (!$("tk-act-date").value) return error("Choose the new deadline.");
      const d = new Date($("tk-act-date").value);
      if (d.getTime() < Date.now() + MIN_HOURS * 3600000) return error("The deadline must be at least " + MIN_HOURS + " hours from now.");
      out.date = d.toISOString();
    }
    const done = act.resolve; act.resolve = null; $("tk-act").close(); done(out);
  });
  $("tk-act-cancel").addEventListener("click", function () { $("tk-act").close(); });
  $("tk-act").addEventListener("close", function () { if (act.resolve) { const r = act.resolve; act.resolve = null; r(null); } });

  // ------------------------------------------------------------------ one task
  const modal = $("tk-modal");
  const showErr = function (t) { $("tk-modal-error").textContent = t || ""; $("tk-modal-error").hidden = !t; };

  function fill(t) {
    current = t;
    $("tk-badges").innerHTML = '<span class="pill">' + esc(t.reference) + "</span>" + pill(t.status) + (t.is_overdue ? '<span class="status-pill dropped">Overdue</span>' : "") +
      '<span class="pill pr-' + t.priority.toLowerCase() + '">' + esc(PRIORITY[t.priority]) + " priority</span>" + (t.is_flagged ? '<span class="pill flag">\uD83D\uDEA9 Flagged for review</span>' : "");
    $("tk-title-view").textContent = t.title;
    $("tk-people").innerHTML = '<div><small>Assigned by</small>' + person(t.assigner) + '</div><span class="arrow">\u2192</span><div><small>Assigned to</small>' + person(t.assignee) + "</div>";
    $("tk-deadline-box").className = "asg-deadline" + (t.is_overdue ? " urgent" : t.status === "COMPLETED" || t.status === "REJECTED" || t.status === "CANCELLED" ? " closed" : "");
    $("tk-deadline-box").innerHTML = "<strong>" + dueLine(t) + "</strong>" + (t.extension_count ? "<span>Originally " + esc(when(t.original_deadline)) + " \u00b7 extended " + t.extension_count + "\u00d7</span>" : "");
    $("tk-desc-view").textContent = t.description;
    let out = "";
    if (t.status === "REJECTED") out += '<div class="cc-note rejected"><strong>Rejected: ' + esc(t.reject_reason_label) + "</strong><span>" + esc(t.reject_explanation) + "</span></div>";
    if (t.completion_note) out += '<div class="cc-note good"><strong>Completion note</strong><span>' + esc(t.completion_note) + "</span></div>";
    if (t.status === "CANCELLED") out += '<div class="cc-note"><strong>Cancelled</strong><span>' + esc(t.cancel_reason || "") + "</span></div>";
    if (t.follow_up_of) out += '<p class="cd-privacy">This is a follow-up to ' + esc(t.follow_up_of.reference) + ", which was rejected.</p>";
    $("tk-outcome").innerHTML = out;
    const answered = t.responded_at, finished = t.status === "COMPLETED" || t.status === "CANCELLED";
    $("tk-timeline").innerHTML =
      '<li class="done"><strong>Assigned</strong><span>' + esc(VA.formatDateTime(t.created_at)) + " by " + esc(t.assigner.name) + "</span></li>" +
      '<li class="' + (answered ? "done " + (t.status === "REJECTED" ? "late" : "") : "") + (t.status === "PENDING" ? " current" : "") + '"><strong>' + (answered ? (t.status === "REJECTED" ? "Rejected" : "Accepted") : "Waiting for an answer") +
        "</strong><span>" + esc(answered ? VA.formatDateTime(t.responded_at) : "Not answered yet") + "</span></li>" +
      '<li class="' + (t.status === "COMPLETED" ? "done " + (t.timeliness === "LATE" ? "late" : "resolved") : t.status === "CANCELLED" ? "done withdrawn" : "") + '"><strong>' +
        (t.status === "COMPLETED" ? (t.timeliness === "LATE" ? "Completed late" : "Completed on time") : t.status === "CANCELLED" ? "Cancelled" : t.status === "REJECTED" ? "Not done" : t.is_overdue ? "Overdue" : "Not done yet") + "</strong><span>" +
        esc(t.completed_at ? VA.formatDateTime(t.completed_at) : t.cancelled_at ? VA.formatDateTime(t.cancelled_at) : "") + "</span></li>";
    $("tk-flags-view").innerHTML = t.flags.length ? '<h3 class="modal-subhead">Flagged for review</h3>' + t.flags.map(function (f) {
      return '<div class="cc-note ' + (f.status === "OPEN" ? "rejected" : "good") + '"><strong>' + (f.status === "OPEN" ? "\uD83D\uDEA9 Waiting for the administration" : "Reviewed: " + ({ NO_ACTION: "no change", TASK_CANCELLED: "task cancelled", DEADLINE_EXTENDED: "deadline changed" }[f.outcome] || "")) +
        "</strong><span>" + esc(f.flagged_by_name) + " \u2014 " + esc(f.reason) + "</span>" + (f.resolution_note ? "<span><b>Decision" + (f.resolved_by_name ? " by " + esc(f.resolved_by_name) : "") + ":</b> " + esc(f.resolution_note) + "</span>" : "") + "</div>";
    }).join("") : "";
    const mine = t.my_role === "ASSIGNER" ? t.assigner.user_id : t.my_role === "ASSIGNEE" ? t.assignee.user_id : null;
    $("tk-thread").innerHTML = t.comments.length ? t.comments.map(function (c) {
      return '<div class="msg ' + (mine && c.author_user_id === mine ? "applicant" : "admin") + '"><strong>' + esc(c.author_name) + "</strong><span>" + esc(c.body) + "</span><small>" + esc(VA.formatDateTime(c.created_at)) + "</small></div>";
    }).join("") : '<p class="home-empty">No messages yet.</p>';
    $("tk-comment-wrap").hidden = !t.can.comment; $("tk-comment").value = "";
    const bar = $("tk-actions");
    bar.innerHTML = "";
    const add = function (label, cls, fn) { const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn " + cls; b.textContent = label; b.addEventListener("click", fn); bar.appendChild(b); };
    if (t.can.accept) add("Accept", "", function () { respond(t, true); });
    if (t.can.reject) add("Reject\u2026", "danger-btn", function () { reject(t); });
    if (t.can.complete) add("Mark as completed", "", function () { complete(t); });
    if (t.can.confirm) add("Confirm it is done", "", function () { send(t.id + "/confirm", {}, "Confirmed as done."); });
    if (t.can.send_back) add("Send back\u2026", "secondary-btn", function () { sendBack(t); });
    if (t.can.edit) add(t.can.edit === "ALL" ? "Edit" : "Change deadline", "secondary-btn", function () { startEdit(t); });
    if (t.can.assign_again) add("Give it to someone else", "", function () { startFollowUp(t); });
    if (t.can.flag) add("\uD83D\uDEA9 Flag for review", "secondary-btn", function () { flag(t); });
    if (t.can.resolve_flag) add("Decide on the flag\u2026", "", function () { resolveFlag(t); });
    if (t.can.cancel) add("Cancel task", "danger-btn", function () { cancel(t); });
    showErr("");
  }

  async function openTask(id) {
    const r = await VA.fetchJson(API + "/" + id);
    if (!r.ok) { VA.toast(msg(r.body, "Could not open it."), "error"); return; }
    fill(r.body);
    if (!modal.open) modal.showModal();
    const h = $("tk-title-view"); h.tabIndex = -1; h.focus({ preventScroll: true });
  }
  async function send(path, data, ok) {
    showErr("");
    const r = await VA.fetchJson(API + "/" + path, json("POST", data));
    if (!r.ok) { const m = msg(r.body, "That did not work."); if (modal.open) showErr(m); else VA.toast(m, "error"); return null; }
    if (ok) VA.toast(ok);
    if (modal.open) fill(r.body);
    refresh();
    return r.body;
  }
  const respond = function (t, accept) { return send(t.id + "/respond", { action: "ACCEPT" }, "Accepted. " + t.assigner.name + " has been told."); };
  async function reject(t) {
    const a = await ask({ title: "Reject this task", intro: "Say why. " + t.assigner.name + " will see your reason and explanation, and it is kept on record.", options: REASONS, selectLabel: "Main reason",
      textLabel: "Explain, in a sentence or two", placeholder: "e.g. Five hours is not enough to arrange sixty desks, and I have invigilation duty until 4 pm.", minText: 10, minMessage: "Explain why (at least a sentence), so the person understands.", ok: "Reject task", danger: true });
    if (a) send(t.id + "/respond", { action: "REJECT", reason: a.choice, explanation: a.text }, "Rejected. " + t.assigner.name + " has been told why.");
  }
  async function complete(t) {
    const a = await ask({ title: "Mark as completed", intro: "Anything " + t.assigner.name + " should know? (optional)", textLabel: "Note", placeholder: "e.g. Done. Keys are with the front desk.", ok: "Mark completed" });
    if (a) send(t.id + "/complete", { note: a.text || null }, "Marked as completed.");
  }
  async function sendBack(t) {
    const a = await ask({ title: "Send back", intro: (t.assignee ? t.assignee.name : "The person") + " will see your reason and the task opens again.", textLabel: "What still needs doing?",
      placeholder: "e.g. Only 40 desks are in place; 20 more are needed.", minText: 5, minMessage: "Say what still needs doing.", ok: "Send back" });
    if (a) send(t.id + "/send-back", { note: a.text }, "Sent back.");
  }
  async function cancel(t) {
    const a = await ask({ title: "Cancel this task", intro: "The person it was assigned to will be told.", textLabel: "Why is it being cancelled?", minText: 3, minMessage: "Give a short reason.", ok: "Cancel task", danger: true });
    if (a) send(t.id + "/cancel", { reason: a.text }, "Cancelled.");
  }
  async function flag(t) {
    const a = await ask({ title: "Flag for the administration", intro: "The administration will review this task and decide. The other person is told you flagged it.", textLabel: "What should they look at?",
      placeholder: "Explain the problem clearly: what was agreed, what changed, why you are asking for a review.", minText: 10, minMessage: "Please explain in at least a sentence.", ok: "Flag for review" });
    if (a) send(t.id + "/flag", { reason: a.text }, "Flagged. The administration will review it.");
  }
  async function resolveFlag(t) {
    const a = await ask({ title: "Decide on the flag", intro: "Read both sides, then choose what happens. Both people are told.", options: [["NO_ACTION", "No change"], ["DEADLINE_EXTENDED", "Change the deadline"], ["TASK_CANCELLED", "Cancel the task"]],
      selectLabel: "Decision", dateWhen: "DEADLINE_EXTENDED", dateLabel: "New deadline (at least 3 hours from now)", textLabel: "Your decision note (both people see it)", minText: 3, minMessage: "Write a short note explaining the decision.", ok: "Save decision" });
    if (a) send(t.id + "/resolve", { outcome: a.choice, note: a.text, new_deadline: a.date }, "Decision saved. Both people have been told.");
  }
  $("tk-comment-send").addEventListener("click", async function () {
    const body = $("tk-comment").value.trim();
    if (!body) return showErr("Write a message first.");
    const r = await send(current.id + "/comments", { body: body }, "Message sent.");
    if (r) $("tk-comment").value = "";
  });
  $("tk-close").addEventListener("click", function () { modal.close(); });

  // ------------------------------------------------------------------ assigning (and editing)
  function showPicked() {
    const sel = $("tk-assignee"), out = $("tk-picked");
    if (!out) return;
    const o = sel.options[sel.selectedIndex];
    out.textContent = sel.value && o ? "Selected: " + o.textContent : "Nobody selected yet \u2014 click a name in the list.";
    out.className = "field-hint" + (sel.value ? " ok" : "");
  }
  function drawStaff() {
    const q = $("tk-person-search").value.trim().toLowerCase(), keepId = $("tk-assignee").value;
    const rows = staff.filter(function (p) { return !q || p.name.toLowerCase().indexOf(q) !== -1 || (p.detail || "").toLowerCase().indexOf(q) !== -1; });
    $("tk-assignee").innerHTML = rows.map(function (p) { return '<option value="' + p.user_id + '">' + esc(p.name) + (p.detail ? " \u2014 " + esc(p.detail) : "") + "</option>"; }).join("") || '<option value="" disabled>Nobody matches</option>';
    if (keepId && rows.some(function (p) { return p.user_id === keepId; })) $("tk-assignee").value = keepId;
    else if (rows.length === 1) $("tk-assignee").value = rows[0].user_id;      // one match: pick it for them
    else $("tk-assignee").selectedIndex = -1;
    showPicked();
  }
  $("tk-assignee").addEventListener("change", showPicked);
  $("tk-assignee").addEventListener("click", showPicked);
  $("tk-assignee").addEventListener("keyup", showPicked);
  function resetForm() {
    editing = followUp = null;
    $("tk-form-title").textContent = "Assign a task"; $("tk-create").textContent = "Assign task";
    ["tk-title", "tk-desc"].forEach(function (id) { $(id).value = ""; $(id).disabled = false; });
    $("tk-priority").value = "NORMAL"; $("tk-priority").disabled = false; $("tk-person-search").disabled = false; $("tk-assignee").disabled = false; $("tk-person-search").value = ""; drawStaff();
    $("tk-followup").hidden = true; $("tk-error").hidden = true;
    const d = new Date(Date.now() + 24 * 3600000); d.setMinutes(0, 0, 0); $("tk-deadline").value = toLocalInput(d);
    $("tk-deadline").min = toLocalInput(new Date(Date.now() + MIN_HOURS * 3600000));
  }
  function startEdit(t) {
    resetForm(); editing = t;
    $("tk-form-title").textContent = t.can.edit === "ALL" ? "Edit " + t.reference : "Change the deadline of " + t.reference; $("tk-create").textContent = "Save changes";
    $("tk-title").value = t.title; $("tk-desc").value = t.description; $("tk-priority").value = t.priority; $("tk-deadline").value = toLocalInput(new Date(t.deadline));
    $("tk-assignee").innerHTML = '<option value="' + t.assignee.user_id + '">' + esc(t.assignee.name) + "</option>"; $("tk-assignee").selectedIndex = 0; showPicked();
    ["tk-person-search", "tk-assignee"].forEach(function (id) { $(id).disabled = true; });
    if (t.can.edit === "DEADLINE") ["tk-title", "tk-desc", "tk-priority"].forEach(function (id) { $(id).disabled = true; });
    modal.close(); VA.showTab("new"); window.scrollTo({ top: 0, behavior: "smooth" });
  }
  function startFollowUp(t) {
    resetForm(); followUp = t;
    $("tk-title").value = t.title; $("tk-desc").value = t.description; $("tk-priority").value = t.priority;
    $("tk-followup").hidden = false;
    $("tk-followup").textContent = t.reference + " was rejected by " + t.assignee.name + " (" + (t.reject_reason_label || "") + "). Choose who should do it instead and set a new deadline.";
    modal.close(); VA.showTab("new"); window.scrollTo({ top: 0, behavior: "smooth" });
  }
  $("tk-create").addEventListener("click", async function () {
    const err = $("tk-error"); err.hidden = true;
    const fail = function (t) { err.textContent = t; err.hidden = false; err.scrollIntoView({ block: "center" }); };
    const dl = $("tk-deadline").value;
    if (!dl) return fail("Choose a deadline.");
    const date = new Date(dl);
    if (date.getTime() < Date.now() + MIN_HOURS * 3600000) return fail("The deadline must be at least " + MIN_HOURS + " hours from now.");
    $("tk-create").disabled = true;
    let r;
    if (editing) {
      const body = { deadline: date.toISOString() };
      if (editing.can.edit === "ALL") Object.assign(body, { title: $("tk-title").value, description: $("tk-desc").value, priority: $("tk-priority").value });
      r = await VA.fetchJson(API + "/" + editing.id, json("PATCH", body));
    } else {
      if (!$("tk-assignee").value) { $("tk-create").disabled = false; return fail("Choose who should do it."); }
      if ($("tk-title").value.trim().length < 3) { $("tk-create").disabled = false; return fail("Give the task a title."); }
      if ($("tk-desc").value.trim().length < 3) { $("tk-create").disabled = false; return fail("Describe what needs to be done."); }
      r = await VA.fetchJson(API, json("POST", { assignee_user_id: $("tk-assignee").value, title: $("tk-title").value, description: $("tk-desc").value, priority: $("tk-priority").value, deadline: date.toISOString(), follow_up_of_id: followUp ? followUp.id : null }));
    }
    $("tk-create").disabled = false;
    if (!r.ok) return fail(msg(r.body, "Could not save the task."));
    VA.toast(editing ? "Saved. " + editing.assignee.name + " has been told." : "Assigned to " + r.body.assignee.name + ". They have been notified.");
    resetForm(); await loadSent(); loadInbox(); if (IS_ADMIN) loadAll(false);
    VA.showTab("sent");
  });
  $("tk-person-search").addEventListener("input", drawStaff);

  // ------------------------------------------------------------------ progress (mine, and one person for the administration)
  const PERIODS = [[30, "Last 30 days"], [60, "Last 60 days"], [90, "Last 90 days"], [0, "All time"]];
  function chips(el, selected, onPick) {
    el.innerHTML = PERIODS.map(function (p) { return '<button type="button" class="chip' + (p[0] === selected ? " active" : "") + '" data-d="' + p[0] + '">' + p[1] + "</button>"; }).join("");
    el.querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { onPick(parseInt(b.getAttribute("data-d"), 10)); }); });
  }
  const tile = function (label, value, sub, tone) { return '<div class="kpi ' + (tone || "") + '"><span class="kpi-label">' + label + '</span><strong class="kpi-value">' + value + '</strong><span class="kpi-sub">' + (sub || "") + "</span></div>"; };
  function receiverTiles(s) {
    return '<div class="kpi-grid">' + tile("Received", s.received, "tasks given to them") + tile("Accepted", s.accepted, pct(s.acceptance_rate) + " of those answered") + tile("Rejected", s.rejected, "turned down", s.rejected ? "warn" : "") +
      tile("Done on time", s.completed_on_time, pct(s.on_time_rate) + " on-time rate", "good") + tile("Done late", s.completed_late, "after the deadline", s.completed_late ? "warn" : "") + tile("Overdue", s.overdue, "not done, past the deadline", s.overdue ? "bad" : "") + "</div>" +
      '<p class="tk-sub">Still open: <b>' + s.still_open + "</b> \u00b7 waiting for an answer: <b>" + s.pending_response + "</b> \u00b7 cancelled: <b>" + s.cancelled + "</b> \u00b7 average time to answer: <b>" + hoursText(s.avg_response_hours) + "</b> \u00b7 average time to finish: <b>" + hoursText(s.avg_completion_hours) + "</b></p>";
  }
  function progress(root, r, who) {
    const a = r.assigning, id = who + "-";
    root.innerHTML =
      '<h3 class="modal-subhead">' + (who === "mine" ? "Tasks given to you" : "Tasks they were given") + "</h3>" + receiverTiles(r.receiving) +
      '<h3 class="modal-subhead">' + (who === "mine" ? "Tasks you gave to others" : "Tasks they gave to others") + '</h3><div class="kpi-grid kpi-grid-5">' +
      tile("Assigned", a.assigned, "") + tile("Rejected by receivers", a.rejected, "", a.rejected ? "warn" : "") + tile("Completed", a.completed, "") + tile("Overdue", a.overdue, "", a.overdue ? "bad" : "") + tile("Flagged", a.flagged, "sent for review", a.flagged ? "warn" : "") + "</div>" +
      '<div class="chart-grid"><section class="sh-card flat"><div class="sh-card-head"><h2>Why tasks were rejected</h2></div><div id="' + id + 'reasons"></div></section>' +
      '<section class="sh-card flat"><div class="sh-card-head"><h2>Week by week</h2></div><div id="' + id + 'weeks"></div></section></div>' +
      '<h3 class="modal-subhead">Rejected tasks, with the reasons given</h3><div id="' + id + 'rejected" class="tk-mini"></div>' +
      '<h3 class="modal-subhead">Late or overdue</h3><div id="' + id + 'late" class="tk-mini"></div>' +
      (r.flagged.length ? '<h3 class="modal-subhead">Flagged for review</h3><div id="' + id + 'flagged" class="tk-mini"></div>' : "");
    const P = Charts.palette();
    Charts.hbars($(id + "reasons"), r.reasons.map(function (x) { return { label: x.label, count: x.count }; }), { empty: "No rejected tasks in this period." });
    Charts.lineChart($(id + "weeks"), r.weeks.map(function (w) { return { label: new Date(w.week_start + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" }), assigned: w.assigned, completed: w.completed, rejected: w.rejected }; }),
      [{ key: "assigned", label: "Given", color: P.blue }, { key: "completed", label: "Completed", color: P.green }, { key: "rejected", label: "Rejected", color: P.red }], { title: "Tasks per week", empty: "Nothing in the last 12 weeks." });
    const mini = function (el, rows, empty, kind) {
      el.innerHTML = rows.length ? rows.map(function (t) {
        return '<article class="tk-row" data-id="' + t.id + '"><div><strong>' + esc(t.title) + '</strong><small>' + esc(t.reference) + " \u00b7 " + esc(t.assigner.name) + " \u2192 " + esc(t.assignee.name) + " \u00b7 " + esc(VA.formatDateTime(t.created_at)) + "</small>" +
          (kind === "rejected" ? '<span class="why"><b>' + esc(t.reject_reason_label || "") + ":</b> " + esc(t.reject_explanation || "") + "</span>" :
            '<span class="why">' + (t.status === "COMPLETED" ? "Completed " + span(t.late_by_hours * 3600000) + " late" : t.status === "PENDING" ? "Never answered \u2014 due " + esc(when(t.deadline)) : "Not finished \u2014 due " + esc(when(t.deadline))) + "</span>") + "</div>" + pill(t.status) + "</article>";
      }).join("") : '<p class="home-empty">' + empty + "</p>";
      el.querySelectorAll(".tk-row").forEach(function (row) { row.addEventListener("click", function () { openTask(row.getAttribute("data-id")); }); });
    };
    mini($(id + "rejected"), r.rejected, "None in this period.", "rejected");
    mini($(id + "late"), r.late, "Nothing late or overdue in this period.", "late");
    if (r.flagged.length) mini($(id + "flagged"), r.flagged, "", "late");
  }
  async function loadMine(days) {
    const box = $("tk-mine");
    if (!box.firstChild) box.innerHTML = '<div class="sh-card-head"><h2>My progress</h2></div><div class="rv-chips" id="tk-mine-chips"></div><div id="tk-mine-body"></div>';
    chips($("tk-mine-chips"), days, loadMine);
    const r = await VA.fetchJson(API + "/reports/me?days=" + days);
    if (!r.ok) { $("tk-mine-body").innerHTML = '<p class="home-empty">' + esc(msg(r.body, "Could not load your progress.")) + "</p>"; return; }
    progress($("tk-mine-body"), r.body, "mine");
  }

  // ------------------------------------------------------------------ the administration
  async function loadFlags() {
    const r = await VA.fetchJson(API + "?flagged=true&days=0&limit=100");
    if (!r.ok) return;
    $("tk-flags").innerHTML = "";
    $("tk-flags-empty").hidden = r.body.length > 0;
    r.body.forEach(function (t) { $("tk-flags").appendChild(card(t, "all")); });
    $("tk-tab-flags").textContent = "Flagged for review" + (r.body.length ? " \u00b7 " + r.body.length : "");
  }
  async function loadAll(more) {
    if (!more) allOffset = 0;
    const p = new URLSearchParams({ limit: "30", offset: String(allOffset), days: $("al-days").value });
    if ($("al-q").value.trim()) p.set("q", $("al-q").value.trim());
    if ($("al-status").value) p.set("status", $("al-status").value);
    if ($("al-overdue").checked) p.set("overdue", "true");
    const r = await VA.fetchJson(API + "?" + p.toString());
    if (!r.ok) return;
    if (!more) $("al-list").innerHTML = "";
    r.body.forEach(function (t) { $("al-list").appendChild(card(t, "all")); });
    allOffset += r.body.length;
    $("al-more").hidden = r.body.length < 30;
    $("al-empty").hidden = $("al-list").children.length > 0;
  }
  let staffDays = 30;
  async function loadStaff(days) {
    staffDays = days;
    chips($("tk-staff-chips"), days, loadStaff);
    const r = await VA.fetchJson(API + "/reports/staff?days=" + days);
    if (!r.ok) return;
    const d = r.body;
    $("tk-staff-totals").innerHTML = receiverTiles(d.totals) + '<p class="tk-sub">Flags: <b>' + d.flags_open + "</b> waiting for a decision \u00b7 <b>" + d.flags_resolved + "</b> decided</p>";
    const body = $("tk-staff-body");
    body.innerHTML = "";
    $("tk-staff-empty").hidden = d.rows.length > 0;
    d.rows.forEach(function (row) {
      const s = row.receiving, a = row.assigning, tr = document.createElement("tr");
      tr.tabIndex = 0;
      tr.innerHTML = '<td><span class="tk-person">' + SU.avatar(row.name) + "<span><strong>" + esc(row.name) + "</strong><small>" + esc(row.detail || "") + "</small></span></span></td>" +
        "<td>" + s.received + "</td><td>" + s.accepted + "</td><td" + (s.rejected ? ' class="warn"' : "") + ">" + s.rejected + "</td><td>" + s.completed_on_time + "</td><td" + (s.completed_late ? ' class="warn"' : "") + ">" + s.completed_late + "</td>" +
        "<td" + (s.overdue ? ' class="bad"' : "") + ">" + s.overdue + "</td><td>" + pct(s.on_time_rate) + "</td><td>" + pct(s.acceptance_rate) + "</td><td>" + a.assigned + "</td>";
      tr.addEventListener("click", function () { openPerson(row.user_id, row.name, staffDays); });
      tr.addEventListener("keydown", function (e) { if (e.key === "Enter") openPerson(row.user_id, row.name, staffDays); });
      body.appendChild(tr);
    });
  }
  async function openPerson(userId, name, days) {
    $("tk-person-title").textContent = name;
    chips($("tk-person-chips"), days, function (d) { openPerson(userId, name, d); });
    $("tk-person-body").innerHTML = '<div class="skeleton-stack"><div class="skeleton-line"></div><div class="skeleton-line short"></div></div>';
    if (!$("tk-person").open) $("tk-person").showModal();
    const r = await VA.fetchJson(API + "/reports/person/" + userId + "?days=" + days);
    if (!r.ok) { $("tk-person-body").innerHTML = '<p class="home-empty">' + esc(msg(r.body, "Could not load.")) + "</p>"; return; }
    progress($("tk-person-body"), r.body, "p");
  }
  $("tk-person-close").addEventListener("click", function () { $("tk-person").close(); });
  if (IS_ADMIN) {
    let timer = null;
    ["al-status", "al-days", "al-overdue"].forEach(function (id) { $(id).addEventListener("change", function () { loadAll(false); }); });
    $("al-q").addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(function () { loadAll(false); }, 300); });
    $("al-more").addEventListener("click", function () { loadAll(true); });
  }

  document.addEventListener("vistadmin:ready", async function () {
    const r = await VA.fetchJson(API + "/staff");
    staff = r.ok ? r.body : [];
    resetForm();
    await Promise.all([loadInbox(), loadSent(), loadMine(30)]);
    if (IS_ADMIN) { loadFlags(); loadAll(false); loadStaff(30); }
    const wanted = new URLSearchParams(window.location.search).get("open");
    if (wanted) openTask(wanted);
  });
})();