(function () {
  "use strict";

  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/accounts";
  const $ = function (id) { return document.getElementById(id); };
  const msg = function (body, fallback) { return body && body.error && body.error.message ? body.error.message : fallback; };
  const json = function (method, data) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }; };
  const money = function (v) { return "Rs. " + Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }); };
  const day = function (iso) { return iso ? new Date(String(iso).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "\u2014"; };
  const ymd = function (d) { const p = function (n) { return String(n).padStart(2, "0"); }; return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()); };
  const tile = function (a, b, c, tone) { return '<div class="kpi ' + (tone || "") + '"><span class="kpi-label">' + a + '</span><strong class="kpi-value">' + b + '</strong><span class="kpi-sub">' + (c || "") + "</span></div>"; };
  const METHOD = { CASH: "Cash", BANK_TRANSFER: "Bank transfer", CHEQUE: "Cheque", ONLINE: "Online" };
  const STATUS = { PENDING_APPROVAL: ["Waiting for approval", "pending"], APPROVED: ["Approved", "active"], REJECTED: ["Rejected", "dropped"], VOIDED: ["Voided", ""] };
  const pill = function (s) { const x = STATUS[s] || [s, ""]; return '<span class="status-pill ' + x[1] + '">' + esc(x[0]) + "</span>"; };
  const timer = {};
  const debounce = function (key, fn) { clearTimeout(timer[key]); timer[key] = setTimeout(fn, 300); };

  let cfg = { expense_approval_limit: 50000, financial_year_start_month: 7, today: ymd(new Date()) };
  let isApprover = false, period = "month", exStatus = "", bkKind = "", cats = { EXPENSE: [], INCOME: [] }, vendorEdit = null, currentExpense = null;

  // ------------------------------------------------------------------ the period for the overview
  function range(p) {
    const t = new Date(cfg.today + "T00:00:00"), y = t.getFullYear(), m = t.getMonth(), fy = cfg.financial_year_start_month - 1;
    const startYear = m >= fy ? y : y - 1;
    if (p === "month") return [ymd(new Date(y, m, 1)), cfg.today, "This month"];
    if (p === "30" || p === "90") return [ymd(new Date(t.getTime() - (parseInt(p, 10) - 1) * 86400000)), cfg.today, "The last " + p + " days"];
    if (p === "fy") return [ymd(new Date(startYear, fy, 1)), cfg.today, "This financial year (from " + new Date(startYear, fy, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" }) + ")"];
    if (p === "lastfy") return [ymd(new Date(startYear - 1, fy, 1)), ymd(new Date(startYear, fy, 0)), "Last financial year"];
    return [null, null, "All time"];
  }

  async function loadOverview() {
    const r0 = range(period), q = new URLSearchParams();
    if (r0[0]) q.set("from_date", r0[0]);
    if (r0[1]) q.set("to_date", r0[1]);
    const r = await VA.fetchJson(API + "/summary?" + q.toString());
    if (!r.ok) return;
    const s = r.body;
    $("ov-range").textContent = r0[2] + (r0[0] ? ": " + day(r0[0]) + " to " + day(r0[1]) : "");
    $("ov-tiles").innerHTML = '<div class="kpi-grid kpi-grid-6">' + tile("Fees collected", money(s.fees_collected), "from students", "good") + tile("Other income", money(s.other_income), "donations, canteen, rent...") + tile("Total income", money(s.total_income), "") +
      tile("Expenses", money(s.expenses), "approved", "warn") + tile(s.net >= 0 ? "Surplus" : "Deficit", money(Math.abs(s.net)), s.net >= 0 ? "income is more than spending" : "spending is more than income", s.net >= 0 ? "good" : "bad") +
      tile("Waiting for approval", s.pending_expenses.count, money(s.pending_expenses.amount), s.pending_expenses.count ? "warn" : "") + "</div>";
    const P = Charts.palette();
    Charts.lineChart($("ov-trend"), s.monthly.map(function (m) { return { label: new Date(m.month + "-01T00:00:00").toLocaleDateString(undefined, { month: "short" }), income: Math.round(m.income / 100) / 10, expenses: Math.round(m.expenses / 100) / 10 }; }),
      [{ key: "income", label: "Income (Rs. thousand)", color: P.green }, { key: "expenses", label: "Expenses (Rs. thousand)", color: P.red }], { title: "Income and expenses per month, in Rs. thousand", empty: "Nothing recorded yet." });
    Charts.hbars($("ov-exp-cat"), s.expenses_by_category.map(function (c) { return { label: c.category, count: Math.round(c.amount) }; }), { empty: "No expenses in this period." });
    Charts.hbars($("ov-inc-cat"), [{ label: "Student fees", count: Math.round(s.fees_collected) }].concat(s.income_by_category.map(function (c) { return { label: c.category, count: Math.round(c.amount) }; })).filter(function (x) { return x.count > 0; }), { empty: "No income in this period." });
    $("ov-vendors").innerHTML = s.top_vendors.length ? s.top_vendors.map(function (v) { return '<div class="auth-row"><span class="grow"><strong>' + esc(v.vendor) + "</strong><small>" + v.count + " payment" + (v.count !== 1 ? "s" : "") + '</small></span><span class="sched"><b>' + money(v.amount) + "</b></span></div>"; }).join("") : '<p class="cc-meta">No vendor spending in this period.</p>';
  }
  $("ov-period").querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { $("ov-period").querySelectorAll("button").forEach(function (x) { x.classList.toggle("active", x === b); }); period = b.getAttribute("data-p"); loadOverview(); }); });

  // ------------------------------------------------------------------ the day book
  async function loadBook() {
    const p = new URLSearchParams({ limit: "500" });
    if (bkKind) p.set("kind", bkKind);
    if ($("bk-q").value.trim()) p.set("q", $("bk-q").value.trim());
    if ($("bk-from").value) p.set("from_date", $("bk-from").value);
    if ($("bk-to").value) p.set("to_date", $("bk-to").value);
    const r = await VA.fetchJson(API + "/book?" + p.toString());
    if (!r.ok) return;
    let inn = 0, out = 0;
    $("bk-body").innerHTML = r.body.map(function (x) {
      if (x.kind === "INCOME") inn += x.amount; else out += x.amount;
      return "<tr><td>" + esc(day(x.date)) + "</td><td>" + esc(x.reference) + "</td><td><strong>" + esc(x.title) + "</strong><small>" + esc([x.party, METHOD[x.payment_method]].filter(Boolean).join(" \u00b7 ")) + "</small></td><td>" + esc(x.category) +
        '</td><td class="num ok">' + (x.kind === "INCOME" ? money(x.amount) : "") + '</td><td class="num bad">' + (x.kind === "EXPENSE" ? money(x.amount) : "") + "</td></tr>";
    }).join("");
    $("bk-empty").hidden = r.body.length > 0;
    $("bk-totals").innerHTML = r.body.length ? r.body.length + " entries \u00b7 money in <b>" + money(inn) + "</b> \u00b7 money out <b>" + money(out) + "</b> \u00b7 " + (inn - out >= 0 ? "net +" : "net \u2212") + money(Math.abs(inn - out)) : "";
  }
  $("bk-kind").querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { $("bk-kind").querySelectorAll("button").forEach(function (x) { x.classList.toggle("active", x === b); }); bkKind = b.getAttribute("data-k"); loadBook(); }); });
  ["bk-from", "bk-to"].forEach(function (id) { $(id).addEventListener("change", loadBook); });
  $("bk-q").addEventListener("input", function () { debounce("bk", loadBook); });

  // ------------------------------------------------------------------ expenses
  async function loadExpenses() {
    const p = new URLSearchParams({ limit: "200" });
    if (exStatus) p.set("status", exStatus);
    if ($("ex-q").value.trim()) p.set("q", $("ex-q").value.trim());
    const r = await VA.fetchJson(API + "/expenses?" + p.toString());
    if (!r.ok) return;
    const body = $("ex-body"); body.innerHTML = "";
    r.body.forEach(function (e) {
      const tr = document.createElement("tr");
      tr.className = "clickrow";
      tr.innerHTML = "<td>" + esc(day(e.expense_date)) + "</td><td>" + esc(e.reference) + "</td><td><strong>" + esc(e.title) + "</strong><small>" + esc(METHOD[e.payment_method]) + (e.attachments.length ? " \u00b7 " + e.attachments.length + " receipt" + (e.attachments.length > 1 ? "s" : "") : "") + "</small></td><td>" +
        esc(e.category_name) + "</td><td>" + esc(e.vendor_name || "\u2014") + '</td><td class="num"><b>' + money(e.amount) + "</b></td><td>" + pill(e.status) + "</td>";
      tr.addEventListener("click", function () { openExpense(e.id); });
      body.appendChild(tr);
    });
    $("ex-empty").hidden = r.body.length > 0;
    const w = await VA.fetchJson(API + "/expenses?status=PENDING_APPROVAL&limit=200", { quiet: true });
    if (w.ok) $("fn-tab-expenses").textContent = "Expenses" + (isApprover && w.body.length ? " \u00b7 " + w.body.length : "");
  }
  $("ex-status").querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { $("ex-status").querySelectorAll("button").forEach(function (x) { x.classList.toggle("active", x === b); }); exStatus = b.getAttribute("data-s"); loadExpenses(); }); });
  $("ex-q").addEventListener("input", function () { debounce("ex", loadExpenses); });

  function fillSelect(sel, items, first) { sel.innerHTML = (first || "") + items.map(function (c) { return '<option value="' + c.id + '">' + esc(c.name) + "</option>"; }).join(""); }
  function fail(id, t) { $(id).textContent = t; $(id).hidden = false; }

  $("ex-new").addEventListener("click", function () {
    $("ex-form").reset(); $("ex-date").value = cfg.today; $("ex-error").hidden = true; refHint(); $("ex-modal").showModal();
  });
  const refHint = function () { const need = ["CHEQUE", "BANK_TRANSFER"].indexOf($("ex-method").value) !== -1; $("ex-ref-hint").textContent = need ? "(needed)" : "(optional)"; };
  $("ex-method").addEventListener("change", refHint);
  $("ex-close").addEventListener("click", function () { $("ex-modal").close(); });
  $("ex-save").addEventListener("click", async function () {
    $("ex-error").hidden = true;
    if (!$("ex-title-in").value.trim()) return fail("ex-error", "Say what the expense was for.");
    if (!($("ex-amount").value > 0)) return fail("ex-error", "Enter the amount.");
    if (!$("ex-date").value) return fail("ex-error", "Choose the date.");
    const fd = new FormData();
    fd.append("category_id", $("ex-cat").value); fd.append("title", $("ex-title-in").value.trim()); fd.append("amount", $("ex-amount").value); fd.append("expense_date", $("ex-date").value); fd.append("payment_method", $("ex-method").value);
    if ($("ex-vendor").value) fd.append("vendor_id", $("ex-vendor").value);
    if ($("ex-desc").value.trim()) fd.append("description", $("ex-desc").value.trim());
    if ($("ex-ref").value.trim()) fd.append("reference_no", $("ex-ref").value.trim());
    Array.prototype.forEach.call($("ex-files").files, function (f) { fd.append("files", f); });
    const r = await VA.fetchJson(API + "/expenses", { method: "POST", body: fd });
    if (!r.ok) return fail("ex-error", msg(r.body, "Could not record it."));
    $("ex-modal").close();
    VA.toast(r.body.status === "PENDING_APPROVAL" ? "Recorded. It is above Rs. " + Number(cfg.expense_approval_limit).toLocaleString() + ", so it is waiting for the administration." : "Expense recorded: " + r.body.reference);
    loadExpenses(); loadOverview(); loadBook();
  });

  // ------------------------------------------------------------------ one expense
  async function openExpense(id) {
    const r = await VA.fetchJson(API + "/expenses/" + id);
    if (!r.ok) return VA.toast(msg(r.body, "Could not open it."), "error");
    const e = r.body; currentExpense = e;
    $("ed-badges").innerHTML = '<span class="pill">' + esc(e.reference) + "</span>" + pill(e.status);
    $("ed-title").textContent = e.title;
    $("ed-meta").textContent = money(e.amount) + " \u00b7 " + day(e.expense_date) + " \u00b7 " + METHOD[e.payment_method] + (e.reference_no ? " \u00b7 ref " + e.reference_no : "");
    $("ed-body").innerHTML = '<div class="cc-grid"><div><span class="cc-meta">Category</span><br>' + esc(e.category_name) + '</div><div><span class="cc-meta">Vendor</span><br>' + esc(e.vendor_name || "\u2014") + '</div><div><span class="cc-meta">Recorded by</span><br>' + esc(e.recorded_by_name || "\u2014") + "</div></div>" +
      (e.description ? "<p>" + esc(e.description) + "</p>" : "") +
      (e.decision_note ? '<div class="cc-note ' + (e.status === "REJECTED" ? "rejected" : "") + '"><strong>' + (e.status === "REJECTED" ? "Rejected" : "Decision note") + (e.decided_by_name ? " by " + esc(e.decided_by_name) : "") + "</strong><span>" + esc(e.decision_note) + "</span></div>" : "") +
      (e.status === "VOIDED" ? '<div class="cc-note rejected"><strong>Voided</strong><span>' + esc(e.void_reason || "") + "</span></div>" : "");
    $("ed-files").innerHTML = e.attachments.length ? e.attachments.map(function (a) { return '<div class="auth-row"><span class="grow"><strong>' + esc(a.original_name) + "</strong><small>" + Math.max(1, Math.round(a.size_bytes / 1024)) + ' KB</small></span><a class="role-btn small-btn secondary-btn" target="_blank" rel="noopener" href="' + API + "/expenses/attachments/" + a.id + '/file">Open</a></div>'; }).join("") : '<p class="cc-meta">No receipts attached.</p>';
    const bar = $("ed-actions"); bar.innerHTML = ""; $("ed-error").hidden = true;
    const add = function (label, cls, fn) { const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn " + cls; b.textContent = label; b.addEventListener("click", fn); bar.appendChild(b); };
    if (isApprover && e.status === "PENDING_APPROVAL") { add("Approve", "", function () { decide("approve", null, "Approved."); }); add("Reject\u2026", "danger-btn", function () { askReason("Reject this expense", "The person who recorded it is told why.", "Reject", true).then(function (t) { if (t) decide("reject", t, "Rejected."); }); }); }
    if (isApprover && e.status === "APPROVED" && e.source === "MANUAL") add("Void\u2026", "danger-btn", function () { askReason("Void this expense", "It stays on record but stops counting in the books. Use it for an entry made by mistake.", "Void it", true).then(function (t) { if (t) decide("void", t, "Voided."); }); });
    if (!$("ed-modal").open) $("ed-modal").showModal();
  }
  async function decide(what, reason, done) {
    const r = await VA.fetchJson(API + "/expenses/" + currentExpense.id + "/" + what, reason ? json("POST", { reason: reason }) : { method: "POST" });
    if (!r.ok) { $("ed-error").textContent = msg(r.body, "That did not work."); $("ed-error").hidden = false; return; }
    VA.toast(done); await openExpense(currentExpense.id); loadExpenses(); loadOverview(); loadBook();
  }
  $("ed-close").addEventListener("click", function () { $("ed-modal").close(); });

  let asking = null;
  function askReason(title, intro, ok, danger) {
    return new Promise(function (resolve) {
      $("fn-ask-title").textContent = title; $("fn-ask-intro").textContent = intro; $("fn-ask-text").value = ""; $("fn-ask-error").hidden = true;
      $("fn-ask-ok").textContent = ok; $("fn-ask-ok").className = danger ? "danger-btn" : "role-btn"; asking = resolve; $("fn-ask").showModal();
    });
  }
  $("fn-ask-ok").addEventListener("click", function () {
    const t = $("fn-ask-text").value.trim();
    if (t.length < 3) { $("fn-ask-error").textContent = "Give a short reason."; $("fn-ask-error").hidden = false; return; }
    const r = asking; asking = null; $("fn-ask").close(); r(t);
  });
  $("fn-ask-cancel").addEventListener("click", function () { $("fn-ask").close(); });
  $("fn-ask").addEventListener("close", function () { if (asking) { const r = asking; asking = null; r(null); } });


  // ------------------------------------------------------------------ purchasing (the management side)
  const PAPI = "/api/v1/purchases";
  const PSTATUS = { REQUESTED: ["Waiting for approval", "pending"], APPROVED: ["Approved: to order", "enrolled"], REJECTED: ["Not approved", "dropped"], ORDERED: ["On order", "enrolled"], RECEIVED: ["Arrived: to pay", "active"], PAID: ["Paid", "active"], CANCELLED: ["Cancelled", ""] };
  const ppill = function (s) { const x = PSTATUS[s] || [s, ""]; return '<span class="status-pill ' + x[1] + '">' + esc(x[0]) + "</span>"; };
  const PSTEPS = ["Asked", "Approved", "Ordered", "Arrived", "Paid"], PSTEP_OF = { REQUESTED: 0, APPROVED: 1, ORDERED: 2, RECEIVED: 3, PAID: 4 };
  let poStatus = "", po = null, poVendors = [];

  async function loadPurchasing() {
    const s = await VA.fetchJson(PAPI + "/summary", { quiet: true });
    if (s.ok) {
      const x = s.body;
      $("po-tiles").innerHTML = '<div class="kpi-grid kpi-grid-6">' + tile("Waiting for approval", x.waiting_for_approval, "", x.waiting_for_approval && isApprover ? "warn" : "") + tile("To order", x.to_order, "approved") + tile("On order", x.on_order, "") +
        tile("Arrived: to pay", x.to_pay, "") + tile("Owed to vendors", money(x.owed_to_vendors), "received, not yet paid", x.owed_to_vendors ? "bad" : "good") + tile("Paid", money(x.paid_total), "in total", "good") + "</div>";
      $("fn-tab-purchasing").textContent = "Purchasing" + (isApprover && x.waiting_for_approval ? " \u00b7 " + x.waiting_for_approval : "");
    }
    const p = new URLSearchParams({ limit: "200" });
    if (poStatus) p.set("status", poStatus);
    if ($("po-q").value.trim()) p.set("q", $("po-q").value.trim());
    const r = await VA.fetchJson(PAPI + "?" + p.toString());
    if (!r.ok) return;
    const body = $("po-body"); body.innerHTML = "";
    r.body.forEach(function (o) {
      const tr = document.createElement("tr"); tr.className = "clickrow";
      tr.innerHTML = "<td>" + esc(o.reference) + "<small>" + esc(day(o.created_at)) + "</small></td><td><strong>" + esc(o.title) + "</strong><small>" + o.items.length + " item" + (o.items.length !== 1 ? "s" : "") + (o.vendor_name ? " \u00b7 " + esc(o.vendor_name) : "") + "</small></td><td>" + esc(o.requested_by_name || "\u2014") +
        '</td><td class="num"><b>' + money(o.actual_total !== null ? o.actual_total : o.estimated_total) + "</b>" + (o.actual_total === null ? "<small>estimated</small>" : "") + "</td><td>" + ppill(o.status) + "</td>";
      tr.addEventListener("click", function () { openPurchase(o.id); });
      body.appendChild(tr);
    });
    $("po-empty").hidden = r.body.length > 0;
  }
  $("po-status").querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { $("po-status").querySelectorAll("button").forEach(function (x) { x.classList.toggle("active", x === b); }); poStatus = b.getAttribute("data-s"); loadPurchasing(); }); });
  $("po-q").addEventListener("input", function () { debounce("po", loadPurchasing); });

  async function openPurchase(id) {
    const r = await VA.fetchJson(PAPI + "/" + id);
    if (!r.ok) return VA.toast(msg(r.body, "Could not open it."), "error");
    const o = r.body; po = o;
    $("po-badges").innerHTML = '<span class="pill">' + esc(o.reference) + "</span>" + ppill(o.status);
    $("po-title").textContent = o.title;
    $("po-meta").textContent = "Asked by " + (o.requested_by_name || "\u2014") + " on " + day(o.created_at) + (o.needed_by ? " \u00b7 needed by " + day(o.needed_by) : "") + (o.category_name ? " \u00b7 " + o.category_name : "");
    const at = PSTEP_OF[o.status];
    $("po-steps").innerHTML = at === undefined ? "" : '<ol class="po-steps">' + PSTEPS.map(function (s, i) { return '<li class="' + (i < at ? "done" : i === at ? "now" : "") + '"><span>' + (i < at ? "\u2713" : i + 1) + "</span>" + s + "</li>"; }).join("") + "</ol>";
    const bits = ["<p>" + esc(o.justification) + "</p>"];
    if (o.suggested_vendor_name) bits.push('<p class="cc-meta">Suggested vendor: ' + esc(o.suggested_vendor_name) + "</p>");
    if (o.decided_at) bits.push('<p class="cc-meta">' + (o.status === "REJECTED" ? "Not approved" : "Approved") + " by " + esc(o.decided_by_name || "the administration") + " on " + esc(day(o.decided_at)) + (o.decision_note ? ": " + esc(o.decision_note) : "") + "</p>");
    if (o.ordered_at) bits.push('<p class="cc-meta">Ordered from <b>' + esc(o.vendor_name || "") + "</b> on " + esc(day(o.ordered_at)) + (o.expected_delivery ? ", expected " + esc(day(o.expected_delivery)) : "") + (o.order_note ? " \u00b7 " + esc(o.order_note) : "") + "</p>");
    if (o.received_at) bits.push('<p class="cc-meta">Received on ' + esc(day(o.received_at)) + " \u00b7 invoice <b>" + esc(o.invoice_no || "") + "</b> \u00b7 total <b>" + money(o.actual_total) + "</b>" + (o.over_estimate_percent !== null && o.over_estimate_percent > 0 ? ' <span class="late">(' + o.over_estimate_percent + "% more than estimated)</span>" : "") + "</p>");
    if (o.paid_at) bits.push('<p class="cc-meta">Paid on ' + esc(day(o.paid_at)) + " by " + esc(METHOD[o.payment_method] || "") + (o.payment_reference ? " (" + esc(o.payment_reference) + ")" : "") + " \u00b7 booked as <b>" + esc(o.expense_reference || "") + "</b></p>");
    if (o.status === "CANCELLED") bits.push('<div class="cc-note rejected"><strong>Cancelled</strong><span>' + esc(o.cancel_reason || "") + "</span></div>");
    $("po-text").innerHTML = bits.join("");
    $("po-items").innerHTML = o.items.map(function (i) { return "<tr><td>" + esc(i.description) + "</td><td>" + i.quantity + "</td><td>" + money(i.estimated_total) + "</td><td>" + (i.received_quantity === null ? "\u2014" : i.received_quantity) + "</td><td>" + (i.actual_total === null ? "\u2014" : money(i.actual_total)) + "</td></tr>"; }).join("");
    const bar = $("po-actions"); bar.innerHTML = ""; $("po-error").hidden = true;
    const add = function (label, cls, fn) { const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn " + cls; b.textContent = label; b.addEventListener("click", fn); bar.appendChild(b); };
    const has = function (a) { return o.actions.indexOf(a) !== -1; };
    if (has("approve")) add("Approve", "", function () { poCall("approve", null, "Approved."); });
    if (has("reject")) add("Reject\u2026", "danger-btn", function () { askReason("Reject this request", "The person who asked is told why.", "Reject", true).then(function (t) { if (t) poCall("reject", { reason: t }, "Rejected."); }); });
    if (has("order")) add("Place the order\u2026", "", openOrderDialog);
    if (has("receive")) add("Goods received\u2026", "", openReceiveDialog);
    if (has("pay")) add("Pay the vendor\u2026", "", openPayDialog);
    if (has("reverse")) add("Reverse the payment\u2026", "danger-btn", function () { askReason("Reverse this payment", "The expense in the books is voided and this goes back to \u201cArrived: to pay\u201d.", "Reverse it", true).then(function (t) { if (t) poCall("reverse-payment", { reason: t }, "Payment reversed."); }); });
    if (has("cancel")) add("Cancel\u2026", "danger-btn", function () { askReason("Cancel this request", "The person who asked is told why.", "Cancel it", true).then(function (t) { if (t) poCall("cancel", { reason: t }, "Cancelled."); }); });
    if (!$("po-modal").open) $("po-modal").showModal();
  }
  async function poCall(what, body, done) {
    const r = await VA.fetchJson(PAPI + "/" + po.id + "/" + what, body ? json("POST", body) : { method: "POST" });
    if (!r.ok) { $("po-error").textContent = msg(r.body, "That did not work."); $("po-error").hidden = false; return false; }
    VA.toast(done); await openPurchase(po.id); loadPurchasing(); loadExpenses(); loadOverview(); loadBook(); return true;
  }
  $("po-close").addEventListener("click", function () { $("po-modal").close(); });

  async function openOrderDialog() {
    const v = await VA.fetchJson(API + "/vendors?active_only=true", { quiet: true });
    poVendors = v.ok ? v.body : [];
    if (!poVendors.length) { $("po-error").textContent = "Add a vendor first (Vendors tab)."; $("po-error").hidden = false; return; }
    fillSelect($("po-order-vendor"), poVendors);
    $("po-order-for").textContent = po.reference + " \u00b7 " + po.title + (po.suggested_vendor_name ? " \u00b7 suggested: " + po.suggested_vendor_name : "");
    $("po-order-date").value = ""; $("po-order-note").value = ""; $("po-order-error").hidden = true; $("po-order").showModal();
  }
  $("po-order-cancel").addEventListener("click", function () { $("po-order").close(); });
  $("po-order-ok").addEventListener("click", async function () {
    const r = await VA.fetchJson(PAPI + "/" + po.id + "/order", json("POST", { vendor_id: $("po-order-vendor").value, expected_delivery: $("po-order-date").value || null, note: $("po-order-note").value.trim() || null }));
    if (!r.ok) return fail("po-order-error", msg(r.body, "Could not place the order."));
    $("po-order").close(); VA.toast("Marked as ordered. " + (po.requested_by_name || "The person who asked") + " has been told."); await openPurchase(po.id); loadPurchasing();
  });

  function openReceiveDialog() {
    $("po-recv-inv").value = ""; $("po-recv-error").hidden = true;
    $("po-recv-body").innerHTML = po.items.map(function (i) {
      return '<tr data-id="' + i.id + '"><td>' + esc(i.description) + "</td><td>" + i.quantity + '</td><td><input class="text-input tiny" type="number" min="0" max="' + i.quantity + '" step="0.01" value="' + i.quantity + '" aria-label="Received" /></td><td><input class="text-input tiny" type="number" min="0" step="0.01" value="' + i.estimated_unit_price + '" aria-label="Price each" /></td><td class="line-total"></td></tr>';
    }).join("");
    const recalc = function () {
      let t = 0;
      $("po-recv-body").querySelectorAll("tr").forEach(function (tr) { const q = parseFloat(tr.children[2].firstChild.value) || 0, p = parseFloat(tr.children[3].firstChild.value) || 0; tr.querySelector(".line-total").textContent = money(q * p); t += q * p; });
      $("po-recv-total").textContent = money(t);
    };
    $("po-recv-body").querySelectorAll("input").forEach(function (i) { i.addEventListener("input", recalc); }); recalc();
    $("po-recv").showModal();
  }
  $("po-recv-cancel").addEventListener("click", function () { $("po-recv").close(); });
  $("po-recv-ok").addEventListener("click", async function () {
    if (!$("po-recv-inv").value.trim()) return fail("po-recv-error", "Enter the invoice or bill number.");
    const lines = Array.prototype.map.call($("po-recv-body").querySelectorAll("tr"), function (tr) { return { item_id: tr.getAttribute("data-id"), received_quantity: parseFloat(tr.children[2].firstChild.value) || 0, actual_unit_price: parseFloat(tr.children[3].firstChild.value) || 0 }; });
    const r = await VA.fetchJson(PAPI + "/" + po.id + "/receive", json("POST", { invoice_no: $("po-recv-inv").value.trim(), lines: lines }));
    if (!r.ok) return fail("po-recv-error", msg(r.body, "Could not record it."));
    $("po-recv").close(); VA.toast("Recorded. " + (po.requested_by_name || "The person who asked") + " has been told it arrived."); await openPurchase(po.id); loadPurchasing();
  });

  function openPayDialog() {
    $("po-pay-for").textContent = po.reference + " \u00b7 " + po.title + " \u00b7 " + money(po.actual_total) + (po.vendor_name ? " to " + po.vendor_name : "");
    $("po-pay-method").value = "CASH"; $("po-pay-ref").value = ""; $("po-pay-date").value = cfg.today; $("po-pay-files").value = ""; $("po-pay-error").hidden = true; $("po-pay").showModal();
  }
  $("po-pay-cancel").addEventListener("click", function () { $("po-pay").close(); });
  $("po-pay-ok").addEventListener("click", async function () {
    const fd = new FormData();
    fd.append("payment_method", $("po-pay-method").value); fd.append("paid_date", $("po-pay-date").value);
    if ($("po-pay-ref").value.trim()) fd.append("reference_no", $("po-pay-ref").value.trim());
    Array.prototype.forEach.call($("po-pay-files").files, function (f) { fd.append("files", f); });
    const r = await VA.fetchJson(PAPI + "/" + po.id + "/pay", { method: "POST", body: fd });
    if (!r.ok) return fail("po-pay-error", msg(r.body, "Could not record the payment."));
    $("po-pay").close(); VA.toast("Paid. Booked as " + r.body.expense_reference + "."); await openPurchase(po.id); loadPurchasing(); loadExpenses(); loadOverview(); loadBook();
  });

  // ------------------------------------------------------------------ other income
  async function loadIncome() {
    const p = new URLSearchParams({ limit: "200" });
    if ($("in-q").value.trim()) p.set("q", $("in-q").value.trim());
    p.set("include_voided", "true");
    const r = await VA.fetchJson(API + "/income?" + p.toString());
    if (!r.ok) return;
    const body = $("in-body"); body.innerHTML = "";
    r.body.forEach(function (i) {
      const tr = document.createElement("tr");
      if (i.voided_at) tr.className = "replaced";
      tr.innerHTML = "<td>" + esc(day(i.income_date)) + "</td><td>" + esc(i.reference) + "</td><td><strong>" + esc(i.received_from) + "</strong><small>" + esc([METHOD[i.payment_method], i.reference_no, i.description].filter(Boolean).join(" \u00b7 ")) + (i.voided_at ? " \u00b7 VOIDED: " + esc(i.void_reason || "") : "") + "</small></td><td>" + esc(i.category_name) + '</td><td class="num"><b>' + money(i.amount) + "</b></td><td></td>";
      if (isApprover && !i.voided_at) {
        const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn secondary-btn"; b.textContent = "Void";
        b.addEventListener("click", function () {
          askReason("Void this income entry", "It stays on record but stops counting. Use it for an entry made by mistake.", "Void it", true).then(async function (t) {
            if (!t) return;
            const x = await VA.fetchJson(API + "/income/" + i.id + "/void", json("POST", { reason: t }));
            if (x.ok) { VA.toast("Voided."); loadIncome(); loadOverview(); loadBook(); } else VA.toast(msg(x.body, "Could not void it."), "error");
          });
        });
        tr.lastElementChild.appendChild(b);
      }
      body.appendChild(tr);
    });
    $("in-empty").hidden = r.body.length > 0;
  }
  $("in-q").addEventListener("input", function () { debounce("in", loadIncome); });
  $("in-new").addEventListener("click", function () { $("in-form").reset(); $("in-date").value = cfg.today; $("in-error").hidden = true; $("in-modal").showModal(); });
  $("in-close").addEventListener("click", function () { $("in-modal").close(); });
  $("in-save").addEventListener("click", async function () {
    $("in-error").hidden = true;
    if ($("in-from").value.trim().length < 2) return fail("in-error", "Say who the money was received from.");
    if (!($("in-amount").value > 0)) return fail("in-error", "Enter the amount.");
    if (!$("in-date").value) return fail("in-error", "Choose the date.");
    const r = await VA.fetchJson(API + "/income", json("POST", { category_id: $("in-cat").value, received_from: $("in-from").value.trim(), amount: $("in-amount").value, income_date: $("in-date").value, payment_method: $("in-method").value,
      reference_no: $("in-ref").value.trim() || null, description: $("in-desc").value.trim() || null }));
    if (!r.ok) return fail("in-error", msg(r.body, "Could not record it."));
    $("in-modal").close(); VA.toast("Income recorded: " + r.body.reference); loadIncome(); loadOverview(); loadBook();
  });

  // ------------------------------------------------------------------ vendors
  async function loadVendors() {
    const r = await VA.fetchJson(API + "/vendors" + ($("vn-q").value.trim() ? "?q=" + encodeURIComponent($("vn-q").value.trim()) : ""));
    if (!r.ok) return;
    const box = $("vn-list"); box.innerHTML = "";
    r.body.forEach(function (v) {
      const row = document.createElement("div");
      row.className = "auth-row" + (v.is_active ? "" : " off");
      row.innerHTML = '<span class="grow"><strong>' + esc(v.name) + (v.is_active ? "" : " (inactive)") + "</strong><small>" + esc([v.contact_person, v.phone, v.email].filter(Boolean).join(" \u00b7 ")) + '</small></span><span class="sched">Spent: <b>' + money(v.total_spent) + "</b></span>";
      const edit = document.createElement("button"); edit.type = "button"; edit.className = "role-btn small-btn secondary-btn"; edit.textContent = "Edit"; edit.addEventListener("click", function () { openVendor(v); });
      const sw = document.createElement("button"); sw.type = "button"; sw.className = "role-btn small-btn secondary-btn"; sw.textContent = v.is_active ? "Switch off" : "Switch on";
      sw.addEventListener("click", async function () { const x = await VA.fetchJson(API + "/vendors/" + v.id, json("PATCH", { is_active: !v.is_active })); if (x.ok) { loadVendors(); refreshLists(); } });
      row.appendChild(edit); row.appendChild(sw); box.appendChild(row);
    });
    $("vn-empty").hidden = r.body.length > 0;
  }
  $("vn-q").addEventListener("input", function () { debounce("vn", loadVendors); });
  function openVendor(v) {
    vendorEdit = v || null; $("vn-title").textContent = v ? "Edit vendor" : "Add a vendor"; $("vn-error").hidden = true;
    $("vn-name").value = v ? v.name : ""; $("vn-contact").value = (v && v.contact_person) || ""; $("vn-phone").value = (v && v.phone) || ""; $("vn-email").value = (v && v.email) || ""; $("vn-tax").value = (v && v.tax_number) || ""; $("vn-address").value = (v && v.address) || "";
    $("vn-modal").showModal();
  }
  $("vn-new").addEventListener("click", function () { openVendor(null); });
  $("vn-close").addEventListener("click", function () { $("vn-modal").close(); });
  $("vn-save").addEventListener("click", async function () {
    if ($("vn-name").value.trim().length < 2) return fail("vn-error", "Give the vendor a name.");
    const body = { name: $("vn-name").value.trim(), contact_person: $("vn-contact").value.trim() || null, phone: $("vn-phone").value.trim() || null, email: $("vn-email").value.trim() || null, tax_number: $("vn-tax").value.trim() || null, address: $("vn-address").value.trim() || null };
    const r = await VA.fetchJson(API + "/vendors" + (vendorEdit ? "/" + vendorEdit.id : ""), json(vendorEdit ? "PATCH" : "POST", body));
    if (!r.ok) return fail("vn-error", msg(r.body, "Could not save it."));
    $("vn-modal").close(); VA.toast("Saved."); loadVendors(); refreshLists();
  });

  // ------------------------------------------------------------------ categories
  async function loadCategories() {
    const r = await VA.fetchJson(API + "/categories");
    if (!r.ok) return;
    ["EXPENSE", "INCOME"].forEach(function (kind) {
      const box = $(kind === "EXPENSE" ? "ct-expense" : "ct-income"); box.innerHTML = "";
      r.body.filter(function (c) { return c.kind === kind; }).forEach(function (c) {
        const row = document.createElement("div");
        row.className = "auth-row" + (c.is_active ? "" : " off");
        row.innerHTML = '<span class="grow"><strong>' + esc(c.name) + "</strong><small>" + (c.is_system ? "System category" : c.is_active ? "In use" : "Switched off") + "</small></span>";
        if (isApprover && !c.is_system) {
          const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn secondary-btn"; b.textContent = c.is_active ? "Switch off" : "Switch on";
          b.addEventListener("click", async function () { const x = await VA.fetchJson(API + "/categories/" + c.id, json("PATCH", { is_active: !c.is_active })); if (x.ok) { loadCategories(); refreshLists(); } else VA.toast(msg(x.body, "Could not change it."), "error"); });
          row.appendChild(b);
        }
        box.appendChild(row);
      });
    });
  }
  document.querySelectorAll("[data-add]").forEach(function (b) {
    b.addEventListener("click", async function () {
      const kind = b.getAttribute("data-add"), input = $(kind === "EXPENSE" ? "ct-new-expense" : "ct-new-income");
      if (input.value.trim().length < 2) return VA.toast("Type the category name.", "error");
      const r = await VA.fetchJson(API + "/categories", json("POST", { kind: kind, name: input.value.trim() }));
      if (r.ok) { input.value = ""; VA.toast("Category added."); loadCategories(); refreshLists(); } else VA.toast(msg(r.body, "Could not add it."), "error");
    });
  });

  async function refreshLists() {
    const c = await VA.fetchJson(API + "/categories?active_only=true", { quiet: true });
    if (c.ok) { cats.EXPENSE = c.body.filter(function (x) { return x.kind === "EXPENSE" && !x.is_system; }); cats.INCOME = c.body.filter(function (x) { return x.kind === "INCOME"; }); fillSelect($("ex-cat"), cats.EXPENSE); fillSelect($("in-cat"), cats.INCOME); }
    const v = await VA.fetchJson(API + "/vendors?active_only=true", { quiet: true });
    if (v.ok) fillSelect($("ex-vendor"), v.body, '<option value="">No vendor</option>');
  }

  document.addEventListener("vistadmin:ready", async function () {
    const c = await VA.fetchJson(API + "/config", { quiet: true });
    if (c.ok) cfg = c.body;
    const me = await VA.fetchJson("/api/v1/auth/me", { quiet: true });
    isApprover = !!(me.ok && (me.body.permissions || []).indexOf("account.approve") !== -1);
    document.querySelectorAll(".ct-add").forEach(function (el) { el.hidden = !isApprover; });
    $("ex-limit").textContent = "Expenses above Rs. " + Number(cfg.expense_approval_limit).toLocaleString() + " wait for the administration to approve them" + (isApprover ? " (you can approve them here)." : ".");
    await refreshLists();
    loadOverview(); loadBook(); loadExpenses(); loadIncome(); loadVendors(); loadCategories(); loadPurchasing();
    const open = new URLSearchParams(window.location.search).get("open");
    if (open === "purchases") VA.showTab("purchasing");
    if (open === "expenses") { VA.showTab("expenses"); exStatus = "PENDING_APPROVAL"; $("ex-status").querySelectorAll("button").forEach(function (x) { x.classList.toggle("active", x.getAttribute("data-s") === exStatus); }); loadExpenses(); }
  });
})();