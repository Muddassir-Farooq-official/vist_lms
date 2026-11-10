/* Student / staff -> My warnings (/api/v1/discipline/me/warnings): read, confirm 'I have read this', reply once, download the letter. */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/discipline/me/warnings";

  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function day(d) { return d ? new Date(String(d).length > 10 ? d : d + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : ""; }
  function err(r, f) { return (r.body && r.body.error && r.body.error.message) || f; }

  function card(w, limit) {
    const c = el("article", "wr-card" + (w.is_final && w.status === "ACTIVE" ? " wr-final" : "") + (w.status === "WITHDRAWN" ? " wr-withdrawn" : ""));
    const head = el("div", "wr-head");
    head.appendChild(el("strong", null, w.violation));
    head.appendChild(el("span", "status-pill " + (w.status === "WITHDRAWN" ? "" : w.is_final ? "dropped" : "pending"),
      w.status === "WITHDRAWN" ? "Withdrawn" : w.is_final ? "Final warning" : "Warning " + w.number + " of " + limit));
    c.appendChild(head);
    c.appendChild(el("p", "cc-meta", "Happened on " + day(w.incident_date) + " · " + w.reference +
      (w.status === "ACTIVE" ? (w.counts ? " · counts until " + day(w.counts_until) : " · no longer counts") : "")));
    c.appendChild(el("p", "wr-note", w.note));
    if (w.action && w.action !== "PENDING") c.appendChild(el("p", "wr-decision", "Decision: " + w.action_text +
      (w.fine_amount ? " Rs. " + Math.round(w.fine_amount).toLocaleString("en-PK") : "") + (w.action_note ? " — " + w.action_note : "")));
    if (w.lifted_at) c.appendChild(el("p", "cc-meta", "Suspension ended on " + day(w.lifted_at) + (w.lift_note ? ": " + w.lift_note : "") + "."));
    if (w.action === "PENDING") c.appendChild(el("p", "wr-decision", "The administration will decide the action on this final warning."));
    if (w.status === "WITHDRAWN") c.appendChild(el("p", "cc-meta", "Withdrawn on " + day(w.withdrawn_at) + ": " + (w.withdraw_reason || "") + ". It does not count."));
    if (w.response) {
      const r = el("blockquote", "wr-reply");
      r.appendChild(el("strong", null, "Your reply (" + day(w.responded_at) + "): "));
      r.appendChild(document.createTextNode(w.response));
      c.appendChild(r);
    }
    const act = el("div", "wr-actions-row");
    if (w.status === "ACTIVE" && !w.acknowledged_at) {
      const ack = el("button", "role-btn small-btn", "I have read this"); ack.type = "button";
      ack.addEventListener("click", async function () {
        ack.disabled = true;
        const r = await VA.fetchJson(API + "/" + encodeURIComponent(w.id) + "/acknowledge", { method: "POST" });
        if (!r.ok) { ack.disabled = false; VA.toast(err(r, "Could not save."), "error"); return; }
        load();
      });
      act.appendChild(ack);
    } else if (w.acknowledged_at) {
      act.appendChild(el("span", "wr-read", "✓ Read on " + day(w.acknowledged_at)));
    }
    const a = el("a", "role-btn small-btn secondary-btn", "Download letter");
    a.href = API + "/" + encodeURIComponent(w.id) + "/letter"; a.target = "_blank"; a.rel = "noopener";
    act.appendChild(a);
    if (w.can_reply) {
      const b = el("button", "role-btn small-btn secondary-btn", "Reply"); b.type = "button";
      act.appendChild(b);
      const form = el("form", "wr-reply-form"); form.hidden = true; form.noValidate = true;
      const ta = el("textarea", "text-input"); ta.rows = 4; ta.maxLength = 4000; ta.value = w.response || "";
      ta.setAttribute("aria-label", "Your reply");
      ta.placeholder = "Explain your side. You can reply once; the administration reads every reply.";
      const send = el("button", "role-btn small-btn", "Send reply"); send.type = "submit";
      const msg = el("p", "form-error"); msg.hidden = true;
      form.appendChild(ta); form.appendChild(msg); form.appendChild(send);
      b.addEventListener("click", function () { form.hidden = !form.hidden; if (!form.hidden) ta.focus(); });
      form.addEventListener("submit", async function (e) {
        e.preventDefault();
        if (ta.value.trim().length < 10) { msg.textContent = "Write at least 10 characters."; msg.hidden = false; return; }
        send.disabled = true;
        const r = await VA.fetchJson(API + "/" + encodeURIComponent(w.id) + "/reply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: ta.value.trim() }) });
        send.disabled = false;
        if (!r.ok) { msg.textContent = err(r, "Could not send."); msg.hidden = false; return; }
        VA.toast("Reply sent to the administration.", "success");
        load();
      });
      c.appendChild(act);
      c.appendChild(form);
      return c;
    }
    c.appendChild(act);
    return c;
  }

  async function load() {
    const r = await VA.fetchJson(API);
    const sum = $("wr-summary"), list = $("wr-mine");
    if (!r.ok) { sum.textContent = err(r, "Could not load."); return; }
    const d = r.body;
    sum.textContent = "";
    const big = el("div", "wr-meter");
    for (let i = 1; i <= d.limit; i++) big.appendChild(el("span", "wr-dot" + (i <= d.counting ? " on" : "") + (i === d.limit && d.counting >= d.limit ? " final" : "")));
    sum.appendChild(big);
    sum.appendChild(el("p", "wr-summary-text", d.counting === 0 ? (d.warnings.length ? "No warning counts against you right now." : "You have no warnings. Keep it up!")
      : d.counting + " of " + d.limit + " warnings count right now." + (d.counting >= d.limit ? " This includes a final warning." : " A " + d.limit + "rd one within 12 months is final.")));
    list.textContent = "";
    d.warnings.forEach(function (w) { list.appendChild(card(w, d.limit)); });
  }

  document.addEventListener("vistadmin:ready", load);
})();
