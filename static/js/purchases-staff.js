(function () {
  "use strict";

  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/purchases";
  const $ = function (id) { return document.getElementById(id); };
  const msg = function (body, fallback) { return body && body.error && body.error.message ? body.error.message : fallback; };
  const money = function (v) { return "Rs. " + Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }); };
  const day = function (iso) { return iso ? new Date(String(iso).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "\u2014"; };
  const STEPS = ["Asked", "Approved", "Ordered", "Arrived", "Paid"];
  const STEP_OF = { REQUESTED: 0, APPROVED: 1, ORDERED: 2, RECEIVED: 3, PAID: 4 };
  const STATUS = { REQUESTED: ["Waiting for approval", "pending"], APPROVED: ["Approved", "enrolled"], REJECTED: ["Not approved", "dropped"], ORDERED: ["On order", "enrolled"], RECEIVED: ["Arrived", "active"], PAID: ["Done", "active"], CANCELLED: ["Cancelled", ""] };
  const pill = function (s) { const x = STATUS[s] || [s, ""]; return '<span class="status-pill ' + x[1] + '">' + esc(x[0]) + "</span>"; };
  let filter = "", current = null;

  function steps(status) {
    const at = STEP_OF[status];
    if (at === undefined) return "";
    return '<ol class="po-steps">' + STEPS.map(function (s, i) { return '<li class="' + (i < at ? "done" : i === at ? "now" : "") + '"><span>' + (i < at ? "\u2713" : i + 1) + "</span>" + s + "</li>"; }).join("") + "</ol>";
  }

  async function load() {
    const r = await VA.fetchJson(API + "/mine" + (filter ? "?status=" + filter : ""));
    const box = $("pr-list"); box.innerHTML = "";
    if (!r.ok) return;
    r.body.forEach(function (o) {
      const el = document.createElement("article");
      el.className = "asg-card " + (o.status === "REJECTED" || o.status === "CANCELLED" ? "missed" : o.status === "PAID" || o.status === "RECEIVED" ? "ok" : "soon");
      el.tabIndex = 0; el.setAttribute("role", "button");
      el.innerHTML = '<div class="asg-top"><span class="pill">' + esc(o.reference) + "</span>" + pill(o.status) + "</div><h3>" + esc(o.title) + "</h3>" + steps(o.status) +
        '<p class="asg-snippet">' + o.items.length + " item" + (o.items.length !== 1 ? "s" : "") + " \u00b7 about <b>" + money(o.estimated_total) + "</b>" + (o.needed_by ? " \u00b7 needed by " + esc(day(o.needed_by)) : "") + "</p>" +
        (o.decision_note && o.status === "REJECTED" ? '<div class="cc-note rejected"><strong>Not approved</strong><span>' + esc(o.decision_note) + "</span></div>" : "") +
        (o.status === "RECEIVED" ? '<div class="cc-note"><strong>It has arrived</strong><span>Collect it from the accounts office.</span></div>' : "");
      const open = function () { openOrder(o.id); };
      el.addEventListener("click", open); el.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
      box.appendChild(el);
    });
    $("pr-empty").hidden = r.body.length > 0;
  }
  $("pr-filter").querySelectorAll("button").forEach(function (b) { b.addEventListener("click", function () { $("pr-filter").querySelectorAll("button").forEach(function (x) { x.classList.toggle("active", x === b); }); filter = b.getAttribute("data-s"); load(); }); });

  async function openOrder(id) {
    const r = await VA.fetchJson(API + "/" + id);
    if (!r.ok) return VA.toast(msg(r.body, "Could not open it."), "error");
    const o = r.body; current = o;
    $("pd-badges").innerHTML = '<span class="pill">' + esc(o.reference) + "</span>" + pill(o.status);
    $("pd-title").textContent = o.title;
    $("pd-meta").textContent = "Asked " + day(o.created_at) + (o.needed_by ? " \u00b7 needed by " + day(o.needed_by) : "");
    $("pd-steps").innerHTML = steps(o.status);
    const bits = [];
    bits.push("<p>" + esc(o.justification) + "</p>");
    if (o.decided_at) bits.push('<p class="cc-meta">' + (o.status === "REJECTED" ? "Not approved" : "Approved") + " by " + esc(o.decided_by_name || "the administration") + " on " + esc(day(o.decided_at)) + (o.decision_note ? ": " + esc(o.decision_note) : "") + "</p>");
    if (o.ordered_at) bits.push('<p class="cc-meta">Ordered from ' + esc(o.vendor_name || "a vendor") + " on " + esc(day(o.ordered_at)) + (o.expected_delivery ? ", expected " + esc(day(o.expected_delivery)) : "") + "</p>");
    if (o.received_at) bits.push('<p class="cc-meta">Arrived on ' + esc(day(o.received_at)) + " \u00b7 final cost " + money(o.actual_total) + "</p>");
    if (o.status === "CANCELLED") bits.push('<div class="cc-note rejected"><strong>Cancelled</strong><span>' + esc(o.cancel_reason || "") + "</span></div>");
    $("pd-body").innerHTML = bits.join("");
    $("pd-items").innerHTML = o.items.map(function (i) { return "<tr><td>" + esc(i.description) + "</td><td>" + i.quantity + "</td><td>" + money(i.estimated_total) + "</td><td>" + (i.received_quantity === null ? "\u2014" : i.received_quantity) + "</td><td>" + (i.actual_total === null ? "\u2014" : money(i.actual_total)) + "</td></tr>"; }).join("");
    const bar = $("pd-actions"); bar.innerHTML = ""; $("pd-error").hidden = true;
    if (o.actions.indexOf("cancel") !== -1) {
      const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn danger-btn"; b.textContent = "Cancel this request";
      b.addEventListener("click", async function () {
        if (!window.confirm("Cancel this request?")) return;
        const x = await VA.fetchJson(API + "/" + o.id + "/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
        if (x.ok) { VA.toast("Cancelled."); $("pd-modal").close(); load(); } else { $("pd-error").textContent = msg(x.body, "Could not cancel it."); $("pd-error").hidden = false; }
      });
      bar.appendChild(b);
    }
    if (!$("pd-modal").open) $("pd-modal").showModal();
  }
  $("pd-close").addEventListener("click", function () { $("pd-modal").close(); });

  // ------------------------------------------------------------------ a new request
  function itemRow(item) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td><input class="text-input" maxlength="200" placeholder="e.g. Office chair" aria-label="Item" value="' + esc((item && item.description) || "") + '" /></td><td><input class="text-input tiny" type="number" min="0.01" step="0.01" aria-label="Quantity" value="' + ((item && item.quantity) || 1) + '" /></td>' +
      '<td><input class="text-input tiny" type="number" min="0.01" step="0.01" aria-label="Price each" value="' + ((item && item.price) || "") + '" /></td><td class="line-total">Rs. 0</td><td><button type="button" class="linkish" aria-label="Remove">Remove</button></td>';
    const update = function () { const q = parseFloat(tr.children[1].firstChild.value) || 0, p = parseFloat(tr.children[2].firstChild.value) || 0; tr.querySelector(".line-total").textContent = money(q * p); total(); };
    tr.querySelectorAll("input").forEach(function (i) { i.addEventListener("input", update); });
    tr.querySelector("button").addEventListener("click", function () { if ($("pr-items").children.length > 1) { tr.remove(); total(); } });
    $("pr-items").appendChild(tr); update();
  }
  function total() {
    let t = 0;
    Array.prototype.forEach.call($("pr-items").children, function (tr) { t += (parseFloat(tr.children[1].firstChild.value) || 0) * (parseFloat(tr.children[2].firstChild.value) || 0); });
    $("pr-total").textContent = money(t);
  }
  const fail = function (t) { $("pr-error").textContent = t; $("pr-error").hidden = false; };
  $("pr-new").addEventListener("click", function () { $("pr-form").reset(); $("pr-items").innerHTML = ""; itemRow(); $("pr-error").hidden = true; $("pr-by").min = new Date().toISOString().slice(0, 10); $("pr-modal").showModal(); });
  $("pr-add-item").addEventListener("click", function () { itemRow(); });
  $("pr-close").addEventListener("click", function () { $("pr-modal").close(); });
  $("pr-save").addEventListener("click", async function () {
    $("pr-error").hidden = true;
    if ($("pr-name").value.trim().length < 3) return fail("Say what you need.");
    if ($("pr-why").value.trim().length < 5) return fail("Explain why it is needed, so the administration can decide.");
    const items = [];
    for (const tr of Array.prototype.slice.call($("pr-items").children)) {
      const d = tr.children[0].firstChild.value.trim(), q = parseFloat(tr.children[1].firstChild.value), p = parseFloat(tr.children[2].firstChild.value);
      if (!d && !q && !p) continue;
      if (d.length < 2 || !(q > 0) || !(p > 0)) return fail("Every item needs a name, a quantity and an estimated price.");
      items.push({ description: d, quantity: q, estimated_unit_price: p });
    }
    if (!items.length) return fail("Add at least one item.");
    const r = await VA.fetchJson(API, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: $("pr-name").value.trim(), justification: $("pr-why").value.trim(), needed_by: $("pr-by").value || null, items: items }) });
    if (!r.ok) return fail(msg(r.body, "Could not send it."));
    $("pr-modal").close(); VA.toast("Sent: " + r.body.reference + ". You will be told when it is decided."); load();
  });

  document.addEventListener("vistadmin:ready", function () {
    load();
    const open = new URLSearchParams(window.location.search).get("open");
    if (open && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(open)) openOrder(open);      // (on the Accounts page "open" names a tab, not a request)
  });
})();