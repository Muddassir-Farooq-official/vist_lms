(function () {
  "use strict";
  // Monthly charges (accounts office): API /api/v1/monthly-charges
  const VA = window.VistAdmin;
  const API = "/api/v1/monthly-charges";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const money = function (v) { return VA.formatMoney(v); };
  const month = function (v) { return new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString("en-GB", { month: "long", year: "numeric" }); };
  const STATUS = { DRAFT: "Waiting for approval", APPROVED: "Approved", DISCARDED: "Sent back" };
  let current = null;
  async function load() {
    const r = await VA.fetchJson(API + ($("mc-status").value ? "?status=" + $("mc-status").value : ""));
    $("mc-runs").innerHTML = (r.ok ? r.body : []).map(function (x) {
      return '<tr data-id="' + x.id + '"><td><strong>' + esc(month(x.month)) + "</strong></td><td>" + esc(x.title) + "</td><td>" + x.students + '</td><td class="num">' + money(x.total) + "</td><td>" +
        esc(STATUS[x.status]) + (x.note ? "<small>" + esc(x.note) + "</small>" : "") + '</td><td><button type="button" class="row-action mc-open">Open</button></td></tr>';
    }).join("") || '<tr><td colspan="6" class="sd-dim">Nothing here.</td></tr>';
  }
  async function open(id) {
    const r = await VA.fetchJson(API + "/" + id);
    if (!r.ok) return;
    current = r.body;
    $("mc-title").textContent = current.title + " — " + month(current.month) + " (" + STATUS[current.status] + ")";
    $("mc-lines").innerHTML = current.lines.map(function (l) {
      return "<tr><td>" + esc(l.roll_number) + "</td><td>" + esc(l.student) + "</td><td>" + esc(l.label) + '</td><td class="num">' + money(l.amount) + "</td><td>" + (l.charged ? "charged" : esc(l.skipped || "")) + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">No lines: nobody to charge.</td></tr>';
    $("mc-actions").hidden = current.status !== "DRAFT";
    $("mc-detail").hidden = false;
  }
  $("mc-status").addEventListener("change", load);
  $("mc-runs").addEventListener("click", function (e) { const b = e.target.closest(".mc-open"); if (b) open(b.closest("tr").getAttribute("data-id")); });
  $("mc-approve").addEventListener("click", async function () {
    if (!(await VA.confirm("Add these " + current.lines.length + " charges (" + money(current.total) + ") to the students' dues?"))) return;
    const r = await VA.fetchJson(API + "/" + current.id + "/approve", { method: "POST" });
    if (!r.ok) return VA.toast(err(r, "Not approved."), "error");
    VA.toast("Approved: " + r.body.charged + " charge(s) added" + (r.body.skipped ? ", " + r.body.skipped + " already charged before" : "") + ".");
    open(current.id); load();
  });
  $("mc-discard").addEventListener("click", async function () {
    const note = await VA.ask({ title: "Send this draft back", label: "What is wrong (the office sees this)", min: 3 });
    if (!note) return;
    const r = await VA.fetchJson(API + "/" + current.id + "/discard", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note: note }) });
    if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
    VA.toast("Sent back."); open(current.id); load();
  });
  load();
})();
