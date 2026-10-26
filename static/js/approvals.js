(function () {
  "use strict";

  // Approvals: /api/v1/approvals. Approvers see every request; anyone else sees the ones they asked for (and can withdraw them).
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const errText = function (r) { return (r.body && r.body.error && r.body.error.message) || "That did not work."; };
  let canDecide = false, me = null, tab = "REQUESTED";

  function say(t, bad) { const el = $("ap-msg"); el.textContent = t || ""; el.hidden = !t; el.className = bad ? "dash-alert error" : "panel-hint"; }
  function btn(text, cls, fn) { const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn " + (cls || ""); b.textContent = text; b.addEventListener("click", fn); return b; }
  function post(url, body) { return VA.fetchJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }); }

  async function load() {
    const r = await VA.fetchJson("/api/v1/approvals?status=" + tab);
    if (!r.ok) return say(errText(r), true);
    const tb = $("ap-rows"); tb.innerHTML = "";
    r.body.forEach(function (x) {
      const tr = document.createElement("tr");
      tr.innerHTML = "<td>" + esc(VA.formatDateTime(x.requested_at)) + "<br><small>" + esc(x.requested_by || "") + "</small></td><td><strong>" + esc(x.label) + "</strong><br>" + esc(x.summary) +
        '</td><td class="num">' + (x.amount != null ? esc(VA.formatMoney(x.amount)) : "—") + "</td><td>" + esc(x.reason || "") + "</td><td>" +
        (x.decided_by ? esc(x.decided_by) + "<br><small>" + esc(VA.formatDateTime(x.decided_at)) + (x.decision_note ? " · " + esc(x.decision_note) : "") + "</small>" : "") + '</td><td class="row-actions"></td>';
      const cell = tr.querySelector(".row-actions");
      const own = me && String(x.requested_by_id) === String(me.id);
      if (x.status === "REQUESTED" && canDecide && !own) {
        cell.appendChild(btn("Approve", "", async function () {
          if (!(await VA.confirm("Approve and carry it out now?\n" + x.summary, "Approve"))) return;
          const res = await post("/api/v1/approvals/" + x.id + "/decide", { approve: true });
          if (!res.ok) return say(errText(res), true);
          say("Approved and done: " + x.summary); load();
        }));
        cell.appendChild(btn("Reject", "danger-btn", async function () {
          const note = await VA.ask({ title: "Reject this request?", label: "Why (the requester is told)", ok: "Reject", danger: true, min: 3 });
          if (!note) return;
          const res = await post("/api/v1/approvals/" + x.id + "/decide", { approve: false, note: note });
          if (!res.ok) return say(errText(res), true);
          say("Rejected."); load();
        }));
      }
      if (x.status === "REQUESTED" && own) {
        cell.appendChild(btn("Withdraw", "secondary-btn", async function () {
          const res = await post("/api/v1/approvals/" + x.id + "/cancel");
          if (!res.ok) return say(errText(res), true);
          load();
        }));
        if (canDecide) cell.insertAdjacentHTML("beforeend", "<small>Another administrator decides this one.</small>");
      }
      tb.appendChild(tr);
    });
    $("ap-empty").hidden = r.body.length > 0;
  }

  document.querySelectorAll(".portal-tab").forEach(function (t) { t.addEventListener("click", function () { tab = t.getAttribute("data-tab"); load(); }); });
  document.addEventListener("vistadmin:ready", function (event) {
    me = event.detail && event.detail.me;
    canDecide = ((me && me.permissions) || []).indexOf("approvals.decide") !== -1;
    load();
  });
})();
