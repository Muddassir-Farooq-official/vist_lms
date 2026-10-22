(function () {
  "use strict";
  // Hostel: rooms and residents, check in / out / move, the monthly charge, history.
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const money = function (v) { return VA.formatMoney(v); };
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/hostel";
  const FOR = { ANY: "Either", MALE: "Boys", FEMALE: "Girls" };
  let canManage = false;
  let rooms = [];
  const err = function (r, f) { return (r.body && r.body.error && r.body.error.message) || f; };
  const day = function (iso) { return iso ? new Date(String(iso).slice(0, 10) + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—"; };
  const json = function (method, body) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }; };

  async function summary() {
    const r = await VA.fetchJson(API + "/summary", { quiet: true });
    if (!r.ok) return;
    const s = r.body;
    $("h-kpis").innerHTML = [["Rooms", s.rooms], ["Beds", s.beds], ["Residents", s.residents], ["Free beds", s.free_beds], ["Unpaid hostel charges", money(s.charges_unpaid)]]
      .map(function (x) { return '<div class="kpi"><div class="kpi-label">' + esc(x[0]) + '</div><div class="kpi-value">' + esc(String(x[1])) + "</div></div>"; }).join("");
  }

  function renderRooms() {
    const q = $("h-q").value.trim().toLowerCase();
    const freeOnly = $("h-free").checked;
    const tb = $("h-rooms").tBodies[0];
    const list = rooms.filter(function (r) {
      if (freeOnly && r.free_beds < 1) return false;
      if (!q) return true;
      return (r.code + " " + (r.block || "") + " " + r.residents.map(function (x) { return x.name + " " + (x.roll_number || ""); }).join(" ")).toLowerCase().indexOf(q) !== -1;
    });
    $("h-empty").hidden = rooms.length > 0;
    tb.innerHTML = "";
    list.forEach(function (r) {
      const tr = document.createElement("tr");
      const people = r.residents.map(function (p) {
        return '<div class="hr-person"><span>' + esc(p.name) + " <small>" + esc((p.roll_number || "") + (p.bed_no ? " · bed " + p.bed_no : "") + " · since " + day(p.since)) + "</small></span>" +
          (canManage ? '<span class="row-actions"><button type="button" class="role-btn small-btn secondary-btn" data-move="' + esc(p.stay_id) + '">Move</button><button type="button" class="role-btn small-btn secondary-btn" data-out="' + esc(p.stay_id) + '" data-name="' + esc(p.name) + '">Leaves</button></span>' : "") + "</div>";
      }).join("") || '<span class="cc-meta">Empty</span>';
      tr.innerHTML = "<td><strong>" + esc(r.code) + "</strong><small>" + esc([r.block, r.floor ? "floor " + r.floor : ""].filter(Boolean).join(" · ")) + "</small></td><td>" + esc(FOR[r.gender] || r.gender) +
        '</td><td class="num">' + (r.capacity - r.free_beds) + " / " + r.capacity + '</td><td class="num">' + money(r.monthly_fee) + "</td><td>" + people + '</td><td class="row-actions"></td>';
      if (canManage && r.free_beds > 0) {
        const b = document.createElement("button"); b.type = "button"; b.className = "role-btn small-btn"; b.textContent = "Give a bed";
        b.addEventListener("click", function () { checkIn(r); });
        tr.lastElementChild.appendChild(b);
      }
      tb.appendChild(tr);
    });
  }

  async function loadRooms() {
    const tb = $("h-rooms").tBodies[0];
    const r = await VA.fetchJson(API + "/rooms");
    if (!r.ok) {
      tb.innerHTML = '<tr><td colspan="6">' + esc(err(r, "Could not load the rooms.")) + ' <button type="button" class="link-btn" id="h-retry">Try again</button></td></tr>';
      $("h-retry").addEventListener("click", loadRooms);
      return;
    }
    rooms = r.body;
    renderRooms();
    summary();
  }

  async function findStudent() {
    const q = await VA.ask({ title: "Which student?", label: "Roll number or CNIC", min: 2 });
    if (!q) return null;
    const r = await VA.fetchJson("/api/v1/students?page_size=5&q=" + encodeURIComponent(q));
    if (!r.ok) { VA.toast(err(r, "Could not search."), "error"); return null; }
    const list = (r.body.items || []).filter(function (s) { return s.status === "ACTIVE"; });
    if (!list.length) { VA.toast("No current student matches “" + q + "”.", "error"); return null; }
    if (list.length === 1) return list[0];
    const pick = await VA.ask({ title: "Several students match", intro: list.map(function (s, i) { return (i + 1) + ". " + s.first_name + " " + s.last_name + " (" + (s.roll_number || "-") + ")"; }).join("   "),
      label: "Type the number of the right one", input: "number" });
    const i = parseInt(pick, 10) - 1;
    return list[i] || null;
  }

  async function checkIn(room) {
    const s = await findStudent();
    if (!s) return;
    const bed = await VA.ask({ title: "Bed for " + s.first_name + " " + s.last_name + " in " + room.code, label: "Bed number (optional)", required: false, min: 0 });
    if (bed === null) return;
    const r = await VA.fetchJson(API + "/stays", json("POST", { room_id: room.id, student_id: s.id, bed_no: bed === true ? null : (bed || null) }));
    if (!r.ok) return VA.toast(err(r, "Could not give the bed."), "error");
    VA.toast(s.first_name + " now lives in " + room.code + ".");
    loadRooms();
  }

  $("h-rooms").addEventListener("click", async function (e) {
    const out = e.target.closest("[data-out]");
    const mv = e.target.closest("[data-move]");
    if (out) {
      const reason = await VA.ask({ title: out.getAttribute("data-name") + " leaves the hostel", label: "Why (left, graduated, expelled...)", min: 3 });
      if (!reason) return;
      const r = await VA.fetchJson(API + "/stays/" + out.getAttribute("data-out") + "/out", json("POST", { reason: reason }));
      if (!r.ok) return VA.toast(err(r, "Could not record it."), "error");
      VA.toast("Recorded. They are not charged after today."); loadRooms();
    } else if (mv) {
      const code = await VA.ask({ title: "Move to which room?", label: "Room (e.g. B-12)", min: 1 });
      if (!code) return;
      const target = rooms.find(function (x) { return x.code.toLowerCase() === String(code).trim().toLowerCase(); });
      if (!target) return VA.toast("There is no room " + code + ".", "error");
      const r = await VA.fetchJson(API + "/stays/" + mv.getAttribute("data-move") + "/move", json("POST", { room_id: target.id }));
      if (!r.ok) return VA.toast(err(r, "Could not move them."), "error");
      VA.toast("Moved to " + target.code + "."); loadRooms();
    }
  });

  $("h-room-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const body = { code: $("h-code").value.trim(), block: $("h-block").value.trim() || null, capacity: parseInt($("h-cap").value, 10),
      gender: $("h-gender").value, monthly_fee: Number($("h-fee").value || 0) };
    if (!body.code || !body.capacity) return VA.toast("Give the room name and the number of beds.", "error");
    const r = await VA.fetchJson(API + "/rooms", json("POST", body));
    if (!r.ok) return VA.toast(err(r, "Could not add the room."), "error");
    ["h-code", "h-cap", "h-fee"].forEach(function (id) { $(id).value = ""; });
    VA.toast("Room added."); loadRooms();
  });
  $("h-q").addEventListener("input", renderRooms);
  $("h-free").addEventListener("change", renderRooms);

  // ---- the monthly charge
  async function charge(dry) {
    const m = $("h-month").value;
    if (!m) return VA.toast("Choose the month.", "error");
    const r = await VA.fetchJson(API + "/charges", json("POST", { month: m + "-01", dry_run: dry }));
    const note = $("h-charge-note");
    if (!r.ok) { note.textContent = err(r, "Could not work it out."); $("h-charge").disabled = true; return; }
    const b = r.body;
    if (dry) {
      note.textContent = b.students + " resident(s) to charge, " + money(b.total) + " in all." + (b.already_charged ? " " + b.already_charged + " already charged for this month." : "");
      $("h-charge").disabled = !b.students;
    } else {
      note.textContent = "Added: " + b.students + " resident(s), " + money(b.total) + ". It now shows in each student's dues.";
      $("h-charge").disabled = true;
      summary();
    }
  }
  $("h-check").addEventListener("click", function () { charge(true); });
  $("h-charge").addEventListener("click", async function () {
    if (!(await VA.confirm("Add the hostel charge for this month to every resident's dues?"))) return;
    charge(false);
  });
  $("h-month").addEventListener("change", function () { $("h-charge").disabled = true; $("h-charge-note").textContent = ""; });

  // ---- history
  let timer = null;
  async function history() {
    const tb = $("h-hist").tBodies[0];
    const q = $("h-hq").value.trim();
    const r = await VA.fetchJson(API + "/history" + (q ? "?q=" + encodeURIComponent(q) : ""));
    if (!r.ok) { tb.innerHTML = '<tr><td colspan="5">' + esc(err(r, "Could not load the history.")) + "</td></tr>"; return; }
    tb.innerHTML = r.body.length ? r.body.map(function (h) {
      return "<tr><td>" + esc(h.student) + "<small>" + esc(h.roll_number || "") + "</small></td><td>" + esc(h.room) + "</td><td>" + esc(day(h.from)) + "</td><td>" + (h.to ? esc(day(h.to)) : '<span class="status-pill active">living there</span>') + "</td><td>" + esc(h.reason || "") + "</td></tr>";
    }).join("") : '<tr><td colspan="5">Nothing yet.</td></tr>';
  }
  $("h-hq").addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(history, 300); });
  document.querySelector('[data-tab="history"]').addEventListener("click", history);

  document.addEventListener("vistadmin:ready", function (ev) {
    const perms = (ev.detail && ev.detail.me && ev.detail.me.permissions) || [];
    canManage = perms.indexOf("hostel.manage") !== -1;
    document.querySelectorAll("[data-manage]").forEach(function (el) { el.hidden = !canManage; });
    $("h-check").hidden = $("h-charge").hidden = !canManage;
    const now = new Date();
    $("h-month").value = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0");
    loadRooms();
  });
})();