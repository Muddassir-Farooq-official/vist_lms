(function () {
  "use strict";
  // Profile completion: how many of the details and documents the office needs are on file.
  //   ProfileCompleteness.fill(tbody, "students" | "employees")  fills every <td data-pc="id"> in a list
  //   ProfileCompleteness.card(el, result)                        a bar + "still missing" list
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;

  function tone(p) { return p >= 100 ? "ok" : p >= 60 ? "mid" : "low"; }
  function bar(p) { return '<span class="pc-meter ' + tone(p) + '"><i style="width:' + p + '%"></i></span>'; }

  async function fill(tbody, type) {
    const cells = Array.prototype.slice.call(tbody.querySelectorAll("td[data-pc]"));
    if (!cells.length) return;
    const r = await VA.fetchJson("/api/v1/records/completeness", { method: "POST", quiet: true, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: type, ids: cells.map(function (c) { return c.getAttribute("data-pc"); }) }) });
    cells.forEach(function (c) {
      const x = r.ok ? r.body[c.getAttribute("data-pc")] : null;
      if (!x) { c.innerHTML = '<span class="sd-dim">—</span>'; return; }
      const tip = x.missing.length ? "Missing: " + x.missing.join(", ") : "Everything is on file";
      c.innerHTML = '<div class="pc-cell" title="' + esc(tip) + '">' + bar(x.percent) + "<b>" + x.percent + "%</b></div>";
    });
  }

  function card(el, x, own, hint) {
    if (!el || !x) return;
    el.hidden = false;
    el.innerHTML = '<div class="pc-card ' + tone(x.percent) + '"><div class="pc-card-top"><strong>' + (own ? "Your profile is " : "Profile ") + x.percent + "% complete</strong>" +
      "<span>" + x.done + " of " + x.total + " on file</span></div>" + bar(x.percent) +
      (x.missing.length ? '<p class="pc-missing">Still missing: ' + x.missing.map(esc).join(", ") + (own ? ". " + (hint || "Add them under Update my details and Documents.") : ".") + "</p>"
        : '<p class="pc-missing">Everything the office needs is on file.</p>') + "</div>";
  }

  window.ProfileCompleteness = { fill: fill, card: card };
})();