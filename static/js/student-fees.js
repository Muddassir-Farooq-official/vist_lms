(function () {
  "use strict";

  // Student "My Fees" page: reads GET /api/v1/fees/accounts/me and shows
  // each fee with its instalments, challan download links, discounts and payments.
  const VA = window.VistAdmin;
  const SU = window.StudentUI;
  const esc = VA.escapeHtml;

  function msg(body, fallback) {
    return body && body.error && body.error.message ? body.error.message : fallback;
  }

  function day(d) {
    return d ? esc(VA.formatDateLong(String(d).slice(0, 10))) : "\u2014";
  }

  function instalmentRows(rows) {
    return rows.map(function (r) {
      const ch = r.challan;
      const challanCell = ch
        ? '<a href="/api/v1/fees/challans/' + encodeURIComponent(ch.id) + '/pdf" target="_blank" rel="noopener">Challan ' + esc(ch.consumer_number || "") + "</a>"
        : "\u2014";
      return "<tr>" +
        "<td>" + esc(r.installment_number != null ? String(r.installment_number) : "\u2014") + "</td>" +
        "<td>" + day(r.due_date) + "</td>" +
        '<td class="num">' + esc(SU.money(r.charged)) + "</td>" +
        '<td class="num">' + esc(SU.money(r.concession)) + "</td>" +
        '<td class="num">' + esc(SU.money(r.paid)) + "</td>" +
        '<td class="num">' + esc(SU.money(r.remaining)) + "</td>" +
        '<td class="num">' + esc(SU.money(r.late_fee || 0)) + "</td>" +
        "<td>" + SU.pill(r.status) + "</td>" +
        "<td>" + challanCell + "</td>" +
        "</tr>";
    }).join("");
  }

  function renderAssignment(a) {
    const section = document.createElement("section");
    section.className = "sh-card";
    const conc = (a.concessions || []).length
      ? '<p class="content-subtitle">Discounts: ' + a.concessions.map(function (c) {
          return esc(c.name || "Discount") + " (" + esc(SU.money(c.amount)) + ")";
        }).join(", ") + "</p>"
      : "";
    section.innerHTML =
      '<div class="sh-card-head"><h2>' + esc(a.fee_name || "Fee") + "</h2>" +
      (a.term_label ? "<span class=\"content-subtitle\">" + esc(a.term_label) + "</span>" : "") + (a.status && a.status !== "ACTIVE" ? SU.pill(a.status) : "") + "</div>" +
      '<p class="content-subtitle">Charged ' + esc(SU.money(a.charged)) +
      " \u00b7 Discount " + esc(SU.money(a.concession)) +
      " \u00b7 Paid " + esc(SU.money(a.paid)) +
      " \u00b7 Remaining " + esc(SU.money(a.remaining)) +
      (a.overdue ? " \u00b7 Overdue " + esc(SU.money(a.overdue)) : "") +
      (a.late_fee_accrued ? " \u00b7 Late fee " + esc(SU.money(a.late_fee_accrued)) : "") + "</p>" +
      conc +
      '<table class="dash-table"><thead><tr><th>#</th><th>Due</th><th class="num">Charged</th><th class="num">Discount</th>' +
      '<th class="num">Paid</th><th class="num">Remaining</th><th class="num">Late fee</th><th>Status</th><th>Challan</th></tr></thead>' +
      "<tbody>" + instalmentRows(a.installments || []) + "</tbody></table>";
    return section;
  }

  async function load() {
    const res = await VA.fetchJson("/api/v1/fees/accounts/me");
    const holder = document.getElementById("fee-assignments");
    holder.innerHTML = "";
    if (!res.ok) {
      holder.innerHTML = '<p class="home-empty">' + esc(msg(res.body, "Could not load your fees.")) + "</p>";
      return;
    }
    const data = res.body;
    const s = data.summary || {};
    document.getElementById("fee-stat-total").textContent = SU.money(s.total_to_pay || 0);
    document.getElementById("fee-stat-overdue").textContent = SU.money(s.overdue || 0);
    document.getElementById("fee-stat-paid").textContent = SU.money(s.paid || 0);
    document.getElementById("fee-stat-remaining").textContent = SU.money(s.remaining || 0);

    if (s.next_due) {
      const nd = document.getElementById("fee-next-due");
      const when = s.next_due.due_date || s.next_due.date || s.next_due[0];
      const amt = s.next_due.amount != null ? s.next_due.amount : s.next_due[1];
      nd.textContent = "Next instalment: " + SU.money(amt) + " due " + VA.formatDateLong(String(when).slice(0, 10)) + ".";
      nd.hidden = false;
    }
    if (s.fines_count) {
      const fn = document.getElementById("fee-fines-note");
      fn.innerHTML = "You also have " + esc(String(s.fines_count)) + " unpaid fine(s) totalling " + esc(SU.money(s.fines_unpaid)) +
        ' \u2014 see <a href="/dashboard/student/fines">My Fines</a>. Pay fees and fines at the accounts office or bank using the challan.';
      fn.hidden = false;
    }

    const list = data.assignments || [];
    document.getElementById("fee-empty").hidden = list.length > 0;
    list.forEach(function (a) { holder.appendChild(renderAssignment(a)); });

    const tbody = document.getElementById("fee-payments-tbody");
    const pays = data.payments || [];
    document.getElementById("fee-payments-empty").hidden = pays.length > 0;
    tbody.innerHTML = pays.map(function (p) {
      const receipt = p.receipt_number
        ? '<a href="/api/v1/fees/receipts/' + encodeURIComponent(p.id) + '/pdf" target="_blank" rel="noopener">' + esc(p.receipt_number) + "</a>"
        : "\u2014";
      return "<tr><td>" + day(p.date) + "</td><td>" + esc(p.fee || "") + "</td><td>" + esc(p.description || "") +
        (p.reference ? " \u00b7 ref " + esc(p.reference) : "") +
        '</td><td class="num">' + esc(SU.money(p.amount)) + "</td><td>" + receipt + "</td></tr>";
    }).join("");
  }

  document.addEventListener("vistadmin:ready", load);
})();