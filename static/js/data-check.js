(function () {
  "use strict";
  // Data checker (GET /api/v1/data-check): one card per check, its records with a link to fix each.
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  let data = null;
  let area = "";
  const SEV = { high: "Fix first", medium: "Fix soon", low: "Check" };

  function draw() {
    if (!data) return;
    $("dc-areas").innerHTML = ['<button type="button" role="tab" data-area="" aria-selected="' + (area === "") + '">All (' + data.problems + ")</button>"]
      .concat(data.areas.map(function (a) {
        return '<button type="button" role="tab" data-area="' + esc(a.area) + '" aria-selected="' + (area === a.area) + '">' + esc(a.area) + " (" + a.problems + ")</button>";
      })).join("");
    const hide = $("dc-hide-ok").checked;
    const shown = data.checks.filter(function (c) { return (!area || c.area === area) && (!hide || c.count); });
    $("dc-list").innerHTML = shown.map(function (c) {
      const more = c.count > c.rows.length ? '<p class="panel-hint">Showing the first ' + c.rows.length + " of " + c.count + ".</p>" : "";
      return '<details class="dc-check sev-' + c.severity + '"' + (c.count && c.severity === "high" ? " open" : "") + "><summary><span class=\"dc-count\">" + c.count +
        '</span><span class="dc-title"><b>' + esc(c.title) + '</b><small>' + esc(c.area) + " · " + esc(c.why) + "</small></span>" +
        (c.count ? '<span class="dc-sev">' + SEV[c.severity] + "</span>" : '<span class="dc-ok">All good</span>') + "</summary>" +
        (c.count ? '<div class="table-scroll"><table class="dash-table"><tbody>' + c.rows.map(function (r) {
          return "<tr><td><strong>" + esc(r.label) + "</strong><small>" + esc(r.code || "") + "</small></td><td>" + esc(r.detail || "") +
            '</td><td><a class="row-action" href="' + esc(r.link) + '">Fix…</a></td></tr>';
        }).join("") + "</tbody></table></div>" + more : "") + "</details>";
    }).join("") || '<p class="empty-state">Nothing to fix here.</p>';
  }

  async function run() {
    $("dc-summary").textContent = "Checking…";
    const r = await VA.fetchJson("/api/v1/data-check");
    if (!r.ok) { $("dc-summary").textContent = (r.body && r.body.error && r.body.error.message) || "Could not check."; return; }
    data = r.body;
    const bad = data.checks.filter(function (c) { return c.count; }).length;
    $("dc-summary").textContent = data.checks.length + " checks: " + (bad ? bad + " found something (" + data.problems + " records)." : "everything looks right.");
    draw();
  }
  $("dc-areas").addEventListener("click", function (e) {
    const b = e.target.closest("button[data-area]");
    if (!b) return;
    area = b.getAttribute("data-area");
    draw();
  });
  $("dc-hide-ok").addEventListener("change", draw);
  $("dc-run").addEventListener("click", run);
  run();
})();
