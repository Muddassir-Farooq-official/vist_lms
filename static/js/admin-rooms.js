(function () {
  "use strict";
  // Admin -> Timetable -> Rooms: classrooms, labs and halls with seats.
  const VA = window.VistAdmin;
  const API = "/api/v1/academics/rooms";
  const tbody = document.getElementById("rooms-tbody");
  if (!tbody) return;
  const KIND = { CLASS: "Classroom", LAB: "Lab", HALL: "Hall" };
  function esc(v) { return VA.escapeHtml(v == null ? "" : String(v)); }
  function err(body, fb) { return (body && body.error && body.error.message) || fb; }

  async function load() {
    const r = await VA.fetchJson(API);
    if (!r.ok) return;
    document.getElementById("rooms-empty").hidden = r.body.length > 0;
    tbody.innerHTML = r.body.map(function (x) {
      return "<tr><td><strong>" + esc(x.name) + "</strong></td><td>" + esc(KIND[x.kind] || x.kind) + "</td><td>" + (x.seats == null ? '<span class="sd-dim">—</span>' : esc(x.seats)) + "</td>" +
        "<td>" + (x.is_active ? '<span class="crs-status OPEN">In use</span>' : '<span class="sd-dim">Not in use</span>') + "</td>" +
        '<td class="crs-actions"><button type="button" class="row-action rm-seats" data-id="' + esc(x.id) + '" data-seats="' + esc(x.seats || "") + '">Seats…</button> ' +
        '<button type="button" class="row-action rm-toggle" data-id="' + esc(x.id) + '" data-active="' + (x.is_active ? "1" : "") + '">' + (x.is_active ? "Take out of use" : "Put back in use") + "</button></td></tr>";
    }).join("");
    tbody.querySelectorAll(".rm-seats").forEach(function (b) {
      b.addEventListener("click", async function () {
        const v = await VA.ask({ title: "Seats", label: "Number of seats (empty = not counted)", value: b.getAttribute("data-seats"), required: false, ok: "Save" });
        if (v == null) return;
        const n = v.trim() === "" ? null : Number(v);
        if (n !== null && !(n >= 1)) return VA.toast("Seats must be a number of 1 or more.", "error");
        const res = await VA.fetchJson(API + "/" + b.getAttribute("data-id"), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ seats: n }) });
        if (!res.ok) return VA.toast(err(res.body, "Could not save."), "error");
        load();
      });
    });
    tbody.querySelectorAll(".rm-toggle").forEach(function (b) {
      b.addEventListener("click", async function () {
        const res = await VA.fetchJson(API + "/" + b.getAttribute("data-id"), { method: "PATCH", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ is_active: !b.getAttribute("data-active") }) });
        if (!res.ok) return VA.toast(err(res.body, "Could not save."), "error");
        load();
      });
    });
  }

  document.getElementById("add-room-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const box = document.getElementById("add-room-error");
    box.hidden = true;
    const seats = document.getElementById("room-seats").value;
    const res = await VA.fetchJson(API, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      name: document.getElementById("room-name").value.trim(), kind: document.getElementById("room-kind").value, seats: seats ? Number(seats) : null }) });
    if (!res.ok) { box.textContent = err(res.body, "Could not add the room."); box.hidden = false; return; }
    e.target.reset();
    VA.toast("Room added.");
    load();
  });
  load();
})();
