(function () {
  "use strict";

  // Fee refunds: /api/v1/fees/refunds ...
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/fees/refunds";
  const money = function (v) { return VA.formatMoney(v); };
  const STATUS = { REQUESTED: "Waiting for approval", APPROVED: "Approved — to pay out", PAID: "Paid", REJECTED: "Rejected", CANCELLED: "Cancelled" };

  function say(t) { const el = document.getElementById("rf-msg"); el.textContent = t; el.hidden = !t; }
  function errText(r) { return (r.body && r.body.error && r.body.error.message) || "That did not work."; }
  async function postJson(url, body) {
    return VA.fetchJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  }

  async function showPosition() {
    const id = document.getElementById("rf-fee").value;
    const box = document.getElementById("rf-position");
    if (!id) { box.hidden = true; document.getElementById("rf-form").hidden = true; return; }
    const r = await VA.fetchJson(API + "/position/" + id);
    if (!r.ok) return say(errText(r));
    const p = r.body;
    box.textContent = "Paid so far " + money(p.net_paid) + " · owed after discounts " + money(p.owed_after_discounts) +
      " · credit balance " + money(p.credit_balance) + " · can write off up to " + money(p.max_write_off) + ".";
    box.hidden = false;
    document.getElementById("rf-form").hidden = false;
  }

  document.getElementById("rf-find").addEventListener("click", async function () {
    say("");
    const roll = document.getElementById("rf-roll").value.trim();
    if (!roll) return say("Enter the roll number.");
    const s = await VA.fetchJson("/api/v1/students?q=" + encodeURIComponent(roll));
    const items = s.ok ? (s.body.items || s.body) : [];
    const st = items.find(function (x) { return x.roll_number === roll; });
    if (!st) return say("No student with roll number " + roll + ".");
    const a = await VA.fetchJson("/api/v1/fees/accounts/students/" + st.id);
    if (!a.ok) return say(errText(a));
    const p = a.body.payer || {};
    const who = document.getElementById("rf-who");
    who.innerHTML = "<strong>" + esc(p.name || "") + "</strong>" + (p.guardian_label ? " " + esc(p.guardian_label) : "") +
      " \u00b7 " + esc([p.identifier, p.group].filter(Boolean).join(" \u00b7 "));
    who.hidden = false;
    const fees = (a.body.assignments || []).filter(function (x) { return x.status === "ACTIVE"; });
    const sel = document.getElementById("rf-fee");
    sel.innerHTML = '<option value="">Choose the fee…</option>' + fees.map(function (f) {
      return '<option value="' + f.id + '">' + esc(f.fee_name + (f.term_label ? " (" + f.term_label + ")" : "") + " — paid " + money(f.paid)) + "</option>";
    }).join("");
    sel.hidden = false;
    if (!fees.length) say("This student has no active fee.");
  });
  document.getElementById("rf-fee").addEventListener("change", showPosition);

  document.getElementById("rf-request").addEventListener("click", async function () {
    const body = { fee_assignment_id: document.getElementById("rf-fee").value, kind: document.getElementById("rf-kind").value,
                   amount: Number(document.getElementById("rf-amount").value), write_off: Number(document.getElementById("rf-writeoff").value || 0),
                   reason: document.getElementById("rf-reason").value.trim() };
    if (!body.amount || body.reason.length < 10) return say("Enter the refund amount and a reason of at least 10 characters.");
    const r = await postJson(API, body);
    if (!r.ok) return say(errText(r));
    say("Sent for approval.");
    ["rf-amount", "rf-writeoff", "rf-reason"].forEach(function (id) { document.getElementById(id).value = ""; });
    load();
  });

  async function decide(id, approve) {
    const note = (await window.VistAdmin.promptText(approve ? "Approve this refund? Optional note:" : "Reject this refund: give the reason", ""));
    if (note === null) return;
    const r = await postJson(API + "/" + id + "/decision", { approve: approve, note: note.trim() || null });
    if (!r.ok) return say(errText(r));
    say(approve ? "Approved. The accounts office can now pay it out." : "Rejected.");
    load();
  }

  async function pay(id) {
    const method = (await window.VistAdmin.promptText("How was it paid back? Type CASH, CHEQUE, BANK_DEPOSIT or ONLINE_TRANSFER", "CHEQUE"));
    if (!method) return;
    const m = method.trim().toUpperCase();
    const ref = m === "CASH" ? "" : (await window.VistAdmin.promptText("Cheque / transfer reference number", ""));
    if (m !== "CASH" && !ref) return;
    const r = await postJson(API + "/" + id + "/pay", { method: m, reference: ref || null });
    if (!r.ok) return say(errText(r));
    say("Refund paid. Voucher " + r.body.voucher_number + " — print it for the payee's signature.");
    window.open("/api/v1/fees/receipts/" + r.body.ledger_entry_id + "/pdf", "_blank");
    load();
  }

  async function load() {
    const f = document.getElementById("rf-filter").value;
    const r = await VA.fetchJson(API + (f ? "?status=" + f : ""));
    if (!r.ok) return say(errText(r));
    document.getElementById("rf-tbody").innerHTML = r.body.map(function (x) {
      const who = x.payer ? (x.payer.name || "") + (x.payer.identifier ? " (" + x.payer.identifier + ")" : "") : "";
      let act = "";
      if (x.status === "REQUESTED") act = '<button type="button" class="role-btn small-btn" data-ok="' + x.id + '">Approve</button><button type="button" class="sd-view tt-del" data-no="' + x.id + '">Reject</button>';
      if (x.status === "APPROVED") act = '<button type="button" class="role-btn small-btn" data-pay="' + x.id + '">Pay out</button><button type="button" class="sd-view tt-del" data-cancel="' + x.id + '">Cancel\u2026</button>';
      if (x.status === "PAID") act = '<a href="/api/v1/fees/receipts/' + x.ledger_entry_id + '/pdf" target="_blank" rel="noopener">' + esc(x.voucher_number) + "</a>";
      const pill = { REQUESTED: "pending", APPROVED: "approved", PAID: "active", REJECTED: "rejected", CANCELLED: "cancelled" }[x.status] || "pending";
      return '<tr><td><span class="vl-c"><strong>' + esc(x.payer ? x.payer.name || "" : "") + "</strong><small>" + esc([x.payer && x.payer.guardian_label, x.payer && x.payer.identifier, "requested " + VA.formatDateLong(String(x.requested_at).slice(0, 10))].filter(Boolean).join(" \u00b7 ")) + "</small></span></td><td>" + esc(x.fee) +
        '</td><td class="num"><strong>' + esc(money(x.amount)) + '</strong></td><td class="num">' + (Number(x.write_off) ? esc(money(x.write_off)) : '<span class="sd-dim">\u2014</span>') +
        '</td><td class="rf-reason">' + esc(x.reason) + (x.decision_note ? "<small>" + esc(x.decision_note) + "</small>" : "") +
        '</td><td><span class="status-pill ' + pill + '">' + esc(STATUS[x.status] || x.status) + '</span></td><td class="crs-actions">' + act + "</td></tr>";
    }).join("") || '<tr><td colspan="7" class="sd-dim">Nothing here.</td></tr>';
    document.querySelectorAll("[data-ok]").forEach(function (b) { b.addEventListener("click", function () { decide(b.dataset.ok, true); }); });
    document.querySelectorAll("[data-no]").forEach(function (b) { b.addEventListener("click", function () { decide(b.dataset.no, false); }); });
    document.querySelectorAll("[data-pay]").forEach(function (b) { b.addEventListener("click", function () { pay(b.dataset.pay); }); });
    document.querySelectorAll("[data-cancel]").forEach(function (b) {
      b.addEventListener("click", async function () {
        const why = await VA.ask({ title: "Cancel this approved refund?", danger: true, ok: "Cancel the refund", intro: "Nothing is paid out. Any charges it wrote off are put back on the fee." });
        if (!why) return;
        const r = await postJson(API + "/" + b.dataset.cancel + "/cancel", { reason: why });
        if (!r.ok) return say(errText(r));
        VA.toast("Refund cancelled."); load();
      });
    });
  }

  document.getElementById("rf-filter").addEventListener("change", load);
  document.addEventListener("vistadmin:ready", load);
})();