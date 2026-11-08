/* Accounts -> 1Bill payments (GET /api/v1/fees/onebill/report?day=). */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function rs(n) { return "Rs. " + Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 }); }

  async function load(day) {
    const r = await VA.fetchJson("/api/v1/fees/onebill/report" + (day ? "?day=" + day : ""));
    const tb = $("ob-rows"); tb.textContent = "";
    if (!r.ok) { const tr = el("tr"); const td = el("td", null, (r.body && r.body.error && r.body.error.message) || "Could not load."); td.colSpan = 8; tr.appendChild(td); tb.appendChild(tr); return; }
    const d = r.body;
    $("ob-off").hidden = d.enabled;
    $("ob-day").value = d.day;
    const k = $("ob-kpis"); k.textContent = "";
    [["Payments", d.count], ["Received", rs(d.totals.amount)], ["Fees", rs(d.totals.fee)], ["Late fees", rs(d.totals.late_fee)], ["Charges", rs(d.totals.convenience)]]
      .forEach(function (p) { const x = el("div", "fb-kpi"); x.appendChild(el("small", null, p[0])); x.appendChild(el("b", null, String(p[1]))); k.appendChild(x); });
    $("ob-banks").textContent = d.by_bank.map(function (b) { return b.bank + ": " + b.count + " · " + rs(b.amount); }).join("   ");
    if (!d.items.length) { const tr = el("tr"); const td = el("td", null, "No 1Bill payments on this day."); td.colSpan = 8; tr.appendChild(td); tb.appendChild(tr); return; }
    d.items.forEach(function (i) {
      const tr = el("tr");
      tr.appendChild(el("td", null, new Date(i.received_at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })));
      tr.appendChild(el("td", null, i.consumer_number));
      tr.appendChild(el("td", null, (i.bank || "—") + " · " + i.tran_auth_id));
      const paid = el("td"); paid.appendChild(el("b", null, rs(i.amount)));
      if (Math.abs(i.to_check) > 0.005) paid.appendChild(el("span", "status-pill pending", " Check"));
      tr.appendChild(paid);
      tr.appendChild(el("td", null, rs(i.fee)));
      tr.appendChild(el("td", null, i.late_fee ? rs(i.late_fee) : "—"));
      tr.appendChild(el("td", null, i.convenience ? rs(i.convenience) : "—"));
      tr.appendChild(el("td", null, (i.receipt_number || "—") + (i.fine_receipts ? " + " + i.fine_receipts : "")));
      tb.appendChild(tr);
    });
  }
  document.addEventListener("vistadmin:ready", function () {
    load();
    $("ob-day").addEventListener("change", function () { if (this.value) load(this.value); });
  });
})();
