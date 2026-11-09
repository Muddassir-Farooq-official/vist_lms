/* Admin -> Certificate requests (/api/v1/records/certificate-requests): approve with a fee, refuse, cancel an unpaid one, print. */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/records/certificate-requests";
  const PILL = { REQUESTED: "pending", AWAITING_FEE: "pending", READY: "active", REJECTED: "dropped", CANCELLED: "" };
  let status = "OPEN";
  let current = null;   // { request, mode: approve | reject | cancel }

  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function day(d) { return d ? new Date(String(d).length > 10 ? d : d + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : ""; }
  function money(v) { return "Rs. " + Math.round(v).toLocaleString("en-PK"); }
  function err(r, f) { return (r.body && r.body.error && r.body.error.message) || f; }
  function btn(text, cls, fn) { const b = el("button", cls, text); b.type = "button"; b.addEventListener("click", fn); return b; }

  function open(r, mode) {
    current = { request: r, mode: mode };
    const titles = { approve: "Approve " + r.kind_label.toLowerCase(), reject: "Refuse this request", cancel: "Cancel this approved request" };
    $("cr-d-title").textContent = titles[mode];
    $("cr-d-who").textContent = r.student + " (" + r.roll_number + ") · for: " + r.purpose;
    const probs = (mode === "approve" && r.problems) || [];
    $("cr-d-problems").hidden = !probs.length;
    $("cr-d-problems").innerHTML = "";
    if (probs.length) {
      $("cr-d-problems").appendChild(el("strong", null, "Not cleared yet:"));
      const ul = el("ul");
      probs.forEach(function (p) { ul.appendChild(el("li", null, p)); });
      $("cr-d-problems").appendChild(ul);
    }
    $("cr-d-fee-l").hidden = mode !== "approve";
    $("cr-d-fee").value = String(Math.round(r.fee));
    $("cr-d-override-l").hidden = !probs.length;
    $("cr-d-override").value = "";
    $("cr-d-note-l").firstChild.textContent = mode === "approve" ? "Note to the student (optional) " : "Reason (the student sees it) ";
    $("cr-d-note").value = "";
    $("cr-d-error").hidden = true;
    $("cr-d-ok").textContent = mode === "approve" ? "Approve" : mode === "reject" ? "Refuse" : "Cancel request";
    $("cr-d-ok").className = mode === "approve" ? "role-btn" : "danger-btn";
    $("cr-dialog").showModal();
    (mode === "approve" ? $("cr-d-fee") : $("cr-d-note")).focus();
  }

  async function submit(e) {
    e.preventDefault();
    const r = current.request, mode = current.mode;
    let body;
    if (mode === "approve") {
      const fee = $("cr-d-fee").value === "" ? null : Number($("cr-d-fee").value);
      if (fee !== null && (isNaN(fee) || fee < 0)) { $("cr-d-error").textContent = "Enter the fee in rupees (0 to waive it)."; $("cr-d-error").hidden = false; return; }
      body = { fee: fee, override_reason: $("cr-d-override").value.trim() || null, note: $("cr-d-note").value.trim() || null };
    } else {
      body = { note: $("cr-d-note").value.trim() };
      if (body.note.length < 4) { $("cr-d-error").textContent = "Give a short reason."; $("cr-d-error").hidden = false; $("cr-d-note").focus(); return; }
    }
    $("cr-d-ok").disabled = true;
    const x = await VA.fetchJson(API + "/" + encodeURIComponent(r.id) + "/" + mode, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    $("cr-d-ok").disabled = false;
    if (!x.ok) {
      $("cr-d-error").textContent = err(x, "Could not save. Please try again.");
      $("cr-d-error").hidden = false;
      if (mode === "approve" && /anyway/.test($("cr-d-error").textContent)) { $("cr-d-override-l").hidden = false; $("cr-d-override").focus(); }
      return;
    }
    $("cr-dialog").close();
    VA.toast(mode === "approve" ? (x.body.status === "READY" ? "Approved and issued: the student can download it now." : "Approved: the student has a challan to pay.")
      : mode === "reject" ? "Refused. The student has been told why." : "Cancelled. What was owed is written off.", "success");
    load();
  }

  function card(r) {
    const c = el("article", "cr-card");
    const head = el("div", "cr-head");
    head.appendChild(el("strong", null, r.student + " · " + r.roll_number));
    head.appendChild(el("span", "status-pill " + (PILL[r.status] || ""), r.status_text));
    c.appendChild(head);
    c.appendChild(el("p", null, r.kind_label + " — " + r.purpose));
    c.appendChild(el("p", "cr-meta", "Asked on " + day(r.asked_on) + (r.fee ? " · fee " + money(r.fee) : " · no fee") +
      (r.challan ? " · challan " + r.challan.consumer_number + " (" + (r.challan.status === "PAID" ? "paid" : "due " + day(r.challan.due_date)) + ")" : "") +
      (r.reference ? " · ref. " + r.reference : "")));
    if (r.problems && r.problems.length) c.appendChild(el("p", "cr-note", "Not cleared: " + r.problems.join(" ")));
    if (r.override_reason) c.appendChild(el("p", "cr-note", "Approved in spite of: " + r.override_reason));
    if (r.note) c.appendChild(el("p", "cr-meta", "Note: " + r.note));
    const act = el("div", "cr-actions");
    if (r.status === "REQUESTED") {
      act.appendChild(btn("Approve", "role-btn small-btn", function () { open(r, "approve"); }));
      act.appendChild(btn("Refuse", "role-btn small-btn secondary-btn", function () { open(r, "reject"); }));
    }
    if (r.status === "AWAITING_FEE") act.appendChild(btn("Cancel", "role-btn small-btn secondary-btn", function () { open(r, "cancel"); }));
    if (r.status === "READY" && !r.revoked) {
      const a = el("a", "role-btn small-btn secondary-btn", "Print");
      a.href = API + "/" + encodeURIComponent(r.id) + "/pdf"; a.target = "_blank"; a.rel = "noopener";
      act.appendChild(a);
    }
    if (r.revoked) c.appendChild(el("p", "cr-note", "This certificate has been cancelled."));
    if (act.childNodes.length) c.appendChild(act);
    return c;
  }

  async function load() {
    const list = $("cr-office");
    const r = await VA.fetchJson(API + (status ? "?status=" + status : ""));
    if (!r.ok) { list.textContent = err(r, "Could not load the requests."); return; }
    const k = r.body.counts;
    $("cr-c-OPEN").textContent = (k.REQUESTED + k.AWAITING_FEE) || "";
    $("cr-c-READY").textContent = k.READY || "";
    list.textContent = "";
    r.body.requests.forEach(function (x) { list.appendChild(card(x)); });
    $("cr-office-empty").hidden = r.body.requests.length > 0;
  }

  document.addEventListener("vistadmin:ready", function () {
    document.querySelectorAll("#cr-tabs .portal-tab").forEach(function (t) {
      t.addEventListener("click", function () {
        document.querySelectorAll("#cr-tabs .portal-tab").forEach(function (o) { o.classList.toggle("active", o === t); o.setAttribute("aria-selected", String(o === t)); });
        status = t.getAttribute("data-status");
        load();
      });
    });
    $("cr-d-form").addEventListener("submit", submit);
    $("cr-d-cancel").addEventListener("click", function () { $("cr-dialog").close(); });
    load();
  });
})();
