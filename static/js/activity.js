(function () {
  "use strict";

  // Activity log (administration; the principal reads it but cannot export). GET /api/v1/audit/activity and friends.
  const VA = window.VistAdmin, esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/audit";
  const KIND = { admin: ["Administrator", "blue"], staff: ["Staff", "teal"], student: ["Student", "purple"], parent: ["Parent", "orange"], system: ["System", "grey"] };
  const MONEY = ["fee", "payment", "fine", "salary", "bonus", "account", "expense", "income", "payroll", "refund", "purchase"];
  let page = 1, pageSize = 50, total = 0, actor = null, actorName = null;

  function iso(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
  function params() {
    const p = new URLSearchParams();
    const set = function (k, v) { if (v) p.set(k, v); };
    set("q", $("al-q").value.trim()); set("kind", $("al-kind").value); set("action", $("al-action").value);
    set("entity_type", $("al-entity").value); set("date_from", $("al-from").value); set("date_to", $("al-to").value);
    if (actor) p.set("actor_user_id", actor);
    return p;
  }

  async function facets() {
    const r = await VA.fetchJson(API + "/activity-facets", { quiet: true });
    if (!r.ok) return;
    const label = function (s) { return s.replace(/_/g, " ").replace(/^./, function (c) { return c.toUpperCase(); }); };
    $("al-action").insertAdjacentHTML("beforeend", r.body.action_groups.map(function (a) { return '<option value="' + esc(a) + '.">' + esc(label(a)) + "</option>"; }).join(""));
    $("al-entity").insertAdjacentHTML("beforeend", r.body.entity_types.map(function (a) { return '<option value="' + esc(a) + '">' + esc(label(a)) + "</option>"; }).join(""));
  }

  async function load() {
    const p = params(); p.set("page", page); p.set("page_size", pageSize);
    const list = $("al-list");
    list.setAttribute("aria-busy", "true");
    const r = await VA.fetchJson(API + "/activity?" + p.toString());
    list.removeAttribute("aria-busy");
    if (!r.ok) { list.innerHTML = ""; return VA.toast((r.body && r.body.error && r.body.error.message) || "Could not load the activity log.", "error"); }
    const d = r.body;
    total = d.total;
    $("al-export").hidden = !d.can_export;
    $("al-count").textContent = total.toLocaleString() + " entr" + (total === 1 ? "y" : "ies");
    $("al-empty").hidden = d.items.length > 0;
    let lastDay = "";
    list.innerHTML = d.items.map(function (x) {
      const k = KIND[x.actor.kind] || KIND.staff, at = new Date(x.at);
      const day = at.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
      const head = day !== lastDay ? '<li class="al-day">' + esc(day) + "</li>" : "";
      lastDay = day;
      return head + '<li><button type="button" class="al-row" data-id="' + esc(x.id) + '">' +
        '<span class="al-time">' + esc(at.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })) + "</span>" +
        '<span class="al-who"><b>' + esc(x.actor.name) + '</b><span class="al-kind tone-' + k[1] + '">' + k[0] + "</span>" +
        (x.actor.username && x.actor.username !== x.actor.name ? "<small>" + esc(x.actor.username) + "</small>" : "") + "</span>" +
        '<span class="al-what">' + esc(x.what) + (x.subject ? " · <b>" + esc(x.subject) + "</b>" : "") +
        "<small>" + esc(x.entity_type.replace(/_/g, " ")) + (x.summary ? " · " + esc(String(x.summary).slice(0, 120)) : "") + "</small></span>" +
        '<span class="al-ip">' + esc(x.ip || "") + "</span></button>" +
        (x.actor.id ? '<button type="button" class="al-filter-person" data-actor="' + esc(x.actor.id) + '" data-name="' + esc(x.actor.name) + '" title="Only this person">Only ' + esc(x.actor.name.split(" ")[0]) + "</button>" : "") + "</li>";
    }).join("");
    const pages = Math.max(1, Math.ceil(total / pageSize));
    $("al-page").textContent = "Page " + page + " of " + pages;
    $("al-prev").disabled = page <= 1;
    $("al-next").disabled = page >= pages;
    $("al-person").hidden = !actor;
    if (actor) $("al-person").innerHTML = "Showing only <b>" + esc(actorName || "one person") + '</b> · <button type="button" class="link-btn" id="al-person-clear">show everyone</button>';
  }

  function cell(v) {
    if (v === null || v === undefined || v === "") return '<span class="muted">—</span>';
    return esc(typeof v === "object" ? JSON.stringify(v) : String(v));
  }
  async function open(id) {
    const r = await VA.fetchJson(API + "/activity/" + encodeURIComponent(id));
    if (!r.ok) return VA.toast("Could not open this entry.", "error");
    const x = r.body, k = KIND[x.actor.kind] || KIND.staff;
    $("al-d-title").textContent = x.what;
    $("al-d-meta").innerHTML = [
      ["When", new Date(x.at).toLocaleString("en-GB")], ["Who", x.actor.name + " (" + k[0] + (x.actor.username ? ", " + x.actor.username : "") + ")"],
      ["Roles", (x.actor.roles || []).join(", ") || "—"], ["Action", x.action], ["Record", x.entity_type + (x.entity_id ? " · " + x.entity_id : "")],
      ["From", (x.ip || "—") + (x.user_agent ? " · " + x.user_agent : "")],
    ].map(function (p) { return "<dt>" + esc(p[0]) + "</dt><dd>" + esc(p[1]) + "</dd>"; }).join("");
    const before = x.before || {}, after = x.after || {};
    const keys = Array.from(new Set(Object.keys(before).concat(Object.keys(after))));
    $("al-d-nodiff").hidden = keys.length > 0;
    $("al-d-diff").innerHTML = keys.map(function (key) {
      const changed = JSON.stringify(before[key]) !== JSON.stringify(after[key]);
      return '<tr class="' + (changed ? "changed" : "") + '"><td>' + esc(key.replace(/_/g, " ")) + "</td><td>" + cell(before[key]) + "</td><td>" + cell(after[key]) + "</td></tr>";
    }).join("");
    $("al-d-extra").textContent = x.metadata && Object.keys(x.metadata).length ? JSON.stringify(x.metadata, null, 2) : "None.";
    $("al-dialog").showModal();
  }

  $("al-form").addEventListener("submit", function (e) { e.preventDefault(); page = 1; load(); });
  $("al-prev").addEventListener("click", function () { if (page > 1) { page--; load(); } });
  $("al-next").addEventListener("click", function () { page++; load(); });
  $("al-d-close").addEventListener("click", function () { $("al-dialog").close(); });
  $("al-list").addEventListener("click", function (e) {
    const f = e.target.closest("[data-actor]");
    if (f) { actor = f.getAttribute("data-actor"); actorName = f.getAttribute("data-name"); page = 1; load(); return; }
    const b = e.target.closest("[data-id]");
    if (b) open(b.getAttribute("data-id"));
  });
  $("al-person").addEventListener("click", function (e) { if (e.target.id === "al-person-clear") { actor = null; actorName = null; page = 1; load(); } });
  document.querySelector(".al-chips").addEventListener("click", function (e) {
    const b = e.target.closest("[data-quick]"); if (!b) return;
    const q = b.getAttribute("data-quick"), now = new Date();
    if (q === "clear") { $("al-form").reset(); actor = null; }
    if (q === "today") { $("al-from").value = iso(now); $("al-to").value = iso(now); }
    if (q === "week") { $("al-from").value = iso(new Date(now.getTime() - 6 * 864e5)); $("al-to").value = iso(now); }
    if (q === "logins") { $("al-action").value = "auth."; }
    if (q === "viewas") { $("al-action").value = "view_as."; }
    if (q === "money") { $("al-q").value = ""; $("al-action").value = (MONEY.filter(function (m) { return $("al-action").querySelector('option[value="' + m + '."]'); })[0] || "fee") + "."; }
    page = 1; load();
  });
  $("al-export").addEventListener("click", function () {
    VA.downloadFile(API + "/activity.csv?" + params().toString(), "activity-log.csv", "activity log");
  });

  document.addEventListener("vistadmin:ready", async function () {
    await facets();
    // Links from elsewhere (e.g. "view as" history, a dashboard) can pre-set filters: ?action=, ?kind=, ?actor_user_id=, ?entity_type=, ?entity_id=
    const u = new URLSearchParams(location.search);
    if (u.get("action")) {
      const v = u.get("action").replace(/\.?$/, ".");
      if (!$("al-action").querySelector('option[value="' + v + '"]')) $("al-action").insertAdjacentHTML("beforeend", '<option value="' + esc(v) + '">' + esc(v) + "</option>");
      $("al-action").value = v;
    }
    if (u.get("kind")) $("al-kind").value = u.get("kind");
    if (u.get("entity_type")) $("al-entity").value = u.get("entity_type");
    if (u.get("q") || u.get("entity_id")) $("al-q").value = u.get("q") || u.get("entity_id");
    if (u.get("actor_user_id")) { actor = u.get("actor_user_id"); actorName = u.get("name"); }
    load();
  });
})();
