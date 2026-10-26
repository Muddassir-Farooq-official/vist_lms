(function () {
  "use strict";

  // Profile changes (administration): /api/v1/profile-changes. Approve applies everything in the request; reject needs a reason.
  const VA = window.VistAdmin, esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/profile-changes";
  let status = "PENDING", rows = [], rejecting = null, timer = null;

  function msg(r, f) { return (r.body && r.body.error && r.body.error.message) || f; }
  function initials(n) { return String(n || "?").split(/\s+/).filter(Boolean).map(function (w) { return w[0]; }).slice(0, 2).join("").toUpperCase(); }

  function card(r) {
    const s = r.subject, pc = r.photo_check || {};
    const photo = r.has_photo ? '<div class="pcr-photos"><figure><span class="pcr-ph">' +
      (s.has_current_photo ? '<img src="' + API + "/" + r.id + '/current-photo" alt="Current photo" loading="lazy" />' : esc(initials(s.name))) +
      "</span><figcaption>Now</figcaption></figure><span class=\"pcr-arrow\">&rarr;</span><figure><span class=\"pcr-ph new\"><img src=\"" + API + "/" + r.id +
      '/photo" alt="New photo" loading="lazy" /></span><figcaption>New' + (pc.checked ? " · face check passed" : "") + "</figcaption></figure></div>" : "";
    const diff = r.changes.length ? '<table class="dash-table pcr-diff"><thead><tr><th>Detail</th><th>On record</th><th>Asked for</th></tr></thead><tbody>' +
      r.changes.map(function (c) { return "<tr><td>" + esc(c.label) + '</td><td class="old">' + esc(c.old || "—") + '</td><td class="new">' + esc(c.new || "(empty)") + "</td></tr>"; }).join("") +
      "</tbody></table>" : "";
    const actions = r.status === "PENDING" ? '<div class="pcr-actions"><button type="button" class="role-btn" data-approve="' + esc(r.id) + '">Approve</button>' +
      '<button type="button" class="danger-btn" data-reject="' + esc(r.id) + '">Reject</button></div>' :
      '<p class="pcr-decided">' + esc(r.status === "CANCELLED" ? "Withdrawn by the person" : (r.status === "APPROVED" ? "Approved" : "Rejected")) +
      (r.decided_at ? " · " + esc(VA.formatDateTime(r.decided_at)) : "") + (r.decision_reason ? " · " + esc(r.decision_reason) : "") + "</p>";
    return '<article class="pcr-card"><header><span class="pl-avatar">' + esc(initials(s.name)) + '</span><div><strong>' + esc(s.name) + "</strong><small>" +
      esc(s.kind === "STUDENT" ? "Student · Roll " : "Staff · ") + esc(s.code) + " · sent " + esc(VA.formatDateTime(r.created_at)) + "</small></div></header>" +
      (r.note ? '<p class="pcr-note">“' + esc(r.note) + "”</p>" : "") + photo + diff + actions + "</article>";
  }

  async function load() {
    const q = $("pcr-q").value.trim();
    const r = await VA.fetchJson(API + "?status=" + status + (q ? "&q=" + encodeURIComponent(q) : ""));
    if (!r.ok) return VA.toast(msg(r, "Could not load the requests."), "error");
    rows = r.body;
    $("pcr-empty").hidden = rows.length > 0;
    $("pcr-empty").textContent = status === "PENDING" ? "Nothing is waiting. All caught up." : "Nothing here.";
    $("pcr-list").innerHTML = rows.map(card).join("");
    if (status === "PENDING") $("pcr-count").textContent = rows.length ? "· " + rows.length : "";
  }

  $("pcr-tabs").addEventListener("click", function (e) {
    const b = e.target.closest("[data-status]"); if (!b) return;
    $("pcr-tabs").querySelectorAll(".portal-tab").forEach(function (t) { t.classList.toggle("active", t === b); t.setAttribute("aria-selected", t === b); });
    status = b.getAttribute("data-status"); load();
  });
  $("pcr-q").addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(load, 300); });
  $("pcr-list").addEventListener("click", async function (e) {
    const a = e.target.closest("[data-approve]"), j = e.target.closest("[data-reject]");
    if (a) {
      const row = rows.find(function (x) { return x.id === a.getAttribute("data-approve"); });
      if (!(await VA.confirm("Approve these changes for " + row.subject.name + "? They go live at once.", "Approve"))) return;
      a.disabled = true;
      const r = await VA.fetchJson(API + "/" + row.id + "/approve", { method: "POST" });
      if (!r.ok) { a.disabled = false; return VA.toast(msg(r, "Could not approve it."), "error"); }
      VA.toast("Approved. " + row.subject.name + " has been notified."); load();
    } else if (j) {
      rejecting = rows.find(function (x) { return x.id === j.getAttribute("data-reject"); });
      $("pcr-reject-who").textContent = rejecting.subject.name + " (" + rejecting.subject.code + ")";
      $("pcr-reason").value = "";
      $("pcr-reject").showModal();
    }
  });
  $("pcr-quick").addEventListener("click", function (e) { const b = e.target.closest("button"); if (b) $("pcr-reason").value = b.textContent; });
  $("pcr-reject-close").addEventListener("click", function () { $("pcr-reject").close(); });
  $("pcr-reject-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const reason = $("pcr-reason").value.trim();
    if (!reason) return VA.toast("Give the reason.", "error");
    const r = await VA.fetchJson(API + "/" + rejecting.id + "/reject", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: reason }) });
    if (!r.ok) return VA.toast(msg(r, "Could not reject it."), "error");
    $("pcr-reject").close();
    VA.toast("Rejected. The person can see your reason."); load();
  });

  document.addEventListener("vistadmin:ready", load);
})();
