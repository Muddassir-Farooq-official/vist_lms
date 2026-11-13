/* Admin -> Classes & sections -> Fees (School mode): monthly vouchers, class fees, transport routes and who is on them.
   API: /api/v1/school/fees/*. Payments, receipts and defaulters stay on the usual Fees pages. */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const API = "/api/v1/school/fees";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (s) { return VA.escapeHtml(s == null ? "" : String(s)); };
  const rs = function (n) { return "Rs. " + Number(n || 0).toLocaleString("en-PK", { maximumFractionDigits: 0 }); };
  function msg(r, f) { return (r.body && r.body.error && r.body.error.message) || f; }
  function json(method, body) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }; }
  function session() { const s = $("sc-session"); return s && s.value ? s.value : ""; }
  function monthValue() { return $("sf-month").value ? $("sf-month").value + "-01" : null; }
  let started = false;

  async function load() {
    if (!started) {
      started = true;
      const now = new Date();
      $("sf-month").value = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0");
      $("sf-month").addEventListener("change", month);
      $("sf-make").addEventListener("click", make);
      $("sf-route-add").addEventListener("submit", addRoute);
    }
    await Promise.all([month(), plans(), routes()]);
  }

  async function month() {
    const r = await VA.fetchJson(API + "/month" + (monthValue() ? "?month=" + monthValue() : ""));
    if (!r.ok) { $("sf-stats").innerHTML = '<p class="dash-alert error">' + esc(msg(r, "Could not load.")) + "</p>"; return; }
    draw(r.body);
  }

  function draw(m) {
    $("sf-stats").innerHTML = [["Vouchers", m.vouchers + " of " + m.students], ["Paid", m.paid + " of " + m.vouchers], ["Billed", rs(m.billed)],
      ["Sibling discounts", rs(m.sibling_discounts)]].map(function (x) {
      return '<div class="sf-stat"><span>' + esc(x[0]) + "</span><strong>" + esc(x[1]) + "</strong></div>";
    }).join("");
    $("sf-make").textContent = "Make the " + m.month_name + " vouchers now";
    $("sf-make").hidden = !m.session;
    if (!m.session) $("sf-stats").insertAdjacentHTML("beforeend", '<p class="rc-info">No session covers this month.</p>');
  }

  async function make() {
    if (!(await VA.confirm("Make the vouchers for everyone in a school class who has none for this month yet? They are made on their own on the 1st; this is only needed sooner. Families are told.", "Make vouchers"))) return;
    $("sf-make").disabled = true;
    const r = await VA.fetchJson(API + "/month", json("POST", { month: monthValue() }));
    $("sf-make").disabled = false;
    if (!r.ok) { VA.toast(msg(r, "Could not make them."), "error"); return; }
    const noPlan = r.body.skipped.filter(function (s) { return s.reason === "no fee plan for the class"; }).length;
    VA.toast(r.body.made + " voucher" + (r.body.made === 1 ? "" : "s") + " made (" + rs(r.body.total) + ")." +
      (noPlan ? " " + noPlan + " student(s) skipped: their class has no fees set." : "") +
      (r.body.skipped.length - noPlan > 0 ? " " + (r.body.skipped.length - noPlan) + " could not be made." : ""), r.body.made ? "success" : "info");
    draw(r.body.view);
  }

  async function plans() {
    const r = await VA.fetchJson(API + "/plans" + (session() ? "?session_label=" + encodeURIComponent(session()) : ""));
    const box = $("sf-plans");
    if (!r.ok) { box.innerHTML = '<p class="dash-alert error">' + esc(msg(r, "Could not load.")) + "</p>"; return; }
    const d = r.body;
    $("sf-rule").textContent = "Vouchers are made on the 1st and are due on the " + d.due_day + suffix(d.due_day) + ". Late fine: " + (d.late_fine ? rs(d.late_fine) + " once" : "none") +
      ". Sibling discount on tuition: 2nd child " + d.sibling_second + "%, 3rd and later " + d.sibling_third + "% (same guardian CNIC). Change these in Settings → Institute type.";
    if (!d.classes.length) { box.innerHTML = '<p class="empty-state">No school classes yet. Set them up on the Classes tab.</p>'; return; }
    box.innerHTML = '<table class="dash-table sf-plan"><thead><tr><th>Class (' + esc(d.session) + ')</th><th class="num">Students</th><th>Monthly tuition</th><th>Admission fee</th><th>Annual charges</th></tr></thead><tbody>' +
      d.classes.map(function (c) {
        const inp = function (k) { return '<input type="number" class="text-input" min="0" step="50" data-k="' + k + '" value="' + (c[k] || 0) + '" aria-label="' + esc(c.class + " " + k.replace(/_/g, " ")) + '">'; };
        return '<tr data-pid="' + c.program_id + '"' + (c.set ? "" : ' class="sf-unset"') + "><td><strong>" + esc(c.class) + "</strong>" + (c.set ? "" : ' <small class="sc-code">not set</small>') +
          '</td><td class="num">' + c.students + "</td><td>" + inp("monthly_tuition") + "</td><td>" + inp("admission_fee") + "</td><td>" + inp("annual_charges") + "</td></tr>";
      }).join("") + '</tbody></table><div class="toolbar"><button type="button" class="role-btn" id="sf-plans-save">Save class fees</button></div>';
    $("sf-plans-save").addEventListener("click", async function () {
      const items = [];
      box.querySelectorAll("tr[data-pid]").forEach(function (tr) {
        const v = function (k) { return Number(tr.querySelector('[data-k="' + k + '"]').value || 0); };
        const it = { program_id: tr.dataset.pid, monthly_tuition: v("monthly_tuition"), admission_fee: v("admission_fee"), annual_charges: v("annual_charges") };
        if (!tr.classList.contains("sf-unset") || it.monthly_tuition || it.admission_fee || it.annual_charges) items.push(it);
      });
      if (!items.length) { VA.toast("Type the fees of at least one class.", "error"); return; }
      const x = await VA.fetchJson(API + "/plans", json("PUT", { session_label: d.session, plans: items }));
      if (!x.ok) { VA.toast(msg(x, "Could not save."), "error"); return; }
      VA.toast("Class fees saved. They count for vouchers made from now on.", "success");
      plans();
    });
  }

  function suffix(n) { return (n % 100 >= 11 && n % 100 <= 13) ? "th" : ({ 1: "st", 2: "nd", 3: "rd" })[n % 10] || "th"; }

  async function routes() {
    const r = await VA.fetchJson(API + "/routes");
    const box = $("sf-routes");
    if (!r.ok) { box.textContent = "Could not load."; return; }
    box.innerHTML = r.body.length ? '<table class="dash-table"><thead><tr><th>Route</th><th class="num">Monthly fee</th><th class="num">Students</th><th>Status</th><th class="crs-actions"></th></tr></thead><tbody>' +
      r.body.map(function (x) {
        return "<tr><td><strong>" + esc(x.name) + '</strong></td><td class="num">' + rs(x.monthly_fee) + '</td><td class="num">' + x.students + "</td><td>" +
          (x.active ? '<span class="status-pill active">On</span>' : '<span class="status-pill">Off</span>') + '</td><td class="crs-actions"><div class="dt-actions">' +
          '<button type="button" class="sd-view" data-riders="' + x.id + '" data-name="' + esc(x.name) + '">Students</button>' +
          '<button type="button" class="sd-view" data-edit="' + x.id + '" data-name="' + esc(x.name) + '" data-fee="' + x.monthly_fee + '" data-active="' + x.active + '">Change</button></div></td></tr>';
      }).join("") + "</tbody></table>" : '<p class="empty-state">No routes yet.</p>';
    box.querySelectorAll("[data-riders]").forEach(function (b) { b.addEventListener("click", function () { riders(b.dataset.riders, b.dataset.name); }); });
    box.querySelectorAll("[data-edit]").forEach(function (b) { b.addEventListener("click", function () { editRoute(b.dataset); }); });
  }

  async function addRoute(e) {
    e.preventDefault();
    const name = $("sf-rn").value.trim(), fee = Number($("sf-rf").value);
    if (name.length < 2 || !$("sf-rf").value) { VA.toast("Type the route's name and monthly fee.", "error"); return; }
    const r = await VA.fetchJson(API + "/routes", json("POST", { name: name, monthly_fee: fee, active: true }));
    if (!r.ok) { VA.toast(msg(r, "Could not add it."), "error"); return; }
    $("sf-rn").value = ""; $("sf-rf").value = "";
    VA.toast("Route added.", "success");
    routes();
  }

  async function editRoute(d) {
    const fee = await VA.ask({ title: "Change " + d.name, intro: "The new fee counts for vouchers made from now on.", label: "Monthly fee (Rs.)", input: "number", value: d.fee, min: 0, ok: "Next" });
    if (fee === null || fee === undefined || fee === "") return;
    const on = await VA.ask({ title: d.name, label: "Route", input: "select", value: d.active === "true" ? "on" : "off",
      options: [{ value: "on", label: "On (students can be put on it)" }, { value: "off", label: "Off (kept on record)" }], ok: "Save" });
    if (!on) return;
    const r = await VA.fetchJson(API + "/routes/" + d.edit, json("PUT", { name: d.name, monthly_fee: Number(fee), active: on === "on" }));
    if (!r.ok) { VA.toast(msg(r, "Could not save."), "error"); return; }
    VA.toast("Saved.", "success");
    routes();
  }

  async function riders(id, name) {
    const box = $("sf-riders");
    const r = await VA.fetchJson(API + "/routes/" + id + "/students");
    if (!r.ok) { VA.toast(msg(r, "Could not load."), "error"); return; }
    box.hidden = false;
    box.innerHTML = '<div class="sc-detail-head"><h2 class="panel-title">' + esc(name) + '</h2><button type="button" class="sd-view" id="sf-rd-close">Close</button></div>' +
      (r.body.length ? '<ul class="sf-rd">' + r.body.map(function (s) {
        return '<li><span><strong>' + esc(s.name) + '</strong> <small class="sc-code">' + esc((s.roll_number || "") + " · " + (s.class || "") + (s.section ? " " + s.section : "")) +
          " · from " + esc(fmtMonth(s.from_month)) + (s.until_month ? " to " + esc(fmtMonth(s.until_month)) : "") + '</small></span><button type="button" class="sd-view tt-del" data-end="' + s.id + '" data-name="' + esc(s.name) + '">Take off</button></li>';
      }).join("") + "</ul>" : '<p class="empty-state">Nobody on this route yet.</p>') +
      '<h3 class="sf-sub">Add students</h3><div class="sf-add"><input type="search" class="text-input" id="sf-find" placeholder="Name or roll number" aria-label="Find students">' +
      '<label class="rc-day">From <input type="month" id="sf-from" class="text-input"></label></div><ul class="sc-cands" id="sf-cands"></ul>' +
      '<div class="toolbar"><button type="button" class="role-btn" id="sf-put">Put the ticked students on this route</button></div>';
    const now = new Date();
    $("sf-from").value = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0");
    $("sf-rd-close").addEventListener("click", function () { box.hidden = true; });
    let t = null;
    $("sf-find").addEventListener("input", function () { clearTimeout(t); t = setTimeout(find, 300); });
    box.querySelectorAll("[data-end]").forEach(function (b) {
      b.addEventListener("click", async function () {
        const last = await VA.ask({ title: "Take " + b.dataset.name + " off", intro: "The last month with transport on the voucher.", label: "Last month (YYYY-MM)",
          value: $("sf-month").value, ok: "Take off", danger: true });
        if (!last) return;
        if (!/^\d{4}-\d{2}$/.test(last.trim())) { VA.toast("Type the month as YYYY-MM, e.g. 2026-12.", "error"); return; }
        const x = await VA.fetchJson(API + "/rides/" + b.dataset.end + "/end", json("POST", { last_month: last.trim() + "-01" }));
        if (!x.ok) { VA.toast(msg(x, "Could not do it."), "error"); return; }
        riders(id, name); routes();
      });
    });
    $("sf-put").addEventListener("click", async function () {
      const ids = Array.prototype.map.call(box.querySelectorAll("#sf-cands input:checked"), function (c) { return c.value; });
      if (!ids.length) { VA.toast("Search and tick the students first.", "error"); return; }
      const x = await VA.fetchJson(API + "/routes/" + id + "/students", json("POST", { student_ids: ids, from_month: $("sf-from").value ? $("sf-from").value + "-01" : null }));
      if (!x.ok) { VA.toast(msg(x, "Could not add them."), "error"); return; }
      VA.toast(x.body.added + " added." + (x.body.failed.length ? " Not added: " + x.body.failed.map(function (f) { return (f.name || "a student") + " (" + f.reason + ")"; }).join("; ") : ""),
        x.body.failed.length ? "error" : "success");
      riders(id, name); routes();
    });
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function find() {
    const q = $("sf-find").value.trim();
    const ul = $("sf-cands");
    if (q.length < 2) { ul.innerHTML = ""; return; }
    const r = await VA.fetchJson("/api/v1/students?status=ACTIVE&page_size=20&q=" + encodeURIComponent(q));
    if (!r.ok) { ul.innerHTML = '<li class="sc-dim">' + esc(msg(r, "Could not search.")) + "</li>"; return; }
    const items = r.body.items || [];
    ul.innerHTML = items.length ? items.map(function (s) {
      return '<li><label><input type="checkbox" value="' + esc(s.id) + '"> <b>' + esc(s.first_name + " " + s.last_name) + "</b> <span>" + esc(s.roll_number || "") + "</span> <small>" +
        esc((s.program_name || "No class") + (s.section_label ? " · " + s.section_label : "")) + "</small></label></li>";
    }).join("") : '<li class="sc-dim">Nobody found.</li>';
  }

  function fmtMonth(iso) {
    const d = new Date(iso + "T00:00:00");
    return isNaN(d) ? iso : d.toLocaleDateString("en-GB", { month: "short", year: "numeric" });
  }

  window.VistSchoolFees = { load: load };
})();
