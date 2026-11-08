(function () {
  "use strict";
  // Activity -> Money changes: one day's discounts, waivers, reversals, back-dated payments ... and "checked" ticks.
  // API: GET /api/v1/audit/money-review, GET .../days, POST .../check
  const VA = window.VistAdmin, esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  if (!$("mr-body")) return;
  const API = "/api/v1/audit/money-review";
  let items = [], loaded = false;

  function iso(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
  function yesterday() { const d = new Date(); d.setDate(d.getDate() - 1); return iso(d); }
  function dayName(s) { return new Date(s + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }); }
  function when(s) { return new Date(s).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }); }

  async function loadDays() {
    const r = await VA.fetchJson(API + "/days", { quiet: true });
    if (!r.ok) return;
    const total = r.body.reduce(function (n, d) { return n + d.unchecked; }, 0);
    $("mr-count").textContent = total ? String(total) : "";
    $("mr-days").innerHTML = r.body.length
      ? "<span class=\"mr-days-label\">Not checked yet:</span>" + r.body.map(function (d) {
        return '<button type="button" class="mr-day" data-day="' + esc(d.day) + '">' + esc(dayName(d.day)) + " <b>" + d.unchecked + "</b></button>";
      }).join("")
      : '<span class="mr-days-label">Everything from the last 14 days is checked.</span>';
  }

  async function load() {
    loaded = true;
    const q = new URLSearchParams({ day: $("mr-day").value || yesterday() });
    if ($("mr-unchecked").checked) q.set("unchecked_only", "true");
    $("mr-body").innerHTML = VA.loadingRowHtml(7);
    const r = await VA.fetchJson(API + "?" + q.toString());
    if (!r.ok) { $("mr-body").innerHTML = ""; return; }
    const d = r.body;
    items = d.items;
    $("mr-summary").textContent = dayName(String(d.day)) + ": " + d.total + " change(s), " + d.unchecked + " not checked";
    $("mr-groups").innerHTML = Object.keys(d.by_group).map(function (g) { return '<span class="mr-group">' + esc(g) + " <b>" + d.by_group[g] + "</b></span>"; }).join("");
    $("mr-empty").hidden = items.length > 0;
    $("mr-body").innerHTML = items.map(function (i) {
      return "<tr" + (i.checked ? ' class="mr-done"' : "") + '><td class="mr-tick">' +
        (i.checked ? "" : '<input type="checkbox" data-id="' + esc(i.id) + '" aria-label="Select this change" />') + "</td>" +
        "<td>" + esc(when(i.at)) + "</td><td><a href=\"" + esc(i.link) + "\">" + esc(i.label) + "</a></td><td>" + esc(i.student || "—") +
        "</td><td>" + esc(i.detail || "") + "</td><td>" + esc(i.by) + "</td><td>" +
        (i.checked ? '<span class="status-pill active">Checked</span><div class="cc-meta">' + esc(i.checked_by || "") + (i.check_note ? ": " + esc(i.check_note) : "") + "</div>" : "—") +
        "</td></tr>";
    }).join("");
    $("mr-all").checked = false;
    sync();
  }

  function picked() { return Array.prototype.map.call(document.querySelectorAll("#mr-body input[data-id]:checked"), function (c) { return c.getAttribute("data-id"); }); }
  function sync() {
    const n = picked().length;
    $("mr-mark").disabled = n === 0;
    $("mr-mark").textContent = n ? "Mark " + n + " as checked" : "Mark selected as checked";
  }
  $("mr-body").addEventListener("change", sync);
  $("mr-all").addEventListener("change", function () {
    document.querySelectorAll("#mr-body input[data-id]").forEach(function (c) { c.checked = $("mr-all").checked; });
    sync();
  });
  $("mr-mark").addEventListener("click", async function () {
    const ids = picked();
    if (!ids.length) return;
    const note = await VA.ask({ title: "Mark " + ids.length + " change(s) as checked", label: "Note (optional)", input: "textarea", min: 0, ok: "Mark as checked",
      intro: "Write anything you found, e.g. \"Discount moved to tuition only\". Leave it empty if all was in order." });
    if (note == null) return;
    $("mr-mark").disabled = true;
    const r = await VA.fetchJson(API + "/check", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: ids, note: note || null }) });
    if (!r.ok) { VA.toast("Could not save. Try again.", "error"); sync(); return; }
    VA.toast(r.body.checked + " marked as checked.");
    load(); loadDays();
  });
  $("mr-form").addEventListener("submit", function (e) { e.preventDefault(); load(); });
  $("mr-days").addEventListener("click", function (e) {
    const b = e.target.closest("[data-day]");
    if (!b) return;
    $("mr-day").value = b.getAttribute("data-day");
    $("mr-unchecked").checked = true;
    load();
  });

  $("mr-day").value = yesterday();
  $("mr-day").max = iso(new Date());
  const tab = document.querySelector('.portal-tab[data-tab="money"]');
  if (tab) tab.addEventListener("click", function () { if (!loaded) load(); });
  document.addEventListener("vistadmin:ready", function () {
    loadDays();
    if (location.hash === "#money" && !loaded) load();
  });
})();
