(function () {
  "use strict";

  // Inventory / store: /api/v1/inventory/...
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/inventory";
  const $ = function (id) { return document.getElementById(id); };
  const money = function (v) { return VA.formatMoney(v); };
  const qty = function (v) { return Number(v || 0).toLocaleString("en-PK", { maximumFractionDigits: 2 }); };
  const tile = function (a, b, c, tone) { return '<div class="kpi ' + (tone || "") + '"><span class="kpi-label">' + a + '</span><strong class="kpi-value">' + b + '</strong><span class="kpi-sub">' + (c || "") + "</span></div>"; };
  const KIND = { OPENING: "Opening stock", RECEIVED: "Received", ISSUED: "Issued", RETURNED: "Returned", ADJUSTED: "Correction", REVERSED: "Reversal" };
  const MODE = { FREE: "free", PAID_NOW: "paid", ADD_TO_DUES: "added to dues" };
  let canManage = false, canAdjust = false, items = [], editing = null, debounce = null;

  function say(t, bad) { const el = $("inv-msg"); el.textContent = t || ""; el.hidden = !t; el.className = bad ? "dash-alert error" : "panel-hint"; if (t) el.scrollIntoView({ block: "nearest" }); }
  function errText(r) { return (r.body && r.body.error && r.body.error.message) || "That did not work."; }
  function postJson(url, body) { return VA.fetchJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }
  function num(id) { const v = $(id).value; return v === "" ? null : Number(v); }

  // ------------------------------------------------------------------ summary + items
  async function loadSummary() {
    const r = await VA.fetchJson(API + "/summary");
    if (!r.ok) return;
    const s = r.body, m = s.month;
    $("inv-tiles").innerHTML = '<div class="kpi-grid kpi-grid-6">' +
      tile("Stock value", money(s.stock_value), s.items + " items", "good") +
      tile("Low stock", s.low_stock, "at or below the warning level", s.low_stock ? "warn" : "") +
      tile("Received this month", money(m.received_value), "at cost") +
      tile("Given free this month", money(m.issued_free_cost), "at cost") +
      tile("Sold this month", money(m.sold_amount), money(m.charged_to_dues) + " added to dues") +
      tile("With people", s.with_people, s.purchases_waiting ? s.purchases_waiting + " purchase(s) to put into stock" : "returnable items out", s.purchases_waiting ? "warn" : "") + "</div>";
  }

  async function loadItems() {
    const p = new URLSearchParams();
    if ($("inv-q").value.trim()) p.set("q", $("inv-q").value.trim());
    if ($("inv-low").checked) p.set("low_only", "true");
    if ($("inv-inactive").checked) p.set("include_inactive", "true");
    const r = await VA.fetchJson(API + "/items?" + p.toString());
    if (!r.ok) return say(errText(r), true);
    const tb = $("inv-items");
    tb.innerHTML = "";
    r.body.forEach(function (i) {
      const tr = document.createElement("tr");
      tr.innerHTML = "<td>" + esc(i.code) + "</td><td><strong>" + esc(i.name) + "</strong>" + (i.is_returnable ? " <small>(returnable)</small>" : "") + (i.is_active ? "" : ' <span class="status-pill dropped">Off</span>') + "</td>" +
        "<td>" + esc(i.category) + '</td><td class="num">' + (i.low_stock ? '<span class="status-pill dropped">' : "<span>") + qty(i.quantity_on_hand) + " " + esc(i.unit) + "</span></td>" +
        '<td class="num">' + money(i.average_cost) + '</td><td class="num">' + money(i.stock_value) + '</td><td class="num">' + (i.sale_price != null ? money(i.sale_price) : "—") + '</td><td class="row-actions"></td>';
      const cell = tr.querySelector(".row-actions");
      const hist = button("History", "secondary-btn", function () { $("hi-item").value = i.id; $("hi-kind").value = ""; openTab("history"); loadHistory(); });
      cell.appendChild(hist);
      if (canManage) cell.appendChild(button("Edit", "secondary-btn", function () { openItem(i); }));
      if (canAdjust) cell.appendChild(button("Correct stock", "secondary-btn", function () { adjustItem(i); }));
      tb.appendChild(tr);
    });
    $("inv-items-empty").hidden = r.body.length > 0;
  }

  async function loadAllItems() {
    const r = await VA.fetchJson(API + "/items?include_inactive=true");
    if (!r.ok) return;
    items = r.body;
    const active = items.filter(function (i) { return i.is_active; });
    $("is-item").innerHTML = '<option value="">Choose…</option>' + active.map(function (i) { return '<option value="' + i.id + '">' + esc(i.name + " (" + qty(i.quantity_on_hand) + " " + i.unit + " in stock)") + "</option>"; }).join("");
    $("hi-item").innerHTML = '<option value="">All items</option>' + items.map(function (i) { return '<option value="' + i.id + '">' + esc(i.code + " — " + i.name) + "</option>"; }).join("");
    const cats = Array.from(new Set(items.map(function (i) { return i.category; })));
    $("inv-categories").innerHTML = cats.map(function (c) { return '<option value="' + esc(c) + '">'; }).join("");
  }

  function button(text, cls, fn) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "role-btn small-btn " + (cls || "");
    b.textContent = text;
    b.addEventListener("click", fn);
    return b;
  }

  function openTab(name) { const t = document.querySelector('.portal-tab[data-tab="' + name + '"]'); if (t) t.click(); }

  async function refreshAll() { await Promise.all([loadSummary(), loadItems(), loadAllItems()]); }

  // ------------------------------------------------------------------ add / edit item
  function openItem(i) {
    editing = i || null;
    $("inv-item-title").textContent = i ? "Edit " + i.code : "Add item";
    $("it-name").value = i ? i.name : "";
    $("it-code").value = i ? i.code : "";
    $("it-code").disabled = !!i;
    $("it-category").value = i ? i.category : "";
    $("it-unit").value = i ? i.unit : "";
    $("it-price").value = i && i.sale_price != null ? i.sale_price : "";
    $("it-reorder").value = i ? i.reorder_level : "";
    $("it-open-qty").value = "";
    $("it-open-cost").value = "";
    $("it-returnable").checked = !!(i && i.is_returnable);
    $("it-active").checked = !i || i.is_active;
    document.querySelectorAll("#inv-item-form [data-new-only]").forEach(function (el) { el.hidden = !!i; });
    document.querySelectorAll("#inv-item-form [data-edit-only]").forEach(function (el) { el.hidden = !i; });
    $("it-error").hidden = true;
    $("inv-item-modal").showModal();
  }

  $("inv-item-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const body = { name: $("it-name").value.trim(), category: $("it-category").value.trim() || null, unit: $("it-unit").value.trim() || null,
      sale_price: num("it-price"), reorder_level: num("it-reorder"), is_returnable: $("it-returnable").checked };
    let r;
    if (editing) {
      body.is_active = $("it-active").checked;
      r = await VA.fetchJson(API + "/items/" + editing.id, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    } else {
      body.code = $("it-code").value.trim() || null;
      body.opening_quantity = num("it-open-qty");
      body.opening_unit_cost = num("it-open-cost");
      r = await postJson(API + "/items", body);
    }
    if (!r.ok) { $("it-error").textContent = errText(r); $("it-error").hidden = false; return; }
    $("inv-item-modal").close();
    say((editing ? "Saved " : "Added ") + r.body.name + " (" + r.body.code + ").");
    refreshAll();
  });
  $("inv-item-close").addEventListener("click", function () { $("inv-item-modal").close(); });
  $("inv-add-open").addEventListener("click", function () { openItem(null); });

  async function adjustItem(i) {
    const change = (await window.VistAdmin.promptText("Correct the stock of " + i.name + " (now " + qty(i.quantity_on_hand) + " " + i.unit + ").\nEnter the change: e.g. -2 for two damaged or lost, 3 for three found in a count."));
    if (change === null || change.trim() === "" || isNaN(Number(change))) return;
    const reason = (await window.VistAdmin.promptText("Why? (kept in the history)"));
    if (!reason) return;
    const r = await postJson(API + "/items/" + i.id + "/adjust", { change: Number(change), reason: reason });
    if (!r.ok) return say(errText(r), true);
    say("Stock corrected: " + i.name + " now " + qty(r.body.balance_after) + " " + i.unit + ".");
    refreshAll();
  }

  // ------------------------------------------------------------------ issue
  function syncIssueForm() {
    const type = $("is-party-type").value, mode = $("is-mode").value;
    $("is-dept-field").hidden = type !== "DEPARTMENT";
    $("is-person-field").hidden = type === "DEPARTMENT";
    if (type === "DEPARTMENT" && mode !== "FREE") $("is-mode").value = "FREE";
    Array.prototype.forEach.call($("is-mode").options, function (o) { o.disabled = type === "DEPARTMENT" && o.value !== "FREE"; });
    const charged = $("is-mode").value !== "FREE";
    document.querySelectorAll("#inv-issue-form [data-charged]").forEach(function (el) { el.hidden = !charged; });
    document.querySelectorAll("#inv-issue-form [data-paid-now]").forEach(function (el) { el.hidden = $("is-mode").value !== "PAID_NOW"; });
    const it = items.find(function (x) { return x.id === $("is-item").value; });
    if (it && charged && !$("is-price").value && it.sale_price) $("is-price").value = it.sale_price;
    const total = charged ? (Number($("is-price").value || 0) * Number($("is-qty").value || 0)) : 0;
    $("is-total").textContent = it ? (it.name + ": " + qty(it.quantity_on_hand) + " " + it.unit + " in stock." + (charged ? " Total to charge: " + money(total) + "." : "")) : "";
  }
  ["is-party-type", "is-mode", "is-item", "is-qty", "is-price"].forEach(function (id) { $(id).addEventListener("change", syncIssueForm); $(id).addEventListener("input", syncIssueForm); });
  $("is-party-type").addEventListener("change", function () { $("is-person").value = ""; $("is-person-id").value = ""; });

  $("is-person").addEventListener("input", function () {
    $("is-person-id").value = "";
    clearTimeout(debounce);
    const q = $("is-person").value.trim(), box = $("is-person-results");
    if (q.length < 2) { box.hidden = true; return; }
    debounce = setTimeout(async function () {
      const r = await VA.fetchJson(API + "/people?" + new URLSearchParams({ party_type: $("is-party-type").value, q: q }));
      if (!r.ok) return;
      box.innerHTML = "";
      if (!r.body.length) { box.innerHTML = '<div class="search-result-item search-result-empty">No one matches.</div>'; box.hidden = false; return; }
      r.body.forEach(function (p) {
        const d = document.createElement("div");
        d.className = "search-result-item";
        d.textContent = p.name + (p.identifier ? " (" + p.identifier + ")" : "");
        d.addEventListener("click", function () { $("is-person").value = d.textContent; $("is-person-id").value = p.id; box.hidden = true; });
        box.appendChild(d);
      });
      box.hidden = false;
    }, 250);
  });

  $("inv-issue-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const type = $("is-party-type").value, mode = $("is-mode").value;
    if (type !== "DEPARTMENT" && !$("is-person-id").value) return say("Pick the person from the list.", true);
    const body = { item_id: $("is-item").value, quantity: Number($("is-qty").value), party_type: type, party_id: type === "DEPARTMENT" ? null : $("is-person-id").value,
      department: type === "DEPARTMENT" ? $("is-dept").value.trim() : null, charge_mode: mode, unit_price: mode === "FREE" ? null : num("is-price"),
      payment_method: mode === "PAID_NOW" ? $("is-method").value : null, payment_reference: mode === "PAID_NOW" ? ($("is-ref").value.trim() || null) : null,
      reason: $("is-reason").value.trim() || null };
    const r = await postJson(API + "/issue", body);
    if (!r.ok) return say(errText(r), true);
    const m = r.body;
    let text = "Issued " + qty(-m.quantity) + " " + m.unit + " of " + m.item_name + " to " + m.party_name + " (" + m.reference + ", " + (MODE[m.charge_mode] || "free") + ").";
    if (m.receipt_number) text += " Receipt " + m.receipt_number + " is in History.";
    if (m.charge_mode === "ADD_TO_DUES") text += " " + money(m.charge_amount) + " was added to their fines.";
    say(text);
    $("is-qty").value = "1"; $("is-reason").value = ""; $("is-ref").value = ""; $("is-price").value = "";
    if (m.receipt_number) window.open(API + "/movements/" + m.id + "/receipt.pdf", "_blank", "noopener");
    refreshAll().then(syncIssueForm);
  });

  // ------------------------------------------------------------------ receive from purchases
  async function loadPurchases() {
    const r = await VA.fetchJson(API + "/purchases-to-stock");
    if (!r.ok) return;
    const box = $("inv-po-list");
    box.innerHTML = "";
    $("inv-po-empty").hidden = r.body.length > 0;
    const options = '<option value="">+ New item from this line</option>' + items.filter(function (i) { return i.is_active; }).map(function (i) { return '<option value="' + i.id + '">' + esc(i.code + " — " + i.name) + "</option>"; }).join("");
    r.body.forEach(function (o) {
      const sec = document.createElement("div");
      sec.className = "panel";
      sec.innerHTML = "<h3>" + esc(o.reference + " — " + o.title) + "</h3><p class=\"panel-hint\">" + esc(o.status.toLowerCase()) + (o.invoice_no ? " · invoice " + esc(o.invoice_no) : "") + "</p>" +
        '<table class="dash-table"><thead><tr><th>Line</th><th class="num">Left to stock</th><th class="num">Unit cost</th><th>Put into</th><th class="num">Quantity</th></tr></thead><tbody>' +
        o.lines.map(function (l) {
          return '<tr data-line="' + l.purchase_item_id + '"><td>' + esc(l.description) + '</td><td class="num">' + qty(l.left_to_stock) + '</td><td class="num">' + money(l.unit_cost) +
            '</td><td><select class="select-input" data-target>' + options + '</select></td><td class="num"><input type="number" class="text-input small-input" min="0" step="0.01" value="' + l.left_to_stock + '" data-qty /></td></tr>';
        }).join("") + "</tbody></table>";
      if (canManage) {
        const go = button("Put into stock", "", async function () {
          const lines = [];
          sec.querySelectorAll("tr[data-line]").forEach(function (tr) {
            const q = Number(tr.querySelector("[data-qty]").value || 0);
            if (q <= 0) return;
            const target = tr.querySelector("[data-target]").value;
            lines.push(target ? { purchase_item_id: tr.getAttribute("data-line"), quantity: q, stock_item_id: target } : { purchase_item_id: tr.getAttribute("data-line"), quantity: q, new_item: {} });
          });
          if (!lines.length) return say("Enter a quantity on at least one line.", true);
          const res = await postJson(API + "/purchases/" + o.order_id + "/receive", { lines: lines });
          if (!res.ok) return say(errText(res), true);
          say(res.body.length + " line(s) of " + o.reference + " put into stock.");
          await refreshAll();
          loadPurchases();
        });
        sec.appendChild(go);
      }
      box.appendChild(sec);
    });
  }

  // ------------------------------------------------------------------ with people
  async function loadHoldings() {
    const r = await VA.fetchJson(API + "/holdings");
    if (!r.ok) return;
    const tb = $("inv-hold");
    tb.innerHTML = "";
    r.body.forEach(function (m) {
      const tr = document.createElement("tr");
      tr.innerHTML = "<td>" + esc(VA.formatDateTime(m.created_at)) + "</td><td>" + esc(m.item_name) + "</td><td>" + esc(m.party_name || "") + '</td><td class="num">' + qty(m.still_out) + " " + esc(m.unit) +
        "</td><td>" + esc(m.reference) + '</td><td class="row-actions"></td>';
      if (canManage) tr.querySelector(".row-actions").appendChild(button("Return", "", function () { returnIssue(m); }));
      tb.appendChild(tr);
    });
    $("inv-hold-empty").hidden = r.body.length > 0;
  }

  async function returnIssue(m) {
    const q = (await window.VistAdmin.promptText("How many " + m.unit + " of " + m.item_name + " came back from " + m.party_name + "? (" + qty(m.still_out) + " still out)", String(m.still_out)));
    if (q === null || q.trim() === "" || isNaN(Number(q))) return;
    const note = (await window.VistAdmin.promptText("Condition / note (optional)")) || null;
    const body = { quantity: Number(q), reason: note };
    if (m.charge_mode && m.charge_mode !== "FREE" && m.unit_price) {
      const back = Number(q) * m.unit_price;
      const choice = await refundDialog(m, back);
      if (choice === null) return;
      Object.assign(body, choice);
    }
    const r = await postJson(API + "/movements/" + m.id + "/return", body);
    if (!r.ok) return say(errText(r), true);
    say("Returned " + qty(r.body.quantity) + " " + m.unit + " of " + m.item_name + ".");
    refreshAll(); loadHoldings(); loadHistory();
  }

  // A charged issue coming back: give the money back or not.
  function refundDialog(m, back) {
    return new Promise(function (resolve) {
      const d = document.createElement("dialog");
      d.className = "modal";
      const dues = m.charge_mode === "ADD_TO_DUES";
      d.innerHTML = '<div class="modal-body"><h2>Give the money back?</h2><p class="cc-meta">' + esc(m.party_name) + " was charged " + esc(VA.formatMoney(m.unit_price)) + " each. For these items that is " +
        esc(VA.formatMoney(back)) + (dues ? ". If the charge is still unpaid it is reduced; if it was paid, the money is given back now." : ", paid at the counter.") + "</p>" +
        '<label class="check-row"><input type="checkbox" id="rf-yes" checked> Give ' + esc(VA.formatMoney(back)) + " back</label>" +
        '<div class="form-field"><label for="rf-method">Given back by</label><select id="rf-method" class="text-input"><option value="CASH">Cash</option><option value="BANK_TRANSFER">Bank transfer</option><option value="CHEQUE">Cheque</option><option value="ONLINE">Online</option></select></div>' +
        '<div class="form-field"><label for="rf-ref">Cheque no. / transaction reference</label><input id="rf-ref" class="text-input" maxlength="100"></div>' +
        '<div class="form-actions"><button type="button" class="role-btn" data-ok>Record the return</button></div><button type="button" class="back-link modal-close-btn" data-no>Cancel</button></div>';
      document.body.appendChild(d);
      function done(v) { if (d.open) d.close(); d.remove(); resolve(v); }
      d.querySelector("[data-no]").addEventListener("click", function () { done(null); });
      d.addEventListener("cancel", function (e) { e.preventDefault(); done(null); });
      d.querySelector("[data-ok]").addEventListener("click", function () {
        const yes = d.querySelector("#rf-yes").checked;
        done(yes ? { refund: true, refund_method: d.querySelector("#rf-method").value, refund_reference: d.querySelector("#rf-ref").value.trim() || null } : { refund: false });
      });
      d.showModal();
    });
  }

  // ------------------------------------------------------------------ history
  async function loadHistory() {
    const p = new URLSearchParams({ limit: "300" });
    if ($("hi-kind").value) p.set("kind", $("hi-kind").value);
    if ($("hi-item").value) p.set("item_id", $("hi-item").value);
    if ($("hi-q").value.trim()) p.set("q", $("hi-q").value.trim());
    if ($("hi-from").value) p.set("from_date", $("hi-from").value);
    if ($("hi-to").value) p.set("to_date", $("hi-to").value);
    const r = await VA.fetchJson(API + "/movements?" + p.toString());
    if (!r.ok) return say(errText(r), true);
    const tb = $("inv-hist");
    tb.innerHTML = "";
    r.body.forEach(function (m) {
      const who = [m.party_name, m.charge_mode ? MODE[m.charge_mode] + (m.charge_amount ? " " + money(m.charge_amount) : "") : null].filter(Boolean).join(" · ");
      const tr = document.createElement("tr");
      tr.innerHTML = "<td>" + esc(m.reference) + "</td><td>" + esc(VA.formatDateTime(m.created_at)) + "</td><td>" + esc(KIND[m.kind] || m.kind) + (m.reversed ? ' <span class="status-pill dropped">reversed</span>' : "") +
        (m.reason && m.reason !== KIND[m.kind] ? "<br><small>" + esc(m.reason) + "</small>" : "") + "</td><td>" + esc(m.item_name) + '</td><td class="num">' + (m.quantity > 0 ? "+" : "") + qty(m.quantity) + " " + esc(m.unit) +
        '</td><td class="num">' + money(m.value) + "</td><td>" + esc(who) +
        (m.receipt_number ? '<br><a href="' + API + "/movements/" + m.id + '/receipt.pdf" target="_blank" rel="noopener">Receipt ' + esc(m.receipt_number) + "</a>" : "") +
        "</td><td>" + esc(m.recorded_by || "") + '</td><td class="row-actions"></td>';
      const cell = tr.querySelector(".row-actions");
      if (canManage && m.kind === "ISSUED" && m.still_out > 0.005 && !m.reversed) cell.appendChild(button("Return", "secondary-btn", function () { returnIssue(m); }));
      if (canAdjust && m.kind !== "REVERSED" && !m.reversed) cell.appendChild(button("Reverse", "danger-btn", async function () {
        const reason = (await window.VistAdmin.promptText("Reverse " + m.reference + " (" + (KIND[m.kind] || m.kind) + ", " + m.item_name + ")? Say why (kept in the history).\nA sale's income is voided; unpaid dues are waived."));
        if (!reason) return;
        const res = await postJson(API + "/movements/" + m.id + "/reverse", { reason: reason });
        if (!res.ok) return say(errText(res), true);
        say(m.reference + " reversed (" + res.body.reference + ").");
        refreshAll(); loadHistory();
      }));
      tb.appendChild(tr);
    });
    $("inv-hist-empty").hidden = r.body.length > 0;
  }

  $("hi-load").addEventListener("click", loadHistory);
  $("inv-q").addEventListener("input", function () { clearTimeout(debounce); debounce = setTimeout(loadItems, 300); });
  $("inv-low").addEventListener("change", loadItems);
  $("inv-inactive").addEventListener("change", loadItems);
  document.querySelectorAll(".portal-tab").forEach(function (t) {
    t.addEventListener("click", function () {
      const name = t.getAttribute("data-tab");
      if (name === "receive") loadPurchases();
      if (name === "holdings") loadHoldings();
      if (name === "history") loadHistory();
      if (name === "issue") syncIssueForm();
    });
  });

  document.addEventListener("vistadmin:ready", function (event) {
    const me = event.detail && event.detail.me;
    const perms = (me && me.permissions) || [];
    canManage = perms.indexOf("inventory.manage") !== -1;
    canAdjust = perms.indexOf("inventory.adjust") !== -1;
    document.querySelectorAll("[data-manage]").forEach(function (el) { el.hidden = !canManage; });
    if (!canManage) { const t = document.querySelector('.portal-tab[data-tab="issue"]'); if (t) t.hidden = true; }
    refreshAll();
  });
})();