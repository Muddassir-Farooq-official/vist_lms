// My decisions (/dashboard/<portal>/decisions, API /api/v1/decisions): everything waiting for this person's approval.
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };

  function msg(text, tone) {
    $("dc-msg").textContent = text || "";
    $("dc-msg").className = "dash-alert" + (tone === "error" ? " error" : " success");
    $("dc-msg").hidden = !text;
  }

  async function load() {
    const r = await VA.fetchJson("/api/v1/decisions");
    if (!r.ok) { msg((r.body && r.body.error && r.body.error.message) || "Could not load.", "error"); return; }
    $("dc-empty").hidden = r.body.length > 0;
    $("dc-rows").innerHTML = r.body.map(function (d) {
      return "<tr><td>" + esc(d.at ? VA.formatDateTime(d.at) : "") + "<br><small>" + esc(d.requested_by || "") + "</small></td>" +
        "<td><strong>" + esc(d.label) + "</strong><br>" + esc(d.summary) + "</td>" +
        '<td class="num">' + (d.amount != null ? "Rs. " + esc(Number(d.amount).toLocaleString()) : "") + "</td>" +
        "<td><small>" + esc(d.decided_by || "") + "</small></td>" +
        '<td class="nowrap"><button type="button" class="role-btn small-btn" data-ok="' + esc(d.action + "|" + d.id) + '">Approve</button> ' +
        '<button type="button" class="role-btn secondary-btn small-btn" data-no="' + esc(d.action + "|" + d.id) + '">Reject</button></td></tr>';
    }).join("");
  }

  $("dc-rows").addEventListener("click", async function (e) {
    const ok = e.target.closest("[data-ok]"), no = e.target.closest("[data-no]");
    if (!ok && !no) return;
    const parts = (ok || no).getAttribute(ok ? "data-ok" : "data-no").split("|");
    let note = null;
    if (no) {
      note = await VA.promptText("Why is it rejected? (the person who asked is told)", "");
      if (!note) return;
    } else if (!(await VA.confirm("Approve this? It is carried out at once.", "Approve"))) return;
    const r = await VA.fetchJson("/api/v1/decisions/" + parts[0] + "/" + parts[1], {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approve: !!ok, note: note }) });
    if (!r.ok) { msg((r.body && r.body.error && r.body.error.message) || "Could not save.", "error"); return; }
    msg(ok ? "Approved." : "Rejected.", "ok");
    load();
  });

  document.addEventListener("vistadmin:ready", load, { once: true });
})();
