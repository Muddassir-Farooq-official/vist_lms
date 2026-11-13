(function () {
  "use strict";

  // Fees page, three tabs: Student accounts (one student's whole fee picture, counter payments, challans),
  // Scholarships & discounts, and Previous dues (arrears brought in from the manual system).
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const money = function (v) { return VA.formatMoney(v); };
  const msg = function (body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    if (body && Array.isArray(body.detail)) return body.detail.map(function (e) { return e.loc[e.loc.length - 1] + ": " + e.msg; }).join("; ");
    return fallback;
  };
  const json = function (method, data) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }; };
  const day = function (iso) { return iso ? new Date(String(iso).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—"; };
  const fail = function (id, text) { $(id).textContent = text; $(id).hidden = false; };
  const today = new Date().toISOString().slice(0, 10);
  const PILL = { PAID: ["active", "Paid"], PARTIAL: ["pending", "Partly paid"], OVERDUE: ["dropped", "Overdue"], UPCOMING: ["", "Not yet due"] };
  const CHALLAN = { PENDING: ["pending", "Payable"], PAID: ["active", "Paid"], CANCELLED: ["", "Not needed"], EXPIRED: ["", "Expired"] };
  let isAdmin = false, current = null;

  // ------------------------------------------------------------------ a student picker (name or roll number)
  function studentPicker(input, list, onPick) {
    let timer = null;
    input.addEventListener("input", function () {
      onPick(null); clearTimeout(timer);
      timer = setTimeout(async function () {
        list.innerHTML = "";
        const q = input.value.trim();
        if (q.length < 2) return;
        const r = await VA.fetchJson("/api/v1/students?page_size=10&q=" + encodeURIComponent(q), { quiet: true });
        if (!r.ok) return;
        (r.body.items || []).forEach(function (s) {
          const b = document.createElement("button"); b.type = "button"; b.className = "picker-item";
          b.innerHTML = "<span><strong>" + esc(s.first_name + " " + s.last_name) + "</strong>" + (s.guardian_label ? ' <span class="picker-family">' + esc(s.guardian_label) + "</span>" : "") +
            "</span><small>" + esc(s.roll_number || "no roll number") + (s.program_code ? " · " + esc(s.program_code) : "") +
            (s.current_semester ? " · semester " + s.current_semester : "") + " · " + esc(String(s.status).toLowerCase()) + "</small>";
          b.addEventListener("click", function () { input.value = s.first_name + " " + s.last_name + (s.roll_number ? " (" + s.roll_number + ")" : ""); list.innerHTML = ""; onPick(s); });
          list.appendChild(b);
        });
        if (!(r.body.items || []).length) list.innerHTML = '<p class="cc-meta">Nobody matches.</p>';
      }, 300);
    });
  }

  function tile(label, value, sub, tone) {
    return '<div class="kpi ' + (tone || "") + '"><span class="kpi-label">' + esc(label) + '</span><strong class="kpi-value">' + value + '</strong><span class="kpi-sub">' + esc(sub || "") + "</span></div>";
  }

  // ------------------------------------------------------------------ Student accounts
  async function loadAccount(studentId) {
    const r = await VA.fetchJson("/api/v1/fees/accounts/students/" + studentId);
    if (!r.ok) { VA.toast(msg(r.body, "Could not open this student's fees."), "error"); return; }
    const a = r.body; current = a;
    $("sa-account").hidden = false;
    $("sa-title").textContent = a.payer.name + (a.payer.guardian_label ? " " + a.payer.guardian_label : "") + (a.payer.identifier ? " · " + a.payer.identifier : "") +
      (a.payer.group ? " · " + a.payer.group : "");
    const s = a.summary;
    $("sa-tiles").innerHTML = '<div class="kpi-grid kpi-grid-6">' +
      tile("Payable", money(s.charged - s.concession), s.concession ? money(s.concession) + " discount" : "") +
      tile("Paid", money(s.paid), "") +
      tile("Still owed", money(s.remaining), s.next_due ? "next: " + money(s.next_due.amount) + " by " + day(s.next_due.date) : "", s.remaining > 0 ? "" : "good") +
      tile("Overdue", money(s.overdue), s.late_fee_accrued ? "late fee " + money(s.late_fee_accrued) : "", s.overdue > 0 ? "bad" : "") +
      tile("Unpaid fines", money(s.fines_unpaid), s.fines_count ? s.fines_count + " fine(s)" : "") +
      tile("Credit", money(s.credit_balance), s.credit_balance ? "paid more than owed" : "") + "</div>";

    const box = $("sa-fees"); box.innerHTML = "";
    if (!a.assignments.length) box.innerHTML = '<p class="empty-state">No fees on this account yet.</p>';
    a.assignments.forEach(function (f) {
      const card = document.createElement("section"); card.className = "sh-card";
      const status = f.status === "ACTIVE" ? "" : ' <span class="status-pill dropped">' + esc(f.status.toLowerCase()) + "</span>";
      card.innerHTML = '<div class="sh-card-head"><h3>' + esc(f.fee_name) + status + '</h3><span class="cc-meta">' + esc(f.term_label || "") +
        " · payable " + money(f.payable) + " · paid " + money(f.paid) + " · <b>still owed " + money(f.remaining) + "</b>" +
        (f.credit > 0.005 ? ' · <span class="status-pill active">credit ' + money(f.credit) + "</span>" : "") +
        (f.late_fee_paused_from && !f.late_fee_paused_until ? " · late fee paused since " + esc(day(f.late_fee_paused_from)) : "") + "</span></div>" +
        '<div class="table-scroll"><table class="dash-table"><thead><tr><th>Installment</th><th>Due</th><th class="num">Charged</th><th class="num">Discount</th><th class="num">Paid</th><th class="num">Still due</th><th>Status</th><th>Challan</th><th></th></tr></thead><tbody></tbody></table></div>' +
        '<p class="form-actions"></p>';
      const tb = card.querySelector("tbody");
      f.installments.forEach(function (row) {
        const p = PILL[row.status] || ["", row.status];
        const c = row.challan;
        const cp = c ? (CHALLAN[c.status] || ["", c.status]) : null;
        const tr = document.createElement("tr");
        tr.innerHTML = "<td>" + esc(f.is_previous_dues ? "Previous dues" : "Installment " + (row.installment_number || "")) + "</td><td>" + esc(day(row.due_date)) + '</td><td class="num">' + money(row.charged) +
          '</td><td class="num">' + (row.concession ? money(row.concession) : "—") + '</td><td class="num">' + money(row.paid) + '</td><td class="num"><b>' + money(row.remaining) + "</b></td>" +
          '<td><span class="status-pill ' + p[0] + '">' + p[1] + "</span>" + (row.late_fee > 0.005 ? "<br><small>late fee " + money(row.late_fee) + "</small>" : "") + "</td>" +
          "<td>" + (c ? '<span class="status-pill ' + cp[0] + '">' + cp[1] + "</span><br><small>" + esc(c.consumer_number) + (c.status === "PENDING" ? " · " + money(c.amount) : "") + "</small>" : "—") + "</td><td></td>";
        const cell = tr.lastElementChild;
        if (c) {
          const pdf = document.createElement("a"); pdf.className = "role-btn small-btn secondary-btn"; pdf.href = "/api/v1/fees/challans/" + c.id + "/pdf";
          pdf.setAttribute("data-download", "challan-" + c.consumer_number + ".pdf"); pdf.textContent = "Challan"; cell.appendChild(pdf);
          if (c.status === "PENDING" && f.status === "ACTIVE") {
            const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn"; b.textContent = "Mark paid…";
            b.addEventListener("click", function () { openChallanPaid(c, f, row); }); cell.appendChild(b);
          }
          if (c.status === "PAID" && isAdmin) {
            const u = document.createElement("button"); u.type = "button"; u.className = "role-btn small-btn danger-btn"; u.textContent = "Undo…";
            u.addEventListener("click", async function () {
              const why = await VA.ask({ title: "Mark this challan unpaid?", danger: true, ok: "Mark unpaid",
                intro: "Use this only when the payment was recorded by mistake or the deposit bounced. The payment is reversed once (its receipt prints VOID) and the installment is owed again." });
              if (!why) return;
              const x = await VA.fetchJson("/api/v1/fees/challans/" + c.id + "/mark-unpaid", json("POST", { reason: why }));
              if (!x.ok) return VA.toast(msg(x.body, "Could not undo it."), "error");
              VA.toast("Done. The installment is owed again."); refresh();
            });
            cell.appendChild(u);
          }
        }
        tb.appendChild(tr);
      });
      const actions = card.querySelector(".form-actions");
      const manage = document.createElement("button"); manage.type = "button"; manage.className = "role-btn small-btn";
      manage.textContent = f.status === "ACTIVE" ? "Take a payment / statement…" : "Statement…";
      manage.addEventListener("click", function () { document.dispatchEvent(new CustomEvent("fees:open-assignment", { detail: { id: f.id } })); });
      actions.appendChild(manage);
      // Advance credit (paid more than this fee needs) can pay another fee of the same student.
      const owing = a.assignments.filter(function (o) { return o.id !== f.id && o.status === "ACTIVE" && o.remaining > 0.005; });
      if (f.credit > 0.005 && owing.length) {
        const mv = document.createElement("button"); mv.type = "button"; mv.className = "role-btn small-btn secondary-btn"; mv.textContent = "Use credit on another fee…";
        mv.addEventListener("click", async function () {
          const pick = await chooseFee(owing, f.credit);
          if (!pick) return;
          const x = await VA.fetchJson("/api/v1/fees/accounts/credit/move", json("POST", { from_assignment_id: f.id, to_assignment_id: pick.id, amount: pick.amount }));
          if (!x.ok) return VA.toast(msg(x.body, "Could not use the credit."), "error");
          VA.toast("Used " + money(x.body.moved) + " of credit. Still owed on that fee: " + money(x.body.still_owed) + "."); refresh();
        });
        actions.appendChild(mv);
      }
      box.appendChild(card);
    });

    $("sa-payments").innerHTML = a.payments.length ? a.payments.map(function (p) {
      return "<tr><td>" + esc(day(p.date)) + "</td><td>" + esc(p.fee) + "</td><td>" + (p.receipt_number ? '<a href="/api/v1/fees/receipts/' + encodeURIComponent(p.id) + '/pdf" target="_blank" rel="noopener">' + esc(p.receipt_number) + "</a>" : esc(p.description || "")) +
        "</td><td>" + esc(String(p.method || "").replace(/_/g, " ").toLowerCase()) + (p.reference ? " #" + esc(p.reference) : "") + '</td><td class="num ' + (p.amount < 0 ? "bad" : "") + '">' + money(p.amount) + "</td></tr>";
    }).join("") : '<tr><td colspan="5" class="cc-meta">No payments yet.</td></tr>';

    $("sa-scholarships").innerHTML = a.scholarships.length ? a.scholarships.map(function (sc) {
      return "<tr><td><strong>" + esc(sc.name) + "</strong><br><small>" + (sc.kind === "PERCENT" ? sc.value + "%" : money(sc.value)) + " \u00b7 " + esc(appliesTo(sc)) + "</small></td><td>" + esc(day(sc.valid_from)) + (sc.valid_until ? " – " + esc(day(sc.valid_until)) : " onwards") +
        '</td><td class="num">' + money(sc.given_so_far) + '</td><td><span class="status-pill ' + (sc.status === "ACTIVE" ? "active" : "") + '">' + (sc.status === "ACTIVE" ? "Running" : "Ended") + "</span></td></tr>";
    }).join("") : '<tr><td colspan="4" class="cc-meta">None.</td></tr>';
  }
  function chooseFee(owing, credit) {
    return new Promise(function (resolve) {
      const d = document.createElement("dialog"); d.className = "modal";
      d.innerHTML = '<div class="modal-body"><h2>Use credit of ' + esc(money(credit)) + '</h2><div class="form-field"><label for="mc-fee">Pay this fee</label><select id="mc-fee" class="select-input">' +
        owing.map(function (o) { return '<option value="' + esc(o.id) + '">' + esc(o.fee_name + " (" + (o.term_label || "") + ") — owes " + money(o.remaining)) + "</option>"; }).join("") +
        '</select></div><div class="form-field"><label for="mc-amount">Amount (empty = as much as possible)</label><input id="mc-amount" type="number" min="1" step="0.01" class="text-input" /></div>' +
        '<p class="cc-meta">No money changes hands: the credit is moved from one fee to the other and both show it on the statement.</p>' +
        '<div class="form-actions"><button type="button" class="role-btn" data-ok>Use the credit</button></div><button type="button" class="back-link modal-close-btn" data-no>Cancel</button></div>';
      document.body.appendChild(d);
      const done = function (v) { if (d.open) d.close(); d.remove(); resolve(v); };
      d.querySelector("[data-no]").addEventListener("click", function () { done(null); });
      d.addEventListener("cancel", function (e) { e.preventDefault(); done(null); });
      d.querySelector("[data-ok]").addEventListener("click", function () {
        const v = d.querySelector("#mc-amount").value;
        done({ id: d.querySelector("#mc-fee").value, amount: v === "" ? null : Number(v) });
      });
      d.showModal();
    });
  }
  function refresh() { if (current) loadAccount(current.payer.id); }
  studentPicker($("sa-search"), $("sa-results"), function (s) { if (s) loadAccount(s.id); });
  // Opened from the student lookup ("Open the fee account"): ?student=<id> opens that student's account straight away.
  (function () {
    let id = null;
    try { id = new URLSearchParams(location.search).get("student"); } catch (e) { id = null; }
    if (id && /^[0-9a-f-]{36}$/i.test(id)) {
      loadAccount(id).then(function () { const el = $("sa-account"); if (el && !el.hidden) el.scrollIntoView({ behavior: "smooth", block: "start" }); });
    }
  })();
  document.addEventListener("fees:changed", refresh);

  // marking a challan paid
  let challan = null;
  function openChallanPaid(c, f, row) {
    challan = c;
    $("cp-for").textContent = current.payer.name + " · " + f.fee_name + " · " + (f.is_previous_dues ? "previous dues" : "installment " + row.installment_number) + " · " + money(c.amount) + " · challan " + c.consumer_number;
    const unposted = Math.max(0, (row.late_fee || 0) - (row.late_fee_posted || 0));
    $("cp-late").hidden = unposted <= 0.005;
    $("cp-late").textContent = unposted > 0.005 ? "This instalment is late: a late fee of " + money(unposted) +
      " will be added to Fines when you record it. Collect it as well (Fines \u2192 Record payment)." : "";
    $("cp-ref").value = ""; $("cp-date").value = today; $("cp-error").hidden = true; $("cp-modal").showModal();
  }
  $("cp-cancel").addEventListener("click", function () { $("cp-modal").close(); });
  $("cp-ok").addEventListener("click", async function () {
    $("cp-error").hidden = true;
    const method = $("cp-method").value;
    if (method !== "CASH" && !$("cp-ref").value.trim()) return fail("cp-error", "Enter the slip, transaction or cheque number (needed to match the bank statement).");
    $("cp-ok").disabled = true;
    const r = await VA.withLateFine(function (extra) {
      return VA.fetchJson("/api/v1/fees/challans/" + challan.id + "/mark-paid", json("POST", Object.assign({ method: method, reference: $("cp-ref").value.trim() || null,
        paid_on: $("cp-date").value || null }, extra)));
    }, { reasonField: "late_fine_reason" });
    $("cp-ok").disabled = false;
    if (!r) return;
    if (!r.ok) return fail("cp-error", msg(r.body, "Could not record it."));
    $("cp-modal").close();
    VA.toast("Recorded. A receipt has been issued." + (r.body && r.body.late_fee_posted ? " Its late fee of " + money(r.body.late_fee_posted) +
      " was added to Fines; collect it there." : ""));
    refresh();
  });

  // ------------------------------------------------------------------ Scholarships & discounts
  let scStudent = null;
  studentPicker($("sc-student"), $("sc-student-list"), function (s) { scStudent = s; });

  // Discount type: "% Percentage" or "Rs. Fixed amount" (a switch; the hidden #sc-kind / #se-kind holds the value),
  // the value box shows % or Rs., and a line under it says exactly what the discount will do before it is saved.
  let structInfo = {}, latestOfType = {};
  function previewText(pre) {
    const kind = $(pre + "-kind").value, v = parseFloat($(pre + "-value").value), scope = $(pre + "-structure").value || "";
    const fixed = kind === "FIXED";
    if (!(v > 0)) {
      return { text: fixed ? "Takes this amount (in rupees) off each fee it covers, spread over that fee's installments."
                           : "Takes this percentage off every installment of the fees it covers.", warn: false };
    }
    if (!fixed && v > 100) return { text: "A percentage can be at most 100.", warn: true };
    const what = fixed ? money(v) : v + "%";
    const example = function (st) {
      if (!st) return "";
      const off = fixed ? Math.min(v, st.total) : Math.round(st.total * v) / 100;
      return " Example: " + st.name + " (" + st.term + "), " + money(st.total) + " → " + money(st.total - off) + " to pay." +
        (fixed && v > st.total ? " (Only " + money(st.total) + " can come off this fee.)" : "");
    };
    if (scope.indexOf("fee:") === 0) {
      const st = structInfo[scope.slice(4)];
      return { text: what + " off this one fee." + example(st), warn: false };
    }
    if (scope.indexOf("type:") === 0) {
      const tid = scope.slice(5), name = feeTypeNames[tid] || "this kind of fee";
      return { text: what + " off " + name + " in every semester" + (fixed ? ", spread over its installments." : ".") + example(structInfo[latestOfType[tid]]), warn: false };
    }
    return fixed
      ? { text: money(v) + " off EACH fee this student is charged (tuition, exam, library… each one gets " + money(v) + " off). For tuition only, choose “Tuition … only (every semester)” under Applies to.", warn: true }
      : { text: v + "% off EVERY fee this student is charged (tuition, exam, card, library…), not only tuition. For tuition only, choose “Tuition … only (every semester)” under Applies to.", warn: true };
  }
  function paintPreview(pre) {
    const out = $(pre + "-preview");
    if (!out) return;
    const p = previewText(pre);
    out.textContent = p.text + ($(pre + "-kind").value === "FIXED" && parseFloat($(pre + "-value").value) > 0 ? " Never more than what is still owed." : "");
    out.classList.toggle("warn", !!p.warn);
  }
  function wireKind(pre) {
    const hidden = $(pre + "-kind"), input = $(pre + "-value");
    const radios = Array.prototype.slice.call(document.querySelectorAll('input[name="' + pre + '-kind-r"]'));
    function paint() {
      const fixed = hidden.value === "FIXED";
      radios.forEach(function (r) { r.checked = r.value === hidden.value; });
      $(pre + "-value-label").textContent = fixed ? "Amount off (Rs.)" : "Percentage off";
      $(pre + "-value-pre").hidden = !fixed; $(pre + "-value-suf").hidden = fixed;
      input.placeholder = fixed ? "e.g. 10000" : "e.g. 25";
      if (fixed) input.removeAttribute("max"); else input.max = "100";
      input.step = fixed ? "1" : "0.01";
      paintPreview(pre);
    }
    radios.forEach(function (r) {
      r.addEventListener("change", function () {
        if (!r.checked || hidden.value === r.value) return;
        hidden.value = r.value; input.value = "";           // a percentage is not an amount: start the number again
        hidden.dispatchEvent(new Event("change")); input.focus();
      });
    });
    hidden.addEventListener("change", paint);
    input.addEventListener("input", function () { paintPreview(pre); });
    $(pre + "-structure").addEventListener("change", function () { paintPreview(pre); });
    paint();
  }

  // "Applies to": every fee; one kind of fee (e.g. Tuition) in every semester; or one fee only.
  let feeTypeNames = {};
  async function loadStructuresForScholarship() {
    const [r, t] = await Promise.all([VA.fetchJson("/api/v1/fees/structures", { quiet: true }), VA.fetchJson("/api/v1/fees/types", { quiet: true })]);
    if (!r.ok) return;
    const types = t.ok ? t.body.filter(function (x) { return x.is_active !== false && x.code !== "OPENING"; }) : [];
    feeTypeNames = {}; structInfo = {}; latestOfType = {};
    types.forEach(function (x) { feeTypeNames[x.id] = x.name; });
    r.body.filter(function (x) { return x.status !== "DRAFT"; }).forEach(function (x) {
      structInfo[x.id] = { name: x.name, term: x.term_label, total: Number(x.total_amount) || 0, from: x.effective_from || "" };
      const cur = structInfo[latestOfType[x.fee_type_id]];
      if (!cur || String(x.effective_from || "") > cur.from) latestOfType[x.fee_type_id] = x.id;
    });
    const sel = $("sc-structure");
    sel.innerHTML = '<option value="">Every fee of this student</option>' +
      (types.length ? '<optgroup label="One kind of fee, every semester">' + types.map(function (x) {
        return '<option value="type:' + x.id + '">' + esc(x.name) + " only (every semester)</option>";
      }).join("") + "</optgroup>" : "") +
      '<optgroup label="One fee only">' + r.body.filter(function (s) { return s.status !== "DRAFT"; }).map(function (s) {
        return '<option value="fee:' + s.id + '">Only: ' + esc(s.name) + " (" + esc(s.term_label) + ")</option>";
      }).join("") + "</optgroup>";
    tuitionScope = "";
    const tuition = types.find(function (x) { return /^TUI/i.test(x.code || ""); }) || types.find(function (x) { return /tuition/i.test(x.name || ""); });
    if (tuition) tuitionScope = "type:" + tuition.id;
    sel.value = tuitionScope;                // a discount is normally on tuition only; "Every fee" must be chosen on purpose
    paintPreview("sc");
  }
  let tuitionScope = "";
  wireKind("sc"); wireKind("se");
  function appliesTo(s) {
    if (s.fee_type_id) return (feeTypeNames[s.fee_type_id] || "One kind of fee") + " only, every semester";
    return s.fee_structure_id ? "one fee only" : "every fee";
  }
  function defaultFrom() {
    const terms = window.VistTerms ? window.VistTerms.open().filter(function (t) { return t.start_date && t.start_date <= today; }) : [];
    terms.sort(function (x, y) { return x.start_date < y.start_date ? 1 : -1; });
    return terms.length ? terms[0].start_date : "";      // empty: the server uses the current term's start (or 1 January)
  }
  $("sc-save").addEventListener("click", async function () {
    $("sc-error").hidden = true;
    if (!scStudent) return fail("sc-error", "Choose the student from the list.");
    if ($("sc-name").value.trim().length < 3) return fail("sc-error", "Give it a name.");
    if (!($("sc-value").value > 0)) return fail("sc-error", $("sc-kind").value === "FIXED" ? "Enter the amount in rupees." : "Enter the percentage.");
    if ($("sc-kind").value === "PERCENT" && parseFloat($("sc-value").value) > 100) return fail("sc-error", "A percentage can be at most 100.");
    if ($("sc-reason").value.trim().length < 5) return fail("sc-error", "Give the reason (it is kept on record).");
    $("sc-save").disabled = true;
    const r = await VA.fetchJson("/api/v1/fees/scholarships", json("POST", {
      student_id: scStudent.id, name: $("sc-name").value.trim(), kind: $("sc-kind").value, value: parseFloat($("sc-value").value),
      fee_structure_id: $("sc-structure").value.indexOf("fee:") === 0 ? $("sc-structure").value.slice(4) : null,
      fee_type_id: $("sc-structure").value.indexOf("type:") === 0 ? $("sc-structure").value.slice(5) : null,
      auto_apply: $("sc-auto").checked, valid_from: $("sc-from").value || null,
      valid_until: $("sc-until").value || null, reason: $("sc-reason").value.trim() }));
    $("sc-save").disabled = false;
    if (!r.ok) return fail("sc-error", msg(r.body, "Could not save it."));
    if (r.body.pending_approval) VA.toast(r.body.message);
    else VA.toast(r.body.applied && r.body.applied.total_concession > 0
      ? "Saved: " + money(r.body.applied.total_concession) + " taken off " + r.body.applied.assignments_updated + " fee(s)."
      : "Saved. Nothing was due inside its dates yet; it will apply to installments due between those dates.");
    ["sc-student", "sc-name", "sc-value", "sc-reason", "sc-until"].forEach(function (id) { $(id).value = ""; }); scStudent = null;
    $("sc-structure").value = tuitionScope; paintPreview("sc");
    loadScholarships(); refresh();
  });
  let scRows = {}, canProposeSc = false;
  async function loadScholarships() {
    const st = $("sc-filter").value;
    const r = await VA.fetchJson("/api/v1/fees/scholarships" + (st ? "?status=" + st : ""));
    if (!r.ok) return;
    scRows = {};
    r.body.forEach(function (s) { scRows[s.id] = s; });
    $("sc-body").innerHTML = r.body.map(function (s) {
      const running = s.status === "ACTIVE";
      return "<tr><td><strong>" + esc(s.student_name) + "</strong><br><small>" + esc(s.roll_number || "") + "</small></td><td>" + esc(s.name) + "<br><small>" + esc(s.reason || "") + "</small></td><td>" +
        (s.kind === "PERCENT" ? s.value + "%" : money(s.value)) + "<br><small>" + esc(appliesTo(s)) + "</small>" + "</td><td>" + esc(day(s.valid_from)) + (s.valid_until ? " – " + esc(day(s.valid_until)) : " onwards") +
        '</td><td class="num">' + money(s.given_so_far) + '</td><td><span class="status-pill ' + (running ? "active" : "") + '">' + (running ? "Running" : "Ended") + "</span></td><td class=\"row-actions\">" +
        (running && canProposeSc ? '<button type="button" class="row-action" data-edit="' + s.id + '">Edit…</button> ' : "") +
        (isAdmin && running ? '<button type="button" class="row-action" data-end="' + s.id + '">End…</button>' : "") +
        (isAdmin && !running ? '<button type="button" class="row-action" data-end="' + s.id + '" title="For a scholarship ended before ending removed the discount from later installments">Finish ending…</button>' : "") + "</td></tr>";
    }).join("");
    $("sc-empty").hidden = r.body.length > 0;
    $("sc-body").querySelectorAll("[data-end]").forEach(function (b) { b.addEventListener("click", function () { openEnd(scRows[b.getAttribute("data-end")]); }); });
    $("sc-body").querySelectorAll("[data-edit]").forEach(function (b) { b.addEventListener("click", function () { openEdit(scRows[b.getAttribute("data-edit")]); }); });
  }

  // ---- remove all discounts (clean start): preview first, then type RESET
  $("sc-reset-open").addEventListener("click", async function () {
    $("sc-reset-error").hidden = true; $("sc-reset-confirm").value = ""; $("sc-reset-save").disabled = true;
    $("sc-reset-summary").textContent = "Working out what would change…"; $("sc-reset-body").innerHTML = "";
    $("sc-reset-modal").showModal();
    const r = await VA.fetchJson("/api/v1/fees/scholarships/reset-preview");
    if (!r.ok) return fail("sc-reset-error", msg(r.body, "Could not work it out."));
    const p = r.body;
    $("sc-reset-summary").innerHTML = p.scholarships ? "<strong>" + p.running + "</strong> running scholarship(s) will end. <strong>" + money(p.removed_from_unpaid) +
      "</strong> of discount comes off the unpaid installments of <strong>" + p.students + "</strong> student(s). " + money(p.kept_on_paid) + " stays on installments already paid."
      : "There are no scholarships or discounts.";
    $("sc-reset-body").innerHTML = p.rows.map(function (x) {
      return "<tr><td><strong>" + esc(x.student) + "</strong><br><small>" + esc(x.roll_number) + "</small></td><td>" + esc(x.scholarship) + "<br><small>" + esc(x.amount) +
        "</small></td><td class=\"num\">" + money(x.removed_from_unpaid) + '</td><td class="num">' + money(x.kept_on_paid_installments) + "</td></tr>";
    }).join("");
    if (p.waiting_requests) fail("sc-reset-error", p.waiting_requests + " scholarship request(s) still wait on the Approvals page. Approve or reject them first.");
    $("sc-reset-save").dataset.ready = p.waiting_requests ? "" : "1";
  });
  $("sc-reset-confirm").addEventListener("input", function () {
    $("sc-reset-save").disabled = !($("sc-reset-save").dataset.ready && $("sc-reset-confirm").value.trim().toUpperCase() === "RESET");
  });
  $("sc-reset-close").addEventListener("click", function () { $("sc-reset-modal").close(); });
  $("sc-reset-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    if ($("sc-reset-save").disabled) return;
    $("sc-reset-error").hidden = true;
    if ($("sc-reset-note").value.trim().length < 5) return fail("sc-reset-error", "Say why (kept on record).");
    $("sc-reset-save").disabled = true;
    const x = await VA.fetchJson("/api/v1/fees/scholarships/reset", json("POST", { confirm: $("sc-reset-confirm").value.trim(), note: $("sc-reset-note").value.trim() }));
    if (!x.ok) { $("sc-reset-save").disabled = false; return fail("sc-reset-error", msg(x.body, "Could not remove them.")); }
    $("sc-reset-modal").close();
    VA.toast("Done: " + x.body.running + " scholarship(s) ended, " + money(x.body.removed_from_unpaid) + " of discount removed from unpaid installments.");
    loadScholarships(); refresh();
  });

  // ---- end: from a chosen day, later unpaid installments lose the discount
  let scEnding = null;
  function openEnd(s) {
    scEnding = s;
    $("sc-end-who").textContent = s.name + " · " + s.student_name + (s.roll_number ? " (" + s.roll_number + ")" : "") + " · " + (s.kind === "PERCENT" ? s.value + "%" : money(s.value));
    const ended = s.status !== "ACTIVE";
    $("sc-end-h").textContent = ended ? "Finish ending this scholarship" : "End scholarship";
    $("sc-end-save").textContent = ended ? "Remove the leftover discount" : "End it";
    $("sc-end-date").value = ended ? String(s.valid_until || s.ended_at || today).slice(0, 10) : today; $("sc-end-date").max = today;
    $("sc-end-reason").value = ""; $("sc-end-error").hidden = true;
    $("sc-end-modal").showModal();
  }
  $("sc-end-close").addEventListener("click", function () { $("sc-end-modal").close(); });
  $("sc-end-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    if (!scEnding) return;
    $("sc-end-error").hidden = true;
    if (!$("sc-end-date").value) return fail("sc-end-error", "Choose the last day it covers.");
    $("sc-end-save").disabled = true;
    const x = await VA.fetchJson("/api/v1/fees/scholarships/" + scEnding.id + "/end", json("POST", { end_date: $("sc-end-date").value, reason: $("sc-end-reason").value.trim() || null }));
    $("sc-end-save").disabled = false;
    if (!x.ok) return fail("sc-end-error", msg(x.body, "Could not end it."));
    $("sc-end-modal").close();
    VA.toast(x.body.withdrawn > 0 ? "Ended. " + money(x.body.withdrawn) + " added back to unpaid installments due after " + day(x.body.valid_until) + "."
      : "Ended. No unpaid installment after " + day(x.body.valid_until) + " had this discount.");
    loadScholarships(); refresh();
  });

  // ---- edit
  let scEditing = null;
  function scopeOf(s) { return s.fee_type_id ? "type:" + s.fee_type_id : s.fee_structure_id ? "fee:" + s.fee_structure_id : ""; }
  function openEdit(s) {
    scEditing = s;
    $("sc-edit-who").textContent = s.student_name + (s.roll_number ? " (" + s.roll_number + ")" : "") + " · given so far " + money(s.given_so_far);
    $("se-structure").innerHTML = $("sc-structure").innerHTML;
    $("se-name").value = s.name; $("se-kind").value = s.kind; $("se-kind").dispatchEvent(new Event("change"));
    $("se-value").value = s.value; $("se-structure").value = scopeOf(s); paintPreview("se");
    $("se-from").value = String(s.valid_from || "").slice(0, 10); $("se-until").value = s.valid_until ? String(s.valid_until).slice(0, 10) : "";
    $("se-auto").checked = !!s.auto_apply; $("se-reason").value = s.reason || ""; $("se-note").value = "";
    $("sc-edit-error").hidden = true;
    $("sc-edit-modal").showModal();
  }
  $("sc-edit-close").addEventListener("click", function () { $("sc-edit-modal").close(); });
  $("sc-edit-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const s = scEditing;
    if (!s) return;
    $("sc-edit-error").hidden = true;
    if ($("se-note").value.trim().length < 5) return fail("sc-edit-error", "Say why you are changing it (kept on record).");
    if (!($("se-value").value > 0)) return fail("sc-edit-error", $("se-kind").value === "FIXED" ? "Enter the amount in rupees." : "Enter the percentage.");
    if ($("se-kind").value === "PERCENT" && parseFloat($("se-value").value) > 100) return fail("sc-edit-error", "A percentage can be at most 100.");
    const body = { note: $("se-note").value.trim() };
    const scope = $("se-structure").value;
    if ($("se-name").value.trim() !== s.name) body.name = $("se-name").value.trim();
    if ($("se-kind").value !== s.kind) body.kind = $("se-kind").value;
    if (parseFloat($("se-value").value) !== Number(s.value)) body.value = parseFloat($("se-value").value);
    if (scope !== scopeOf(s)) {
      body.fee_type_id = scope.indexOf("type:") === 0 ? scope.slice(5) : null;
      body.fee_structure_id = scope.indexOf("fee:") === 0 ? scope.slice(4) : null;
    }
    if ($("se-from").value && $("se-from").value !== String(s.valid_from).slice(0, 10)) body.valid_from = $("se-from").value;
    if (($("se-until").value || null) !== (s.valid_until ? String(s.valid_until).slice(0, 10) : null)) body.valid_until = $("se-until").value || null;
    if ($("se-auto").checked !== !!s.auto_apply) body.auto_apply = $("se-auto").checked;
    if ($("se-reason").value.trim() !== (s.reason || "")) body.reason = $("se-reason").value.trim();
    if (Object.keys(body).length === 1) return fail("sc-edit-error", "Nothing was changed.");
    $("sc-edit-save").disabled = true;
    const x = await VA.fetchJson("/api/v1/fees/scholarships/" + s.id, json("PATCH", body));
    $("sc-edit-save").disabled = false;
    if (!x.ok) return fail("sc-edit-error", msg(x.body, "Could not save the changes."));
    $("sc-edit-modal").close();
    VA.toast(x.body.pending_approval ? x.body.message : "Saved. Unpaid installments now carry " + money(x.body.applied) + " off.");
    loadScholarships(); refresh();
  });
  $("sc-filter").addEventListener("change", loadScholarships);
  $("sa-grant").addEventListener("click", function () {
    if (!current) return;
    document.querySelector('.portal-tab[data-tab="scholarships"]').click();
    scStudent = { id: current.payer.id }; $("sc-student").value = current.payer.name + (current.payer.identifier ? " (" + current.payer.identifier + ")" : "");
    $("sc-name").focus();
  });

  // ------------------------------------------------------------------ Previous dues
  let pdStudent = null;
  studentPicker($("pd-student"), $("pd-student-list"), function (s) { pdStudent = s; });
  $("pd-save").addEventListener("click", async function () {
    $("pd-error").hidden = true;
    if (!pdStudent) return fail("pd-error", "Choose the student from the list.");
    if (!($("pd-amount").value > 0)) return fail("pd-error", "Enter the amount still owed.");
    if (!$("pd-due").value) return fail("pd-error", "Enter the date it must be paid by.");
    $("pd-save").disabled = true;
    const r = await VA.fetchJson("/api/v1/fees/opening-balances", json("POST", { student_id: pdStudent.id, amount: $("pd-amount").value, due_date: $("pd-due").value, note: $("pd-note").value.trim() || null }));
    $("pd-save").disabled = false;
    if (!r.ok) return fail("pd-error", msg(r.body, "Could not add it."));
    VA.toast("Previous dues added, with challan " + r.body.consumer_number + ".");
    ["pd-student", "pd-amount", "pd-note"].forEach(function (id) { $(id).value = ""; }); pdStudent = null;
    loadDues(); refresh();
  });
  $("sa-prev").addEventListener("click", function () {
    if (!current) return;
    document.querySelector('.portal-tab[data-tab="previous-dues"]').click();
    pdStudent = { id: current.payer.id }; $("pd-student").value = current.payer.name + (current.payer.identifier ? " (" + current.payer.identifier + ")" : "");
    $("pd-amount").focus();
  });

  async function sendSheet(path) {
    const f = $("pd-file").files[0];
    if (!f) { VA.toast("Choose the CSV file first.", "error"); return null; }
    const fd = new FormData(); fd.append("file", f);
    return VA.fetchJson("/api/v1/fees/opening-balances/" + path, { method: "POST", body: fd });
  }
  $("pd-check").addEventListener("click", async function () {
    const r = await sendSheet("check");
    if (!r) return;
    const table = $("pd-check-table");
    if (!r.ok) { $("pd-check-summary").textContent = msg(r.body, "Could not read the sheet."); table.hidden = true; $("pd-import").disabled = true; return; }
    const bad = r.body.filter(function (x) { return !x.ok; });
    const total = r.body.reduce(function (t, x) { return t + (x.ok ? x.amount : 0); }, 0);
    $("pd-check-summary").textContent = r.body.length + " row(s), " + money(total) + (bad.length ? ". " + bad.length + " row(s) have problems: fix them and check again." : ". Every row is correct.");
    table.hidden = false;
    table.tBodies[0].innerHTML = r.body.map(function (x) {
      return '<tr class="' + (x.ok ? "" : "replaced") + '"><td>' + x.line + "</td><td>" + esc(x.roll_number) + "</td><td>" + esc(x.student_name || "—") + '</td><td class="num">' + (x.amount != null ? money(x.amount) : "—") +
        "</td><td>" + esc(day(x.due_date)) + "</td><td>" + (x.errors.length ? '<span class="bad">' + esc(x.errors.join("; ")) + "</span>" : "OK") + "</td></tr>";
    }).join("");
    $("pd-import").disabled = bad.length > 0;
  });
  $("pd-import").addEventListener("click", async function () {
    if (!(await VA.confirm("Import every row? Each student gets the amount as previous dues, with a challan, and is told by notification.", "Import"))) return;
    $("pd-import").disabled = true;
    const r = await sendSheet("import");
    if (!r) return;
    if (!r.ok) { $("pd-import").disabled = false; return VA.toast(msg(r.body, "Nothing was imported."), "error"); }
    VA.toast("Imported " + r.body.imported + " row(s), " + money(r.body.total_amount) + ".");
    $("pd-file").value = ""; $("pd-check-table").hidden = true; $("pd-check-summary").textContent = ""; loadDues();
  });

  let pdTimer = null;
  $("pd-q").addEventListener("input", function () { clearTimeout(pdTimer); pdTimer = setTimeout(loadDues, 300); });
  async function loadDues() {
    const q = $("pd-q").value.trim();
    const r = await VA.fetchJson("/api/v1/fees/opening-balances" + (q ? "?q=" + encodeURIComponent(q) : ""));
    if (!r.ok) return;
    $("pd-body").innerHTML = r.body.map(function (x) {
      return "<tr><td><strong>" + esc(x.student_name) + "</strong><br><small>" + esc(x.roll_number || "") + "</small></td><td>" + esc(x.note || "") + (x.cancelled ? "<br><small>" + money(x.cancelled) + " cancelled</small>" : "") +
        "</td><td>" + esc(day(x.due_date)) + '</td><td class="num">' + money(x.amount) + '</td><td class="num">' + money(x.paid) + '</td><td class="num"><b>' + money(x.remaining) + "</b></td><td>" +
        (isAdmin && x.remaining > 0.005 ? '<button type="button" class="row-action" data-cancel="' + x.ledger_entry_id + '">Cancel…</button>' : "") + "</td></tr>";
    }).join("");
    $("pd-empty").hidden = r.body.length > 0;
    $("pd-body").querySelectorAll("[data-cancel]").forEach(function (b) {
      b.addEventListener("click", async function () {
        const why = await VA.ask({ title: "Cancel this previous-dues entry?", danger: true, ok: "Cancel it", min: 5,
          intro: "For an entry typed in by mistake. What is still owed on it is written off; anything already paid stays recorded." });
        if (!why) return;
        const x = await VA.fetchJson("/api/v1/fees/opening-balances/" + b.getAttribute("data-cancel") + "/cancel", json("POST", { reason: why }));
        if (!x.ok) return VA.toast(msg(x.body, "Could not cancel it."), "error");
        VA.toast(x.body.pending_approval ? x.body.message : money(x.body.written_off) + " written off."); loadDues(); refresh();
      });
    });
  }

  document.addEventListener("vistadmin:ready", function (event) {
    const roles = (event.detail && event.detail.me && event.detail.me.roles) || [];
    isAdmin = roles.indexOf("ADMIN") !== -1;
    document.querySelectorAll(".admin-only").forEach(function (el) { el.hidden = !isAdmin; });
    // The accounts office proposes scholarships and discounts; an administrator approves them (Approvals page).
    const perms = (event.detail && event.detail.me && event.detail.me.permissions) || [];
    const canPropose = perms.indexOf("scholarship.manage") !== -1 || perms.indexOf("scholarship.propose") !== -1;
    document.querySelectorAll(".can-propose").forEach(function (el) { el.hidden = !canPropose; });
    canProposeSc = canPropose;
    // Previous dues: the administrator and the accounts office can bring them in; only an administrator can cancel one.
    const canAddDues = perms.indexOf("fee.opening_balance.add") !== -1;
    document.querySelectorAll(".can-add-dues").forEach(function (el) { el.hidden = !canAddDues; });
    $("sc-from").value = defaultFrom();
    setTimeout(function () { if (!$("sc-from").dataset.touched) $("sc-from").value = defaultFrom(); }, 1500);   // once the terms have loaded
    $("sc-from").addEventListener("change", function () { $("sc-from").dataset.touched = "1"; });
    $("pd-due").value = today;
    loadStructuresForScholarship(); loadScholarships(); loadDues();
  });
})();
