// Admin > Smart timetable > Teacher workload and What is free (app/timetabling/workload.py).
(function () {
  "use strict";

  const VA = window.VistAdmin;
  const API = "/api/v1/academics/timetable/smart";
  const DAYFULL = { MONDAY: "Monday", TUESDAY: "Tuesday", WEDNESDAY: "Wednesday", THURSDAY: "Thursday", FRIDAY: "Friday", SATURDAY: "Saturday", SUNDAY: "Sunday" };
  const $ = function (id) { return document.getElementById(id); };
  if (!$("wl-terms")) return;
  function esc(v) { return VA.escapeHtml(v == null ? "" : String(v)); }
  function msg(r, fb) { return (r && r.body && r.body.error && r.body.error.message) || fb; }
  function checked(box) { return Array.prototype.map.call(box.querySelectorAll("input:checked"), function (i) { return i.value; }); }
  function json(method, body) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }; }
  function err(id, text) { $(id).textContent = text || ""; $(id).hidden = !text; }

  async function loadTerms() {
    const r = await VA.fetchJson("/api/v1/academics/terms");
    const terms = r.ok ? r.body : [];
    ["wl-terms", "fr-terms"].forEach(function (id) {
      $(id).innerHTML = terms.length ? terms.map(function (t, i) {
        return '<label><input type="checkbox" value="' + esc(t) + '"' + (i === 0 ? " checked" : "") + " /> " + esc(t) + "</label>";
      }).join("") : '<span class="sd-dim">No terms yet.</span>';
    });
  }

  // ================================================================== workload
  let lastTerms = [];
  async function showWorkload() {
    const t = checked($("wl-terms"));
    if (!t.length) { err("wl-error", "Tick at least one term."); return; }
    err("wl-error", "");
    lastTerms = t;
    const r = await VA.fetchJson(API + "/workload?terms=" + encodeURIComponent(t.join("||")));
    if (!r.ok) { err("wl-error", msg(r, "Could not load the workload.")); return; }
    drawWorkload(r.body);
  }

  function bar(row) {
    const top = Math.max(row.max, row.credit_hours, 1);
    const w = Math.min(100, Math.round(row.credit_hours / top * 100));
    const minAt = Math.round(row.min / top * 100), maxAt = Math.round(row.max / top * 100);
    const cls = row.status === "OVER" ? "wl-over" : row.status === "UNDER" ? "wl-under" : "wl-ok";
    return '<div class="wl-bar" title="' + row.credit_hours + " of " + row.min + "–" + row.max + ' credit hours"><span class="' + cls + '" style="width:' + w + '%"></span>' +
      '<i style="left:' + minAt + '%"></i><i style="left:' + maxAt + '%"></i></div>';
  }

  function drawWorkload(d) {
    $("wl-range").textContent = d.default_min + " and " + d.default_max;
    const s = d.summary;
    $("wl-summary").innerHTML =
      '<div class="pt-chip"><span class="pt-bad">' + s.over + " over</span> · " + s.ok + ' within range · <span class="wl-under-t">' + s.under + " under</span> · " + s.teachers + " teachers</div>" +
      '<div class="pt-chip">' + d.unassigned.length + " section(s) without a teacher (" + d.unassigned_credit_hours + " credit hrs) · " + s.capacity_left + " credit hrs of room left</div>" +
      (d.school_mode ? '<div class="pt-chip">School mode: the most is not enforced.</div>' : "");
    $("wl-tbody").innerHTML = d.teachers.map(function (row) {
      const st = row.status === "OVER" ? '<span class="status-pill dropped">Over</span>' : row.status === "UNDER" ? '<span class="status-pill pending">Under</span>' : '<span class="status-pill active">OK</span>';
      return '<tr data-id="' + esc(row.id) + '"><td><strong>' + esc(row.name) + '</strong> <span class="sd-dim">' + esc(row.code) + "</span>" + (row.department ? "<small>" + esc(row.department) + "</small>" : "") + "</td>" +
        "<td><strong>" + row.credit_hours + "</strong> credit hrs " + st + bar(row) + "</td>" +
        '<td class="num">' + row.periods_a_week + "</td>" +
        '<td><input type="number" class="text-input stt-num" min="0" max="60" data-f="load_min" value="' + (row.own_range ? row.min : "") + '" placeholder="' + row.min + '" aria-label="At least" /> – ' +
        '<input type="number" class="text-input stt-num" min="0" max="60" data-f="load_max" value="' + (row.own_range ? row.max : "") + '" placeholder="' + row.max + '" aria-label="At most" />' +
        ' <span class="sd-dim" data-status>' + (row.own_range ? "own range" : "") + "</span></td>" +
        "<td>" + (row.sections.map(function (x) { return esc(x.code + " " + x.section) + ' <span class="sd-dim">(' + x.credit_hours + ")</span>"; }).join(", ") || '<span class="sd-dim">none</span>') + "</td></tr>";
    }).join("") || '<tr><td colspan="5">No teachers.</td></tr>';
    $("wl-table").hidden = false;
    $("wl-unassigned").innerHTML = d.unassigned.length ?
      '<h3 class="tt-sub">Sections without a teacher (' + d.unassigned.length + ")</h3><details><summary>Show the list</summary><p class=\"panel-hint\">" +
      d.unassigned.map(function (u) { return esc(u.code + " " + u.section + " · " + u.term) + " (" + u.credit_hours + ")"; }).join("; ") + "</p></details>" +
      '<div class="stt-actions"><button type="button" class="role-btn" id="wl-propose">Propose teachers for them…</button></div><div id="wl-proposals"></div>' : "";
  }

  $("wl-tbody").addEventListener("change", async function (e) {
    const tr = e.target.closest("tr[data-id]");
    if (!tr) return;
    const val = function (f) { const v = tr.querySelector('[data-f="' + f + '"]').value.trim(); return v === "" ? null : parseInt(v, 10); };
    const st = tr.querySelector("[data-status]");
    st.textContent = "Saving…";
    const r = await VA.fetchJson(API + "/workload/teachers/" + tr.dataset.id + "/range", json("PUT", { load_min: val("load_min"), load_max: val("load_max") }));
    if (!r.ok) { st.textContent = msg(r, "Not saved"); return; }
    showWorkload();
  });

  $("wl-unassigned").addEventListener("click", async function (e) {
    if (e.target.id === "wl-propose") {
      const r = await VA.fetchJson(API + "/workload/proposals?terms=" + encodeURIComponent(lastTerms.join("||")));
      if (!r.ok) { err("wl-error", msg(r, "Could not make proposals.")); return; }
      const p = r.body;
      $("wl-proposals").innerHTML = (p.proposals.length ? '<table class="dash-table"><thead><tr><th></th><th>Section</th><th class="num">Credit hrs</th><th>Proposed teacher</th><th>Their load after</th></tr></thead><tbody>' +
        p.proposals.map(function (x) {
          return '<tr><td><input type="checkbox" checked data-off="' + esc(x.offering_id) + '" data-t="' + esc(x.teacher_id) + '" aria-label="Assign" /></td><td>' + esc(x.code + " " + x.section) + ' <span class="sd-dim">' + esc(x.term) + "</span></td>" +
            '<td class="num">' + x.credit_hours + "</td><td>" + esc(x.teacher) + (x.taught_before ? ' <span class="pt-comb">taught it before</span>' : "") + "</td><td>" + x.teacher_after + " / " + x.teacher_max + "</td></tr>";
        }).join("") + '</tbody></table><div class="stt-actions"><button type="button" class="role-btn" id="wl-apply">Assign the ticked teachers</button></div>' : "") +
        (p.left.length ? '<p class="dash-alert">No teacher has room for: ' + p.left.map(function (x) { return esc(x.code + " " + x.section); }).join(", ") + ". Raise someone's most, or add a teacher.</p>" : "");
    }
    if (e.target.id === "wl-apply") {
      const items = Array.prototype.map.call($("wl-proposals").querySelectorAll("input[data-off]:checked"), function (c) { return { offering_id: c.dataset.off, teacher_id: c.dataset.t }; });
      if (!items.length) return;
      e.target.disabled = true;
      const r = await VA.fetchJson(API + "/workload/assign", json("POST", { items: items }));
      e.target.disabled = false;
      if (!r.ok) { err("wl-error", msg(r, "Could not assign.") + " Nothing was changed."); return; }
      VA.toast(r.body.assigned + " section(s) now have a teacher.");
      showWorkload();
    }
  });
  $("wl-show").addEventListener("click", showWorkload);

  // ================================================================== what is free
  let free = null;
  async function showFree() {
    const t = checked($("fr-terms"));
    if (!t.length) { err("fr-error", "Tick at least one term."); return; }
    err("fr-error", "");
    const r = await VA.fetchJson(API + "/free?terms=" + encodeURIComponent(t.join("||")));
    if (!r.ok) { err("fr-error", msg(r, "Could not load.")); return; }
    free = r.body;
    $("fr-bar").hidden = false;
    $("fr-class").innerHTML = free.classes.map(function (c) { return "<option>" + esc(c) + "</option>"; }).join("") || '<option value="">(no classes)</option>';
    drawFree();
  }
  function drawFree() {
    const mode = $("fr-mode").value, d = free;
    $("fr-class-wrap").hidden = mode !== "class";
    $("fr-detail").hidden = true;
    const busy = new Set(mode === "class" ? (d.class_busy[$("fr-class").value] || []) : []);
    let html = '<thead><tr><th class="pt-when">Period</th>' + d.days.map(function (x) { return "<th>" + esc(DAYFULL[x]) + "</th>"; }).join("") + "</tr></thead><tbody>";
    d.periods.forEach(function (p) {
      html += '<tr><td class="pt-when"><strong>P' + esc(p.number) + '</strong><br><span class="sd-dim">' + esc(p.start) + "–" + esc(p.end) + "</span></td>";
      d.days.forEach(function (day) {
        const key = day + "|" + p.id, c = d.cells[key];
        if (!c) { html += '<td class="pt-na">—</td>'; return; }
        if (mode === "class") { html += busy.has(key) ? "<td>Class</td>" : '<td class="pt-free">Free</td>'; return; }
        html += '<td><button type="button" class="stt-cls" data-cell="' + esc(key) + '"><strong>' + c.free_rooms.length + "</strong> of " + c.rooms_total + " rooms free" +
          "<small>" + c.free_teachers.length + " of " + c.teachers_total + " teachers free · " + c.classes.length + " class(es) on</small></button></td>";
      });
      html += "</tr>";
    });
    $("fr-grid").innerHTML = html + "</tbody>";
    $("fr-grid").hidden = false;
  }
  $("fr-grid").addEventListener("click", function (e) {
    const b = e.target.closest("[data-cell]");
    if (!b) return;
    const c = free.cells[b.dataset.cell], parts = b.dataset.cell.split("|");
    const p = free.periods.find(function (x) { return x.id === parts[1]; });
    const box = $("fr-detail");
    box.hidden = false;
    box.innerHTML = '<h3 class="tt-sub">' + esc(DAYFULL[parts[0]]) + ", P" + esc(p.number) + " (" + esc(p.start) + "–" + esc(p.end) + ")</h3>" +
      "<p><strong>Free rooms:</strong> " + (c.free_rooms.map(function (r) { return esc(r.name) + (r.seats ? " (" + r.seats + (r.kind === "LAB" ? ", lab" : "") + ")" : r.kind === "LAB" ? " (lab)" : ""); }).join(", ") || "none") + "</p>" +
      "<p><strong>Free teachers</strong> (least busy first): " + (c.free_teachers.map(function (t) { return esc(t.name) + ' <span class="sd-dim">' + t.credit_hours + "/" + t.max + "</span>"; }).join(", ") || "none") + "</p>" +
      "<p><strong>Classes on:</strong> " + (c.classes.map(esc).join("; ") || "none") + "</p>";
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  $("fr-show").addEventListener("click", showFree);
  $("fr-mode").addEventListener("change", function () { if (free) drawFree(); });
  $("fr-class").addEventListener("change", function () { if (free) drawFree(); });

  loadTerms();
})();
