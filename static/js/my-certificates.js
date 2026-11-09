/* Student -> Certificates: ask for one, pay the challan, download it (/api/v1/records/me/certificate-requests). */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/records/me/certificate-requests";
  const PILL = { REQUESTED: "pending", AWAITING_FEE: "pending", READY: "active", REJECTED: "dropped", CANCELLED: "" };
  let kinds = [];

  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function day(d) { return d ? new Date(String(d).length > 10 ? d : d + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : ""; }
  function money(v) { return "Rs. " + Math.round(v).toLocaleString("en-PK"); }
  function err(r, f) { return (r.body && r.body.error && r.body.error.message) || f; }

  function showFee() {
    const k = kinds.find(function (x) { return x.kind === $("cr-kind").value; });
    $("cr-fee").textContent = !k ? "" : k.fee > 0 ? "Fee: " + money(k.fee) + ". You pay it with a challan once the office approves your request." : "No fee for this certificate.";
  }

  function card(r) {
    const c = el("article", "cr-card");
    const head = el("div", "cr-head");
    head.appendChild(el("strong", null, r.kind_label));
    head.appendChild(el("span", "status-pill " + (PILL[r.status] || ""), r.status_text));
    c.appendChild(head);
    c.appendChild(el("p", "cr-meta", "For: " + r.purpose + " · asked on " + day(r.asked_on)));
    if (r.note && (r.status === "REJECTED" || r.status === "CANCELLED" || r.status === "AWAITING_FEE")) c.appendChild(el("p", "cr-note", "Office: " + r.note));
    const act = el("div", "cr-actions");
    if (r.status === "AWAITING_FEE" && r.challan) {
      c.appendChild(el("p", "cr-meta", "Fee " + money(r.challan.amount) + " · pay by " + day(r.challan.due_date) + " · consumer number " + r.challan.consumer_number));
      const a = el("a", "role-btn small-btn", "Download challan");
      a.href = "/api/v1/fees/challans/" + encodeURIComponent(r.challan.id) + "/pdf"; a.target = "_blank"; a.rel = "noopener";
      act.appendChild(a);
      act.appendChild(el("span", "cr-meta", "The certificate is ready to download as soon as the fee is paid."));
    }
    if (r.status === "READY") {
      if (r.revoked) {
        c.appendChild(el("p", "cr-note", "The office has cancelled this certificate. Please contact them."));
      } else {
        const a = el("a", "role-btn small-btn", "Download certificate");
        a.href = API + "/" + encodeURIComponent(r.id) + "/pdf"; a.target = "_blank"; a.rel = "noopener";
        act.appendChild(a);
        act.appendChild(el("span", "cr-meta", "Ref. " + r.reference + " · anyone can check it with the QR code on it"));
      }
    }
    if (r.status === "REQUESTED") {
      const b = el("button", "link-btn", "Cancel request"); b.type = "button";
      b.addEventListener("click", async function () {
        b.disabled = true;
        const x = await VA.fetchJson(API + "/" + encodeURIComponent(r.id) + "/cancel", { method: "POST" });
        if (!x.ok) { b.disabled = false; VA.toast(err(x, "Could not cancel."), "error"); return; }
        load();
      });
      act.appendChild(b);
    }
    if (act.childNodes.length) c.appendChild(act);
    return c;
  }

  async function load() {
    const list = $("cr-list");
    const r = await VA.fetchJson(API);
    if (!r.ok) { list.textContent = err(r, "Could not load your requests."); return; }
    if (!kinds.length) {
      kinds = r.body.kinds;
      $("cr-kind").innerHTML = "";
      kinds.forEach(function (k) { const o = el("option", null, k.label + (k.fee > 0 ? " — " + money(k.fee) : "")); o.value = k.kind; $("cr-kind").appendChild(o); });
      showFee();
    }
    list.textContent = "";
    r.body.requests.forEach(function (x) { list.appendChild(card(x)); });
    $("cr-empty").hidden = r.body.requests.length > 0;
  }

  document.addEventListener("vistadmin:ready", function () {
    $("cr-kind").addEventListener("change", showFee);
    $("cr-form").addEventListener("submit", async function (e) {
      e.preventDefault();
      const purpose = $("cr-purpose").value.trim();
      if (purpose.length < 4) { $("cr-error").textContent = "Say what you need it for."; $("cr-error").hidden = false; $("cr-purpose").focus(); return; }
      $("cr-send").disabled = true;
      const r = await VA.fetchJson(API, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: $("cr-kind").value, purpose: purpose }) });
      $("cr-send").disabled = false;
      if (!r.ok) { $("cr-error").textContent = err(r, "Could not send. Please try again."); $("cr-error").hidden = false; return; }
      $("cr-error").hidden = true;
      $("cr-purpose").value = "";
      load();
    });
    load();
  });
})();
