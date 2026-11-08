(function () {
  "use strict";
  // Staff attendance -> Leave requests: approve (optionally as another kind) or refuse. API: /api/v1/staff-attendance/leave*
  const VA = window.VistAdmin, esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  if (!$("lv-panel")) return;
  const KIND = { CASUAL: "Casual", SICK: "Sick", UNPAID: "Unpaid" };
  const STATUS = { REQUESTED: "Waiting", APPROVED: "Approved", REJECTED: "Refused", CANCELLED: "Withdrawn" };
  const day = function (iso) { return new Date(String(iso).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }); };
  let rows = [];

  function message(body, fallback) {
    const e = body && (body.error || body);
    return (e && (e.message || (typeof e.detail === "string" ? e.detail : ""))) || fallback;
  }

  async function load() {
    const tab = document.querySelector('[data-tab="leave"]');
    if (tab && tab.hidden) return;                       // the tab is for those who decide on leave
    const st = $("lv-status").value;
    const r = await VA.fetchJson("/api/v1/staff-attendance/leave" + (st ? "?status=" + st : ""), { quiet: true });
    if (!r.ok) return;
    rows = r.body;
    if (st === "REQUESTED") $("lv-count").textContent = rows.length ? String(rows.length) : "";
    $("lv-empty").hidden = rows.length > 0;
    $("lv-body").innerHTML = rows.map(function (x) {
      const b = x.balances;
      const left = b ? (x.kind === "CASUAL" ? b.casual_left_month + " casual this month" : x.kind === "SICK" ? b.sick_left_year + " sick this year" : "—") : "";
      return "<tr><td><strong>" + esc(x.employee_name) + "</strong><div class=\"cc-meta\">" + esc(x.employee_code || "") + "</div></td><td>" + esc(KIND[x.kind] || x.kind) +
        "</td><td>" + esc(day(x.first_day)) + (x.last_day !== x.first_day ? " – " + esc(day(x.last_day)) : "") + "<div class=\"cc-meta\">" + x.days + " working day(s)</div></td><td>" +
        esc(x.reason) + (x.note_url ? '<div><a class="link-btn" href="' + esc(x.note_url) + '" target="_blank" rel="noopener">Open medical note</a></div>'
          : x.kind === "SICK" ? '<div class="cc-meta">No medical note</div>' : "") + "</td><td>" + esc(left) + "</td><td>" + esc(STATUS[x.status] || x.status) + (x.decision_note ? "<div class=\"cc-meta\">" + esc(x.decision_note) + "</div>" : "") +
        "</td><td>" + (x.status === "REQUESTED" ? '<button type="button" class="role-btn small-btn" data-ok="' + esc(x.id) + '">Approve</button> ' +
          '<button type="button" class="secondary-btn small-btn" data-other="' + esc(x.id) + '">Approve as…</button> ' +
          '<button type="button" class="danger-btn small-btn" data-no="' + esc(x.id) + '">Refuse</button>' : "") + "</td></tr>";
    }).join("");
  }

  // "Approve as...": a small dialog with a dropdown of the kinds (e.g. casual beyond the allowance -> unpaid).
  function pickKind(x) {
    return new Promise(function (resolve) {
      const d = document.createElement("dialog");
      d.className = "modal ask-dialog";
      d.innerHTML = '<div class="modal-body"><h2>Approve as another kind</h2><p class="cc-meta">' + esc(x.employee_name) + " asked for " + esc((KIND[x.kind] || "").toLowerCase()) +
        " leave. For example, casual leave beyond this month's allowance can be approved as unpaid.</p>" +
        '<div class="form-field"><label for="lv-as">Approve as</label><select id="lv-as" class="select-input">' +
        Object.keys(KIND).filter(function (k) { return k !== x.kind; }).map(function (k) { return '<option value="' + k + '">' + KIND[k] + " leave</option>"; }).join("") +
        '</select></div><div class="form-actions"><button type="button" class="role-btn" data-v="yes">Approve</button></div>' +
        '<button type="button" class="back-link modal-close-btn" data-v="no">Cancel</button></div>';
      document.body.appendChild(d);
      function done(v) { if (d.open) d.close(); d.remove(); resolve(v); }
      d.querySelector('[data-v="yes"]').addEventListener("click", function () { done(d.querySelector("#lv-as").value); });
      d.querySelector('[data-v="no"]').addEventListener("click", function () { done(null); });
      d.addEventListener("cancel", function (ev) { ev.preventDefault(); done(null); });
      d.showModal();
    });
  }

  async function decide(id, body) {
    const r = await VA.fetchJson("/api/v1/staff-attendance/leave/" + id + "/decide", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { VA.toast(message(r.body, "Could not save the decision."), "error"); return; }
    VA.toast(r.body.status === "APPROVED" ? "Approved: those days are excused." : "Refused; the applicant has been told.");
    load();
  }
  $("lv-body").addEventListener("click", async function (e) {
    const ok = e.target.closest("[data-ok]"), other = e.target.closest("[data-other]"), no = e.target.closest("[data-no]");
    if (ok) {
      const x = rows.find(function (r) { return r.id === ok.getAttribute("data-ok"); });
      if (await VA.confirm("Approve " + (KIND[x.kind] || "").toLowerCase() + " leave for " + x.employee_name + ", " + x.days + " working day(s)?", "Approve")) decide(x.id, { approve: true });
    } else if (other) {
      const x = rows.find(function (r) { return r.id === other.getAttribute("data-other"); });
      const k = await pickKind(x);
      if (k) decide(x.id, { approve: true, as_kind: k });
    } else if (no) {
      const note = await VA.ask({ title: "Refuse leave", label: "Why (the applicant sees this)", input: "textarea", min: 5, ok: "Refuse", danger: true });
      if (note != null) decide(no.getAttribute("data-no"), { approve: false, note: note });
    }
  });
  $("lv-status").addEventListener("change", load);
  document.addEventListener("vistadmin:ready", load);
})();
