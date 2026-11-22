(function () {
  "use strict";
  // Seating & invigilation (API: /api/v1/exams/seating). One datesheet at a time; open a time slot to seat it,
  // see each room's grid, set its invigilators and print door lists / attendance sheets.
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const send = function (method, url, body) { return VA.fetchJson(url, { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }); };
  const API = "/api/v1/exams/seating";
  const day = function (v) { return new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" }); };
  let sched = "", data = null, slot = null, rooms = [], plan = null, invRoom = null;

  async function loadSchedules() {
    const r = await VA.fetchJson("/api/v1/exams/schedules");
    if (!r.ok) { $("se-msg").textContent = err(r, "Could not load the datesheets."); return; }
    const list = Array.isArray(r.body) ? r.body : (r.body.items || r.body.schedules || []);
    $("se-sched").innerHTML = list.map(function (s) { return '<option value="' + s.id + '">' + esc(s.name + " — " + s.term_label) + (s.status === "PUBLISHED" ? "" : " (draft)") + "</option>"; }).join("") ||
      '<option value="">No datesheet yet</option>';
    sched = $("se-sched").value;
    load();
  }

  async function load() {
    if (!sched) { loadRooms(); return; }
    $("se-duty-pdf").href = API + "/" + sched + "/duty-sheet.pdf";
    const r = await VA.fetchJson(API + "/" + sched);
    if (!r.ok) { $("se-msg").textContent = err(r, "Could not load."); return; }
    data = r.body;
    rooms = data.rooms;
    $("se-slots").innerHTML = data.slots.map(function (s, i) {
      return "<tr><td><strong>" + esc(day(s.exam_date)) + "</strong><small>" + s.start_time + "–" + s.end_time + "</small></td><td>" +
        esc(s.papers.map(function (p) { return p.label + " (" + p.students + ")"; }).join(", ")) + "</td><td>" + s.students + "</td><td>" +
        (s.done ? "✓ all" : s.seated) + "</td><td>" + esc(s.rooms.join(", ") || "—") + "</td><td>" + s.invigilators + "</td>" +
        '<td><button type="button" class="row-action se-open" data-i="' + i + '">Open</button></td></tr>';
    }).join("") || '<tr><td colspan="7" class="sd-dim">No papers on this datesheet.</td></tr>';
    drawRooms();
    if (slot) { const again = data.slots.find(function (s) { return s.exam_date === slot.exam_date && s.start_time === slot.start_time; }); if (again) openSlot(again); }
  }

  function drawRooms() {
    $("se-room-body").innerHTML = rooms.map(function (r) {
      return '<tr data-id="' + r.id + '"><td><strong>' + esc(r.name) + "</strong><small>" + esc(r.kind) + (r.seats ? " · " + r.seats + " seats" : "") + "</small></td>" +
        '<td><input type="number" class="text-input se-rows" min="1" max="60" value="' + (r.exam_rows || "") + '" style="max-width:90px" aria-label="Rows"></td>' +
        '<td><input type="number" class="text-input se-cols" min="1" max="30" value="' + (r.exam_cols || "") + '" style="max-width:90px" aria-label="Columns"></td>' +
        "<td>" + (r.exam_seats || "—") + '</td><td><button type="button" class="row-action se-room-save">Save</button></td></tr>';
    }).join("") || '<tr><td colspan="5" class="sd-dim">No rooms listed yet (Timetable → Rooms).</td></tr>';
  }
  async function loadRooms() {
    const r = await VA.fetchJson(API + "/rooms");
    if (r.ok) { rooms = r.body; drawRooms(); }
  }
  $("se-room-body").addEventListener("click", async function (e) {
    const b = e.target.closest(".se-room-save");
    if (!b) return;
    const tr = b.closest("tr"), rows = tr.querySelector(".se-rows").value, cols = tr.querySelector(".se-cols").value;
    const r = await send("PUT", API + "/rooms/" + tr.getAttribute("data-id"), { exam_rows: rows ? Number(rows) : null, exam_cols: cols ? Number(cols) : null });
    if (!r.ok) { VA.toast(err(r, "Not saved."), "error"); return; }
    VA.toast("Saved.");
    load();
  });

  // ---------------------------------------------------------------- one slot
  $("se-slots").addEventListener("click", function (e) {
    const b = e.target.closest(".se-open");
    if (b) openSlot(data.slots[Number(b.getAttribute("data-i"))]);
  });
  function q() { return "exam_date=" + slot.exam_date + "&start_time=" + slot.start_time; }
  async function openSlot(s) {
    slot = s;
    $("se-slot").hidden = false;
    $("se-slot-title").textContent = day(s.exam_date) + " " + s.start_time + "–" + s.end_time + ": " + s.papers.map(function (p) { return p.label; }).join(", ") + " — " + s.students + " students";
    $("se-door").href = API + "/" + sched + "/door-lists.pdf?" + q();
    $("se-sheets").href = API + "/" + sched + "/attendance-sheets.pdf?" + q();
    const usable = rooms.filter(function (r) { return r.exam_seats; });
    $("se-room-pick").innerHTML = usable.map(function (r) {
      return '<label class="rn-missing"><input type="checkbox" class="se-use" value="' + r.id + '"' + (s.rooms.length ? (s.rooms.indexOf(r.name) !== -1 ? " checked" : "") : " checked") + "> " +
        esc(r.name) + " (" + r.exam_seats + ")</label>";
    }).join("") || '<span class="panel-hint">Set rows and columns for the exam rooms below first.</span>';
    const r = await VA.fetchJson(API + "/" + sched + "/plan?" + q());
    if (!r.ok) { $("se-rooms").innerHTML = '<p class="panel-hint">' + esc(err(r, "Could not load.")) + "</p>"; return; }
    plan = r.body;
    const order = plan.papers.map(function (p) { return p.label; });
    $("se-rooms").innerHTML = plan.rooms.map(function (room) {
      const at = {};
      room.seats.forEach(function (x) { at[x.row + "-" + x.col] = x; });
      let cells = "";
      for (let row = 1; row <= room.rows; row++) {
        for (let col = 1; col <= room.cols; col++) {
          const x = at[row + "-" + col];
          cells += x ? '<div class="se-seat" data-p="' + (order.indexOf(x.paper) % 6) + '" title="' + esc(x.name) + '"><b>' + esc(x.roll_number || "") + "</b>" + esc(x.paper) + " · R" + row + "-C" + col + "</div>"
                     : '<div class="se-seat empty">R' + row + "-C" + col + "</div>";
        }
      }
      const inv = room.invigilators.map(function (i) { return esc(i.name) + (i.role === "IN_CHARGE" ? " (in charge)" : ""); }).join(", ") || '<span class="ro-bad">none yet</span>';
      return '<div class="se-room"><h3>' + esc(room.name) + ' <small class="sd-dim">' + room.students + " students · " +
        esc(Object.keys(room.papers).map(function (k) { return k + ": " + room.papers[k]; }).join(", ")) + "</small></h3>" +
        '<p class="panel-hint">Invigilators (' + room.invigilators.length + " of " + room.needed + " needed): " + inv +
        ' <button type="button" class="row-action se-inv" data-room="' + room.room_id + '" data-name="' + esc(room.name) + '">Change…</button></p>' +
        '<div class="se-grid" style="grid-template-columns:repeat(' + room.cols + ',minmax(74px,1fr))">' + cells + "</div></div>";
    }).join("") || '<p class="panel-hint">Not seated yet: press Make the seating.</p>';
  }
  $("se-seat").addEventListener("click", async function () {
    const ids = Array.prototype.map.call(document.querySelectorAll(".se-use:checked"), function (c) { return c.value; });
    if (!ids.length) { VA.toast("Choose at least one room.", "error"); return; }
    if (slot.seated && !window.confirm("Make this slot's seating again? The current seats are replaced.")) return;
    this.disabled = true;
    const r = await send("POST", API + "/" + sched + "/seat", { exam_date: slot.exam_date, start_time: slot.start_time, room_ids: ids });
    this.disabled = false;
    $("se-msg").textContent = r.ok ? r.body.seated + " students seated in " + r.body.rooms.join(", ") + "." : err(r, "Not seated.");
    load();
  });
  $("se-auto").addEventListener("click", async function () {
    this.disabled = true;
    const r = await send("POST", API + "/" + sched + "/invigilators/auto", { exam_date: slot.exam_date, start_time: slot.start_time });
    this.disabled = false;
    $("se-msg").textContent = r.ok ? r.body.added + " invigilator(s) proposed." + (r.body.short.length ? " " + r.body.short.join("; ") + "." : "") + " Change any of them below." : err(r, "Could not propose.");
    load();
  });

  // ---------------------------------------------------------------- change a room's invigilators
  $("se-rooms").addEventListener("click", async function (e) {
    const b = e.target.closest(".se-inv");
    if (!b) return;
    invRoom = b.getAttribute("data-room");
    const room = plan.rooms.find(function (x) { return x.room_id === invRoom; });
    $("se-inv-who").textContent = b.getAttribute("data-name") + " · " + day(slot.exam_date) + " " + slot.start_time;
    const r = await VA.fetchJson(API + "/" + sched + "/teachers?" + q() + "&end_time=" + slot.end_time);
    const on = room.invigilators.map(function (i) { return i.employee_id; });
    const lead = (room.invigilators.find(function (i) { return i.role === "IN_CHARGE"; }) || {}).employee_id;
    const list = r.ok ? r.body : [];
    $("se-inv-list").innerHTML = list.map(function (t) {
      return '<label class="' + (t.free || on.indexOf(t.employee_id) !== -1 ? "" : "busy") + '"><input type="checkbox" value="' + t.employee_id + '"' + (on.indexOf(t.employee_id) !== -1 ? " checked" : "") + "> " +
        esc(t.name) + " <small>" + esc(t.code) + (t.free ? "" : " · busy then") + "</small></label>";
    }).join("");
    $("se-inv-lead").innerHTML = '<option value="">The first ticked</option>' + list.map(function (t) { return '<option value="' + t.employee_id + '"' + (t.employee_id === lead ? " selected" : "") + ">" + esc(t.name) + "</option>"; }).join("");
    $("se-inv-error").hidden = true;
    $("se-inv-modal").showModal();
  });
  $("se-inv-cancel").addEventListener("click", function () { $("se-inv-modal").close(); });
  $("se-inv-save").addEventListener("click", async function () {
    const ids = Array.prototype.map.call($("se-inv-list").querySelectorAll("input:checked"), function (c) { return c.value; });
    const r = await send("PUT", API + "/" + sched + "/invigilators", { exam_date: slot.exam_date, start_time: slot.start_time, room_id: invRoom,
      employee_ids: ids, in_charge_id: $("se-inv-lead").value || null });
    if (!r.ok) { $("se-inv-error").textContent = err(r, "Not saved."); $("se-inv-error").hidden = false; return; }
    $("se-inv-modal").close();
    load();
  });

  $("se-tell").addEventListener("click", async function () {
    if (!window.confirm("Send each invigilator their rooms and times? Each duty is told once.")) return;
    const r = await send("POST", API + "/" + sched + "/tell");
    $("se-msg").textContent = r.ok ? r.body.teachers + " teacher(s) told about " + r.body.duties + " duty(ies)." : err(r, "Not sent.");
    load();
  });
  $("se-sched").addEventListener("change", function () { sched = this.value; slot = null; $("se-slot").hidden = true; load(); });
  loadSchedules();
})();
