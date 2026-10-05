(function () {
  "use strict";
  const VA = window.VistAdmin, GU = window.GateUI, esc = VA.escapeHtml, API = "/api/v1/gate";
  const $ = function (id) { return document.getElementById(id); };
  let kind = "", timer = null;

  async function load() {
    const p = new URLSearchParams({ limit: "500" });
    if ($("l-day").value) p.set("day", $("l-day").value);
    if (kind) p.set("kind", kind);
    if ($("l-late").checked) p.set("late_only", "true");
    if ($("l-q").value.trim()) p.set("q", $("l-q").value.trim());
    const r = await VA.fetchJson(API + "/logs?" + p.toString());
    const box = $("l-list");
    box.innerHTML = "";
    if (!r.ok) { box.innerHTML = '<p class="home-empty">' + esc((r.body && r.body.error && r.body.error.message) || "Could not load the log.") + "</p>"; return; }
    r.body.forEach(function (l) { box.appendChild(GU.logCard(l, { onUndo: undo })); });
    $("l-empty").hidden = r.body.length > 0;
    const late = r.body.filter(function (l) { return l.late_minutes > 0; }).length;
    $("l-count").textContent = r.body.length ? r.body.length + " entries \u00b7 " + late + " late" : "";
  }
  async function undo(l) {
    if (!window.confirm("Take back the last mark for " + l.name + "?")) return;
    const r = await VA.fetchJson(API + "/logs/" + l.id + "/undo", { method: "POST" });
    if (r.ok) { VA.toast("Taken back."); load(); } else VA.toast((r.body && r.body.error && r.body.error.message) || "Could not undo it.", "error");
  }
  $("l-kind").querySelectorAll("button").forEach(function (b) {
    b.addEventListener("click", function () { $("l-kind").querySelectorAll("button").forEach(function (x) { x.classList.toggle("active", x === b); }); kind = b.getAttribute("data-kind"); load(); });
  });
  $("l-late").addEventListener("change", load);
  $("l-day").addEventListener("change", load);
  $("l-q").addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(load, 250); });
  document.addEventListener("vistadmin:ready", function () {
    const today = new Date(), p = function (n) { return String(n).padStart(2, "0"); }, fmt = function (d) { return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()); };
    const old = new Date(today.getTime() - 7 * 86400000);
    $("l-day").value = fmt(today); $("l-day").max = fmt(today); $("l-day").min = fmt(old);
    load();
  });
})();