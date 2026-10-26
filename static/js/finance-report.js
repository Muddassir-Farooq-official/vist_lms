(function () {
  "use strict";
  // Finance reports: defaulters with aging, daily collection, owed to vendors, bank reconciliation, exports.
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const money = function (v) { return VA.formatMoney(v); };
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/finance-reports";
  const METHOD = { CASH: "Cash", BANK_TRANSFER: "Bank transfer", CHEQUE: "Cheque", ONLINE: "Online", CARD: "Card", BANK_DEPOSIT: "Bank deposit / challan", ONLINE_TRANSFER: "Online transfer", ONE_BILL: "1Bill" };
  const day = function (iso) { return iso ? new Date(String(iso).slice(0, 10) + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—"; };
  const kpis = function (el, list) {
    el.innerHTML = list.map(function (x) { return '<div class="kpi"><div class="kpi-label">' + esc(x[0]) + '</div><div class="kpi-value">' + esc(String(x[1])) + "</div>" + (x[2] ? '<div class="kpi-sub">' + esc(x[2]) + "</div>" : "") + "</div>"; }).join("");
  };
  const failRow = function (tb, cols, r, retry) {
    tb.innerHTML = '<tr><td colspan="' + cols + '">' + esc((r.body && r.body.error && r.body.error.message) || "Could not load this.") + ' <button type="button" class="link-btn">Try again</button></td></tr>';
    tb.querySelector("button").addEventListener("click", retry);
  };
  const iso = function (d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); };

  // ------------------------------------------------------------ defaulters
  function dfQuery() {
    const p = new URLSearchParams();
    if ($("df-program").value) p.set("program_id", $("df-program").value);
    if ($("df-sem").value) p.set("semester", $("df-sem").value);
    if ($("df-min").value) p.set("min_amount", $("df-min").value);
    return p.toString();
  }
  async function loadDefaulters() {
    const q = dfQuery();
    $("df-csv").href = API + "/defaulters.csv" + (q ? "?" + q : "");
    const tb = $("df-table").tBodies[0];
    const r = await VA.fetchJson(API + "/defaulters" + (q ? "?" + q : ""));
    if (!r.ok) return failRow(tb, 10, r, loadDefaulters);
    const t = r.body.totals;
    kpis($("df-kpis"), [["Students", r.body.students.length], ["Total overdue", money(t.total)], ["1–30 days", money(t["1-30"])],
      ["31–60 days", money(t["31-60"])], ["61–90 days", money(t["61-90"])], ["Over 90 days", money(t["90+"])]]);
    $("df-empty").hidden = r.body.students.length > 0;
    tb.innerHTML = r.body.students.map(function (s) {
      return "<tr><td><strong>" + esc(s.name) + "</strong><small>" + esc((s.roll_number || "—") + " · overdue since " + day(s.oldest_due) + " (" + s.days_overdue + " days)") + "</small></td>" +
        "<td>" + esc((s.program || "—") + (s.semester ? " · Sem " + s.semester : "")) + "</td>" +
        "<td>" + esc(s.phone || "") + (s.guardian_phone ? "<small>Guardian " + esc(s.guardian_phone) + "</small>" : "") + "</td>" +
        ["1-30", "31-60", "61-90", "90+"].map(function (b) { return '<td class="num">' + (s[b] ? money(s[b]) : "") + "</td>"; }).join("") +
        '<td class="num"><b>' + money(s.overdue) + '</b></td><td class="num">' + (s.late_fee_accrued ? money(s.late_fee_accrued) : "") + '</td><td class="num">' + (s.fines_pending ? money(s.fines_pending) : "") + "</td></tr>";
    }).join("");
  }

  // ------------------------------------------------------------ daily collection
  async function loadCollection() {
    const from = $("co-from").value, to = $("co-to").value || from;
    const q = "from_date=" + from + "&to_date=" + to;
    $("co-csv").href = API + "/collection.csv?" + q;
    $("co-csv").setAttribute("data-download", "collection-" + from + (to !== from ? "-to-" + to : "") + ".csv");
    const tb = $("co-table").tBodies[0];
    const r = await VA.fetchJson(API + "/collection?" + q);
    if (!r.ok) return failRow(tb, 6, r, loadCollection);
    kpis($("co-kpis"), [["Net collected", money(r.body.total)], ["Received", money(r.body.received)], ["Reversed or refunded", money(r.body.taken_back)]].concat(Object.keys(r.body.by_method).map(function (m) { return [METHOD[m] || m, money(r.body.by_method[m])]; })));
    $("co-empty").hidden = r.body.rows.length > 0;
    tb.innerHTML = r.body.rows.map(function (g) {
      const parts = Object.keys(g.by_method).map(function (m) { return esc(METHOD[m] || m) + " " + money(g.by_method[m]); }).join(" · ");
      return "<tr><td>" + esc(day(g.day)) + "</td><td>" + esc(g.cashier) + '</td><td class="num">' + g.count + "</td><td>" + parts + (g.taken_back ? "<br><small>reversed or refunded " + money(g.taken_back) + "</small>" : "") + '</td><td class="num"><b>' + money(g.cash) + '</b></td><td class="num">' + money(g.total) + "</td></tr>";
    }).join("");
  }

  // ------------------------------------------------------------ payables
  async function loadPayables() {
    const tb = $("pa-table").tBodies[0];
    const r = await VA.fetchJson(API + "/payables");
    if (!r.ok) return failRow(tb, 4, r, loadPayables);
    kpis($("pa-kpis"), [["Owed to vendors", money(r.body.total)], ["Purchases waiting", r.body.orders], ["Vendors", r.body.vendors.length]]);
    $("pa-empty").hidden = r.body.vendors.length > 0;
    tb.innerHTML = r.body.vendors.map(function (v) {
      const orders = v.orders.map(function (o) { return esc(o.reference) + (o.invoice_no ? " (bill " + esc(o.invoice_no) + ")" : "") + " " + money(o.amount) + ", received " + esc(day(o.received_on)); }).join("<br>");
      return "<tr><td><strong>" + esc(v.vendor) + "</strong>" + (v.phone ? "<small>" + esc(v.phone) + "</small>" : "") + "</td><td><small>" + orders + '</small></td><td class="num">' + v.oldest_days + '</td><td class="num"><b>' + money(v.total) + "</b></td></tr>";
    }).join("");
  }

  // ------------------------------------------------------------ bank reconciliation
  $("bk-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const err = $("bk-error"); err.hidden = true;
    const f = $("bk-file").files[0];
    if (!f) { err.textContent = "Choose the bank statement file first."; err.hidden = false; return; }
    const fd = new FormData(); fd.append("file", f);
    const r = await VA.fetchJson(API + "/reconcile", { method: "POST", body: fd });
    if (!r.ok) { err.textContent = (r.body && r.body.error && r.body.error.message) || "Could not read that file."; err.hidden = false; return; }
    const b = r.body;
    kpis($("bk-kpis"), [["Statement lines", b.bank_lines], ["Matched", b.matched.length], ["Only in the bank", b.unmatched_bank.length], ["Only in the books", b.unmatched_books.length],
      ["Bank total", money(b.bank_total)], ["Books total", money(b.book_total), day(b.from_date) + " – " + day(b.to_date)]]);
    $("bk-bank").tBodies[0].innerHTML = b.unmatched_bank.length ? b.unmatched_bank.map(function (x) {
      return "<tr><td>" + esc(day(x.date)) + "</td><td>" + esc(x.reference) + '</td><td class="num">' + money(x.amount) + "</td></tr>";
    }).join("") : '<tr><td colspan="3">Everything in the statement is recorded.</td></tr>';
    $("bk-books").tBodies[0].innerHTML = b.unmatched_books.length ? b.unmatched_books.map(function (x) {
      return "<tr><td>" + esc(day(x.date)) + "</td><td>" + esc(x.kind + (x.receipt ? " " + x.receipt : "") + (x.title ? ": " + x.title : "")) + "</td><td>" + esc(METHOD[x.method] || x.method) + "</td><td>" + esc(x.reference) + '</td><td class="num">' + money(x.amount) + "</td></tr>";
    }).join("") : '<tr><td colspan="5">Everything recorded appears in the statement.</td></tr>';
    $("bk-matched").tBodies[0].innerHTML = b.matched.map(function (m) {
      return "<tr><td>" + esc(day(m.bank.date)) + "</td><td>" + esc(m.bank.reference) + "</td><td>" + esc(m.book.kind + (m.book.receipt ? " " + m.book.receipt : "")) + "</td><td>" + esc(m.matched_on) + '</td><td class="num">' + money(m.bank.amount) + "</td></tr>";
    }).join("");
  });

  // ------------------------------------------------------------ exports
  function exportLinks() {
    const p = new URLSearchParams();
    if ($("ex-from").value) p.set("from_date", $("ex-from").value);
    if ($("ex-to").value) p.set("to_date", $("ex-to").value);
    $("ex-book").href = API + "/book.csv" + (p.toString() ? "?" + p : "");
    $("ex-ded").href = API + "/deductions.csv?year=" + encodeURIComponent($("ex-year").value);
    $("ex-ded").setAttribute("data-download", "salary-deductions-" + $("ex-year").value + ".csv");
    const run = $("ex-run").value;
    $("ex-bank").hidden = !run;
    if (run) {
      $("ex-bank").href = API + "/salary-bank-sheet/" + run + ".csv";
      VA.fetchJson(API + "/salary-bank-sheet/" + run, { quiet: true }).then(function (r) {
        if (!r.ok) { $("ex-bank-note").textContent = ""; return; }
        $("ex-bank-note").textContent = r.body.to_bank + " employees, " + money(r.body.bank_total) + " to the bank." +
          (r.body.without_account.length ? " No bank account on record (pay by cash or cheque): " + r.body.without_account.join(", ") + "." : "");
      });
    }
  }
  ["ex-from", "ex-to", "ex-year", "ex-run"].forEach(function (id) { $(id).addEventListener("change", exportLinks); });

  document.addEventListener("vistadmin:ready", async function (ev) {
    const perms = (ev.detail && ev.detail.me && ev.detail.me.permissions) || [];
    const today = new Date();
    $("co-from").value = iso(today); $("co-to").value = iso(today);
    $("ex-year").value = today.getFullYear();
    $("df-load").addEventListener("click", loadDefaulters);
    $("co-load").addEventListener("click", loadCollection);
    const progs = perms.indexOf("program.view") !== -1 ? await VA.fetchJson("/api/v1/programs", { quiet: true }) : { ok: false };
    if (!progs.ok) $("df-program").hidden = true;          // not everyone may list programs; the other filters still work
    else progs.body.forEach(function (p) { const o = document.createElement("option"); o.value = p.id; o.textContent = p.code + " — " + p.name; $("df-program").appendChild(o); });
    const runs = perms.indexOf("payroll.view") !== -1 ? await VA.fetchJson("/api/v1/payroll/runs", { quiet: true }) : { ok: false };
    const sel = $("ex-run");
    if (runs.ok && runs.body.length) {
      runs.body.forEach(function (r) { const o = document.createElement("option"); o.value = r.id; o.textContent = new Date(String(r.month).slice(0, 10) + "T00:00:00").toLocaleDateString("en-GB", { month: "long", year: "numeric" }) + " (" + String(r.status).toLowerCase() + ")"; sel.appendChild(o); });
    } else {
      const o = document.createElement("option"); o.value = ""; o.textContent = runs.ok ? "No payroll prepared yet" : "Payroll not available to you"; sel.appendChild(o);
    }
    exportLinks();
    loadDefaulters(); loadCollection(); loadPayables();
  });
})();