(function () {
  "use strict";

  const VA = window.VistAdmin;
  const SU = window.StudentUI;
  const esc = VA.escapeHtml;
  const API = "/api/v1/assignments";
  const $ = function (id) { return document.getElementById(id); };
  const msg = function (body, fallback) { return body && body.error && body.error.message ? body.error.message : fallback; };

  let assignments = [];
  let reviewing = null;      // the assignment being reviewed
  let roster = [];
  let filter = "ALL";
  let firstLoad = true;
  let rejectTarget = null;
  let editing = null;

  const when = function (iso) {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) + ", " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  };
  // value for <input type="datetime-local"> in the browser's own time
  const toLocalInput = function (iso) {
    const d = new Date(iso), p = function (n) { return String(n).padStart(2, "0"); };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" + p(d.getHours()) + ":" + p(d.getMinutes());
  };
  const fromLocalInput = function (v) { return new Date(v).toISOString(); };

  // ------------------------------------------------------------------ my assignments
  function card(a) {
    const total = Math.max(a.enrolled, 1);
    const seg = function (n, cls) { return n ? '<span class="seg ' + cls + '" style="width:' + (n / total * 100) + '%" title="' + n + '"></span>' : ""; };
    const el = document.createElement("article");
    el.className = "asg-card " + (a.is_open ? "" : "missed");
    el.innerHTML =
      '<div class="asg-top"><span class="pill">' + esc(a.course_code) + '</span><span class="pill">Section ' + esc(a.section_label) + "</span>" +
      '<span class="status-pill ' + (a.is_open ? "active" : "dropped") + '">' + (a.is_open ? "Open" : "Closed") + "</span></div>" +
      "<h3>" + esc(a.title) + "</h3>" +
      '<p class="asg-due"><strong>' + (a.is_open ? "Due " : "Closed ") + esc(when(a.deadline)) + "</strong></p>" +
      '<div class="asg-bar" role="img" aria-label="' + a.accepted + " accepted, " + a.awaiting_review + " waiting, " + a.rejected + " sent back, " + a.not_submitted + ' not handed in">' +
      seg(a.accepted, "ok") + seg(a.awaiting_review, "wait") + seg(a.rejected, "bad") + seg(a.not_submitted, "none") + "</div>" +
      '<ul class="asg-legend"><li><i class="ok"></i>' + a.accepted + " accepted</li><li><i class=\"wait\"></i><strong>" + a.awaiting_review + "</strong> to review</li><li><i class=\"bad\"></i>" + a.rejected +
      " sent back</li><li><i class=\"none\"></i>" + a.not_submitted + " not handed in</li></ul>" +
      '<div class="asg-actions"></div>';
    const actions = el.querySelector(".asg-actions");
    const add = function (label, cls, fn) { const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn " + cls; b.textContent = label; b.addEventListener("click", fn); actions.appendChild(b); };
    add("Review" + (a.awaiting_review ? " (" + a.awaiting_review + ")" : ""), a.awaiting_review ? "" : "secondary-btn", function () { openReview(a); });
    add("Edit", "secondary-btn", function () { openEdit(a); });
    if (a.is_open) add("Close now", "secondary-btn", function () { closeNow(a); });
    if (a.submitted === 0) add("Delete", "danger-btn", function () { remove(a); });
    return el;
  }

  async function loadList() {
    const course = $("tl-course").value;
    const r = await VA.fetchJson(API + "/teaching" + (course ? "?offering_id=" + course : ""));
    const box = $("tl-list");
    box.innerHTML = "";
    if (!r.ok) { box.innerHTML = '<p class="home-empty">' + esc(msg(r.body, "Could not load assignments.")) + "</p>"; return; }
    assignments = r.body;
    $("tl-empty").hidden = assignments.length > 0;
    assignments.forEach(function (a) { box.appendChild(card(a)); });
    const waiting = assignments.reduce(function (n, a) { return n + a.awaiting_review; }, 0);
    $("tab-review").textContent = "Review submissions" + (waiting ? " \u00b7 " + waiting : "");
  }

  async function closeNow(a) {
    if (!window.confirm("Close \u201c" + a.title + "\u201d now? Nobody will be able to hand in or change their link.")) return;
    const r = await VA.fetchJson(API + "/" + a.id + "/close", { method: "POST" });
    if (r.ok) { VA.toast("Closed."); loadList(); } else VA.toast(msg(r.body, "Could not close it."), "error");
  }
  async function remove(a) {
    if (!window.confirm("Delete \u201c" + a.title + "\u201d? Nobody has handed anything in yet.")) return;
    const r = await VA.fetchJson(API + "/" + a.id, { method: "DELETE" });
    if (r.ok) { VA.toast("Deleted."); loadList(); } else VA.toast(msg(r.body, "Could not delete it."), "error");
  }

  // ------------------------------------------------------------------ edit
  function openEdit(a) {
    editing = a;
    $("ed-title").value = a.title; $("ed-instructions").value = a.instructions; $("ed-deadline").value = toLocalInput(a.deadline);
    $("ed-error").hidden = true;
    $("ed-modal").showModal();
  }
  $("ed-save").addEventListener("click", async function () {
    const dl = $("ed-deadline").value;
    const r = await VA.fetchJson(API + "/" + editing.id, { method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: $("ed-title").value, instructions: $("ed-instructions").value, deadline: dl ? fromLocalInput(dl) : undefined }) });
    if (!r.ok) { $("ed-error").textContent = msg(r.body, "Could not save."); $("ed-error").hidden = false; return; }
    $("ed-modal").close(); VA.toast("Saved."); loadList();
  });
  $("ed-close").addEventListener("click", function () { $("ed-modal").close(); });

  // ------------------------------------------------------------------ review
  const CHIPS = [["ALL", "Everyone"], ["SUBMITTED", "To review"], ["NOT_SUBMITTED", "Not handed in"], ["REJECTED", "Sent back"], ["ACCEPTED", "Accepted"]];
  const STATE = { SUBMITTED: ["To review", "enrolled"], ACCEPTED: ["Accepted", "active"], REJECTED: ["Sent back", "dropped"], NOT_SUBMITTED: ["Not handed in", "pending"] };

  async function openReview(a) {
    reviewing = a; filter = "ALL"; firstLoad = true;
    $("rv-empty-pick").hidden = true; $("rv-body").hidden = false;
    $("rv-title").textContent = a.title;
    $("rv-meta").textContent = a.course_code + " \u00b7 Section " + a.section_label + " \u00b7 " + (a.is_open ? "due " : "closed ") + when(a.deadline);
    VA.showTab("review");
    await loadRoster();
  }

  async function loadRoster() {
    $("rv-rows").innerHTML = '<div class="skeleton-stack"><div class="skeleton-line"></div><div class="skeleton-line short"></div></div>';
    const r = await VA.fetchJson(API + "/" + reviewing.id + "/submissions");
    if (!r.ok) { $("rv-rows").innerHTML = '<p class="home-empty">' + esc(msg(r.body, "Could not load the class.")) + "</p>"; return; }
    roster = r.body;
    // Land on what needs doing: if anything is waiting for a decision, show just those first.
    if (firstLoad && roster.some(function (x) { return x.state === "SUBMITTED"; })) filter = "SUBMITTED";
    firstLoad = false;
    drawReview();
  }

  function drawReview() {
    const count = function (s) { return s === "ALL" ? roster.length : roster.filter(function (x) { return x.state === s; }).length; };
    $("rv-chips").innerHTML = CHIPS.map(function (c) {
      return '<button type="button" class="chip' + (filter === c[0] ? " active" : "") + '" data-f="' + c[0] + '">' + c[1] + " \u00b7 " + count(c[0]) + "</button>";
    }).join("");
    $("rv-chips").querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { filter = b.getAttribute("data-f"); drawReview(); }); });
    const rows = roster.filter(function (x) { return filter === "ALL" || x.state === filter; });
    const box = $("rv-rows");
    box.innerHTML = "";
    $("rv-empty").hidden = rows.length > 0;
    rows.forEach(function (x) {
      const st = STATE[x.state];
      const el = document.createElement("article");
      el.className = "rv-row " + x.state.toLowerCase();
      el.innerHTML =
        '<div class="rv-who">' + SU.avatar(x.student_name) + '<span><strong>' + esc(x.student_name) + "</strong><small>" + esc(x.roll_number || "") + "</small></span>" +
        '<span class="status-pill ' + st[1] + '">' + st[0] + "</span></div>" +
        (x.drive_url
          ? '<div class="rv-work"><a class="role-btn small-btn secondary-btn" href="' + esc(x.drive_url) + '" target="_blank" rel="noopener noreferrer">Open Drive link \u2197</a>' +
            '<small>' + esc(VA.formatDateTime(x.submitted_at)) + (x.resubmission_count ? " \u00b7 replaced " + x.resubmission_count + "\u00d7" : "") + "</small></div>"
          : '<div class="rv-work"><small>Nothing handed in' + (reviewing.is_open ? " yet" : "; the deadline has passed") + ".</small></div>") +
        (x.note ? '<p class="rv-note">\u201c' + esc(x.note) + "\u201d</p>" : "") +
        (x.feedback ? '<p class="rv-note feedback"><strong>Your feedback:</strong> ' + esc(x.feedback) + "</p>" : "") +
        '<div class="rv-actions"></div>';
      const actions = el.querySelector(".rv-actions");
      if (x.submission_id) {
        const accept = document.createElement("button");
        accept.type = "button"; accept.className = "role-btn small-btn"; accept.textContent = x.state === "ACCEPTED" ? "Accepted \u2713" : "Accept";
        accept.disabled = x.state === "ACCEPTED";
        accept.addEventListener("click", function () { decide(x, "ACCEPTED", null); });
        const reject = document.createElement("button");
        reject.type = "button"; reject.className = "danger-btn small-btn"; reject.textContent = x.state === "REJECTED" ? "Rejected" : "Reject";
        reject.disabled = x.state === "REJECTED";
        reject.addEventListener("click", function () { openReject(x); });
        actions.appendChild(accept); actions.appendChild(reject);
      }
      box.appendChild(el);
    });
  }

  async function decide(x, decision, feedback) {
    const r = await VA.fetchJson(API + "/submissions/" + x.submission_id + "/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision: decision, feedback: feedback }) });
    if (!r.ok) { VA.toast(msg(r.body, "Could not save your decision."), "error"); return false; }
    VA.toast(decision === "ACCEPTED" ? "Accepted. The student has been told." : "Sent back. The student has been told why.");
    await loadRoster(); loadList();
    return true;
  }

  function openReject(x) {
    rejectTarget = x;
    $("rj-who").textContent = x.student_name + (x.roll_number ? " \u00b7 " + x.roll_number : "");
    $("rj-reason").value = ""; $("rj-error").hidden = true;
    $("rj-warn").hidden = reviewing.is_open;
    $("rj-modal").showModal();
  }
  $("rj-send").addEventListener("click", async function () {
    const reason = $("rj-reason").value.trim();
    if (reason.length < 3) { $("rj-error").textContent = "Say why, so the student knows what to fix."; $("rj-error").hidden = false; return; }
    if (await decide(rejectTarget, "REJECTED", reason)) $("rj-modal").close();
  });
  $("rj-close").addEventListener("click", function () { $("rj-modal").close(); });
  $("rv-back").addEventListener("click", function (e) { e.preventDefault(); VA.showTab("list"); });

  // ------------------------------------------------------------------ new assignment
  async function loadCourses() {
    const r = await VA.fetchJson("/api/v1/academics/offerings/me/overview");
    const courses = r.ok ? r.body : [];
    const options = courses.map(function (c) { return '<option value="' + c.offering_id + '">' + esc(c.course_code + " \u2014 " + c.course_title + " (Section " + c.section_label + ", " + c.term_label + ")") + "</option>"; }).join("");
    $("as-course").innerHTML = options || '<option value="">You have no classes assigned this term</option>';
    $("tl-course").innerHTML = '<option value="">All my classes</option>' + options;
  }

  $("as-create").addEventListener("click", async function () {
    const err = $("as-error");
    err.hidden = true;
    const fail = function (t) { err.textContent = t; err.hidden = false; };
    if (!$("as-course").value) return fail("Choose the class this is for.");
    if ($("as-title").value.trim().length < 3) return fail("Give the assignment a title.");
    if ($("as-instructions").value.trim().length < 3) return fail("Write the instructions.");
    if (!$("as-deadline").value) return fail("Choose a deadline.");
    $("as-create").disabled = true;
    const r = await VA.fetchJson(API, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ course_offering_id: $("as-course").value, title: $("as-title").value, instructions: $("as-instructions").value, deadline: fromLocalInput($("as-deadline").value) }) });
    $("as-create").disabled = false;
    if (!r.ok) return fail(msg(r.body, "Could not set the assignment."));
    VA.toast("Assignment set. " + r.body.enrolled + " students have been notified.");
    $("as-title").value = ""; $("as-instructions").value = "";
    await loadList();
    VA.showTab("list");
  });

  $("tl-new").addEventListener("click", function () { VA.showTab("new"); });
  $("tl-course").addEventListener("change", loadList);

  document.addEventListener("vistadmin:ready", async function () {
    const tomorrow = new Date(Date.now() + 86400000); tomorrow.setHours(23, 59, 0, 0);
    $("as-deadline").value = toLocalInput(tomorrow.toISOString());
    await loadCourses();
    await loadList();
    const pick = new URLSearchParams(window.location.search).get("open");
    const target = pick && assignments.find(function (a) { return a.id === pick; });
    if (target) openReview(target);
  });
})();