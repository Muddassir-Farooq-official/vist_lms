(function () {
  "use strict";
  // Fee defaulters: the overdue list by batch term / session, SMS reminders (preview first), promises to pay and
  // admit-card exemptions for fees (administrators). API: /api/v1/fee-defaulters
  const VA = window.VistAdmin;
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const money = function (v) { return VA.formatMoney(v); };
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/fee-defaulters";
  const canExempt = $("fd-root").getAttribute("data-can-exempt") === "yes";
  const err = function (b, fb) { return (b && b.error && b.error.message) || fb; };
  const day = function (v) { return v ? new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—"; };
  const post = function (url, body) { return VA.fetchJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }); };
  let rows = [];
  let terms = [];
  let defaultMessage = "";
  const chosen = new Set();

  function query() {
    const p = new URLSearchParams();
    if ($("fd-term").value) p.set("term_id", $("fd-term").value);
    else if ($("fd-session").value) p.set("session_id", $("fd-session").value);
    if ($("fd-program").value) p.set("program_id", $("fd-program").value);
    if ($("fd-sem").value) p.set("semester", $("fd-sem").value);
    if ($("fd-days").value) p.set("min_days", $("fd-days").value);
    if ($("fd-min").value) p.set("min_amount", $("fd-min").value);
    return p.toString();
  }

  function fillTerms() {
    const sid = $("fd-session").value;
    const keep = $("fd-term").value;
    $("fd-term").innerHTML = '<option value="">' + (sid ? "Every batch of the session" : "All batch terms") + "</option>" +
      terms.filter(function (t) { return !sid || t.session_id === sid; }).map(function (t) {
        return '<option value="' + t.id + '">' + esc(t.label) + (t.open ? "" : " (closed)") + "</option>";
      }).join("");
    if (keep && Array.prototype.some.call($("fd-term").options, function (o) { return o.value === keep; })) $("fd-term").value = keep;
  }

  async function loadChoices() {
    const r = await VA.fetchJson(API + "/choices");
    if (!r.ok) return;
    terms = r.body.terms;
    defaultMessage = r.body.default_message;
    $("fd-session").insertAdjacentHTML("beforeend", r.body.sessions.map(function (s) { return '<option value="' + s.id + '">' + esc(s.name) + "</option>"; }).join(""));
    $("fd-program").insertAdjacentHTML("beforeend", r.body.programs.map(function (p) { return '<option value="' + p.id + '">' + esc(p.code + " — " + p.name) + "</option>"; }).join(""));
    $("fd-sms-help").textContent = "These are filled in for each student: " + r.body.placeholders.join(" ") + ".";
    fillTerms();
  }

  function followUp(s) {
    const bits = [];
    if (s.promise) bits.push('<span class="ok">Promised by ' + esc(day(s.promise.promised_date)) + (s.promise.amount ? " (" + money(s.promise.amount) + ")" : "") + "</span>");
    if (s.exempt) bits.push("<span>Admit card allowed</span>");
    if (s.last_reminder) bits.push('<small class="sd-dim">Last fee SMS ' + esc(day(s.last_reminder)) + (s.reminded_today ? " (today)" : "") + "</small>");
    return bits.join("<br>") || '<small class="sd-dim">—</small>';
  }

  function syncSelected() {
    $("fd-selected").textContent = chosen.size ? chosen.size + " selected" : "";
    $("fd-sms").disabled = chosen.size === 0;
    $("fd-all").checked = rows.length > 0 && rows.every(function (s) { return chosen.has(s.student_id); });
  }

  async function load() {
    const q = query();
    $("fd-csv").href = API + "/export.csv" + (q ? "?" + q : "");
    const tb = $("fd-table").tBodies[0];
    const r = await VA.fetchJson(API + (q ? "?" + q : ""));
    if (!r.ok) { tb.innerHTML = '<tr><td colspan="8">' + esc(err(r.body, "Could not load the list.")) + "</td></tr>"; return; }
    const d = r.body;
    rows = d.students;
    chosen.forEach(function (id) { if (!rows.some(function (s) { return s.student_id === id; })) chosen.delete(id); });
    const t = d.totals;
    $("fd-kpis").innerHTML = [["Students", d.count], ["Total overdue", money(t.total)], ["Over 90 days", money(t["90+"])],
      ["Promised to pay", d.promised], ["Admit card allowed", d.exempted]].map(function (x) {
      return '<div class="kpi"><div class="kpi-label">' + esc(x[0]) + '</div><div class="kpi-value">' + esc(String(x[1])) + "</div></div>";
    }).join("");
    $("fd-empty").hidden = rows.length > 0;
    tb.innerHTML = rows.map(function (s) {
      const name = s.name + " (" + (s.roll_number || "") + ")";
      return '<tr data-id="' + s.student_id + '" data-name="' + esc(name) + '"><td><input type="checkbox" class="fd-pick" aria-label="Select ' + esc(s.name) + '"' + (chosen.has(s.student_id) ? " checked" : "") + "></td>" +
        "<td><strong>" + esc(s.name) + "</strong><small>" + esc((s.father ? s.father + " · " : "") + (s.roll_number || "—")) + "</small></td>" +
        "<td>" + esc((s.program || "—") + (s.semester ? " · Sem " + s.semester : "")) + "</td>" +
        '<td class="num"><b>' + money(s.overdue) + "</b>" + (s.fines_pending ? "<small>+ fines " + money(s.fines_pending) + "</small>" : "") + "</td>" +
        "<td>" + esc(day(s.oldest_due)) + "<small>" + s.days_overdue + " days</small></td>" +
        "<td>" + esc(s.phone || "—") + (s.guardian_phone ? "<small>Parent " + esc(s.guardian_phone) + "</small>" : "") + "</td>" +
        "<td>" + followUp(s) + "</td>" +
        '<td><button type="button" class="row-action fd-promise">Promise…</button>' +
        (canExempt && !s.exempt ? ' <button type="button" class="row-action fd-exempt">Allow admit card…</button>' : "") + "</td></tr>";
    }).join("");
    syncSelected();
  }

  $("fd-table").addEventListener("change", function (e) {
    if (!e.target.classList.contains("fd-pick")) return;
    const id = e.target.closest("tr").getAttribute("data-id");
    if (e.target.checked) chosen.add(id); else chosen.delete(id);
    syncSelected();
  });
  $("fd-all").addEventListener("change", function () {
    const on = this.checked;
    rows.forEach(function (s) { if (on) chosen.add(s.student_id); else chosen.delete(s.student_id); });
    $("fd-table").querySelectorAll(".fd-pick").forEach(function (c) { c.checked = on; });
    syncSelected();
  });

  // ------------------------------------------------------------ SMS: preview, then send
  function smsBody(dry) {
    return { student_ids: Array.from(chosen), to: $("fd-sms-to").value, message: $("fd-sms-text").value.trim() || null, dry_run: dry };
  }
  function showPreview(d, sent) {
    const skipped = Object.keys(d.skipped).map(function (k) { return d.skipped[k] + " " + k; }).join(", ");
    $("fd-sms-preview").innerHTML = '<p class="' + (sent ? "dash-alert ok" : "panel-hint") + '">' + (sent ? "Queued: " : "Will send: ") +
      "<b>" + d.messages + " SMS</b> to " + d.students + " student(s)" + (skipped ? ". Skipped: " + esc(skipped) + "." : ".") + "</p>" +
      (sent ? "" : '<p class="panel-hint">Example: <i>' + esc(d.sample) + "</i></p>");
  }
  $("fd-sms").addEventListener("click", function () {
    $("fd-sms-who").textContent = chosen.size + " student(s) selected. Nobody gets more than one fee SMS a day, and nobody with a promise to pay gets one.";
    if (!$("fd-sms-text").value) $("fd-sms-text").value = defaultMessage;
    $("fd-sms-preview").innerHTML = ""; $("fd-sms-error").hidden = true; $("fd-sms-send").disabled = true;
    $("fd-sms-modal").showModal();
  });
  ["fd-sms-to", "fd-sms-text"].forEach(function (id) { $(id).addEventListener("input", function () { $("fd-sms-send").disabled = true; }); });
  $("fd-sms-check").addEventListener("click", async function () {
    $("fd-sms-error").hidden = true;
    const r = await post(API + "/remind", smsBody(true));
    if (!r.ok) { $("fd-sms-error").textContent = err(r.body, "Could not prepare it."); $("fd-sms-error").hidden = false; return; }
    showPreview(r.body, false);
    $("fd-sms-send").disabled = r.body.messages === 0;
  });
  $("fd-sms-send").addEventListener("click", async function () {
    this.disabled = true;
    const r = await post(API + "/remind", smsBody(false));
    if (!r.ok) { $("fd-sms-error").textContent = err(r.body, "Not sent."); $("fd-sms-error").hidden = false; return; }
    showPreview(r.body, true);
    VA.toast(r.body.messages + " SMS queued.");
    load();
  });
  $("fd-sms-cancel").addEventListener("click", function () { $("fd-sms-modal").close(); });

  // ------------------------------------------------------------ promises
  let target = null;
  $("fd-table").addEventListener("click", function (e) {
    const tr = e.target.closest("tr");
    if (!tr) return;
    target = tr.getAttribute("data-id");
    if (e.target.classList.contains("fd-promise")) {
      $("fd-pr-who").textContent = tr.getAttribute("data-name");
      $("fd-pr-date").value = ""; $("fd-pr-amount").value = ""; $("fd-pr-note").value = ""; $("fd-pr-error").hidden = true;
      $("fd-pr-modal").showModal(); $("fd-pr-date").focus();
    } else if (e.target.classList.contains("fd-exempt")) {
      $("fd-ex-who").textContent = tr.getAttribute("data-name");
      $("fd-ex-reason").value = ""; $("fd-ex-until").value = ""; $("fd-ex-error").hidden = true;
      $("fd-ex-modal").showModal(); $("fd-ex-reason").focus();
    }
  });
  $("fd-pr-cancel").addEventListener("click", function () { $("fd-pr-modal").close(); });
  $("fd-pr-save").addEventListener("click", async function () {
    const body = { student_id: target, promised_date: $("fd-pr-date").value || null, amount: $("fd-pr-amount").value ? Number($("fd-pr-amount").value) : null,
      note: $("fd-pr-note").value.trim() || null };
    if (!body.promised_date) { $("fd-pr-error").textContent = "Choose the date."; $("fd-pr-error").hidden = false; return; }
    const r = await post(API + "/promises", body);
    if (!r.ok) { $("fd-pr-error").textContent = err(r.body, "Not saved."); $("fd-pr-error").hidden = false; return; }
    $("fd-pr-modal").close();
    VA.toast("Promise saved: no fee reminder or admit-card fee hold until " + day(r.body.promised_date) + ".");
    load(); loadPromises();
  });
  async function loadPromises() {
    const r = await VA.fetchJson(API + "/promises" + ($("fd-pr-old").checked ? "?include_old=true" : ""));
    const tb = $("fd-pr-tbody");
    if (!r.ok) { tb.innerHTML = '<tr><td colspan="6">' + esc(err(r.body, "Could not load.")) + "</td></tr>"; return; }
    tb.innerHTML = r.body.map(function (p) {
      return "<tr><td><strong>" + esc(p.name) + "</strong><small>" + esc(p.roll_number || "") + "</small></td><td>" + esc(day(p.promised_date)) + '</td><td class="num">' +
        (p.amount ? money(p.amount) : "") + "</td><td>" + esc(p.note || "") + "</td><td>" + esc(p.created_by || "—") + "</td><td>" +
        (p.active ? '<button type="button" class="row-action fd-pr-x" data-id="' + p.id + '">Cancel</button>'
                  : '<span class="sd-dim">' + (p.cancelled_at ? "cancelled" + (p.cancelled_by ? " by " + esc(p.cancelled_by) : "") : "date passed") + "</span>") + "</td></tr>";
    }).join("") || '<tr><td colspan="6" class="sd-dim">None.</td></tr>';
  }
  $("fd-pr-old").addEventListener("change", loadPromises);
  $("fd-pr-tbody").addEventListener("click", async function (e) {
    const b = e.target.closest(".fd-pr-x");
    if (!b || !window.confirm("Cancel this promise? Reminders and the admit-card fee hold apply again.")) return;
    b.disabled = true;
    const r = await post(API + "/promises/" + b.getAttribute("data-id") + "/cancel");
    if (!r.ok) { VA.toast(err(r.body, "Not cancelled."), "error"); b.disabled = false; return; }
    load(); loadPromises();
  });

  // ------------------------------------------------------------ exemptions (administrators)
  $("fd-ex-cancel").addEventListener("click", function () { $("fd-ex-modal").close(); });
  $("fd-ex-save").addEventListener("click", async function () {
    const r = await post(API + "/exemptions", { student_id: target, reason: $("fd-ex-reason").value.trim(), until: $("fd-ex-until").value || null });
    if (!r.ok) { $("fd-ex-error").textContent = err(r.body, "Not saved."); $("fd-ex-error").hidden = false; return; }
    $("fd-ex-modal").close();
    VA.toast("The admit card can be printed despite overdue fees.");
    load(); loadExemptions();
  });
  async function loadExemptions() {
    const r = await VA.fetchJson(API + "/exemptions" + ($("fd-ex-old").checked ? "?include_old=true" : ""));
    const tb = $("fd-ex-tbody");
    if (!r.ok) { tb.innerHTML = '<tr><td colspan="5">' + esc(err(r.body, "Could not load.")) + "</td></tr>"; return; }
    tb.innerHTML = r.body.map(function (e) {
      return "<tr><td><strong>" + esc(e.name) + "</strong><small>" + esc(e.roll_number || "") + "</small></td><td>" + esc(e.reason) + "</td><td>" +
        (e.until ? esc(day(e.until)) : "until revoked") + "</td><td>" + esc(e.created_by || "—") + "</td><td>" +
        (e.active ? (canExempt ? '<button type="button" class="row-action fd-ex-x" data-id="' + e.id + '">Revoke</button>' : "")
                  : '<span class="sd-dim">' + (e.revoked_at ? "revoked" + (e.revoked_by ? " by " + esc(e.revoked_by) : "") : "expired") + "</span>") + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">None.</td></tr>';
  }
  $("fd-ex-old").addEventListener("change", loadExemptions);
  $("fd-ex-tbody").addEventListener("click", async function (e) {
    const b = e.target.closest(".fd-ex-x");
    if (!b || !window.confirm("Revoke? The admit card is held again while fees are overdue.")) return;
    b.disabled = true;
    const r = await post(API + "/exemptions/" + b.getAttribute("data-id") + "/revoke");
    if (!r.ok) { VA.toast(err(r.body, "Not revoked."), "error"); b.disabled = false; return; }
    load(); loadExemptions();
  });

  $("fd-session").addEventListener("change", function () { fillTerms(); load(); });
  ["fd-term", "fd-program"].forEach(function (id) { $(id).addEventListener("change", load); });
  $("fd-load").addEventListener("click", load);
  loadChoices().then(load);
  loadPromises();
  loadExemptions();
})();
