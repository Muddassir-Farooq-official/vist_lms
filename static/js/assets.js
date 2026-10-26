(function () {
  "use strict";

  // Fixed assets: /api/v1/assets/...
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/assets";
  const $ = function (id) { return document.getElementById(id); };
  const money = function (v) { return v == null ? "—" : VA.formatMoney(v); };
  const day = function (v) { return v ? new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—"; };
  const words = function (v) { return String(v || "").replace(/_/g, " ").toLowerCase().replace(/^./, function (c) { return c.toUpperCase(); }); };
  const tile = function (a, b, c, tone) { return '<div class="kpi ' + (tone || "") + '"><span class="kpi-label">' + a + '</span><strong class="kpi-value">' + b + '</strong><span class="kpi-sub">' + (c || "") + "</span></div>"; };
  const STATUS_PILL = { IN_USE: "active", IN_STORE: "", UNDER_REPAIR: "pending", MISSING: "dropped", DISPOSED: "dropped" };
  const RESULT = { FOUND: "Found", WRONG_PLACE: "Found elsewhere", MISSING: "Missing", NOT_CHECKED: "Not checked yet" };
  const METHOD = { STRAIGHT_LINE: "Straight line", REDUCING_BALANCE: "Reducing balance", NONE: "Not depreciated" };
  let canManage = false, canApprove = false, cats = [], locs = [], listIds = [], openCheck = null, importText = null, debounce = null, summary = null;

  function say(t, bad) { const el = $("as-msg"); el.textContent = t || ""; el.hidden = !t; el.className = bad ? "dash-alert error" : "panel-hint"; if (t) el.scrollIntoView({ block: "nearest" }); }
  function errText(r) { return (r.body && r.body.error && r.body.error.message) || "That did not work."; }
  function send(url, method, body) { return VA.fetchJson(url, { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }); }
  function num(id) { const v = $(id).value; return v === "" ? null : Number(v); }
  function val(id) { const v = $(id).value.trim(); return v === "" ? null : v; }
  function button(text, cls, fn) { const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn " + (cls || ""); b.textContent = text; b.addEventListener("click", fn); return b; }
  function opts(list, blank, label) { return (blank != null ? '<option value="">' + esc(blank) + "</option>" : "") + list.map(function (x) { return '<option value="' + esc(x.id) + '">' + esc(label(x)) + "</option>"; }).join(""); }
  function openTab(name) { const t = document.querySelector('.portal-tab[data-tab="' + name + '"]'); if (t) t.click(); }

  function lastMonthEnd() { const t = new Date(); const d = new Date(t.getFullYear(), t.getMonth(), 0); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }

  // ------------------------------------------------------------------ summary, lists of categories and places
  async function loadSummary() {
    const r = await VA.fetchJson(API + "/summary");
    if (!r.ok) return;
    summary = r.body;
    const s = r.body;
    $("as-tiles").innerHTML = '<div class="kpi-grid kpi-grid-6">' +
      tile("Book value", money(s.book_value), s.assets + " assets on the books", "good") +
      tile("Cost", money(s.cost), money(s.accumulated) + " depreciated") +
      tile("Depreciation posted to", s.depreciated_to ? day(s.depreciated_to) : "Not yet", "next: " + day(s.next_period_end), s.assets && (!s.depreciated_to || s.depreciated_to < lastMonthEnd()) ? "warn" : "") +
      tile("Missing", s.missing, s.under_repair + " under repair", s.missing ? "warn" : "") +
      tile("Disposals waiting", s.disposals_waiting, s.disposed + " disposed of so far", s.disposals_waiting ? "warn" : "") +
      tile("Open checks", s.open_checks, s.warranty_ending + " warranties ending in 60 days") + "</div>";
    if (!$("dp-month").value && s.next_period_end) $("dp-month").value = String(s.next_period_end).slice(0, 7);
  }

  async function loadLists() {
    const [c, l] = await Promise.all([VA.fetchJson(API + "/categories"), VA.fetchJson(API + "/locations")]);
    if (c.ok) cats = c.body;
    if (l.ok) locs = l.body;
    const activeCats = cats.filter(function (x) { return x.is_active; });
    const activeLocs = locs.filter(function (x) { return x.is_active; });
    $("rg-cat").innerHTML = opts(cats, "All categories", function (x) { return x.name; });
    $("rg-loc").innerHTML = opts(locs, "All places", function (x) { return x.name; });
    $("ad-cat").innerHTML = opts(activeCats, "Choose…", function (x) { return x.code + " — " + x.name; });
    $("ad-loc").innerHTML = opts(activeLocs, "No particular place", function (x) { return x.name; });
    $("ck-loc").innerHTML = opts(activeLocs, "Every place", function (x) { return x.name; });
    renderSetup();
  }

  // ------------------------------------------------------------------ register
  async function loadRegister() {
    const p = new URLSearchParams({ limit: "1000" });
    if (val("rg-q")) p.set("q", val("rg-q"));
    if ($("rg-cat").value) p.set("category_id", $("rg-cat").value);
    if ($("rg-loc").value) p.set("location_id", $("rg-loc").value);
    if ($("rg-status").value) p.set("status", $("rg-status").value);
    const r = await VA.fetchJson(API + "?" + p.toString());
    if (!r.ok) return say(errText(r), true);
    const tb = $("rg-rows"); tb.innerHTML = "";
    listIds = [];
    r.body.items.forEach(function (a) {
      if (a.status !== "DISPOSED") listIds.push(a.id);
      const tr = document.createElement("tr");
      tr.innerHTML = "<td><strong>" + esc(a.tag) + "</strong></td><td>" + esc(a.name) + (a.make_model ? "<br><small>" + esc(a.make_model) + "</small>" : "") + "</td><td>" + esc(a.category) +
        "</td><td>" + esc(a.location || "—") + "</td><td>" + esc(a.custodian || "—") + '</td><td><span class="status-pill ' + (STATUS_PILL[a.status] || "") + '">' + esc(words(a.status)) + "</span>" +
        (a.condition !== "GOOD" ? "<br><small>" + esc(words(a.condition)) + "</small>" : "") + '</td><td class="num">' + money(a.cost) + (a.cost_is_estimate ? "<br><small>estimate</small>" : "") +
        '</td><td class="num">' + money(a.book_value) + '</td><td class="row-actions"></td>';
      tr.querySelector(".row-actions").appendChild(button("Open", "secondary-btn", function () { openAsset(a.id); }));
      tb.appendChild(tr);
    });
    $("rg-empty").hidden = r.body.items.length > 0;
    $("rg-count").textContent = r.body.total > r.body.items.length ? "Showing " + r.body.items.length + " of " + r.body.total + ". Narrow the search to see the rest." : r.body.total + " assets.";
  }
  ["rg-cat", "rg-loc", "rg-status"].forEach(function (id) { $(id).addEventListener("change", loadRegister); });
  $("rg-q").addEventListener("input", function () { clearTimeout(debounce); debounce = setTimeout(loadRegister, 300); });
  $("rg-labels").addEventListener("click", function () {
    if (!listIds.length) return say("Nothing in the list to print.", true);
    const url = $("rg-loc").value && !val("rg-q") && !$("rg-cat").value && !$("rg-status").value ? API + "/labels.pdf?location_id=" + $("rg-loc").value : API + "/labels.pdf?ids=" + listIds.slice(0, 1000).join(",");
    VA.downloadFile(url, "asset-labels.pdf", "labels");
  });

  // ------------------------------------------------------------------ one asset
  async function openAsset(id) {
    const r = await VA.fetchJson(API + "/" + id);
    if (!r.ok) return say(errText(r), true);
    renderAsset(r.body);
    if (!$("as-modal").open) $("as-modal").showModal();
  }
  $("as-close").addEventListener("click", function () { $("as-modal").close(); });

  function fact(label, value, sub) { return '<div class="as-fact"><span>' + esc(label) + "</span><strong>" + esc(value) + "</strong><small>" + esc(sub || "") + "</small></div>"; }

  function renderAsset(a) {
    const d = a.depreciation;
    const live = a.status !== "DISPOSED";
    const box = $("as-detail");
    box.innerHTML = '<h2>' + esc(a.tag) + " — " + esc(a.name) + "</h2>" +
      '<p class="cc-meta"><span class="status-pill ' + (STATUS_PILL[a.status] || "") + '">' + esc(words(a.status)) + "</span> · " + esc(a.category) + " · condition " + esc(words(a.condition)) +
      (a.disposed_on ? " · disposed of " + day(a.disposed_on) : "") + "</p>" +
      '<div class="as-facts">' + fact("Cost", money(a.cost), a.cost_is_estimate ? "estimated value" : (a.acquired_on ? "bought " + day(a.acquired_on) : "")) +
      fact("Depreciation", money(d.opening_accumulated + d.posted), d.opening_accumulated ? money(d.opening_accumulated) + " before the system" : (d.depreciated_to ? "to " + day(d.depreciated_to) : "none posted yet")) +
      fact("Book value", money(d.book_value), d.describe) + fact("Place", a.location || "—", a.custodian ? "held by " + a.custodian : (a.department || "")) + "</div>" +
      '<dl class="detail-list">' +
      [["Make / model", a.make_model], ["Serial no.", a.serial_number], ["Department", a.department], ["Vendor", a.vendor], ["Invoice", a.invoice_no],
       ["Warranty until", a.warranty_until ? day(a.warranty_until) : null], ["Depreciated in this system from", day(d.starts)], ["Residual value", d.residual_value ? money(d.residual_value) : null],
       ["Notes", a.notes]].filter(function (x) { return x[1]; }).map(function (x) { return "<dt>" + esc(x[0]) + "</dt><dd>" + esc(x[1]) + "</dd>"; }).join("") + "</dl>" +
      '<div class="rp-actions" id="ax-actions"></div><div id="ax-form"></div>' +
      (d.projection.length ? "<h3>Next five years (if nothing changes)</h3><table class=\"dash-table\"><thead><tr><th>Year</th><th class=\"num\">Depreciation</th><th class=\"num\">Book value after</th></tr></thead><tbody>" +
        d.projection.slice(0, 5).map(function (p) { return "<tr><td>" + day(p.from) + " – " + day(p.to) + '</td><td class="num">' + money(p.depreciation) + '</td><td class="num">' + money(p.book_value_after) + "</td></tr>"; }).join("") + "</tbody></table>" : "") +
      (a.entries.length ? "<h3>Depreciation posted</h3><table class=\"dash-table\"><thead><tr><th>Period</th><th class=\"num\">Amount</th><th class=\"num\">Book value after</th><th></th></tr></thead><tbody>" +
        a.entries.map(function (e) { return "<tr" + (e.reversed ? ' class="muted"' : "") + "><td>" + day(e.from) + " – " + day(e.to) + '</td><td class="num">' + money(e.amount) + '</td><td class="num">' + money(e.book_value_after) +
          "</td><td>" + (e.reversed ? "reversed" : e.disposal ? "up to disposal" : "") + "</td></tr>"; }).join("") + "</tbody></table>" : "") +
      "<h3>History</h3><table class=\"dash-table\"><thead><tr><th>Date</th><th>What</th><th>By</th></tr></thead><tbody>" +
      a.events.slice().reverse().map(function (e) { return "<tr><td>" + day(e.on) + "</td><td><strong>" + esc(words(e.kind)) + "</strong> " + esc(e.summary) + (e.cost ? " · " + money(e.cost) : "") +
        (e.vendor_name ? " · " + esc(e.vendor_name) : "") + (e.note ? "<br><small>" + esc(e.note) + "</small>" : "") + "</td><td>" + esc(e.by || "") + "</td></tr>"; }).join("") + "</tbody></table>" +
      (a.disposals.length ? "<h3>Disposal requests</h3><ul>" + a.disposals.map(function (x) { return "<li>" + esc(words(x.method)) + " on " + day(x.disposed_on) + " — " + esc(words(x.status)) +
        (x.gain_or_loss != null ? " · book value " + money(x.book_value) + ", received " + money(x.proceeds) : "") + (x.decision_note ? " · " + esc(x.decision_note) : "") + "</li>"; }).join("") + "</ul>" : "");
    const act = $("ax-actions");
    act.appendChild(button("Print label", "secondary-btn", function () { VA.downloadFile(a.label_url, a.tag + "-label.pdf", "label"); }));
    if (canManage && live) {
      act.appendChild(button("Move / hand over / status", "", function () { changeForm(a); }));
      act.appendChild(button("Record a repair", "secondary-btn", function () { repairForm(a); }));
      act.appendChild(button("Edit details", "secondary-btn", function () { detailsForm(a); }));
      if (!a.disposals.some(function (x) { return x.status === "REQUESTED"; })) act.appendChild(button("Dispose of…", "danger-btn", function () { disposeForm(a); }));
    }
    if (canApprove && live && !d.posted) act.appendChild(button("Correct cost / terms", "secondary-btn", function () { correctForm(a); }));
  }

  function form(html, onSubmit) {
    const f = $("ax-form");
    f.innerHTML = '<form class="form-grid two-col as-form sh-card">' + html + '<p class="dash-alert error two-span" hidden></p><div class="form-actions two-span"><button type="submit" class="role-btn">Save</button> ' +
      '<button type="button" class="role-btn secondary-btn" data-cancel>Cancel</button></div></form>';
    const el = f.querySelector("form");
    el.querySelector("[data-cancel]").addEventListener("click", function () { f.innerHTML = ""; });
    el.addEventListener("submit", async function (e) {
      e.preventDefault();
      const r = await onSubmit(el);
      if (r && !r.ok) { const er = el.querySelector(".dash-alert"); er.textContent = errText(r); er.hidden = false; }
    });
    el.scrollIntoView({ block: "nearest" });
    return el;
  }
  const field = function (id, label, input) { return '<div class="form-field"><label for="' + id + '">' + esc(label) + "</label>" + input + "</div>"; };
  async function after(a, text) { say(text); await Promise.all([loadSummary(), loadRegister()]); openAsset(a.id); }

  function changeForm(a) {
    const el = form(field("cf-loc", "Place", '<select id="cf-loc" class="select-input">' + opts(locs.filter(function (x) { return x.is_active || x.id === a.location_id; }), "No particular place", function (x) { return x.name; }) + "</select>") +
      field("cf-emp", "Held by", '<input id="cf-emp" class="text-input" autocomplete="off" placeholder="Name or employee code" value="' + esc(a.custodian || "") + '" /><div id="cf-emp-list" class="picker-list"></div><input type="hidden" id="cf-emp-id" value="' + esc(a.custodian_employee_id || "") + '" />') +
      field("cf-cond", "Condition", '<select id="cf-cond" class="select-input"><option value="GOOD">Good</option><option value="FAIR">Fair</option><option value="POOR">Poor</option><option value="UNUSABLE">Unusable</option></select>') +
      field("cf-status", "Status", '<select id="cf-status" class="select-input"><option value="IN_USE">In use</option><option value="IN_STORE">In store</option><option value="UNDER_REPAIR">Under repair</option><option value="MISSING">Missing</option></select>') +
      field("cf-on", "Date of the change", '<input id="cf-on" type="date" class="text-input" />') + field("cf-note", "Note", '<input id="cf-note" class="text-input" maxlength="500" />'),
    async function () {
      const body = { location_id: $("cf-loc").value || null, custodian_employee_id: $("cf-emp").value.trim() ? ($("cf-emp-id").value || a.custodian_employee_id) : null,
        condition: $("cf-cond").value, status: $("cf-status").value, on: val("cf-on"), note: val("cf-note") };
      const r = await send(API + "/" + a.id, "PATCH", body);
      if (r.ok) after(a, "Saved " + a.tag + ".");
      return r;
    });
    $("cf-loc").value = a.location_id || ""; $("cf-cond").value = a.condition; $("cf-status").value = a.status;
    picker("cf-emp");
    return el;
  }

  function repairForm(a) {
    form(field("rp-what", "Work done", '<input id="rp-what" class="text-input" maxlength="250" required placeholder="Replaced power supply" />') +
      field("rp-on", "Date", '<input id="rp-on" type="date" class="text-input" />') + field("rp-cost", "Cost (Rs.)", '<input id="rp-cost" type="number" min="0" step="0.01" class="text-input" />') +
      field("rp-vendor", "Done by", '<input id="rp-vendor" class="text-input" maxlength="150" />') +
      field("rp-status", "Status afterwards", '<select id="rp-status" class="select-input"><option value="">Unchanged</option><option value="IN_USE">In use</option><option value="UNDER_REPAIR">Still under repair</option></select>') +
      field("rp-note", "Note", '<input id="rp-note" class="text-input" maxlength="500" />'),
    async function () {
      const r = await send(API + "/" + a.id + "/maintenance", "POST", { summary: $("rp-what").value.trim(), on: val("rp-on"), cost: num("rp-cost"), vendor_name: val("rp-vendor"), status: val("rp-status"), note: val("rp-note") });
      if (r.ok) after(a, "Repair recorded for " + a.tag + ". Pay the bill through Expenses.");
      return r;
    });
  }

  function detailsForm(a) {
    form(field("df-name", "Name", '<input id="df-name" class="text-input" maxlength="150" required />') + field("df-make", "Make / model", '<input id="df-make" class="text-input" maxlength="150" />') +
      field("df-serial", "Serial no.", '<input id="df-serial" class="text-input" maxlength="100" />') + field("df-dept", "Department", '<input id="df-dept" class="text-input" maxlength="100" />') +
      field("df-warranty", "Warranty until", '<input id="df-warranty" type="date" class="text-input" />') + field("df-notes", "Notes", '<input id="df-notes" class="text-input" maxlength="2000" />'),
    async function () {
      const r = await send(API + "/" + a.id, "PATCH", { name: $("df-name").value.trim(), make_model: $("df-make").value.trim(), serial_number: $("df-serial").value.trim(),
        department: $("df-dept").value.trim(), warranty_until: val("df-warranty"), notes: $("df-notes").value.trim() });
      if (r.ok) after(a, "Saved " + a.tag + ".");
      return r;
    });
    $("df-name").value = a.name; $("df-make").value = a.make_model || ""; $("df-serial").value = a.serial_number || ""; $("df-dept").value = a.department || "";
    $("df-warranty").value = a.warranty_until || ""; $("df-notes").value = a.notes || "";
  }

  function disposeForm(a) {
    form('<p class="panel-hint two-span">Book value today is about ' + money(a.depreciation.book_value) + ". The administration approves; then it is taken off the books.</p>" +
      field("dx-method", "How", '<select id="dx-method" class="select-input"><option value="SOLD">Sold</option><option value="SCRAPPED">Scrapped</option><option value="DONATED">Donated</option><option value="LOST">Lost / stolen</option><option value="WRITTEN_OFF">Written off</option></select>') +
      field("dx-on", "Date", '<input id="dx-on" type="date" class="text-input" />') + field("dx-amount", "Money received (Rs.)", '<input id="dx-amount" type="number" min="0" step="0.01" value="0" class="text-input" />') +
      field("dx-buyer", "Buyer", '<input id="dx-buyer" class="text-input" maxlength="150" />') +
      field("dx-pm", "Received by", '<select id="dx-pm" class="select-input"><option value="CASH">Cash</option><option value="BANK_TRANSFER">Bank transfer</option><option value="CHEQUE">Cheque</option><option value="ONLINE">Online</option></select>') +
      field("dx-ref", "Slip / cheque no.", '<input id="dx-ref" class="text-input" maxlength="80" />') + field("dx-reason", "Why", '<input id="dx-reason" class="text-input" maxlength="300" required />'),
    async function () {
      const amount = num("dx-amount") || 0;
      const r = await send(API + "/" + a.id + "/disposal", "POST", { method: $("dx-method").value, disposed_on: val("dx-on"), proceeds: amount, buyer: val("dx-buyer"),
        payment_method: amount ? $("dx-pm").value : null, payment_reference: val("dx-ref"), reason: $("dx-reason").value.trim() });
      if (r.ok) after(a, "Disposal of " + a.tag + " sent for approval.");
      return r;
    });
  }

  function correctForm(a) {
    const d = a.depreciation;
    form('<p class="panel-hint two-span">Only possible while no depreciation has been posted for this asset. The old and new figures are kept in its history.</p>' +
      field("cx-cost", "Cost (Rs.)", '<input id="cx-cost" type="number" min="0" step="0.01" class="text-input" value="' + a.cost + '" />') +
      field("cx-date", "Purchase date", '<input id="cx-date" type="date" class="text-input" value="' + esc(a.acquired_on || "") + '" />') +
      field("cx-from", "Depreciate from", '<input id="cx-from" type="date" class="text-input" value="' + esc(d.starts || "") + '" />') +
      field("cx-method", "Method", '<select id="cx-method" class="select-input"><option value="">Same as the category</option><option value="STRAIGHT_LINE">Straight line</option><option value="REDUCING_BALANCE">Reducing balance</option><option value="NONE">Not depreciated</option></select>') +
      field("cx-life", "Useful life (years)", '<input id="cx-life" type="number" min="0.5" step="0.5" class="text-input" value="' + (d.useful_life_years || "") + '" />') +
      field("cx-rate", "Rate % a year", '<input id="cx-rate" type="number" min="1" max="100" step="0.5" class="text-input" value="' + (d.rate_percent || "") + '" />') +
      field("cx-reason", "Why (kept on record)", '<input id="cx-reason" class="text-input" maxlength="500" required />'),
    async function () {
      const r = await send(API + "/" + a.id + "/correct", "POST", { cost: num("cx-cost"), acquired_on: val("cx-date"), depreciation_from: val("cx-from"), method: val("cx-method"),
        useful_life_years: num("cx-life"), rate_percent: num("cx-rate"), reason: $("cx-reason").value.trim() });
      if (r.ok) after(a, "Corrected " + a.tag + ".");
      return r;
    });
    $("cx-method").value = d.method_override || "";
  }

  // A small staff picker: typing a name or code shows matches; choosing one fills the hidden id.
  function picker(prefix) {
    const q = $(prefix), list = $(prefix + "-list"), hidden = $(prefix + "-id");
    let t = null;
    q.addEventListener("input", function () {
      hidden.value = ""; clearTimeout(t);
      t = setTimeout(async function () {
        list.innerHTML = "";
        if (q.value.trim().length < 2) return;
        const r = await VA.fetchJson(API + "/people?q=" + encodeURIComponent(q.value.trim()), { quiet: true });
        if (!r.ok) return;
        r.body.forEach(function (p) {
          const b = document.createElement("button"); b.type = "button"; b.className = "picker-item";
          b.innerHTML = "<strong>" + esc(p.name) + "</strong><small>" + esc(p.code + (p.department ? " · " + p.department : "")) + "</small>";
          b.addEventListener("click", function () { hidden.value = p.id; q.value = p.name + " (" + p.code + ")"; list.innerHTML = ""; });
          list.appendChild(b);
        });
      }, 300);
    });
  }

  // ------------------------------------------------------------------ add
  function syncAddForm() {
    const opening = $("ad-source").value === "OPENING";
    document.querySelectorAll("[data-opening]").forEach(function (el) { el.hidden = !opening; });
    $("ad-date").previousElementSibling.textContent = opening && $("ad-estimate").checked ? "Purchase date (if known)" : "Purchase date";
    $("ad-cost").previousElementSibling.textContent = opening && $("ad-estimate").checked ? "Value today (Rs.) — each" : "Cost (Rs.) — each";
  }
  $("ad-source").addEventListener("change", syncAddForm);
  $("ad-estimate").addEventListener("change", syncAddForm);
  picker("ad-emp");

  $("ad-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const body = { source: $("ad-source").value, category_id: $("ad-cat").value, name: $("ad-name").value.trim(), make_model: val("ad-make"), serial_number: val("ad-serial"),
      location_id: $("ad-loc").value || null, department: val("ad-dept"), custodian_employee_id: $("ad-emp-id").value || null, condition: $("ad-cond").value,
      acquired_on: val("ad-date"), cost: num("ad-cost"), quantity: num("ad-qty") || 1, tag: val("ad-tag"), warranty_until: val("ad-warranty"), invoice_no: val("ad-invoice"),
      method: val("ad-method"), useful_life_years: num("ad-life"), rate_percent: num("ad-rate"), residual_value: num("ad-residual"), notes: val("ad-notes") };
    if (body.source === "OPENING") { body.cost_is_estimate = $("ad-estimate").checked; body.depreciation_from = val("ad-golive"); }
    if (!body.category_id) return say("Choose a category.", true);
    $("ad-save").disabled = true;
    const r = await send(API, "POST", body);
    $("ad-save").disabled = false;
    if (!r.ok) return say(errText(r), true);
    say("Added " + r.body.map(function (x) { return x.tag; }).join(", ") + ". Print their labels from the register.");
    ["ad-name", "ad-make", "ad-serial", "ad-tag", "ad-emp", "ad-emp-id", "ad-invoice", "ad-notes"].forEach(function (id) { $(id).value = ""; });
    $("ad-qty").value = "1";
    loadSummary(); loadRegister(); loadLists();
  });

  async function loadPurchases() {
    const r = await VA.fetchJson(API + "/from-purchases");
    const box = $("po-list"); box.innerHTML = "";
    if (!r.ok) return;
    r.body.forEach(function (o) {
      const card = document.createElement("div"); card.className = "sh-card";
      card.innerHTML = "<h3>" + esc(o.reference) + " — " + esc(o.title) + '</h3><p class="cc-meta">Received ' + day(o.received_on) + (o.invoice_no ? " · invoice " + esc(o.invoice_no) : "") + "</p>";
      o.lines.forEach(function (line) {
        const row = document.createElement("div"); row.className = "toolbar";
        row.innerHTML = "<span><strong>" + esc(line.description) + "</strong> — " + line.left + " left at " + money(line.unit_cost) + " each</span>" +
          '<input type="number" class="text-input" min="1" max="' + line.left + '" value="' + line.left + '" aria-label="How many" style="max-width:6rem" />' +
          '<select class="select-input" aria-label="Category">' + opts(cats.filter(function (c) { return c.is_active; }), "Category…", function (x) { return x.name; }) + "</select>" +
          '<select class="select-input" aria-label="Place">' + opts(locs.filter(function (x) { return x.is_active; }), "Place…", function (x) { return x.name; }) + "</select>";
        row.appendChild(button("Add to register", "", async function () {
          const inputs = row.querySelectorAll("input, select");
          if (!inputs[1].value) return say("Choose a category for " + line.description + ".", true);
          const res = await send(API + "/from-purchases", "POST", { order_id: o.order_id, purchase_item_id: line.purchase_item_id, quantity: Number(inputs[0].value), category_id: inputs[1].value, location_id: inputs[2].value || null });
          if (!res.ok) return say(errText(res), true);
          say("Added " + res.body.map(function (x) { return x.tag; }).join(", ") + ".");
          loadPurchases(); loadSummary(); loadRegister();
        }));
        card.appendChild(row);
      });
      box.appendChild(card);
    });
    $("po-empty").hidden = r.body.length > 0;
  }

  // ------------------------------------------------------------------ import
  $("im-file").addEventListener("change", function () {
    importText = null; $("im-go").hidden = true; $("im-result").innerHTML = "";
    const f = $("im-file").files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = function () { importText = String(reader.result || ""); };
    reader.readAsText(f);
  });
  async function runImport(commit) {
    if (!importText) return say("Choose the filled-in CSV file first.", true);
    const r = await send(API + "/import", "POST", { csv: importText, go_live: val("im-golive"), commit: commit });
    if (!r.ok) return say(errText(r), true);
    const x = r.body;
    $("im-result").innerHTML = '<p class="' + (x.errors.length ? "dash-alert error" : "panel-hint") + '">' + x.rows + " rows: " + x.good + " good, " + x.errors.length + " with problems. Cost " + money(x.total_cost) +
      ", book value " + money(x.total_book_value) + (x.new_places.length ? ". New places: " + esc(x.new_places.join(", ")) : "") + (x.imported ? ". <strong>" + x.imported + " added.</strong>" : "") + "</p>" +
      (x.errors.length ? "<ul>" + x.errors.map(function (er) { return "<li>Row " + er.row + (er.name ? " (" + esc(er.name) + ")" : "") + ": " + esc(er.problems.join("; ")) + "</li>"; }).join("") + "</ul>" : "");
    $("im-go").hidden = !!(x.errors.length || x.imported || !x.good);
    if (x.imported) { importText = null; $("im-file").value = ""; loadSummary(); loadRegister(); loadLists(); }
  }
  $("im-check").addEventListener("click", function () { runImport(false); });
  $("im-go").addEventListener("click", function () { runImport(true); });
  $("im-template").addEventListener("click", function (e) { e.preventDefault(); VA.downloadFile(this.href, "assets-import-template.csv", "template"); });

  // ------------------------------------------------------------------ depreciation
  function periodEnd() {
    const v = $("dp-month").value;
    if (!v) return null;
    const y = Number(v.slice(0, 4)), m = Number(v.slice(5, 7));
    const last = new Date(y, m, 0).getDate();
    return v + "-" + String(last).padStart(2, "0");
  }
  $("dp-month").addEventListener("change", function () { $("dp-post").disabled = true; $("dp-preview-box").innerHTML = ""; });
  $("dp-preview").addEventListener("click", async function () {
    const end = periodEnd(); if (!end) return say("Choose a month.", true);
    const r = await send(API + "/depreciation/preview", "POST", { period_end: end });
    if (!r.ok) return say(errText(r), true);
    const p = r.body;
    $("dp-preview-box").innerHTML = '<p class="panel-hint">' + p.lines.length + " assets, total " + money(p.total) + (p.after_run ? " (after the run up to " + day(p.after_run) + ")" : "") + ".</p>" +
      '<div class="table-scroll"><table class="dash-table"><thead><tr><th>Tag</th><th>Asset</th><th>Period</th><th>Method</th><th class="num">Book value</th><th class="num">Depreciation</th><th class="num">After</th></tr></thead><tbody>' +
      p.lines.map(function (l) { return "<tr><td>" + esc(l.tag) + "</td><td>" + esc(l.name) + "</td><td>" + day(l.from) + " – " + day(l.to) + "</td><td>" + esc(l.method) + '</td><td class="num">' + money(l.book_value_before) +
        '</td><td class="num">' + money(l.amount) + '</td><td class="num">' + money(l.book_value_after) + "</td></tr>"; }).join("") + "</tbody></table></div>";
    $("dp-post").disabled = !p.lines.length;
  });
  $("dp-post").addEventListener("click", async function () {
    const end = periodEnd();
    if (!(await VA.confirm("Post depreciation up to " + day(end) + "? It can be undone only by the administration, and only while it is the latest run.", "Post"))) return;
    const r = await send(API + "/depreciation/runs", "POST", { period_end: end });
    if (!r.ok) return say(errText(r), true);
    say("Posted " + money(r.body.total) + " for " + r.body.assets + " assets up to " + day(r.body.period_end) + ".");
    $("dp-post").disabled = true; $("dp-preview-box").innerHTML = "";
    loadRuns(); loadSummary(); loadRegister();
  });
  async function loadRuns() {
    const r = await VA.fetchJson(API + "/depreciation/runs");
    if (!r.ok) return;
    const tb = $("dp-runs"); tb.innerHTML = "";
    r.body.forEach(function (x) {
      const tr = document.createElement("tr");
      tr.innerHTML = "<td>" + day(x.period_end) + '</td><td class="num">' + x.assets + '</td><td class="num">' + money(x.total) + "</td><td>" + esc(x.posted_by || "") + "<br><small>" + esc(VA.formatDateTime(x.posted_at)) + "</small></td><td>" +
        (x.reversed ? '<span class="status-pill dropped">Reversed</span><br><small>' + esc((x.reversed_by || "") + ": " + (x.reverse_reason || "")) + "</small>" : '<span class="status-pill active">Posted</span>') + '</td><td class="row-actions"></td>';
      if (canApprove && x.can_reverse) tr.querySelector(".row-actions").appendChild(button("Reverse", "danger-btn", async function () {
        const reason = await VA.ask({ title: "Reverse the run up to " + day(x.period_end) + "?", label: "Why (kept on record)", ok: "Reverse", danger: true, min: 10 });
        if (!reason) return;
        const res = await send(API + "/depreciation/runs/" + x.id + "/reverse", "POST", { reason: reason });
        if (!res.ok) return say(errText(res), true);
        say("Run reversed."); loadRuns(); loadSummary(); loadRegister();
      }));
      tb.appendChild(tr);
    });
    $("dp-runs-empty").hidden = r.body.length > 0;
  }

  // ------------------------------------------------------------------ disposals
  async function loadDisposals() {
    const r = await VA.fetchJson(API + "/disposals");
    if (!r.ok) return;
    const tb = $("ds-rows"); tb.innerHTML = "";
    r.body.forEach(function (x) {
      const tr = document.createElement("tr");
      const bv = x.book_value != null ? x.book_value : x.book_value_now;
      tr.innerHTML = "<td>" + esc(VA.formatDateTime(x.requested_at)) + "<br><small>" + esc(x.requested_by || "") + "</small></td><td><strong>" + esc(x.tag) + "</strong> " + esc(x.name) + "<br><small>" + esc(x.reason) + "</small></td><td>" +
        esc(words(x.method)) + (x.buyer ? "<br><small>" + esc(x.buyer) + "</small>" : "") + "</td><td>" + day(x.disposed_on) + '</td><td class="num">' + money(bv) + (x.book_value == null ? "<br><small>today</small>" : "") +
        '</td><td class="num">' + money(x.proceeds) + '</td><td class="num">' + (x.gain_or_loss == null ? "—" : money(x.gain_or_loss)) + "</td><td>" + esc(words(x.status)) +
        (x.decided_by ? "<br><small>" + esc(x.decided_by + (x.decision_note ? ": " + x.decision_note : "")) + "</small>" : "") + '</td><td class="row-actions"></td>';
      const cell = tr.querySelector(".row-actions");
      cell.appendChild(button("Open", "secondary-btn", function () { openAsset(x.asset_id); }));
      if (canApprove && x.status === "REQUESTED") {
        cell.appendChild(button("Approve", "", async function () {
          if (!(await VA.confirm("Take " + x.tag + " off the books on " + day(x.disposed_on) + "?" + (x.proceeds ? " " + money(x.proceeds) + " will be recorded as income." : ""), "Approve"))) return;
          const res = await send(API + "/disposals/" + x.id + "/decide", "POST", { approve: true });
          if (!res.ok) return say(errText(res), true);
          say(x.tag + " disposed of. Book value " + money(res.body.book_value) + "."); loadDisposals(); loadSummary(); loadRegister();
        }));
        cell.appendChild(button("Reject", "danger-btn", async function () {
          const note = await VA.ask({ title: "Reject this disposal?", label: "Why", ok: "Reject", danger: true });
          if (!note) return;
          const res = await send(API + "/disposals/" + x.id + "/decide", "POST", { approve: false, note: note });
          if (!res.ok) return say(errText(res), true);
          loadDisposals(); loadSummary();
        }));
      }
      tb.appendChild(tr);
    });
    $("ds-empty").hidden = r.body.length > 0;
  }

  // ------------------------------------------------------------------ physical checks
  async function loadChecks() {
    const r = await VA.fetchJson(API + "/checks");
    if (!r.ok) return;
    const tb = $("ck-rows"); tb.innerHTML = "";
    r.body.forEach(function (c) {
      const counts = Object.keys(c.counts).map(function (k) { return (RESULT[k] || k) + ": " + c.counts[k]; }).join(", ");
      const tr = document.createElement("tr");
      tr.innerHTML = "<td>" + esc(VA.formatDateTime(c.started_at)) + "</td><td>" + esc(c.title) + "</td><td>" + esc(c.location) + "</td><td>" + esc(counts) + "</td><td>" + esc(words(c.status)) + '</td><td class="row-actions"></td>';
      tr.querySelector(".row-actions").appendChild(button(c.status === "OPEN" ? "Continue" : "View", "secondary-btn", function () { showCheck(c.id); }));
      tb.appendChild(tr);
    });
    $("ck-empty").hidden = r.body.length > 0;
  }
  async function showCheck(id) {
    const r = await VA.fetchJson(API + "/checks/" + id);
    if (!r.ok) return say(errText(r), true);
    const c = r.body; openCheck = c;
    $("ck-open").hidden = false;
    $("ck-open-title").textContent = c.title;
    $("ck-open-meta").textContent = c.location + " · started by " + (c.started_by || "") + " · " + Object.keys(c.counts).map(function (k) { return (RESULT[k] || k) + ": " + c.counts[k]; }).join(", ") +
      (c.status === "CLOSED" ? " · closed " + VA.formatDateTime(c.closed_at) : "");
    $("ck-scan-form").hidden = !canManage || c.status !== "OPEN";
    const tb = $("ck-lines"); tb.innerHTML = "";
    c.lines.forEach(function (l) {
      const tr = document.createElement("tr");
      tr.innerHTML = "<td>" + esc(l.tag) + "</td><td>" + esc(l.name) + (l.expected ? "" : " <small>(not on the list)</small>") + "</td><td>" + esc(l.expected_location || "—") + "</td><td>" + esc(RESULT[l.result] || l.result) +
        "</td><td>" + esc(l.found_location || "") + "</td><td>" + esc(words(l.condition || "")) + '</td><td class="row-actions"></td>';
      if (canManage && c.status === "OPEN" && l.result === "NOT_CHECKED") tr.querySelector(".row-actions").appendChild(button("Seen", "secondary-btn", function () { mark({ asset_id: l.asset_id }); }));
      tb.appendChild(tr);
    });
    if (c.status === "OPEN" && canManage) $("ck-scan").focus();
  }
  async function mark(body) {
    body.condition = $("ck-cond").value || null;
    const r = await send(API + "/checks/" + openCheck.id + "/mark", "POST", body);
    if (!r.ok) { VA.toast(errText(r), "error"); return; }
    VA.toast(r.body.tag + ": " + (RESULT[r.body.result] || r.body.result));
    showCheck(openCheck.id);
  }
  $("ck-scan-form").addEventListener("submit", function (e) { e.preventDefault(); const t = $("ck-scan").value.trim(); $("ck-scan").value = ""; if (t) mark({ tag: t }); });
  $("ck-start").addEventListener("click", async function () {
    const r = await send(API + "/checks", "POST", { location_id: $("ck-loc").value || null, title: val("ck-title") });
    if (!r.ok) return say(errText(r), true);
    $("ck-title").value = ""; loadChecks(); showCheck(r.body.id); loadSummary();
  });
  $("ck-close").addEventListener("click", async function () {
    if (!(await VA.confirm("Close this check? Everything on the list that was not seen will be marked Missing.", "Close the check"))) return;
    const r = await send(API + "/checks/" + openCheck.id + "/close", "POST", { apply_moves: $("ck-moves").checked });
    if (!r.ok) return say(errText(r), true);
    const x = r.body;
    say("Check closed: " + x.found + " found, " + x.wrong_place + " in the wrong place (" + x.moved + " moved), " + x.missing + " missing. Print the result from Reports > Physical check result.");
    showCheck(openCheck.id); loadChecks(); loadSummary(); loadRegister();
  });

  // ------------------------------------------------------------------ categories and places
  function renderSetup() {
    const ct = $("ct-rows"); ct.innerHTML = "";
    cats.forEach(function (c) {
      const how = c.method === "STRAIGHT_LINE" ? "Straight line, " + c.useful_life_years + " years" : c.method === "REDUCING_BALANCE" ? "Reducing balance, " + c.rate_percent + "% a year" : "Not depreciated";
      const tr = document.createElement("tr");
      tr.innerHTML = "<td>" + esc(c.code) + "</td><td>" + esc(c.name) + (c.is_active ? "" : ' <span class="status-pill dropped">Off</span>') + "</td><td>" + esc(how) + (c.residual_percent ? ", residual " + c.residual_percent + "%" : "") +
        '</td><td class="num">' + c.assets + '</td><td class="row-actions"></td>';
      if (canManage) {
        tr.querySelector(".row-actions").appendChild(button("Change rate", "secondary-btn", async function () {
          if (c.method === "NONE") return say("This category is not depreciated.", true);
          const sl = c.method === "STRAIGHT_LINE";
          const v = await VA.ask({ title: c.name, intro: "Applies to depreciation posted from now on; what is already posted does not change.", label: sl ? "Useful life (years)" : "Rate % a year", input: "number", value: sl ? c.useful_life_years : c.rate_percent, ok: "Save" });
          if (v == null) return;
          const res = await send(API + "/categories/" + c.id, "PATCH", sl ? { useful_life_years: Number(v) } : { rate_percent: Number(v) });
          if (!res.ok) return say(errText(res), true);
          loadLists();
        }));
        tr.querySelector(".row-actions").appendChild(button(c.is_active ? "Switch off" : "Switch on", "secondary-btn", async function () {
          const res = await send(API + "/categories/" + c.id, "PATCH", { is_active: !c.is_active });
          if (!res.ok) return say(errText(res), true);
          loadLists();
        }));
      }
      ct.appendChild(tr);
    });
    const lc = $("lc-rows"); lc.innerHTML = "";
    locs.forEach(function (l) {
      const tr = document.createElement("tr");
      tr.innerHTML = "<td>" + esc(l.name) + (l.is_active ? "" : ' <span class="status-pill dropped">Off</span>') + "</td><td>" + esc(l.building || "") + "</td><td>" + esc(l.department || "") + '</td><td class="num">' + l.assets + '</td><td class="row-actions"></td>';
      const cell = tr.querySelector(".row-actions");
      cell.appendChild(button("Labels", "secondary-btn", function () { VA.downloadFile(API + "/labels.pdf?location_id=" + l.id, "asset-labels.pdf", "labels"); }));
      if (canManage) {
        cell.appendChild(button("Rename", "secondary-btn", async function () {
          const v = await VA.ask({ title: "Rename " + l.name, label: "New name", value: l.name, ok: "Save", min: 2 });
          if (!v) return;
          const res = await send(API + "/locations/" + l.id, "PATCH", { name: v });
          if (!res.ok) return say(errText(res), true);
          loadLists(); loadRegister();
        }));
        cell.appendChild(button(l.is_active ? "Switch off" : "Switch on", "secondary-btn", async function () {
          const res = await send(API + "/locations/" + l.id, "PATCH", { is_active: !l.is_active });
          if (!res.ok) return say(errText(res), true);
          loadLists();
        }));
      }
      lc.appendChild(tr);
    });
  }
  function syncCatForm() { const m = $("ct-method").value; $("ct-life").parentNode.hidden = m !== "STRAIGHT_LINE"; $("ct-rate").parentNode.hidden = m !== "REDUCING_BALANCE"; $("ct-residual").parentNode.hidden = m === "NONE"; }
  $("ct-method").addEventListener("change", syncCatForm);
  $("ct-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const r = await send(API + "/categories", "POST", { code: $("ct-code").value.trim().toUpperCase(), name: $("ct-name").value.trim(), method: $("ct-method").value,
      useful_life_years: $("ct-method").value === "STRAIGHT_LINE" ? num("ct-life") : null, rate_percent: $("ct-method").value === "REDUCING_BALANCE" ? num("ct-rate") : null, residual_percent: num("ct-residual") || 0 });
    if (!r.ok) return say(errText(r), true);
    say("Category " + r.body.name + " added."); $("ct-code").value = ""; $("ct-name").value = ""; loadLists();
  });
  $("ct-suggested").addEventListener("click", async function () {
    const r = await send(API + "/categories/suggested", "POST", {});
    if (!r.ok) return say(errText(r), true);
    say(r.body.added.length ? "Added: " + r.body.added.join(", ") + ". Check the rates with your auditor before posting depreciation." : "The usual categories are already there.");
    loadLists();
  });
  $("lc-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const r = await send(API + "/locations", "POST", { name: $("lc-name").value.trim(), building: val("lc-building"), department: val("lc-dept") });
    if (!r.ok) return say(errText(r), true);
    ["lc-name", "lc-building", "lc-dept"].forEach(function (id) { $(id).value = ""; });
    loadLists();
  });

  // ------------------------------------------------------------------ start
  document.querySelectorAll(".portal-tab").forEach(function (t) {
    t.addEventListener("click", function () {
      const name = t.getAttribute("data-tab");
      if (name === "add") loadPurchases();
      if (name === "depreciation") loadRuns();
      if (name === "disposals") loadDisposals();
      if (name === "checks") loadChecks();
    });
  });

  async function openFromHash() {
    const m = /tag=([^&]+)/.exec(location.hash || "");
    if (!m) return;
    const r = await VA.fetchJson(API + "/lookup?tag=" + encodeURIComponent(decodeURIComponent(m[1])));
    if (!r.ok) return say(errText(r), true);
    openAsset(r.body.id);
  }
  window.addEventListener("hashchange", openFromHash);

  document.addEventListener("vistadmin:ready", async function (event) {
    const perms = (event.detail && event.detail.me && event.detail.me.permissions) || [];
    canManage = perms.indexOf("asset.manage") !== -1;
    canApprove = perms.indexOf("asset.approve") !== -1;
    document.querySelectorAll("[data-manage]").forEach(function (el) { el.hidden = !canManage; });
    syncAddForm(); syncCatForm();
    await loadLists();
    await Promise.all([loadSummary(), loadRegister()]);
    if (!cats.length && canManage) { say("Start here: add the categories (the usual ones in one click) and the places, then add assets."); openTab("setup"); }
    openFromHash();
  });
})();
