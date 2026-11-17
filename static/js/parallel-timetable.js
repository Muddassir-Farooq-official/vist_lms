(function () {
  "use strict";
  // Admin -> Timetable -> Batches side by side: several batch terms in one grid, free periods in red, and auto-fill.
  const VA = window.VistAdmin;
  const API = "/api/v1/academics/timetable/parallel";
  const box = document.getElementById("pt-terms");
  if (!box) return;
  const DAY = { MONDAY: "Mon", TUESDAY: "Tue", WEDNESDAY: "Wed", THURSDAY: "Thu", FRIDAY: "Fri", SATURDAY: "Sat", SUNDAY: "Sun" };
  const errBox = document.getElementById("pt-error");
  const fillBtn = document.getElementById("pt-fill");
  function esc(v) { return VA.escapeHtml(v == null ? "" : String(v)); }
  function err(body, fb) { return (body && body.error && body.error.message) || fb; }
  function chosen() { return Array.from(box.querySelectorAll("input:checked")).map(function (i) { return i.value; }); }
  function showError(msg) { errBox.textContent = msg; errBox.hidden = !msg; }

  async function loadTerms() {
    const r = await VA.fetchJson("/api/v1/academics/terms");
    if (!r.ok) { box.innerHTML = '<span class="sd-dim">Could not load terms.</span>'; return; }
    if (!r.body.length) { box.innerHTML = '<span class="sd-dim">No terms yet.</span>'; return; }
    box.innerHTML = r.body.map(function (t) {
      return '<label><input type="checkbox" value="' + esc(t) + '" /> ' + esc(t) + "</label>";
    }).join("");
  }

  function draw(d) {
    const sum = document.getElementById("pt-summary");
    sum.innerHTML = d.tracks.map(function (t) {
      return '<div class="pt-chip"><strong>' + esc(t.term) + " · Section " + esc(t.section) + "</strong>" +
        (t.free ? '<span class="pt-bad">' + t.free + " free period" + (t.free === 1 ? "" : "s") + "</span>" : '<span class="pt-good">No free period</span>') +
        " of " + t.periods_in_week + " · " + (t.missing_classes ? t.missing_classes + " weekly class" + (t.missing_classes === 1 ? "" : "es") + " still to place" : "all courses placed") +
        (t.clashes.length ? ' · <span class="pt-bad">' + t.clashes.length + " overlap(s)</span>" : "") + "</div>";
    }).join("");
    const grid = document.getElementById("pt-grid");
    let html = '<thead><tr><th class="pt-when">Day / period</th>' + d.tracks.map(function (t) {
      return "<th>" + esc(t.term) + "<br><span class=\"sd-dim\">Section " + esc(t.section) + "</span></th>";
    }).join("") + "</tr></thead><tbody>";
    d.days.forEach(function (day) {
      d.periods.forEach(function (p, i) {
        html += '<tr class="' + (i === 0 ? "pt-day-start" : "") + '"><td class="pt-when"><strong>' + esc(DAY[day] || day) + "</strong> P" + esc(p.number) +
          '<br><span class="sd-dim">' + esc(p.start) + "–" + esc(p.end) + "</span></td>";
        d.tracks.forEach(function (t) {
          const items = t.cells[day + "|" + p.id] || [];
          if (!items.length) { html += '<td class="pt-free">Free</td>'; return; }
          const ids = new Set(items.map(function (x) { return x.offering_id; }));
          html += '<td class="' + (ids.size > 1 ? "pt-clash" : "") + '">' + items.map(function (x) {
            return '<span class="pt-cls"><strong>' + esc(x.code) + "</strong>" + (x.combined_with.length ? '<span class="pt-comb" title="Combined with ' + esc(x.combined_with.join(", ")) + '">combined</span>' : "") +
              "<small>" + esc(x.teacher || "No teacher") + " · " + esc(x.room) + "</small></span>";
          }).join("") + "</td>";
        });
        html += "</tr>";
      });
    });
    grid.innerHTML = html + "</tbody>";
    grid.hidden = false;
    fillBtn.disabled = false;
  }

  async function show() {
    showError("");
    document.getElementById("pt-plan").innerHTML = "";
    const terms = chosen();
    if (!terms.length) return showError("Tick at least one term.");
    const r = await VA.fetchJson(API + "?terms=" + encodeURIComponent(terms.join("||")));
    if (!r.ok) return showError(err(r.body, "Could not load the timetables."));
    draw(r.body);
  }

  function planTable(rows, withReason) {
    if (!rows.length) return "";
    return '<table class="dash-table"><thead><tr><th>Course</th><th>Batch(es)</th><th>Section</th>' +
      (withReason ? "<th>Classes</th><th>Why not</th>" : "<th>Day</th><th>Period</th><th>Room</th><th>Teacher</th>") + "</tr></thead><tbody>" +
      rows.map(function (x) {
        return "<tr><td><strong>" + esc(x.code) + "</strong></td><td>" + esc(x.terms.join(", ")) + "</td><td>" + esc(x.section) + "</td>" +
          (withReason ? "<td>" + esc(x.missing) + "</td><td>" + esc(x.reason) + "</td>"
            : "<td>" + esc(DAY[x.day] || x.day) + "</td><td>P" + esc(x.period) + " " + esc(x.time) + "</td><td>" + esc(x.room) + "</td><td>" + esc(x.teacher || "") + "</td>") + "</tr>";
      }).join("") + "</tbody></table>";
  }

  async function fill() {
    showError("");
    const terms = chosen();
    if (!terms.length) return showError("Tick at least one term.");
    const post = function (preview) {
      return VA.fetchJson(API + "/auto-fill", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ terms: terms, preview: preview }) });
    };
    const r = await post(true);
    if (!r.ok) return showError(err(r.body, "Could not work out a plan."));
    const plan = document.getElementById("pt-plan");
    const p = r.body;
    plan.innerHTML = '<div class="pt-plan"><h3 class="tt-sub">Proposed classes (' + p.planned.length + ")</h3>" +
      (p.planned.length ? planTable(p.planned, false) : '<p class="sd-dim">Nothing to add: every section already has its weekly classes, or no free period suits them.</p>') +
      (p.unplaced.length ? '<h3 class="tt-sub">Could not be placed</h3>' + planTable(p.unplaced, true) : "") +
      (p.planned.length ? '<div class="att-go"><button type="button" class="role-btn" id="pt-apply">Add these ' + p.planned.length + " classes</button></div>" : "") +
      '<p class="panel-hint">Free periods left after this mean those students have fewer weekly class hours than the week has periods. Add a tutorial, lab or library period, or shorten the day.</p></div>';
    const apply = document.getElementById("pt-apply");
    if (!apply) return;
    apply.addEventListener("click", async function () {
      if (!(await VA.confirm("Add " + p.planned.length + " weekly classes to the timetable? Classes for the rest of each term are created; existing classes are not changed.", "Add classes"))) return;
      apply.disabled = true;
      const res = await post(false);
      if (!res.ok) { apply.disabled = false; return showError(err(res.body, "Could not add the classes.")); }
      VA.toast(res.body.created.length + " class(es) added" + (res.body.failed.length ? ", " + res.body.failed.length + " refused" : "") + ".");
      await show();
      if (res.body.failed.length) {
        document.getElementById("pt-plan").innerHTML = '<h3 class="tt-sub">Refused when adding</h3>' + planTable(res.body.failed.map(function (x) { return Object.assign({}, x, { missing: 1 }); }), true);
      }
    });
  }

  document.getElementById("pt-show").addEventListener("click", show);
  fillBtn.addEventListener("click", fill);
  loadTerms();
})();
