(function () {
  "use strict";

  // "View as a person" (administration): search, then open that person's screens read-only.
  const D = window.Dash, VA = window.VistAdmin, esc = D.esc;
  const $ = function (id) { return document.getElementById(id); };
  let timer = null, seq = 0;

  async function search() {
    const q = $("va-q").value.trim(), mine = ++seq;
    $("va-error").hidden = true;
    if (q.length < 2) { $("va-list").innerHTML = ""; $("va-hint").hidden = false; $("va-hint").textContent = "Type at least two letters."; return; }
    const r = await VA.fetchJson("/api/v1/view-as/people?q=" + encodeURIComponent(q), { quiet: true });
    if (mine !== seq) return;
    if (!r.ok) { $("va-error").textContent = (r.body && r.body.error && r.body.error.message) || "Could not search."; $("va-error").hidden = false; return; }
    const rows = r.body || [];
    $("va-hint").hidden = rows.length > 0;
    $("va-hint").textContent = "Nobody matches “" + q + "”.";
    $("va-list").innerHTML = rows.map(function (p) {
      const initials = String(p.name || "?").split(/\s+/).filter(Boolean).map(function (w) { return w[0]; }).slice(0, 2).join("").toUpperCase();
      return '<li><div><span class="pl-avatar" aria-hidden="true">' + esc(initials) + '</span><span class="pl-body"><strong>' + esc(p.name) +
        ' <span class="va-kind">' + esc((p.roles || []).join(", ") || p.kind) + "</span></strong><small>" + esc(p.detail || "") + "</small></span>" +
        (p.can_view ? '<button type="button" class="role-btn small-btn" data-view="' + esc(p.user_id) + '" data-name="' + esc(p.name) + '">View as</button>'
          : '<span class="pl-value">' + (String((p.roles || []).join(" ")).indexOf("Admin") !== -1 || !(p.roles || []).length ? "Not available" : "") + "</span>") +
        "</div></li>";
    }).join("");
  }

  $("va-q").addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(search, 250); });
  $("va-list").addEventListener("click", async function (e) {
    const b = e.target.closest("[data-view]");
    if (!b) return;
    if (!(await VA.confirm("Open " + b.getAttribute("data-name") + "'s screens, read-only, for up to 30 minutes? This is kept in the activity log.", "View as"))) return;
    b.disabled = true;
    const r = await VA.fetchJson("/api/v1/view-as/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: b.getAttribute("data-view") }) });
    if (!r.ok) { b.disabled = false; return VA.toast((r.body && r.body.error && r.body.error.message) || "Could not open the view.", "error"); }
    try { sessionStorage.removeItem("vist_ident"); } catch (err) { /* ignore */ }
    location.href = r.body.redirect || "/";
  });

  async function recent() {
    const r = await VA.fetchJson("/api/v1/audit/activity?action=view_as&page_size=8", { quiet: true });
    const rows = (r.ok && r.body && r.body.items) || [];
    $("va-recent-empty").hidden = rows.length > 0;
    $("va-recent").innerHTML = rows.map(function (x) {
      const verb = x.action === "view_as.started" ? "started viewing" : "stopped viewing";
      return '<li><span class="tl-dot"></span><span class="tl-body"><b>' + esc(x.actor.name) + "</b> " + verb + " " + esc(x.subject || "a person") + "'s screens<small>" +
        esc(VA.formatDateTime(x.at)) + (x.ip ? " · " + esc(x.ip) : "") + "</small></span></li>";
    }).join("");
  }

  document.addEventListener("vistadmin:ready", function () { recent(); $("va-q").focus(); });
})();
