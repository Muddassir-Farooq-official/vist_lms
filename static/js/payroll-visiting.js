// Payroll -> Visiting faculty: each visiting teacher's rate per lecture, and the month's lectures (API /api/v1/payroll/visiting).
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  if (!$("vf-body")) return;
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const money = function (v) { return Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 0 }); };
  const API = "/api/v1/payroll/visiting";
  const now = new Date();
  $("vf-month").value = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0");
  const month = function () { return $("vf-month").value + "-01"; };

  async function load() {
    $("vf-error").hidden = true;
    const r = await VA.fetchJson(API + "?month=" + month());
    if (!r.ok) { $("vf-error").textContent = (r.body && r.body.error && r.body.error.message) || "Could not load."; $("vf-error").hidden = false; return; }
    const ppl = r.body.people;
    $("vf-empty").hidden = !!ppl.length;
    $("vf-body").innerHTML = ppl.map(function (p) {
      const c = p.count;
      return '<tr data-id="' + esc(p.employee_id) + '"><td><strong>' + esc(p.name) + '</strong><small class="list-sub">' + esc(p.code || "") + "</small></td>" +
        '<td><input type="number" class="text-input small-input vf-rate" min="1" step="1" value="' + (p.rate == null ? "" : p.rate) + '" placeholder="not set" aria-label="Rate per lecture for ' + esc(p.name) + '" /> ' +
        '<button type="button" class="role-btn small-btn secondary-btn vf-save">Save</button></td>' +
        '<td class="num">' + p.held + '</td><td class="num">' + c.TAKEN + '</td><td class="num">' + c.NOT_TAKEN + '</td><td class="num">' + c.COVERED +
        '</td><td class="num">' + c.BY_OFFICE + '</td><td class="num">' + (p.rate ? money(p.earned) : "—") + '</td><td class="num">' + (p.rate ? money(p.not_paid) : "—") + "</td>" +
        '<td><a class="role-btn small-btn secondary-btn" target="_blank" rel="noopener" href="' + API + "/" + esc(p.employee_id) + "/lectures.pdf?month=" + month() + '">Lecture sheet</a></td></tr>';
    }).join("");
  }

  $("vf-body").addEventListener("click", async function (e) {
    const b = e.target.closest(".vf-save");
    if (!b) return;
    const tr = b.closest("tr");
    const v = tr.querySelector(".vf-rate").value.trim();
    b.disabled = true;
    const r = await VA.fetchJson(API + "/" + tr.dataset.id + "/rate", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lecture_rate: v === "" ? null : Number(v) }) });
    b.disabled = false;
    if (!r.ok) { VA.toast((r.body && r.body.error && r.body.error.message) || "Not saved.", "error"); return; }
    VA.toast(v === "" ? "Rate removed: paid a monthly salary again." : "Saved: paid Rs. " + money(v) + " a lecture from the next payroll worked out.", "success");
    load();
  });
  $("vf-month").addEventListener("change", load);
  document.querySelectorAll('[data-tab="visiting"]').forEach(function (t) { t.addEventListener("click", load); });
  if (location.hash === "#visiting") load();
})();
