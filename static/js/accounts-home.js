(function () {
  "use strict";

  // Accounts office dashboard. Read-only: every number comes from an endpoint the accounts office already uses.
  const D = window.Dash, VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const METHOD = { CASH: "Cash", BANK_DEPOSIT: "Bank deposit", ONLINE_TRANSFER: "Online transfer", CHEQUE: "Cheque", ONE_BILL: "1Bill", BANK_TRANSFER: "Bank", ONLINE: "Online" };
  const RUN_STEPS = [["DRAFT", "Draft"], ["SUBMITTED", "Sent for approval"], ["APPROVED", "Approved"], ["PAID", "Paid"]];

  function hero(me, d) {
    D.greet("ac-greet", "ac-date", me);
    if (d.period) {
      const c = d.period.closed_through;
      D.chip("ac-books", c ? "Books closed up to " + new Date(c + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "Books open", false);
    }
  }

  function kpis(d) {
    if (d.today) {
      D.set("k-today", D.money(d.today.received));
      const n = (d.today.rows || []).reduce(function (s, r) { return s + (r.count || 0); }, 0);
      D.set("k-today-sub", n ? D.num(n) + " payment" + (n === 1 ? "" : "s") + (d.today.taken_back ? " · " + D.money(d.today.taken_back) + " taken back" : "") : "No payments yet today");
    }
    if (d.month) {
      const m = d.month;
      D.set("k-fees", D.money(m.fees_collected));
      D.set("k-fees-sub", m.fines_collected ? "+ " + D.money(m.fines_collected) + " in fines" : "fees received since the 1st");
      D.set("k-income", D.money(m.total_income));
      D.set("k-income-sub", D.money(m.fees_collected + m.fines_collected) + " fees and fines · " + D.money(m.other_income) + " other");
      D.set("k-exp", D.money(m.expenses));
      D.set("k-exp-sub", m.pending_expenses && m.pending_expenses.count ? D.num(m.pending_expenses.count) + " waiting for approval" : "all approved");
      D.set("k-net", D.money(m.net));
      D.set("k-net-sub", m.net < 0 ? "spending is more than income" : "income minus expenses");
      D.bad("k-net-sub", m.net < 0);
    }
    if (d.fees) {
      D.set("k-owed", D.money(d.fees.total_outstanding));
      D.set("k-owed-sub", d.fees.total_overdue > 0 ? D.money(d.fees.total_overdue) + " overdue · " + D.num(d.fees.overdue_assignment_count) + " students" : "Nothing overdue");
      D.bad("k-owed-sub", d.fees.total_overdue > 0);
    }
  }

  function latestRun(runs) { return (runs || []).filter(function (r) { return r.status !== "CANCELLED"; })[0] || null; }

  function todo(d) {
    const items = [];
    const refunds = d.refunds || [];
    const requested = refunds.filter(function (r) { return r.status === "REQUESTED"; }).length;
    const toPay = refunds.filter(function (r) { return r.status === "APPROVED"; }).length;
    items.push({ count: toPay, text: "approved refunds to pay out", href: "/dashboard/accounts/refunds", tone: "red" });
    items.push({ count: requested, text: "refund requests waiting for the administration", href: "/dashboard/accounts/refunds", tone: "blue" });
    if (d.purchases) {
      items.push({ count: d.purchases.to_pay, text: "vendor bills to pay (" + D.money(d.purchases.owed_to_vendors) + " owed)", href: "/dashboard/accounts/inventory", tone: "red" });
      items.push({ count: d.purchases.to_order, text: "approved purchases to order", href: "/dashboard/accounts/inventory" });
      items.push({ count: d.purchases.on_order, text: "orders on the way (receive them when they arrive)", href: "/dashboard/accounts/inventory", tone: "blue" });
    }
    if (d.month && d.month.pending_expenses) items.push({ count: d.month.pending_expenses.count, text: "expenses waiting for approval", href: "/dashboard/accounts/finance", tone: "blue" });
    const mine = (d.mine || []).filter(function (r) { return r.status === "REQUESTED"; }).length;
    items.push({ count: mine, text: "of your requests are waiting for a second approval", href: "/dashboard/accounts/approvals", tone: "blue" });
    const run = latestRun(d.runs);
    const now = new Date(), thisMonth = D.iso(now).slice(0, 7);
    if (run && run.status === "DRAFT") items.push({ count: "!", text: "Payroll for " + run.month_label + (run.returned_reason ? " was sent back: " + run.returned_reason : " is still a draft"), href: "/dashboard/accounts/payroll", tone: run.returned_reason ? "red" : "amber" });
    if (run && run.status === "APPROVED") items.push({ count: "!", text: "Payroll for " + run.month_label + " is approved and waiting to be paid", href: "/dashboard/accounts/payroll", tone: "red" });
    if (now.getDate() >= 20 && (!run || String(run.month).slice(0, 7) !== thisMonth)) {
      items.push({ count: "!", text: "Payroll for " + now.toLocaleDateString("en-GB", { month: "long", year: "numeric" }) + " has not been prepared", href: "/dashboard/accounts/payroll" });
    }
    if (d.inventory) items.push({ count: d.inventory.low_stock, text: "store items are low on stock", href: "/dashboard/accounts/inventory", tone: "blue" });
    if (d.assets) items.push({ count: d.assets.disposals_waiting, text: "asset disposals waiting", href: "/dashboard/accounts/assets", tone: "blue" });
    if (d.tasks) items.push({ count: d.tasks.needs_response + d.tasks.overdue, text: "of your tasks need an answer or are overdue", href: "/dashboard/accounts/tasks" });
    D.attention("attn-list", "attn-clear", items);
  }

  function defaulters(def) {
    if (!def) { D.hide("def-aging"); return; }
    const t = def.totals || {};
    const buckets = [["1-30", "1–30 days", "amber"], ["31-60", "31–60", "amber"], ["61-90", "61–90", "red"], ["90+", "Over 90", "red"]];
    const max = Math.max.apply(null, buckets.map(function (b) { return t[b[0]] || 0; }).concat([0]));
    $("def-aging").innerHTML = buckets.map(function (b) {
      const v = t[b[0]] || 0, h = max ? Math.max(4, Math.round(100 * v / max)) : 4;
      return '<div class="aging-col"><span class="aging-val">' + D.esc(D.compact(v)) + '</span><span class="aging-bar"><span class="seg seg-' + b[2] + '" style="height:' + h + '%"></span></span><span class="aging-lbl">' + b[1] + "</span></div>";
    }).join("");
    const rows = (def.students || []).slice().sort(function (a, b) { return (b.overdue || 0) - (a.overdue || 0); }).slice(0, 5);
    $("def-empty").hidden = rows.length > 0;
    D.people("def-list", rows.map(function (s) {
      return { title: s.name, sub: (s.roll_number || "") + (s.days_overdue ? " · " + s.days_overdue + " days late" : ""), value: D.money(s.overdue), bad: true };
    }));
  }

  function counter(d) {
    const by = (d.today && d.today.by_method) || {};
    const keys = Object.keys(by);
    D.stats("today-methods", keys.length ? keys.map(function (k) { return [METHOD[k] || k, D.money(by[k])]; }) : [["Cash", D.money(0)], ["Bank deposit", D.money(0)], ["Online", D.money(0)]]);
    const pays = (d.fees && d.fees.recent_payments) || [];
    $("pay-empty").hidden = pays.length > 0;
    D.people("pay-list", pays.slice(0, 5).map(function (p) {
      return { title: p.payer_name, sub: p.fee_structure_name + " · " + VA.formatDateTime(p.created_at), value: D.money(p.amount) };
    }));
  }

  function categories(m) {
    if (!m) { D.hide("cat-exp"); return; }
    const hb = function (id, rows, empty) {
      if (!window.Charts) return;
      window.Charts.hbars($(id), (rows || []).slice().sort(function (a, b) { return b.amount - a.amount; }).slice(0, 5).map(function (r) {
        return { label: r.category, count: Math.round(r.amount) };
      }), { empty: empty });
      $(id).querySelectorAll(".hbar-val").forEach(function (el) { el.textContent = D.compact(Number(el.textContent)); });
    };
    hb("cat-exp", m.expenses_by_category, "Nothing spent yet this month.");
    hb("cat-inc", m.income_by_category, "No other income yet this month.");
  }

  function payroll(runs) {
    const box = $("payroll-box");
    if (!runs) { D.hide("payroll-box"); return; }
    const run = latestRun(runs);
    if (!run) { box.innerHTML = '<p class="dash-empty">No payroll has been prepared yet.</p><a class="role-btn small-btn" href="/dashboard/accounts/payroll">Prepare payroll</a>'; return; }
    const at = RUN_STEPS.findIndex(function (s) { return s[0] === run.status; });
    box.innerHTML = '<div class="payroll-head"><strong>' + D.esc(run.month_label) + '</strong><span class="status-pill ' + D.esc(String(run.status).toLowerCase()) + '">' + D.esc(run.status) + "</span></div>" +
      '<ol class="steps">' + RUN_STEPS.map(function (s, i) { return '<li class="' + (i < at ? "done" : i === at ? "now" : "") + '"><span></span>' + s[1] + "</li>"; }).join("") + "</ol>" +
      '<ul class="fact-list">' +
      "<li><span>Staff paid</span><b>" + D.num(run.employees_count) + "</b></li>" +
      "<li><span>Gross</span><b>" + D.money(run.total_gross) + "</b></li>" +
      "<li><span>Deductions</span><b>" + D.money(run.total_deductions) + "</b></li>" +
      "<li><span>Net pay</span><b>" + D.money(run.total_net) + "</b></li></ul>" +
      (run.returned_reason && run.status === "DRAFT" ? '<p class="dash-alert error">Sent back: ' + D.esc(run.returned_reason) + "</p>" : "");
  }

  function store(d) {
    if (d.purchases) D.stats("t-purchases", [["to approve", d.purchases.waiting_for_approval], ["to order", d.purchases.to_order], ["on order", d.purchases.on_order], ["to pay", d.purchases.to_pay, d.purchases.to_pay ? "bad" : ""]]);
    else D.hide("t-purchases");
    if (d.inventory) D.stats("t-store", [["stock value", D.compact(d.inventory.stock_value)], ["items", D.num(d.inventory.items)], ["low stock", D.num(d.inventory.low_stock), d.inventory.low_stock ? "bad" : ""]]);
    else D.hide("t-store");
    if (d.assets) D.stats("t-assets", [["book value", D.compact(d.assets.book_value)], ["assets", D.num(d.assets.assets)], ["missing", D.num(d.assets.missing), d.assets.missing ? "bad" : ""]]);
    else D.hide("t-assets");
  }

  async function load(me) {
    const now = new Date(), first = new Date(now.getFullYear(), now.getMonth(), 1);
    const d = await D.getAll({
      today: "/api/v1/finance-reports/collection?from_date=" + D.iso(now) + "&to_date=" + D.iso(now),
      month: "/api/v1/accounts/summary?from_date=" + D.iso(first) + "&to_date=" + D.iso(now),
      year: "/api/v1/accounts/summary",
      fees: "/api/v1/fees/dashboard",
      defaulters: "/api/v1/finance-reports/defaulters",
      refunds: "/api/v1/fees/refunds",
      purchases: "/api/v1/purchases/summary",
      period: "/api/v1/accounts/period-close",
      runs: "/api/v1/payroll/runs",
      mine: "/api/v1/approvals?mine=true",
      inventory: "/api/v1/inventory/summary",
      assets: "/api/v1/assets/summary",
      tasks: "/api/v1/tasks/summary",
      calendar: D.upcoming(),
      announcements: "/api/v1/announcements?limit=12",
    });
    if (!d.month && !d.fees) { const e = $("ac-error"); e.textContent = "Could not load the dashboard. Please refresh the page."; e.hidden = false; }
    D.run(function () { hero(me, d); });
    D.run(function () { kpis(d); });
    D.run(function () { D.moneyChart($("m-chart"), d.year && d.year.monthly, ["m-income", "m-expenses", "m-net"]); });
    D.run(function () { todo(d); });
    D.run(function () { defaulters(d.defaulters); });
    D.run(function () { counter(d); });
    D.run(function () { categories(d.month); });
    D.run(function () { payroll(d.runs); });
    D.run(function () { store(d); });
    D.run(function () { D.calendar("cal-list", "cal-empty", d.calendar, 3); });
    D.run(function () { D.announcements("ann-list", "ann-empty", d.announcements, 1, "/dashboard/accounts/announcements"); });
  }

  document.addEventListener("vistadmin:ready", function (ev) { load(ev.detail && ev.detail.me); });
})();
