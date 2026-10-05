(function () {
  "use strict";

  const VA = window.VistAdmin;
  const SU = window.StudentUI;
  const esc = VA.escapeHtml;
  const API = "/api/v1/assignments";
  const $ = function (id) { return document.getElementById(id); };

  let items = [];
  let current = null;

  const STATE = {
    NOT_SUBMITTED: { label: "Not handed in", cls: "pending" }, SUBMITTED: { label: "Waiting for your teacher", cls: "enrolled" },
    ACCEPTED: { label: "Accepted", cls: "active" }, REJECTED: { label: "Sent back", cls: "dropped" }, MISSED: { label: "Missed", cls: "dropped" },
  };
  const pill = function (state) { const s = STATE[state] || { label: state, cls: "" }; return '<span class="status-pill ' + s.cls + '">' + esc(s.label) + "</span>"; };
  const msg = function (body, fallback) { return body && body.error && body.error.message ? body.error.message : fallback; };

  function dueText(a) {
    const d = new Date(a.deadline);
    return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) + ", " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }
  // "in 2 days", "in 5 h 10 min", "closed 3 h ago"
  function left(a) {
    const ms = new Date(a.deadline).getTime() - Date.now();
    const abs = Math.abs(ms), mins = Math.floor(abs / 60000), hrs = Math.floor(mins / 60), days = Math.floor(hrs / 24);
    const span = days >= 2 ? days + " days" : hrs >= 1 ? hrs + " h" + (hrs < 10 ? " " + (mins % 60) + " min" : "") : Math.max(mins, 1) + " min";
    return ms > 0 ? { text: "closes in " + span, urgent: ms < 24 * 3600000, soon: ms < 3 * 86400000 } : { text: "closed " + span + " ago", urgent: false, soon: false };
  }

  function bucket(a) {
    if (a.state === "SUBMITTED" || a.state === "ACCEPTED") return "done";
    if (a.is_open) return "todo";                                  // not handed in, or sent back and still time to fix it
    return "closed";
  }

  function card(a) {
    const l = left(a), todo = bucket(a) === "todo";
    const el = document.createElement("article");
    el.className = "asg-card " + (a.state === "ACCEPTED" ? "ok" : todo && l.urgent ? "urgent" : todo && l.soon ? "soon" : a.state === "MISSED" || a.state === "REJECTED" && !a.is_open ? "missed" : "");
    el.tabIndex = 0;
    el.setAttribute("role", "button");
    const s = a.submission;
    el.innerHTML =
      '<div class="asg-top"><span class="pill">' + esc(a.course_code) + "</span>" + pill(a.state) + "</div>" +
      "<h3>" + esc(a.title) + "</h3>" +
      '<p class="asg-snippet">' + esc(a.instructions.replace(/\s+/g, " ").slice(0, 160)) + (a.instructions.length > 160 ? "\u2026" : "") + "</p>" +
      '<p class="asg-due' + (todo && l.urgent ? " urgent" : "") + '" data-deadline="' + esc(a.deadline) + '"><strong>Due ' + esc(dueText(a)) + '</strong> \u00b7 <span class="asg-left">' + esc(l.text) + "</span></p>" +
      (a.state === "REJECTED" && s && s.feedback ? '<div class="cc-note"><strong>Your teacher says</strong><span>' + esc(s.feedback) + "</span></div>" : "") +
      '<div class="asg-foot"><span class="asg-teacher">' + (a.teacher_name ? SU.teacherLine(a.teacher_name) : "") + "</span>" +
      '<span class="role-btn small-btn' + (todo ? "" : " secondary-btn") + '">' +
      (a.can_submit ? (s ? (a.state === "REJECTED" ? "Fix and resubmit" : "Replace link") : "Hand in") : "View") + "</span></div>";
    const open = function () { openModal(a); };
    el.addEventListener("click", open);
    el.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    return el;
  }

  function render() {
    const groups = { todo: [], done: [], closed: [] };
    items.forEach(function (a) { groups[bucket(a)].push(a); });
    ["todo", "done", "closed"].forEach(function (k) {
      const box = $("asg-" + k);
      box.innerHTML = "";
      groups[k].forEach(function (a) { box.appendChild(card(a)); });
      $("asg-" + k + "-empty").hidden = groups[k].length > 0;
    });
    $("tab-todo").textContent = "To do" + (groups.todo.length ? " \u00b7 " + groups.todo.length : "");
    $("tab-done").textContent = "Handed in" + (groups.done.length ? " \u00b7 " + groups.done.length : "");
    $("tab-closed").textContent = "Closed" + (groups.closed.length ? " \u00b7 " + groups.closed.length : "");
  }

  async function load() {
    const r = await VA.fetchJson(API + "/mine");
    if (!r.ok) {
      $("asg-todo").innerHTML = '<p class="home-empty">' + esc(msg(r.body, "Could not load your assignments.")) + "</p>";
      return;
    }
    items = r.body;
    render();
  }

  // ------------------------------------------------------------------ one assignment
  const modal = $("asg-modal");
  function showError(text) { $("asg-error").textContent = text; $("asg-error").hidden = !text; }

  function openModal(a) {
    current = a;
    const s = a.submission, l = left(a);
    $("asg-badges").innerHTML = pill(a.state);
    $("asg-title").textContent = a.title;
    $("asg-meta").textContent = a.course_code + " \u2014 " + a.course_title + (a.teacher_name ? " \u00b7 " + a.teacher_name : "");
    $("asg-deadline").className = "asg-deadline" + (a.is_open && l.urgent ? " urgent" : "") + (a.is_open ? "" : " closed");
    $("asg-deadline").innerHTML = "<strong>" + (a.is_open ? "Due " : "Was due ") + esc(dueText(a)) + "</strong><span>" + esc(l.text) + "</span>";
    $("asg-instructions").innerHTML = String(a.instructions).split(/\n{2,}/).map(function (p) { return "<p>" + esc(p).replace(/\n/g, "<br>") + "</p>"; }).join("");
    $("asg-feedback").innerHTML = s && s.feedback
      ? '<div class="cc-note ' + (a.state === "ACCEPTED" ? "good" : "") + '"><strong>' + (a.state === "ACCEPTED" ? "Comment from your teacher" : "Your teacher sent this back") + "</strong><span>" + esc(s.feedback) + "</span></div>" : "";
    $("asg-private").hidden = true; showError("");

    const form = $("asg-form-wrap"), ro = $("asg-readonly");
    ro.hidden = true; ro.innerHTML = "";
    if (a.can_submit) {
      form.hidden = false;
      $("asg-form-title").textContent = s ? (a.state === "REJECTED" ? "Fix it and hand it in again" : "Replace your link") : "Hand in your work";
      $("asg-url").value = s ? s.drive_url : "";
      $("asg-note").value = s && s.note ? s.note : "";
      $("asg-submit").textContent = s ? "Hand in again" : "Submit";
    } else {
      form.hidden = true;
      ro.hidden = false;
      ro.innerHTML = s
        ? '<h3 class="modal-subhead">Your submission</h3><p><a class="role-btn small-btn secondary-btn" href="' + esc(s.drive_url) + '" target="_blank" rel="noopener noreferrer">Open your link \u2197</a></p>' +
          '<p class="cc-meta">Handed in ' + esc(VA.formatDateTime(s.resubmitted_at || s.submitted_at)) + (s.resubmission_count ? " \u00b7 replaced " + s.resubmission_count + " time" + (s.resubmission_count > 1 ? "s" : "") : "") + "</p>" +
          (s.note ? '<p class="cd-text">' + esc(s.note) + "</p>" : "") +
          '<p class="cd-privacy">' + (a.state === "ACCEPTED" ? "Your teacher accepted this, so it can no longer be changed." : "The deadline has passed, so it can no longer be changed.") + "</p>"
        : '<p class="asg-missed">The deadline has passed and nothing was handed in. Submissions are closed. Speak to your teacher if there is a problem.</p>';
    }
    modal.showModal();
    const title = $("asg-title"); title.tabIndex = -1; title.focus({ preventScroll: true });
  }

  async function send(acknowledge) {
    showError("");
    const url = $("asg-url").value.trim();
    if (!url) return showError("Paste your Google Drive link first.");
    const btn = $("asg-submit"); btn.disabled = true;
    const r = await VA.fetchJson(API + "/" + current.id + "/submit", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ drive_url: url, note: $("asg-note").value.trim() || null, acknowledge_private: !!acknowledge }),
    });
    btn.disabled = false;
    if (!r.ok) {
      if (r.body && r.body.error && r.body.error.code === "DRIVE_LINK_PRIVATE") {
        $("asg-private-text").textContent = r.body.error.message;
        $("asg-private").hidden = false;
        $("asg-private").scrollIntoView({ block: "center" });
        return;
      }
      return showError(msg(r.body, "Could not hand it in."));
    }
    modal.close();
    VA.toast(current.submission ? "Handed in again. Your teacher will see the new link." : "Handed in. Your teacher will review it.");
    load();
  }

  $("asg-submit").addEventListener("click", function () { send(false); });
  $("asg-anyway").addEventListener("click", function () { send(true); });
  $("asg-close").addEventListener("click", function () { modal.close(); });
  setInterval(function () {                                         // keep the countdowns honest while the page stays open
    document.querySelectorAll(".asg-due").forEach(function (p) {
      const l = left({ deadline: p.getAttribute("data-deadline") });
      p.querySelector(".asg-left").textContent = l.text;
    });
  }, 30000);

  document.addEventListener("vistadmin:ready", async function () {
    await load();
    const wanted = new URLSearchParams(window.location.search).get("open");
    const target = wanted && items.find(function (a) { return a.id === wanted; });
    if (target) { VA.showTab(bucket(target)); openModal(target); }
  });
})();