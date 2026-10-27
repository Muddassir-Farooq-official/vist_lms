(function () {
  "use strict";

  // Gate visitor log book: /api/v1/gate/...  (guard: Visitors + Log book; administration: the log with corrections and CSV)
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/gate";
  const $ = function (id) { return document.getElementById(id); };
  const msg = function (r, fallback) { return (r.body && r.body.error && r.body.error.message) || fallback; };
  const json = function (method, data) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }; };
  const tile = function (a, b, c, tone) { return '<div class="kpi ' + (tone || "") + '"><span class="kpi-label">' + a + '</span><strong class="kpi-value">' + b + '</strong><span class="kpi-sub">' + (c || "") + "</span></div>"; };
  const stay = function (m) { const h = Math.floor(m / 60); return h ? h + " h " + (m % 60) + " min" : m + " min"; };
  let canManage = false, editing = null, cnicTimer = null;

  async function loadTiles() {
    if (!$("vg-tiles")) return;
    const r = await VA.fetchJson(API + "/summary", { quiet: true });
    if (!r.ok) return;
    const s = r.body;
    $("vg-tiles").innerHTML = '<div class="kpi-grid kpi-grid-6">' + tile("Inside now", s.inside_now, s.inside_since_before_today ? s.inside_since_before_today + " since before today" : "", s.inside_since_before_today ? "warn" : "") +
      tile("Entries today", s.entries_today, s.people_today + " people") + "</div>";
  }

  // ------------------------------------------------------------------ the guard's form
  function formBody() {
    return { visitor_name: $("vg-name").value.trim(), cnic: $("vg-cnic").value.trim() || null, phone: $("vg-phone").value.trim() || null, coming_from: $("vg-from").value.trim() || null,
      purpose: $("vg-purpose").value.trim(), person_to_meet: $("vg-meet").value.trim() || null, vehicle_no: $("vg-vehicle").value.trim() || null,
      people_count: parseInt($("vg-count").value || "1", 10), card_no: $("vg-card").value.trim() || null, note: $("vg-note").value.trim() || null };
  }
  if ($("vg-form")) {
    $("vg-cnic").addEventListener("input", function () {
      clearTimeout(cnicTimer);
      const digits = $("vg-cnic").value.replace(/\D/g, "");
      $("vg-hint").hidden = true;
      if (digits.length !== 13) return;
      cnicTimer = setTimeout(async function () {
        const r = await VA.fetchJson(API + "/visitors/by-cnic/" + digits, { quiet: true });
        if (!r.ok || !r.body) return;
        const v = r.body;
        if (!$("vg-name").value) $("vg-name").value = v.visitor_name;
        if (!$("vg-phone").value && v.phone) $("vg-phone").value = v.phone;
        if (!$("vg-from").value && v.coming_from) $("vg-from").value = v.coming_from;
        $("vg-hint").textContent = "Returning visitor: " + v.visits + " earlier visit(s), last on " + v.entry_date + " (" + v.purpose + ")." + (v.inside ? " Still marked inside!" : "");
        $("vg-hint").hidden = false;
      }, 250);
    });
    $("vg-form").addEventListener("submit", async function (e) {
      e.preventDefault();
      $("vg-error").hidden = true;
      const body = formBody();
      if (body.visitor_name.length < 2 || body.purpose.length < 2) { $("vg-error").textContent = "Enter at least the name and the purpose."; $("vg-error").hidden = false; return; }
      const r = await VA.fetchJson(API + "/visitors", json("POST", body));
      if (!r.ok) { $("vg-error").textContent = msg(r, "Could not save it."); $("vg-error").hidden = false; return; }
      VA.toast(r.body.visitor_name + " written in at " + r.body.entry_time + " (" + r.body.reference + ").");
      $("vg-form").reset(); $("vg-count").value = "1"; $("vg-hint").hidden = true; $("vg-cnic").focus();
      loadInside(); loadTiles();
    });
  }

  async function markOut(v, after) {
    const r = await VA.fetchJson(API + "/visitors/" + v.id + "/out", { method: "POST" });
    if (!r.ok) return VA.toast(msg(r, "Could not mark them out."), "error");
    VA.toast(v.visitor_name + " left at " + r.body.exit_time + ".");
    after(); loadTiles();
  }

  async function loadInside() {
    if (!$("vg-inside")) return;
    const r = await VA.fetchJson(API + "/inside");
    if (!r.ok) return;
    const tb = $("vg-inside");
    tb.innerHTML = "";
    r.body.forEach(function (v) {
      const tr = document.createElement("tr");
      tr.innerHTML = "<td><strong>" + esc(v.entry_time) + "</strong><small>" + esc(String(v.entry_date)) + " · " + stay(v.minutes_inside) + "</small></td><td><strong>" + esc(v.visitor_name) + "</strong><small>" +
        esc([v.cnic, v.phone].filter(Boolean).join(" · ")) + "</small></td><td>" + esc(v.purpose) + "<small>" + esc(v.person_to_meet || "") + "</small></td><td>" +
        esc([v.vehicle_no, v.card_no ? "card " + v.card_no : null, v.people_count > 1 ? v.people_count + " people" : null].filter(Boolean).join(" · ")) + '</td><td class="row-actions"></td>';
      const b = document.createElement("button");
      b.type = "button"; b.className = "role-btn small-btn"; b.textContent = "Mark out";
      b.addEventListener("click", function () { markOut(v, loadInside); });
      tr.querySelector(".row-actions").appendChild(b);
      tb.appendChild(tr);
    });
    $("vg-inside-empty").hidden = r.body.length > 0;
  }

  // ------------------------------------------------------------------ the log book
  function logParams() {
    const p = new URLSearchParams({ limit: "500" });
    if ($("vl-q").value.trim()) p.set("q", $("vl-q").value.trim());
    if ($("vl-from").value) p.set("from_date", $("vl-from").value);
    if ($("vl-to").value) p.set("to_date", $("vl-to").value);
    return p;
  }
  async function loadLog() {
    if (!$("vl-body")) return;
    const p = logParams();
    if ($("vl-inside").checked) p.set("inside_only", "true");
    const r = await VA.fetchJson(API + "/visitors?" + p.toString());
    if (!r.ok) return VA.toast(msg(r, "Could not load the log book."), "error");
    const tb = $("vl-body");
    tb.innerHTML = "";
    r.body.forEach(function (v) {
      const tr = document.createElement("tr");
      const day = VA.formatDateLong ? VA.formatDateLong(String(v.entry_date)) : String(v.entry_date);
      // Each cell's lines sit in one block, so on phones (card rows) they stay stacked beside the label.
      const cellOf = function (inner) { return '<td><span class="vl-c">' + inner + "</span></td>"; };
      tr.innerHTML =
        cellOf("<strong>" + esc(v.visitor_name) + "</strong><small>" + esc([v.reference, v.coming_from].filter(Boolean).join(" · ")) + "</small>") +
        cellOf("<strong>" + esc(String(v.entry_time).slice(0, 5)) + " \u2192 " + (v.exit_time ? esc(String(v.exit_time).slice(0, 5)) : '<span class="status-pill pending">inside</span>') + "</strong><small>" + esc(day) + "</small>") +
        cellOf(esc(v.cnic || "—") + "<small>" + esc(v.phone || "") + "</small>") +
        cellOf(esc(v.purpose) + "<small>" + esc(v.person_to_meet || "") + "</small>") +
        cellOf(esc([v.vehicle_no, v.people_count > 1 ? v.people_count + " people" : null, v.card_no ? "card " + v.card_no : null].filter(Boolean).join(" · ") || "—")) +
        cellOf(esc(v.in_by || "") + (v.out_by ? "<small>out: " + esc(v.out_by) + "</small>" : "")) + '<td class="crs-actions"></td>';
      const cell = tr.querySelector(".crs-actions");
      if (v.inside) {
        const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn"; b.textContent = "Mark out";
        b.addEventListener("click", function () { markOut(v, loadLog); }); cell.appendChild(b);
      }
      if (canManage) {
        const e = document.createElement("button"); e.type = "button"; e.className = "sd-view"; e.textContent = "Correct";
        e.addEventListener("click", function () { openEdit(v); }); cell.appendChild(e);
      }
      tb.appendChild(tr);
    });
    $("vl-empty").hidden = r.body.length > 0;
    $("vl-count").textContent = r.body.length + " entr" + (r.body.length === 1 ? "y" : "ies") + (canManage ? "" : " (you can search the last 7 days)");
    if ($("vl-export")) { $("vl-export").href = API + "/visitors.csv?" + logParams().toString(); $("vl-export").hidden = !canManage; }
  }

  function openEdit(v) {
    editing = v;
    $("vl-edit-title").textContent = "Correct " + v.reference;
    $("ve-name").value = v.visitor_name; $("ve-cnic").value = v.cnic || ""; $("ve-phone").value = v.phone || ""; $("ve-purpose").value = v.purpose;
    $("ve-meet").value = v.person_to_meet || ""; $("ve-vehicle").value = v.vehicle_no || ""; $("ve-in").value = v.entry_time; $("ve-out").value = v.exit_time || ""; $("ve-note").value = v.note || "";
    $("ve-error").hidden = true;
    $("vl-edit").showModal();
  }
  if ($("vl-edit-form")) {
    $("vl-edit-form").addEventListener("submit", async function (e) {
      e.preventDefault();
      const body = { visitor_name: $("ve-name").value.trim(), cnic: $("ve-cnic").value.trim() || null, phone: $("ve-phone").value.trim() || null, purpose: $("ve-purpose").value.trim(),
        person_to_meet: $("ve-meet").value.trim() || null, vehicle_no: $("ve-vehicle").value.trim() || null, entry_time: $("ve-in").value || null, exit_time: $("ve-out").value || null, note: $("ve-note").value.trim() || null };
      const r = await VA.fetchJson(API + "/visitors/" + editing.id, json("PATCH", body));
      if (!r.ok) { $("ve-error").textContent = msg(r, "Could not save it."); $("ve-error").hidden = false; return; }
      $("vl-edit").close(); VA.toast("Corrected."); loadLog();
    });
    $("ve-close").addEventListener("click", function () { $("vl-edit").close(); });
    $("vl-load").addEventListener("click", loadLog);
    $("vl-inside").addEventListener("change", loadLog);
    $("vl-q").addEventListener("keydown", function (e) { if (e.key === "Enter") loadLog(); });
  }

  document.addEventListener("vistadmin:ready", function (event) {
    const me = event.detail && event.detail.me;
    canManage = !!(me && (me.permissions || []).indexOf("gate.manage") !== -1);
    if ($("vl-from") && !$("vl-from").value) {
      const d = new Date(); d.setDate(d.getDate() - (canManage ? 30 : 7));
      $("vl-from").value = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
    }
    loadTiles(); loadInside(); loadLog();
  });
})();