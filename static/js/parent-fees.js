/* Parent -> Fees (GET /api/v1/parents/me/children/{id}/fees). */
(function () {
  "use strict";
  const P = window.ParentChild, VA = window.VistAdmin, el = P.el;
  const $ = function (id) { return document.getElementById(id); };
  const STATUS = { PAID: ["Paid", "active"], PARTIAL: ["Part paid", "pending"], OVERDUE: ["Overdue", "rejected"], PENDING: ["Not due yet", "draft"], DUE: ["Due", "pending"], WAIVED: ["Waived", "draft"] };

  function sumCard(label, value, sub, tone) {
    const d = el("div", "pp-kpi" + (tone ? " " + tone : ""));
    d.appendChild(el("small", null, label)); d.appendChild(el("b", null, value)); if (sub) d.appendChild(el("span", null, sub));
    return d;
  }

  P.onChange = async function (id) {
    const r = await VA.fetchJson("/api/v1/parents/me/children/" + encodeURIComponent(id) + "/fees");
    if (!r.ok) { P.error("Could not load the fees. Please refresh the page."); return; }
    const d = r.body, s = d.summary;
    $("pf-view").hidden = false;
    const sum = $("pf-sum"); sum.textContent = "";
    sum.appendChild(sumCard("To pay now", P.money(s.total_to_pay), s.overdue > 0 ? P.money(s.overdue) + " overdue" : "nothing overdue", s.overdue > 0 ? "bad" : ""));
    sum.appendChild(sumCard("Paid so far", P.money(s.paid), s.concession ? P.money(s.concession) + " scholarship / discount" : ""));
    sum.appendChild(sumCard("Next due", s.next_due ? P.day(s.next_due.date, { day: "numeric", month: "short" }) : "—", s.next_due ? P.money(s.next_due.amount) : "no upcoming instalment"));
    if (s.fines_unpaid > 0) sum.appendChild(sumCard("Unpaid fines", P.money(s.fines_unpaid), s.fines_count + (s.fines_count === 1 ? " fine" : " fines"), "bad"));

    const due = $("pf-due"); due.textContent = "";
    let n = 0;
    (d.assignments || []).forEach(function (a) {
      (a.installments || []).forEach(function (i) {
        if (!(i.remaining > 0)) return;
        n += 1;
        const st = STATUS[i.status] || [i.status, ""];
        const li = el("li", "pp-row");
        const body = el("div", "pp-row-body");
        body.appendChild(el("strong", null, i.label || ((a.fee_name || "Fee") + (a.installments.length > 1 ? " · instalment " + i.installment_number : ""))));
        if (i.items && i.items.length) body.appendChild(el("small", "pf-items", i.items.map(function (x) { return x[0] + " " + P.money(x[1]); }).join(" + ") +
          (i.concession ? " − discount " + P.money(i.concession) : "")));
        body.appendChild(el("small", null, "Due " + P.day(i.due_date) + (i.late_fee ? " · late fee so far " + P.money(i.late_fee) : "") +
          (i.challan && i.challan.consumer_number ? " · consumer no. " + i.challan.consumer_number : "")));
        li.appendChild(body);
        li.appendChild(el("b", "pp-amt", P.money(i.remaining)));
        li.appendChild(el("span", "status-pill " + st[1], st[0]));
        if (i.challan) {
          const a2 = el("a", "role-btn small-btn", "Download challan");
          a2.href = "/api/v1/fees/challans/" + encodeURIComponent(i.challan.id) + "/pdf"; a2.target = "_blank"; a2.rel = "noopener";
          li.appendChild(a2);
        }
        due.appendChild(li);
      });
    });
    $("pf-due-none").hidden = n > 0;

    const paid = $("pf-paid"); paid.textContent = "";
    (d.payments || []).forEach(function (p) {
      const li = el("li", "pp-row");
      const body = el("div", "pp-row-body");
      body.appendChild(el("strong", null, (p.fee || "Payment")));
      body.appendChild(el("small", null, P.day(p.date) + (p.description ? " · " + p.description : "")));
      li.appendChild(body);
      li.appendChild(el("b", "pp-amt", P.money(p.amount)));
      if (p.receipt_number) {
        const a = el("a", "role-btn small-btn secondary-btn", "Receipt " + p.receipt_number);
        a.href = "/api/v1/fees/receipts/" + encodeURIComponent(p.id) + "/pdf"; a.target = "_blank"; a.rel = "noopener";
        li.appendChild(a);
      }
      paid.appendChild(li);
    });
    $("pf-paid-none").hidden = (d.payments || []).length > 0;
  };
})();
