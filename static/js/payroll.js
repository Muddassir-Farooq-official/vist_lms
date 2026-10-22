(function () {
  "use strict";

  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/payroll";
  const $ = function (id) { return document.getElementById(id); };
  const msg = function (body, fallback) { return body && body.error && body.error.message ? body.error.message : fallback; };
  const json = function (method, data) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }; };
  const money = function (v) { return VA.formatMoney(v); };
  const day = function (iso) { return iso ? new Date(String(iso).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "\u2014"; };
  const tile = function (a, b, c, tone) { return '<div class="kpi ' + (tone || "") + '"><span class="kpi-label">' + a + '</span><strong class="kpi-value">' + b + '</strong><span class="kpi-sub">' + (c || "") + "</span></div>"; };
  const STATUS = { DRAFT: ["Draft", "pending"], APPROVED: ["Approved: ready to pay", "enrolled"], PAID: ["Paid", "active"], CANCELLED: ["Cancelled", ""] };
  const pill = function (s) { const x = STATUS[s] || [s, ""]; return '<span class="status-pill ' + x[1] + '">' + esc(x[0]) + "</span>"; };
  const fail = function (id, t) { $(id).textContent = t; $(id).hidden = false; };
  let isApprover = false, run = null, slipEdit = null, itemEmployee = null, advEmployee = null, today = new Date().toISOString().slice(0, 10);

  // ------------------------------------------------------------------ an employee picker
  function picker(input, list, onPick) {
    let timer = null;
    const state = { employee: null };
    input.addEventListener("input", function () {
      state.employee = null; onPick(null); clearTimeout(timer);
      timer = setTimeout(async function () {
        list.innerHTML = "";
        const q = input.value.trim();
        if (q.length < 2) return;
        const r = await VA.fetchJson(API + "/employees?q=" + encodeURIComponent(q), { quiet: true });
        if (!r.ok) return;
        r.body.forEach(function (e) {
          const b = document.createElement("button"); b.type = "button"; b.className = "picker-item";
          b.innerHTML = "<strong>" + esc(e.name) + "</strong><small>" + esc(e.employee_code) + " \u00b7 " + esc(e.job_title || "") + " \u00b7 " + money(e.base_salary) + "</small>";
          b.addEventListener("click", function () { state.employee = e; input.value = e.name + " (" + e.employee_code + ")"; list.innerHTML = ""; onPick(e); });
          list.appendChild(b);
        });
        if (!r.body.length) list.innerHTML = '<p class="cc-meta">Nobody matches.</p>';
      }, 300);
    });
    return state;
  }

  // ------------------------------------------------------------------ the runs
  async function loadRuns() {
    const r = await VA.fetchJson(API + "/runs");
    if (!r.ok) return;
    const body = $("rn-body"); body.innerHTML = "";
    r.body.forEach(function (x) {
      const tr = document.createElement("tr"); tr.className = "clickrow";
      tr.innerHTML = "<td><strong>" + esc(x.month_label) + "</strong><small>" + esc(x.reference) + "</small></td><td>" + pill(x.status) + '</td><td class="num">' + x.employees_count + '</td><td class="num">' + money(x.total_gross) + '</td><td class="num">' + money(x.total_deductions) + '</td><td class="num"><b>' + money(x.total_net) + "</b></td>";
      tr.addEventListener("click", function () { openRun(x.id); });
      body.appendChild(tr);
    });
    $("rn-empty").hidden = r.body.length > 0;
  }
  $("pr-prepare").addEventListener("click", async function () {
    $("pr-prep-error").hidden = true;
    if (!$("pr-month").value) return fail("pr-prep-error", "Choose the month to pay for.");
    const wd = parseInt($("pr-wd").value || "0", 10);
    const r = await VA.fetchJson(API + "/runs", json("POST", { month: $("pr-month").value + "-01", working_days: wd > 0 ? wd : null }));
    if (!r.ok) return fail("pr-prep-error", msg(r.body, "Could not prepare it."));
    VA.toast("Prepared payroll for " + r.body.month_label + ": " + r.body.employees_count + " employees." + (r.body.skipped.length ? " Left out: " + r.body.skipped.join("; ") + "." : ""), r.body.skipped.length ? "error" : undefined);
    loadRuns(); openRun(r.body.id);
  });

  async function openRun(id) {
    const r = await VA.fetchJson(API + "/runs/" + id);
    if (!r.ok) return VA.toast(msg(r.body, "Could not open it."), "error");
    const d = r.body; run = d;
    $("rd-badges").innerHTML = '<span class="pill">' + esc(d.reference) + "</span>" + pill(d.status);
    $("rd-title").textContent = "Payroll for " + d.month_label;
    $("rd-meta").textContent = [d.created_by_name ? "Prepared by " + d.created_by_name : "", d.approved_by_name ? "approved by " + d.approved_by_name : "", d.status === "PAID" ? "paid " + day(d.paid_date) + (d.payment_reference ? " (" + d.payment_reference + ")" : "") + " \u00b7 booked as " + (d.expense_reference || "") : ""].filter(Boolean).join(" \u00b7 ");
    $("rd-tiles").innerHTML = '<div class="kpi-grid kpi-grid-6">' + tile("Employees", d.employees_count, d.held_count ? d.held_count + " held back" : "") + tile("Gross pay", money(d.total_gross), "") + tile("Deductions", money(d.total_deductions), "tax, advances, unpaid days...") + tile("Net pay", money(d.total_net), "to be paid out", "good") + "</div>";
    $("rd-problems").innerHTML = d.problems.length ? '<p class="dash-alert error">' + d.problems.map(esc).join("<br>") + "</p>" : (d.status === "CANCELLED" ? '<div class="cc-note rejected"><strong>Cancelled</strong><span>' + esc(d.cancel_reason || "") + "</span></div>" : "");
    const body = $("rd-body"); body.innerHTML = "";
    d.payslips.forEach(function (s) {
      const tr = document.createElement("tr");
      if (s.held) tr.className = "replaced";
      const notes = [s.attendance_note || "", s.days_payable < s.days_in_month ? "joined: " + s.days_payable + "/" + s.days_in_month + " days" : "", s.unpaid_days ? s.unpaid_days + " unpaid day" + (s.unpaid_days !== 1 ? "s" : "") : "", s.adjustment ? (s.adjustment > 0 ? "+" : "\u2212") + money(Math.abs(s.adjustment)) + (s.adjustment_note ? " " + s.adjustment_note : "") : "", s.held ? "HELD: " + (s.held_reason || "") : ""].filter(Boolean);
      tr.innerHTML = "<td><strong>" + esc(s.employee_name) + "</strong><small>" + esc(s.employee_code) + " \u00b7 " + esc(s.job_title || "") + '</small></td><td class="num">' + money(s.base_salary) + '</td><td class="num">' + s.working_days + '</td><td class="num">' + s.absent_days + (s.casual_leaves ? "<small>" + s.casual_leaves + " casual</small>" : "") +
        '</td><td class="num">' + s.late_marks + (s.late_days ? "<small>= " + s.late_days + " day" + (s.late_days > 1 ? "s" : "") + "</small>" : "") + (s.attendance_override ? "<small>set by hand</small>" : "") + '</td><td class="num">' + money(s.gross) + '</td><td class="num">' + money(s.deductions) + '</td><td class="num ' + (s.net < 0 ? "bad" : "") + '"><b>' + money(s.net) + "</b></td><td><small>" + esc(notes.join(" \u00b7 ")) + "</small></td><td></td>";
      const cell = tr.lastElementChild;
      const pdf = document.createElement("a"); pdf.className = "role-btn small-btn secondary-btn"; pdf.href = API + "/payslips/" + s.id + "/pdf"; pdf.setAttribute("data-download", "payslip-" + s.employee_code + ".pdf"); pdf.setAttribute("data-label", "file"); pdf.textContent = "PDF"; cell.appendChild(pdf);
      if (d.status === "DRAFT") { const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn"; b.textContent = "Adjust"; b.addEventListener("click", function () { openSlip(s); }); cell.appendChild(b); }
      if (s.held && (d.status === "APPROVED" || d.status === "PAID")) {
        const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn"; b.textContent = d.status === "PAID" ? "Pay now\u2026" : "Release";
        b.addEventListener("click", function () {
          if (d.status === "PAID") return openPay(s);
          VA.fetchJson(API + "/runs/" + run.id + "/payslips/" + s.id + "/release", json("POST", { payment_method: "CASH", paid_date: today, reference_no: null }))
            .then(function (r) { if (!r.ok) return fail("rd-error", msg(r.body, "Could not release it.")); VA.toast("Released: it will be paid with the rest of the month."); openRun(run.id); loadRuns(); });
        });
        cell.appendChild(b);
      }
      if (s.released_on) { const sm = document.createElement("small"); sm.textContent = "Paid separately " + day(s.released_on); cell.appendChild(sm); }
      body.appendChild(tr);
    });
    const bar = $("rd-actions"); bar.innerHTML = ""; $("rd-error").hidden = true;
    const add = function (label, cls, fn) { const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn " + cls; b.textContent = label; b.addEventListener("click", fn); bar.appendChild(b); };
    if (d.status === "DRAFT") { add("Recalculate", "secondary-btn", function () { call("recalculate", null, "Worked out again."); }); if (isApprover) add("Approve", "", function () { call("approve", null, "Approved."); }); }
    if (d.status === "APPROVED") add("Pay the salaries\u2026", "", openPay);
    if (d.status === "DRAFT" || d.status === "APPROVED") add("Cancel\u2026", "danger-btn", function () { ask("Cancel this payroll run", "Nothing has been paid. You can prepare the month again.", "Cancel it").then(function (t) { if (t) call("cancel", { reason: t }, "Cancelled."); }); });
    if (d.status === "PAID" && isApprover) add("Reverse the payment\u2026", "danger-btn", function () { ask("Reverse this payment", "The salary expense is voided, advances recovered are restored, and the run goes back to Approved.", "Reverse it").then(function (t) { if (t) call("reverse-payment", { reason: t }, "Payment reversed."); }); });
    if (!$("rd-modal").open) $("rd-modal").showModal();
  }
  async function call(what, body, done) {
    const r = await VA.fetchJson(API + "/runs/" + run.id + "/" + what, body ? json("POST", body) : { method: "POST" });
    if (!r.ok) return fail("rd-error", msg(r.body, "That did not work."));
    VA.toast(done); await openRun(run.id); loadRuns(); loadAdvances();
  }
  $("rd-close").addEventListener("click", function () { $("rd-modal").close(); });

  // ------------------------------------------------------------------ adjusting a payslip
  async function openSlip(s) {
    slipEdit = s;
    $("sl-title").textContent = s.employee_name; $("sl-sub").textContent = s.employee_code + " \u00b7 basic " + money(s.base_salary) + " \u00b7 " + s.days_in_month + " days in the month";
    $("sl-wd").value = s.working_days; $("sl-absent").value = s.absent_days; $("sl-late").value = s.late_marks; $("sl-reset").checked = false;
    $("sl-att").textContent = (s.attendance_note || "") + " Day's pay: " + money(s.day_rate) + ". Casual leave forgiven: " + s.casual_leaves + ". Every " + s.late_marks_per_day + " late / early marks = 1 day." + (s.attendance_override ? " (Figures set by hand.)" : "");
    $("sl-unpaid").value = s.unpaid_days || 0; $("sl-adj").value = s.adjustment || ""; $("sl-note").value = s.adjustment_note || ""; $("sl-hold").checked = s.held; $("sl-hold-reason").value = s.held_reason || ""; $("sl-hold-wrap").hidden = !s.held; $("sl-error").hidden = true;
    const d = await VA.fetchJson(API + "/payslips/" + s.id, { quiet: true });
    $("sl-lines").innerHTML = d.ok ? "<b>Now:</b> " + d.body.earnings.concat(d.body.deduction_lines.map(function (x) { return { label: x.label, amount: -x.amount }; })).map(function (x) { return esc(x.label) + " " + (x.amount < 0 ? "\u2212" : "") + money(Math.abs(x.amount)); }).join(" \u00b7 ") : "";
    $("sl-modal").showModal();
  }
  $("sl-hold").addEventListener("change", function () { $("sl-hold-wrap").hidden = !$("sl-hold").checked; });
  $("sl-close").addEventListener("click", function () { $("sl-modal").close(); });
  $("sl-save").addEventListener("click", async function () {
    const body = { working_days: parseInt($("sl-wd").value || "0", 10) || null, unpaid_days: parseFloat($("sl-unpaid").value || "0"), adjustment: parseFloat($("sl-adj").value || "0"), adjustment_note: $("sl-note").value.trim(), held: $("sl-hold").checked, held_reason: $("sl-hold").checked ? $("sl-hold-reason").value.trim() : null };
    if ($("sl-reset").checked) body.reset_attendance = true;
    else {
      if (parseFloat($("sl-absent").value) !== slipEdit.absent_days) body.absent_days = parseFloat($("sl-absent").value || "0");
      if (parseInt($("sl-late").value, 10) !== slipEdit.late_marks) body.late_marks = parseInt($("sl-late").value || "0", 10);
    }
    const r = await VA.fetchJson(API + "/runs/" + run.id + "/payslips/" + slipEdit.id, json("PATCH", body));
    if (!r.ok) return fail("sl-error", msg(r.body, "Could not save it."));
    $("sl-modal").close(); VA.toast("Saved. The payslip has been worked out again."); await openRun(run.id); loadRuns();
  });

  // ------------------------------------------------------------------ paying
  let paySlip = null;   // a held payslip paid on its own, or null for the whole month
  let paySecurity = null;   // a security deposit being paid back
  function openPaySecurity(p) {
    paySlip = null; paySecurity = p;
    $("pp-for").textContent = "Security deposit of " + p.name + ": " + money(p.held) + " to pay back";
    $("pp-ref").value = ""; $("pp-date").value = today; $("pp-error").hidden = true; $("pp-modal").showModal();
  }
  function openPay(slip) {
    paySecurity = null;
    paySlip = slip && slip.id ? slip : null;
    $("pp-for").textContent = paySlip ? paySlip.employee_name + " \u00b7 held payslip for " + run.month_label + " \u00b7 " + money(paySlip.net)
      : run.month_label + " \u00b7 " + run.employees_count + " employees \u00b7 " + money(run.total_net); $("pp-ref").value = ""; $("pp-date").value = today; $("pp-error").hidden = true; $("pp-modal").showModal(); }
  $("pp-cancel").addEventListener("click", function () { $("pp-modal").close(); });
  $("pp-ok").addEventListener("click", async function () {
    const body = { payment_method: $("pp-method").value, paid_date: $("pp-date").value, reference_no: $("pp-ref").value.trim() || null };
    if (paySecurity) {
      const s = await VA.fetchJson(API + "/security/" + paySecurity.employee_id + "/release", json("POST", body));
      if (!s.ok) return fail("pp-error", msg(s.body, "Could not record the payment."));
      $("pp-modal").close(); VA.toast("Paid back " + money(s.body.paid_back) + ". Booked as " + s.body.expense_reference + "."); paySecurity = null; loadSecurity(); return;
    }
    const path = paySlip ? "/payslips/" + paySlip.id + "/release" : "/pay";
    const r = await VA.fetchJson(API + "/runs/" + run.id + path, json("POST", body));
    if (!r.ok) return fail("pp-error", msg(r.body, "Could not record the payment."));
    $("pp-modal").close();
    VA.toast(paySlip ? "Paid " + paySlip.employee_name + "'s held salary. It is booked as its own expense." : "Paid. Everyone has been sent their payslip. Booked as " + r.body.expense_reference + ".");
    paySlip = null; await openRun(run.id); loadRuns();
  });

  let asking = null;
  function ask(title, intro, ok) {
    return new Promise(function (resolve) { $("pa-ask-title").textContent = title; $("pa-ask-intro").textContent = intro; $("pa-ask-text").value = ""; $("pa-ask-error").hidden = true; $("pa-ask-ok").textContent = ok; asking = resolve; $("pa-ask").showModal(); });
  }
  $("pa-ask-ok").addEventListener("click", function () { const t = $("pa-ask-text").value.trim(); if (t.length < 3) return fail("pa-ask-error", "Give a short reason."); const r = asking; asking = null; $("pa-ask").close(); r(t); });
  $("pa-ask-cancel").addEventListener("click", function () { $("pa-ask").close(); });
  $("pa-ask").addEventListener("close", function () { if (asking) { const r = asking; asking = null; r(null); } });

  // ------------------------------------------------------------------ allowances and deductions
  async function loadItems() {
    if (!itemEmployee) return;
    const r = await VA.fetchJson(API + "/items?employee_id=" + itemEmployee.id);
    if (!r.ok) return;
    $("it-body").innerHTML = r.body.map(function (i) {
      return "<tr><td><strong>" + esc(i.name) + "</strong></td><td>" + (i.kind === "ALLOWANCE" ? "Allowance" : "Deduction") + "</td><td>" + (i.calc === "PERCENT_OF_BASE" ? i.value + "% of basic" : "Fixed") + '</td><td class="num"><b>' + (i.calc === "PERCENT_OF_BASE" ? money(itemEmployee.base_salary * i.value / 100) : money(i.value)) + '</b></td><td><label class="check-row"><input type="checkbox" data-item="' + i.id + '"' + (i.is_active ? " checked" : "") + " /> <span>" + (i.is_active ? "On" : "Off") + "</span></label></td></tr>";
    }).join("") || '<tr><td colspan="5" class="cc-meta">No allowances or deductions yet.</td></tr>';
    $("it-body").querySelectorAll("input[data-item]").forEach(function (c) {
      c.addEventListener("change", async function () { const x = await VA.fetchJson(API + "/items/" + c.getAttribute("data-item"), json("PATCH", { is_active: c.checked })); if (x.ok) loadItems(); else VA.toast(msg(x.body, "Could not change it."), "error"); });
    });
  }
  const itPick = picker($("it-emp"), $("it-emp-list"), function (e) { itemEmployee = e; $("it-panel").hidden = !e; if (e) { $("it-title").textContent = e.name + " \u00b7 basic salary " + money(e.base_salary); loadItems(); } });
  $("it-add").addEventListener("click", async function () {
    $("it-error").hidden = true;
    if (!itemEmployee) return fail("it-error", "Choose the employee first.");
    if ($("it-name").value.trim().length < 2) return fail("it-error", "Give it a name.");
    if (!($("it-value").value > 0)) return fail("it-error", "Enter the amount or percentage.");
    const r = await VA.fetchJson(API + "/items", json("POST", { employee_id: itemEmployee.id, kind: $("it-kind").value, name: $("it-name").value.trim(), calc: $("it-calc").value, value: $("it-value").value }));
    if (!r.ok) return fail("it-error", msg(r.body, "Could not add it."));
    $("it-name").value = ""; $("it-value").value = ""; VA.toast("Added."); loadItems();
  });

  // ------------------------------------------------------------------ advances
  async function loadAdvances() {
    const r = await VA.fetchJson(API + "/advances");
    if (!r.ok) return;
    $("ad-body").innerHTML = r.body.map(function (a) { return "<tr><td><strong>" + esc(a.employee_name) + "</strong><small>" + esc(a.employee_code) + "</small></td><td>" + esc(day(a.granted_on)) + '</td><td class="num">' + money(a.amount) + '</td><td class="num">' + money(a.monthly_recovery) + '</td><td class="num"><b>' + money(a.balance) + '</b></td><td><span class="status-pill ' + (a.status === "ACTIVE" ? "pending" : "active") + '">' + (a.status === "ACTIVE" ? "Being recovered" : "Cleared") + "</span></td></tr>"; }).join("");
    $("ad-empty").hidden = r.body.length > 0;
  }
  const adPick = picker($("ad-emp"), $("ad-emp-list"), function (e) { advEmployee = e; });
  $("ad-save").addEventListener("click", async function () {
    $("ad-error").hidden = true;
    if (!advEmployee) return fail("ad-error", "Choose the employee from the list.");
    if (!($("ad-amount").value > 0) || !($("ad-rec").value > 0)) return fail("ad-error", "Enter the advance and how much is taken back each month.");
    if (!$("ad-date").value || $("ad-reason").value.trim().length < 3) return fail("ad-error", "Enter the date and the reason.");
    const r = await VA.fetchJson(API + "/advances", json("POST", { employee_id: advEmployee.id, amount: $("ad-amount").value, monthly_recovery: $("ad-rec").value, reason: $("ad-reason").value.trim(), payment_method: $("ad-method").value, reference_no: $("ad-ref").value.trim() || null, granted_on: $("ad-date").value }));
    if (!r.ok) return fail("ad-error", msg(r.body, "Could not record it."));
    VA.toast("Advance paid. It will be taken back from the next payslips."); ["ad-emp", "ad-amount", "ad-rec", "ad-ref", "ad-reason"].forEach(function (id) { $(id).value = ""; }); advEmployee = null; loadAdvances();
  });

  // ------------------------------------------------------------------ security deposits
  async function loadWithholdings() {
    const y = $("wh-year");
    if (!y.value) y.value = new Date().getFullYear();
    const r = await VA.fetchJson(API + "/withholdings?year=" + encodeURIComponent(y.value));
    const tb = $("wh-table").tBodies[0];
    if (!r.ok) { tb.innerHTML = '<tr><td colspan="3">' + esc(msg(r.body, "Could not load this.")) + "</td></tr>"; return; }
    tb.innerHTML = r.body.months.length ? r.body.months.map(function (m) {
      const parts = Object.keys(m.by_head).map(function (k) { return esc(k) + " " + money(m.by_head[k]); }).join(" \u00b7 ");
      return "<tr><td>" + esc(new Date(String(m.month).slice(0, 10) + "T00:00:00").toLocaleDateString("en-GB", { month: "long", year: "numeric" })) + "</td><td>" + parts + '</td><td class="num"><b>' + money(m.total) + "</b></td></tr>";
    }).join("") : '<tr><td colspan="3">Nothing held from paid payrolls in ' + esc(String(r.body.year)) + ".</td></tr>";
    $("wh-total").textContent = "Year total: " + money(r.body.total) + (r.body.heads.length ? " (" + r.body.heads.map(function (h) { return h.name + " " + money(h.total); }).join(", ") + ")" : "") +
      ". Security deposits held right now: " + money(r.body.security_held_now) + ".";
  }
  $("wh-year").addEventListener("change", loadWithholdings);

  async function loadSecurity() {
    loadWithholdings();
    const r = await VA.fetchJson(API + "/security");
    if (!r.ok) return;
    $("sc-intro").textContent = "Each month " + (r.body.institute_percent ? r.body.institute_percent + "% of basic salary is held" : "nothing is held (the institute percentage in Settings \u2192 Payroll is 0)") +
      " until the total held equals one basic salary. A different percentage (0 = none) can be set for one person. Total held now: " + money(r.body.total_held) + ".";
    const tb = $("sc-body"); tb.innerHTML = "";
    r.body.people.forEach(function (p) {
      const tr = document.createElement("tr");
      tr.innerHTML = "<td><strong>" + esc(p.name) + "</strong>" + (p.left ? ' <span class="status-pill dropped">left</span>' : "") + "<small>" + esc(p.employee_code + " \u00b7 " + (p.job_title || "")) + (p.released ? " \u00b7 " + money(p.released) + " paid back" : "") + '</small></td><td class="num">' + money(p.base_salary) + '</td><td class="num"><b>' + money(p.held) + "</b>" +
        (p.opening ? "<small>" + money(p.opening) + " before</small>" : "") + '</td><td class="num">' + money(p.remaining) + "</td><td>" + (p.own_percent == null ? '<span class="cc-meta">institute</span>' : p.own_percent + "%") + (p.note ? "<small>" + esc(p.note) + "</small>" : "") + "</td><td></td>";
      if (isApprover) {
        const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn secondary-btn"; b.textContent = "Change";
        b.addEventListener("click", async function () {
          const pct = (await window.VistAdmin.promptText("Security % of basic for " + p.name + " (empty = the institute's %, 0 = none):", p.own_percent == null ? "" : String(p.own_percent)));
          if (pct === null) return;
          const opening = (await window.VistAdmin.promptText("Deposit already held before this system (Rs.):", String(p.opening || 0)));
          if (opening === null) return;
          const x = await VA.fetchJson(API + "/settings/" + p.employee_id, json("PUT", { security_percent: pct.trim() === "" ? null : Number(pct), security_opening: Number(opening || 0), note: p.note }));
          if (!x.ok) return VA.toast(msg(x.body, "Could not save it."), "error");
          VA.toast("Saved. It applies when a draft payroll is recalculated."); loadSecurity();
        });
        tr.lastElementChild.appendChild(b);
      }
      if (p.held > 0) {
        const pb = document.createElement("button"); pb.type = "button"; pb.className = "role-btn small-btn" + (p.left ? "" : " secondary-btn"); pb.textContent = "Pay back\u2026";
        pb.addEventListener("click", function () { openPaySecurity(p); });
        tr.lastElementChild.appendChild(pb);
      }
      tb.appendChild(tr);
    });
  }
  document.querySelectorAll('.portal-tab[data-tab="security"]').forEach(function (t) { t.addEventListener("click", loadSecurity); });

  document.addEventListener("vistadmin:ready", async function () {
    const me = await VA.fetchJson("/api/v1/auth/me", { quiet: true });
    isApprover = !!(me.ok && (me.body.permissions || []).indexOf("payroll.approve") !== -1);
    const d = new Date(); $("pr-month").value = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0"); $("ad-date").value = today;
    loadRuns(); loadAdvances();
    const open = new URLSearchParams(window.location.search).get("open");
    if (open === "advances") VA.showTab("advances");
  });
})();