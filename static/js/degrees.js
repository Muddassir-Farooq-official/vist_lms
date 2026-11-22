(function () {
  "use strict";
  // Degrees & transcripts (administration, exam office): API /api/v1/alumni/documents
  const VA = window.VistAdmin;
  const API = "/api/v1/alumni/documents";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const send = function (url, method, body) { return VA.fetchJson(url, { method: method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); };
  const NAMES = { PREPARING: "Being prepared", READY: "Ready", COLLECTED: "Collected", POSTED: "Posted", CANCELLED: "Cancel" };
  let timer = null;
  async function load() {
    const qs = new URLSearchParams({ status: $("dg-status").value });
    if ($("dg-q").value.trim()) qs.set("q", $("dg-q").value.trim());
    const r = await VA.fetchJson(API + "?" + qs.toString());
    $("dg-list").innerHTML = (r.ok ? r.body : []).map(function (d) {
      return '<tr data-id="' + d.id + '"><td><strong>' + esc(d.name) + "</strong><small>" + esc((d.roll_number || "") + (d.program ? " · " + d.program : "")) + "</small></td><td>" +
        (d.kind === "DEGREE" ? "Degree" : "Transcript") + (d.copies > 1 ? " × " + d.copies : "") + (d.by_student ? "<small>asked by the graduate</small>" : "") + "</td><td>" + esc(d.status_name) +
        (d.handed_to ? "<small>" + esc(d.handed_to) + "</small>" : "") + "</td><td>" + esc(d.serial_no || "—") + "</td><td>" +
        d.next.map(function (n) { return '<button type="button" class="row-action dg-go" data-to="' + n + '">' + NAMES[n] + "</button>"; }).join(" ") + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">Nothing here.</td></tr>';
  }
  $("dg-status").addEventListener("change", load);
  $("dg-q").addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(load, 300); });
  $("dg-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const r = await send(API, "POST", { roll_number: $("dg-roll").value.trim(), kind: $("dg-kind").value, copies: Number($("dg-copies").value || 1), note: $("dg-note").value || null });
    if (!r.ok) return VA.toast(err(r, "Not started."), "error");
    VA.toast("Started."); $("dg-form").reset(); load();
  });
  $("dg-list").addEventListener("click", async function (e) {
    const b = e.target.closest(".dg-go");
    if (!b) return;
    const to = b.getAttribute("data-to"), body = { status: to };
    if (to === "READY") { body.serial_no = await VA.ask({ title: "Ready to collect", label: "Serial number", min: 1 }); if (!body.serial_no) return; }
    if (to === "COLLECTED" || to === "POSTED") {
      body.handed_to = await VA.ask({ title: to === "COLLECTED" ? "Collected" : "Posted", label: to === "COLLECTED" ? "Who collected it (name, CNIC)" : "Courier and tracking number", min: 3 });
      if (!body.handed_to) return;
    }
    if (to === "CANCELLED" && !(await VA.confirm("Cancel this?"))) return;
    const r = await send(API + "/" + b.closest("tr").getAttribute("data-id"), "POST", body);
    if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
    load();
  });
  load();
})();
