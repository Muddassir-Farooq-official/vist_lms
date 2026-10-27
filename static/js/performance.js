(function () {
  "use strict";

  // Staff scorecard: GET /api/v1/performance?month=YYYY-MM-01. Admin dashboard shows the top five of last month;
  // Employees → Performance shows everyone for any month. "Give bonus" posts to /api/v1/employees/{id}/bonuses.
  const VA = window.VistAdmin, esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const PALETTE = ["#2563eb", "#7c3aed", "#db2777", "#dc2626", "#d97706", "#059669", "#0891b2", "#4f46e5"];
  const PARTS = [["attendance", "Attendance"], ["punctuality", "Punctuality"], ["tasks", "Tasks"], ["conduct", "Conduct"]];
  let current = null, bonusFor = null;

  function initials(n) { const w = String(n || "").trim().split(/\s+/).filter(Boolean); return ((w[0] || "?")[0] + (w.length > 1 ? w[w.length - 1][0] : "")).toUpperCase(); }
  function tone(n) { let h = 0; const t = String(n || ""); for (let i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) >>> 0; return h % PALETTE.length; }
  function avatar(p) {
    return '<span class="sd-avatar" data-tone="' + tone(p.name) + '">' + (p.has_photo ? '<img src="/api/v1/employees/' + esc(p.employee_id) + '/photo" alt="" loading="lazy" />' : esc(initials(p.name))) + "</span>";
  }
  function level(v) { return v === null || v === undefined ? "none" : v >= 90 ? "great" : v >= 75 ? "good" : v >= 60 ? "fair" : "low"; }
  function badge(p) { return '<span class="pf-score pf-' + level(p.score) + '" title="' + esc(p.grade) + '">' + (p.score === null ? "—" : p.score) + "</span>"; }
  function bar(v, hint) {
    if (v === null || v === undefined) return '<span class="pf-na" title="Nothing to measure">—</span>';
    return '<span class="pf-meter pf-' + level(v) + '" title="' + esc(hint || "") + '"><span class="pf-track"><i style="width:' + v + '%"></i></span><b>' + v + "</b></span>";
  }
  function hints(p) {
    const a = p.attendance, t = p.tasks;
    return {
      attendance: a.present + " of " + a.scheduled + " working days present",
      punctuality: a.late + " late, " + a.early + " left early",
      tasks: (t.on_time + t.late + t.overdue) ? t.on_time + " on time, " + t.late + " late, " + t.overdue + " overdue" : "No tasks due this month",
      conduct: p.complaints + " complaint" + (p.complaints === 1 ? "" : "s") + " against",
    };
  }
  function lastMonth() { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0"); }
  async function load(month) {
    const r = await VA.fetchJson("/api/v1/performance" + (month ? "?month=" + month + "-01" : ""), { quiet: true });
    return r.ok ? r.body : null;
  }

  // ---- admin dashboard: top five
  async function top() {
    const box = $("top-perf"); if (!box) return;
    const d = await load(lastMonth());
    if (!d) { $("top-perf-panel").hidden = true; return; }
    current = d;
    $("top-perf-month").textContent = d.month_label;
    const best = d.people.filter(function (p) { return p.score !== null; }).slice(0, 5);
    $("top-perf-empty").hidden = best.length > 0;
    box.innerHTML = best.map(function (p) {
      const h = hints(p);
      return '<li><span class="pf-rank">' + p.rank + "</span>" + avatar(p) + '<span class="pf-who"><strong>' + esc(p.name) + "</strong><small>" + esc(p.job_title || "") + " · " + esc(h.attendance) + "</small></span>" +
        badge(p) + bonusBtn(p) + "</li>";
    }).join("");
  }
  function bonusBtn(p) {
    return p.bonus_this_month > 0 ? '<span class="pf-given" title="A bonus was given in this month or the month after">Bonus ' + esc(VA.formatMoney(p.bonus_this_month)) + "</span>"
      : '<button type="button" class="role-btn small-btn secondary-btn pf-give" data-bonus="' + esc(p.employee_id) + '">Give bonus</button>';
  }

  // ---- Employees → Performance: everyone
  async function table() {
    const body = $("pf-body"); if (!body) return;
    if (!$("pf-month").value) $("pf-month").value = lastMonth();
    const d = await load($("pf-month").value);
    if (!d) { $("pf-empty").hidden = false; $("pf-empty").textContent = "Could not load the scorecard."; return; }
    current = d;
    const scored = d.people.filter(function (p) { return p.score !== null; });
    const great = scored.filter(function (p) { return p.score >= 90; }).length, low = scored.filter(function (p) { return p.score < 60; }).length;
    $("pf-summary").innerHTML = [["Average score", d.average === null ? "—" : String(d.average)], ["Excellent (90+)", String(great)], ["Need attention (below 60)", String(low)],
      ["Period", d.month_label + (d.complete ? "" : " (so far)")]].map(function (k) { return '<div class="pf-sum"><span>' + esc(k[0]) + "</span><strong>" + esc(k[1]) + "</strong></div>"; }).join("");
    $("pf-empty").hidden = d.people.length > 0;
    body.innerHTML = d.people.map(function (p) {
      const h = hints(p);
      return "<tr><td class=\"num pf-rankcell\">" + (p.rank || "—") + '</td><td class="sd-who"><div>' + avatar(p) + '<span class="sd-name"><strong>' + esc(p.name) + "</strong><small>" +
        esc([p.employee_code, p.job_title].filter(Boolean).join(" · ")) + "</small></span></div></td><td>" + badge(p) + '<small class="pf-grade">' + esc(p.grade) + "</small></td>" +
        PARTS.map(function (x) { return "<td>" + bar(p.parts[x[0]], h[x[0]]) + "<small class=\"pf-hint\">" + esc(h[x[0]]) + "</small></td>"; }).join("") +
        '<td class="num">' + (p.score === null ? "" : bonusBtn(p)) + "</td></tr>";
    }).join("");
  }

  // ---- give a bonus
  const dlg = $("pf-bonus");
  if (dlg) {
    document.addEventListener("click", function (e) {
      const b = e.target.closest("[data-bonus]"); if (!b || !current) return;
      bonusFor = current.people.find(function (p) { return p.employee_id === b.getAttribute("data-bonus"); });
      if (!bonusFor) return;
      $("pf-bonus-who").textContent = bonusFor.name + " · score " + bonusFor.score + " (" + bonusFor.grade + ") · " + current.month_label;
      $("pf-bonus-amount").value = "";
      $("pf-bonus-reason").value = (bonusFor.rank === 1 ? "Best performer" : "Top performer") + ", " + current.month_label + " (score " + bonusFor.score + ")";
      $("pf-bonus-quick").innerHTML = [2000, 5000, 10000].map(function (v) { return '<button type="button" class="chip-btn" data-amt="' + v + '">' + esc(VA.formatMoney(v)) + "</button>"; }).join("");
      $("pf-bonus-error").hidden = true;
      dlg.showModal();
    });
    $("pf-bonus-quick").addEventListener("click", function (e) { const b = e.target.closest("[data-amt]"); if (b) $("pf-bonus-amount").value = b.getAttribute("data-amt"); });
    $("pf-bonus-close").addEventListener("click", function () { dlg.close(); });
    $("pf-bonus-form").addEventListener("submit", async function (e) {
      e.preventDefault();
      const amount = parseFloat($("pf-bonus-amount").value), reason = $("pf-bonus-reason").value.trim();
      if (!(amount > 0) || !reason) { $("pf-bonus-error").textContent = "Enter the amount and the reason."; $("pf-bonus-error").hidden = false; return; }
      $("pf-bonus-go").disabled = true;
      const r = await VA.fetchJson("/api/v1/employees/" + bonusFor.employee_id + "/bonuses", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ amount: amount, reason: reason }) });
      $("pf-bonus-go").disabled = false;
      if (!r.ok) { $("pf-bonus-error").textContent = (r.body && r.body.error && r.body.error.message) || "Could not give the bonus."; $("pf-bonus-error").hidden = false; return; }
      dlg.close();
      VA.toast("Bonus of " + VA.formatMoney(amount) + " given to " + bonusFor.name + ". It goes on the next payroll.");
      if ($("pf-body")) table(); else top();
    });
  }
  if ($("pf-month")) $("pf-month").addEventListener("change", table);

  document.addEventListener("vistadmin:ready", function () { top(); table(); });
})();