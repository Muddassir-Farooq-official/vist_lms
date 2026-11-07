(function () {
  "use strict";
  // Attendance -> Attendance fines: charge fines by band for a term (preview first), and decide on students below the
  // lowest band. API: POST /api/v1/attendance/fines/run, GET /api/v1/attendance/shortfalls, POST .../shortfalls/{id}/decide
  const VA = window.VistAdmin, esc = VA.escapeHtml, money = VA.formatMoney;
  const $ = function (id) { return document.getElementById(id); };
  if (!$("af-panel")) return;
  const termSel = $("af-term"), box = $("af-preview"), chargeBtn = $("af-charge"), err = $("af-error");
  const STATUS = { FINED: "Fined", AWAITING_DECISION: "Awaiting decision", ALLOWED_WITH_FINE: "Allowed with a fine", EXCUSED: "Excused", DROPPED: "Dropped" };
  let last = null;

  function fail(el, m) { el.textContent = m; el.hidden = false; }
  function message(body, fallback) {
    const e = body && (body.error || body);
    return (e && (e.message || (typeof e.detail === "string" ? e.detail : ""))) || fallback;
  }

  async function loadTerms() {
    const r = await VA.fetchJson("/api/v1/academics/terms", { quiet: true });
    const terms = r.ok ? r.body : [];                         // labels, newest registered term first
    const opts = terms.map(function (t) { return '<option value="' + esc(t) + '">' + esc(t) + "</option>"; }).join("");
    termSel.innerHTML = terms.length ? opts : '<option value="">No term yet</option>';
    $("sf-term").innerHTML = '<option value="">Every term</option>' + opts;
  }

  function tile(n, label, tone) {
    return '<div class="att-tile"' + (tone && n ? ' data-tone="' + tone + '"' : "") + "><b>" + n + "</b><span>" + label + "</span></div>";
  }
  const ACTION = { fine: "Fine now", "top-up": "Difference now", review: "Administration decides", unchanged: "Already done" };

  async function preview() {
    err.hidden = true; chargeBtn.hidden = true;
    if (!termSel.value) return;
    box.innerHTML = '<p class="empty-state">Working out every student’s attendance…</p>';
    const r = await VA.fetchJson("/api/v1/attendance/fines/run", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ term_label: termSel.value, confirm: false }) });
    if (!r.ok) { box.innerHTML = ""; return fail(err, message(r.body, "Could not check the term.")); }
    const p = last = r.body;
    const legend = p.bands.map(function (b) { return "<span>Band " + b.band + ": " + esc(b.label) + " → " + money(b.amount) + " per subject</span>"; }).join("") +
      "<span>Below " + p.review_below + "%: the administration decides</span>";
    const rows = p.items.map(function (i) {
      return "<tr><td>" + esc(i.roll_number || "") + "</td><td>" + esc(i.student) + "</td><td>" + esc(i.course) + "</td>" +
        "<td>" + i.present + " of " + i.classes + " · <b>" + i.percentage + "%</b></td><td>" + (i.band === 4 ? "Below " + p.review_below + "%" : "Band " + i.band) + "</td>" +
        "<td>" + (i.charge ? money(i.charge) : "—") + "</td><td>" + esc(ACTION[i.action] || i.action) + (i.already_charged ? " (charged " + money(i.already_charged) + " before)" : "") + "</td></tr>";
    }).join("");
    box.innerHTML = '<div class="af-legend">' + legend + "</div>" +
      '<div class="att-tiles">' + tile(p.new_fines, "New fines", "warn") + tile(p.top_ups, "Worse band: difference", "warn") +
      tile(p.to_review, "Below " + p.review_below + "%: to decide", "bad") + tile(money(p.amount), "To charge now") + "</div>" +
      (p.unmarked_classes ? '<p class="att-early"><strong>' + p.unmarked_classes + " class(es) this term have no attendance recorded.</strong> " +
        'Mark or cancel them first (<a href="#attention" data-goto-tab="attention">Needs attention</a>), or these percentages will be off.</p>' : "") +
      (p.items.length ? '<div class="table-scroll"><table class="dash-table"><thead><tr><th>Roll no.</th><th>Student</th><th>Subject</th><th>Attendance</th><th>Band</th><th>Fine</th><th>What happens</th></tr></thead><tbody>' +
        rows + "</tbody></table></div>" : '<p class="empty-state">Nobody in this term is below ' + p.minimum + "% so far.</p>");
    const todo = p.new_fines + p.top_ups + p.to_review;
    chargeBtn.hidden = !todo;
    chargeBtn.textContent = "Charge " + (p.new_fines + p.top_ups) + " fine(s): " + money(p.amount) + (p.to_review ? " and list " + p.to_review + " for a decision" : "");
  }

  chargeBtn.addEventListener("click", async function () {
    if (!last) return;
    const ok = await VA.confirm("Charge " + (last.new_fines + last.top_ups) + " attendance fine(s) totalling " + money(last.amount) + " for " + termSel.value +
      "? Students and guardians are told (SMS if switched on). Each fine shows under Fines and can be waived there.", "Charge the fines");
    if (!ok) return;
    chargeBtn.disabled = true;
    const r = await VA.fetchJson("/api/v1/attendance/fines/run", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ term_label: termSel.value, confirm: true }) });
    chargeBtn.disabled = false;
    if (!r.ok) return fail(err, message(r.body, "Could not charge the fines."));
    VA.toast("Charged " + (r.body.new_fines + r.body.top_ups) + " fine(s): " + money(r.body.amount) + (r.body.to_review ? "; " + r.body.to_review + " listed for a decision." : "."));
    preview(); loadDecisions();
  });
  $("af-check").addEventListener("click", preview);
  termSel.addEventListener("change", function () { box.innerHTML = ""; chargeBtn.hidden = true; });

  // ---- decisions and the record of everything charged
  const list = $("sf-body"), sfErr = $("sf-error");
  async function loadDecisions() {
    sfErr.hidden = true;
    const q = new URLSearchParams();
    if ($("sf-term").value) q.set("term_label", $("sf-term").value);
    if ($("sf-status").value) q.set("status", $("sf-status").value);
    list.innerHTML = VA.loadingRowHtml(7);
    const r = await VA.fetchJson("/api/v1/attendance/shortfalls?" + q.toString(), { quiet: true });
    if (!r.ok) { list.innerHTML = ""; return fail(sfErr, message(r.body, "Could not load the list.")); }
    const waiting = r.body.filter(function (x) { return x.status === "AWAITING_DECISION"; }).length;
    const badge = $("att-fines-count"); if (badge) badge.textContent = $("sf-status").value === "AWAITING_DECISION" && waiting ? String(waiting) : badge.textContent;
    if (!r.body.length) { list.innerHTML = '<tr><td colspan="7" class="empty-state">Nothing here.</td></tr>'; return; }
    list.innerHTML = r.body.map(function (x) {
      const actions = x.status === "AWAITING_DECISION"
        ? '<button type="button" class="role-btn small-btn" data-d="FINE" data-id="' + x.id + '">Allow with a fine</button> ' +
          '<button type="button" class="secondary-btn small-btn" data-d="EXCUSE" data-id="' + x.id + '">Excuse</button> ' +
          '<button type="button" class="danger-btn small-btn" data-d="DROP" data-id="' + x.id + '">Drop from course</button>'
        : esc(x.decision_note || "");
      return "<tr><td>" + esc(x.roll_number || "") + "</td><td>" + esc(x.student) + "</td><td>" + esc(x.course) + " · " + esc(x.term_label) + "</td>" +
        "<td><b>" + x.percentage + "%</b></td><td>" + esc(STATUS[x.status] || x.status) + "</td>" +
        "<td>" + (x.amount_charged ? money(x.amount_charged) + (x.fines_unpaid ? " (" + money(x.fines_unpaid) + " unpaid)" : " (paid)") : "—") + "</td><td>" + actions + "</td></tr>";
    }).join("");
  }
  list.addEventListener("click", async function (e) {
    const b = e.target.closest("[data-d]");
    if (!b) return;
    const d = b.getAttribute("data-d");
    let amount = null;
    if (d === "FINE") {
      amount = await VA.ask({ title: "Allow with a fine", intro: "The student may continue in this subject once the fine is paid; then the marks decide.",
        label: "Fine (Rs.)", input: "number", value: last && last.bands ? last.bands[2].amount : "", ok: "Next" });
      if (amount == null) return;
    }
    const titles = { FINE: "Allow with a fine", EXCUSE: "Excuse the low attendance", DROP: "Drop from the course" };
    const note = await VA.ask({ title: titles[d], label: "Reason (kept on record)", input: "textarea", min: 5, ok: titles[d], danger: d === "DROP",
      intro: d === "DROP" ? "The student is withdrawn from this subject (shows as W). This cannot be undone here." : "" });
    if (note == null) return;
    const r = await VA.fetchJson("/api/v1/attendance/shortfalls/" + b.getAttribute("data-id") + "/decide", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision: d, note: note, amount: amount == null ? null : parseFloat(amount) }) });
    if (!r.ok) return fail(sfErr, message(r.body, "Could not save the decision."));
    VA.toast("Saved: " + (STATUS[r.body.status] || r.body.status) + ".");
    loadDecisions();
  });
  ["sf-term", "sf-status"].forEach(function (id) { $(id).addEventListener("change", loadDecisions); });

  document.addEventListener("vistadmin:ready", function () { loadTerms().then(loadDecisions); });
})();
