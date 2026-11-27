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

  let all = [], kind = "";
  function draw() {
    const rows = kind ? all.filter(function (d) { return d.label === kind; }) : all;
    $("dc-empty").hidden = rows.length > 0;
    $("dc-rows").innerHTML = rows.map(function (d) {
      return '<article class="dc-item">' +
        '<div class="dc-top"><span class="dc-kind">' + esc(d.label) + "</span>" + (d.amount != null ? '<span class="dc-amount">Rs. ' + esc(Number(d.amount).toLocaleString()) + "</span>" : "") + "</div>" +
        '<p class="dc-what">' + esc(d.summary) + "</p>" +
        '<p class="dc-meta">' + esc(d.requested_by || "") + (d.at ? " &middot; " + esc(VA.formatDateTime(d.at)) : "") + "</p>" +
        '<div class="dc-actions"><button type="button" class="role-btn small-btn" data-ok="' + esc(d.action + "|" + d.id) + '">Approve</button>' +
        '<button type="button" class="role-btn secondary-btn small-btn" data-no="' + esc(d.action + "|" + d.id) + '">Reject</button>' +
        (d.link && location.pathname.indexOf("/dashboard/admin/") === 0 ? '<a class="link-btn dc-open" href="' + esc(d.link) + '">Open</a>' : "") + "</div></article>";
    }).join("");
  }
  async function load() {
    const r = await VA.fetchJson("/api/v1/decisions");
    if (!r.ok) { msg((r.body && r.body.error && r.body.error.message) || "Could not load.", "error"); return; }
    all = r.body;
    const kinds = {};
    all.forEach(function (d) { kinds[d.label] = (kinds[d.label] || 0) + 1; });
    const names = Object.keys(kinds);
    if (kind && !kinds[kind]) kind = "";
    $("dc-filter").hidden = names.length < 2;
    $("dc-filter").innerHTML = '<button type="button" class="dc-pill' + (kind ? "" : " on") + '" data-kind="">All (' + all.length + ")</button>" +
      names.map(function (k) { return '<button type="button" class="dc-pill' + (kind === k ? " on" : "") + '" data-kind="' + esc(k) + '">' + esc(k) + " (" + kinds[k] + ")</button>"; }).join("");
    draw();
  }
  $("dc-filter").addEventListener("click", function (e) {
    const b = e.target.closest("[data-kind]");
    if (!b) return;
    kind = b.getAttribute("data-kind");
    $("dc-filter").querySelectorAll(".dc-pill").forEach(function (x) { x.classList.toggle("on", x === b); });
    draw();
  });

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
