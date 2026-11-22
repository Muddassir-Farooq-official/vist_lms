(function () {
  "use strict";
  // Transport (administration): API /api/v1/transport; the monthly draft goes to /api/v1/monthly-charges
  const VA = window.VistAdmin;
  const API = "/api/v1/transport";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const money = function (v) { return VA.formatMoney(v); };
  async function send(url, method, body, ok) {
    const r = await VA.fetchJson(url, { method: method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) { VA.toast(err(r, "Not saved."), "error"); return null; }
    if (ok) VA.toast(ok);
    return r.body;
  }
  async function load() {
    const r = await VA.fetchJson(API + "/routes");
    $("tr-routes").innerHTML = (r.ok ? r.body : []).map(function (x) {
      const stops = x.stops.map(function (s) { return "<li>" + esc(s.name) + (s.pickup_time ? " <small>" + s.pickup_time + "</small>" : "") + ' <button type="button" class="link-btn tr-del-stop" data-id="' + s.id + '">remove</button></li>'; }).join("");
      const riders = x.riders.map(function (p) {
        return "<tr><td>" + esc(p.roll_number) + "</td><td>" + esc(p.name) + "</td><td>" + esc(p.stop || "—") + '</td><td class="num">' + money(p.monthly_fee) + '</td><td><button type="button" class="row-action tr-end" data-id="' + p.rider_id + '">Stops riding</button></td></tr>';
      }).join("") || '<tr><td colspan="5" class="sd-dim">No riders.</td></tr>';
      const opts = '<option value="">—</option>' + x.stops.map(function (s) { return '<option value="' + s.id + '">' + esc(s.name) + "</option>"; }).join("");
      return '<section class="sh-card" data-route="' + x.id + '"><h2 class="panel-title">' + esc(x.name) + (x.is_active ? "" : " (closed)") + '</h2><p class="panel-hint">' +
        esc([x.vehicle, x.seats ? x.riders.length + " / " + x.seats + " seats" : x.riders.length + " riders", x.driver_name, x.driver_phone].filter(Boolean).join(" · ")) + " · " + money(x.monthly_fee) + " a month</p>" +
        "<h3>Stops</h3><ol>" + (stops || '<li class="sd-dim">No stops yet.</li>') + "</ol>" +
        '<form class="form-grid tr-stop"><label>Stop<input name="n" class="text-input" maxlength="100" required></label><label>Pick-up<input name="t" type="time" class="text-input"></label><label>Order<input name="p" type="number" min="0" max="200" value="' + x.stops.length + '" class="text-input"></label><div class="decision-bar"><button class="role-btn secondary-btn" type="submit">Add stop</button></div></form>' +
        '<h3>Riders</h3><div class="table-scroll"><table class="dash-table"><thead><tr><th>Roll no.</th><th>Name</th><th>Stop</th><th class="num">Fee</th><th></th></tr></thead><tbody>' + riders + "</tbody></table></div>" +
        '<form class="form-grid tr-rider"><label>Roll number<input name="r" class="text-input" maxlength="30" required></label><label>Stop<select name="s" class="text-input">' + opts + '</select></label><label>Fee (blank = route fee)<input name="f" type="number" min="0" class="text-input"></label><div class="decision-bar"><button class="role-btn" type="submit">Give a seat</button></div></form></section>';
    }).join("");
  }
  $("tr-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    if (await send(API + "/routes", "POST", { name: $("tr-name").value, vehicle: $("tr-vehicle").value || null, seats: $("tr-seats").value ? Number($("tr-seats").value) : null,
      driver_name: $("tr-driver").value || null, driver_phone: $("tr-phone").value || null, monthly_fee: Number($("tr-fee").value || 0) }, "Route added.")) { $("tr-form").reset(); load(); }
  });
  $("tr-routes").addEventListener("submit", async function (e) {
    const f = e.target, route = f.closest("[data-route]").getAttribute("data-route");
    e.preventDefault();
    if (f.classList.contains("tr-stop")) {
      if (await send(API + "/routes/" + route + "/stops", "POST", { name: f.n.value, pickup_time: f.t.value || null, position: Number(f.p.value || 0) }, "Stop added.")) load();
    } else if (f.classList.contains("tr-rider")) {
      if (await send(API + "/riders", "POST", { route_id: route, roll_number: f.r.value.trim(), stop_id: f.s.value || null, monthly_fee: f.f.value === "" ? null : Number(f.f.value) }, "Seat given.")) load();
    }
  });
  $("tr-routes").addEventListener("click", async function (e) {
    const stop = e.target.closest(".tr-del-stop"), end = e.target.closest(".tr-end");
    if (stop && (await send(API + "/stops/" + stop.getAttribute("data-id"), "DELETE", null, "Stop removed."))) load();
    if (end && confirm("This student stops riding from today?") && (await send(API + "/riders/" + end.getAttribute("data-id") + "/end", "POST", {}, "Recorded."))) load();
  });
  $("tr-prepare").addEventListener("click", async function () {
    if (!$("tr-month").value) return VA.toast("Choose the month.", "error");
    const r = await send("/api/v1/monthly-charges/prepare", "POST", { kind: "TRANSPORT", month: $("tr-month").value + "-01" });
    if (r) $("tr-note").textContent = "Draft ready: " + r.students + " rider(s), " + money(r.total) + ". Approve it under Monthly charges.";
  });
  const now = new Date();
  $("tr-month").value = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0");
  load();
})();
