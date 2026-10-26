(function () {
  "use strict";
  // Read-only student directory for the principal, HOD and exam office. Opening a row shows the record (student-sheet.js).
  const VA = window.VistAdmin, Sheet = window.StudentSheet;
  const $ = function (id) { return document.getElementById(id); };
  const SIZE = 15;
  let page = 1, pages = 1, timer = null;

  async function load() {
    const p = new URLSearchParams({ page: String(page), page_size: String(SIZE) });
    const q = $("sl-q").value.trim();
    if (q) p.set("q", q);
    if ($("sl-status").value) p.set("status", $("sl-status").value);
    if ($("sl-program").value) p.set("program_id", $("sl-program").value);
    const r = await VA.fetchJson("/api/v1/students?" + p.toString());
    const tb = $("sl-table").tBodies[0];
    if (!r.ok) {
      tb.innerHTML = "";
      $("sl-empty").textContent = (r.body && r.body.error && r.body.error.message) || "Could not load the students. Try again.";
      $("sl-empty").hidden = false;
      return;
    }
    const d = r.body;
    pages = Math.max(1, Math.ceil(d.total / d.page_size));
    tb.innerHTML = d.items.map(Sheet.rowHtml).join("");
    $("sl-empty").hidden = d.items.length > 0;
    $("sl-empty").textContent = q ? "No student matches “" + q + "”." : "No students here.";
    const from = d.total ? (page - 1) * d.page_size + 1 : 0;
    $("sl-info").textContent = from + "–" + Math.min(d.total, page * d.page_size) + " of " + d.total + " students";
    $("sl-prev").disabled = page <= 1;
    $("sl-next").disabled = page >= pages;
  }
  async function programs() {
    const r = await VA.fetchJson("/api/v1/students/programs", { quiet: true });
    if (!r.ok || !Array.isArray(r.body)) { $("sl-program").hidden = true; return; }
    r.body.forEach(function (x) { const o = document.createElement("option"); o.value = x.id; o.textContent = x.code + " — " + x.name; $("sl-program").appendChild(o); });
  }
  function again() { page = 1; load(); }

  $("sl-q").addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(again, 300); });
  $("sl-status").addEventListener("change", again);
  $("sl-program").addEventListener("change", again);
  $("sl-prev").addEventListener("click", function () { if (page > 1) { page -= 1; load(); } });
  $("sl-next").addEventListener("click", function () { if (page < pages) { page += 1; load(); } });
  $("sl-table").addEventListener("click", function (e) { const t = e.target.closest("[data-open]"); if (t) Sheet.open(t.getAttribute("data-open")); });
  $("sl-table").addEventListener("keydown", function (e) {
    const t = e.target.closest("tr[data-open]");
    if (t && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); Sheet.open(t.getAttribute("data-open")); }
  });
  document.addEventListener("vistadmin:ready", function () { programs(); load(); });
})();