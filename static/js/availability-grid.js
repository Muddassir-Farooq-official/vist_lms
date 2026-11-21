// A teacher's unavailable times: a grid of days x class periods to tick, plus the teacher's own limits.
// Used by Admin > Smart timetable > Teachers' available times, and by the teacher's My Timetable page.
//   AvailabilityGrid.render(container, data, { admin: bool, title: str, onSave: async (payload) => response })
(function () {
  "use strict";

  const DAY = { MONDAY: "Mon", TUESDAY: "Tue", WEDNESDAY: "Wed", THURSDAY: "Thu", FRIDAY: "Fri", SATURDAY: "Sat", SUNDAY: "Sun" };
  function esc(v) { return window.VistAdmin ? window.VistAdmin.escapeHtml(v == null ? "" : String(v)) : String(v == null ? "" : v).replace(/[&<>"']/g, function (c) { return "&#" + c.charCodeAt(0) + ";"; }); }

  function render(box, data, opts) {
    opts = opts || {};
    const blocked = new Set(data.blocked || []);
    const periods = (data.periods || []).filter(function (p) { return p.kind === "CLASS"; });
    const readOnly = !opts.admin && data.locked;
    let html = (opts.title ? "<h3 class=\"tt-sub\">" + esc(opts.title) + "</h3>" : "") +
      (readOnly ? '<p class="dash-alert">The administration has fixed your available times. Ask them if something needs to change.</p>' : "") +
      '<p class="panel-hint">Tick the periods <strong>you cannot teach</strong>. Tick a day name to tick the whole day.</p>' +
      '<div class="pt-wrap"><table class="dash-table av-grid no-stack"><thead><tr><th>Period</th>';
    data.days.forEach(function (d) {
      html += '<th><label class="av-day"><input type="checkbox" data-day="' + d + '"' + (readOnly ? " disabled" : "") + " /> " + esc(DAY[d] || d) + "</label></th>";
    });
    html += "</tr></thead><tbody>";
    periods.forEach(function (p) {
      html += '<tr><td class="pt-when">P' + esc(p.number) + '<br><span class="sd-dim">' + esc(p.start) + "–" + esc(p.end) + "</span></td>";
      data.days.forEach(function (d) {
        const applies = !p.days || !p.days.length || p.days.indexOf(d) >= 0;
        if (!applies) { html += '<td class="pt-na">—</td>'; return; }
        const key = d + "|" + p.id;
        html += '<td class="av-cell"><label><input type="checkbox" data-cell="' + esc(key) + '"' + (blocked.has(key) ? " checked" : "") +
          (readOnly ? " disabled" : "") + ' /> <span>Not available</span></label></td>';
      });
      html += "</tr>";
    });
    html += "</tbody></table></div>" +
      '<div class="ar-form av-limits">' +
      '<label>At most<input type="number" class="text-input stt-num" data-f="max_per_day" min="1" max="12" value="' + esc(data.max_per_day || "") + '"' + (readOnly ? " disabled" : "") + ' placeholder="—" /> classes a day</label>' +
      '<label>At most<input type="number" class="text-input stt-num" data-f="max_in_row" min="1" max="12" value="' + esc(data.max_in_row || "") + '"' + (readOnly ? " disabled" : "") + ' placeholder="—" /> periods in a row</label>' +
      '<label class="av-note">Note<input type="text" class="text-input" data-f="note" maxlength="300" value="' + esc(data.note || "") + '"' + (readOnly ? " disabled" : "") + ' placeholder="e.g. Visiting: Tue and Thu only" /></label>' +
      (opts.admin ? '<label><input type="checkbox" data-f="locked"' + (data.locked ? " checked" : "") + " /> Fixed by the administration (the teacher cannot change it)</label>" : "") +
      "</div>" +
      (readOnly ? "" : '<div class="stt-actions"><button type="button" class="role-btn" data-save>Save</button> <span class="sd-dim" data-status></span></div>') +
      '<p class="dash-alert error" data-err hidden></p>' +
      '<p class="sd-dim">Empty limits = the limits chosen when the timetable is generated.</p>';
    box.innerHTML = html;

    box.querySelectorAll("input[data-day]").forEach(function (dayBox) {
      const cells = box.querySelectorAll('input[data-cell^="' + dayBox.dataset.day + '|"]');
      dayBox.checked = cells.length > 0 && Array.prototype.every.call(cells, function (c) { return c.checked; });
      dayBox.addEventListener("change", function () { cells.forEach(function (c) { c.checked = dayBox.checked; }); });
    });
    const save = box.querySelector("[data-save]");
    if (!save) return;
    save.addEventListener("click", async function () {
      const err = box.querySelector("[data-err]");
      err.hidden = true;
      const num = function (f) { const v = box.querySelector('[data-f="' + f + '"]').value.trim(); return v ? parseInt(v, 10) : null; };
      const payload = {
        blocked: Array.prototype.map.call(box.querySelectorAll("input[data-cell]:checked"), function (c) { return c.dataset.cell; }),
        max_per_day: num("max_per_day"), max_in_row: num("max_in_row"),
        note: box.querySelector('[data-f="note"]').value.trim() || null,
      };
      const lk = box.querySelector('[data-f="locked"]');
      if (lk) payload.locked = lk.checked;
      save.disabled = true;
      const r = await opts.onSave(payload);
      save.disabled = false;
      if (!r.ok) { err.textContent = (r.body && r.body.error && r.body.error.message) || "Could not save."; err.hidden = false; return; }
      box.querySelector("[data-status]").textContent = "Saved.";
      if (opts.onSaved) opts.onSaved(r.body);
    });
  }

  window.AvailabilityGrid = { render: render };
})();
