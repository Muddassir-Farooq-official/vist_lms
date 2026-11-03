(function () {
  "use strict";

  // Activity log → Security: refused and attack-looking attempts (GET /api/v1/security/summary and /events).
  const VA = window.VistAdmin, esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/security";
  const TONE = { FAILED_SIGNIN: "orange", SIGNIN_LIMIT: "orange", ACCESS_DENIED: "purple", SUSPICIOUS_INPUT: "red", BAD_TOKEN: "red", PROBE: "teal" };
  const SHORT = { FAILED_SIGNIN: "Wrong password", SIGNIN_LIMIT: "Sign-in limit", ACCESS_DENIED: "Not allowed", SUSPICIOUS_INPUT: "Attack-looking input",
    BAD_TOKEN: "Forged sign-in", PROBE: "Scanning" };
  let kinds = {}, loaded = false;

  function when(iso) { return VA.formatDateTime ? VA.formatDateTime(iso) : iso; }
  function ipLink(ip) { return ip ? '<button type="button" class="link-btn" data-ip="' + esc(ip) + '">' + esc(ip) + "</button>" : "—"; }

  async function loadSummary() {
    const r = await VA.fetchJson(API + "/summary?days=" + $("sec-days").value);
    if (!r.ok) return VA.toast((r.body && r.body.error && r.body.error.message) || "Could not load the security summary.", "error");
    const d = r.body;
    kinds = d.kinds;
    if ($("sec-kind").options.length === 1) {
      $("sec-kind").insertAdjacentHTML("beforeend", Object.keys(kinds).map(function (k) { return '<option value="' + esc(k) + '">' + esc(SHORT[k] || k) + "</option>"; }).join(""));
    }
    const tiles = [["FAILED_SIGNIN", d.failed_signins], ["ACCESS_DENIED", d.counts.ACCESS_DENIED], ["SUSPICIOUS_INPUT", d.counts.SUSPICIOUS_INPUT],
      ["BAD_TOKEN", d.counts.BAD_TOKEN], ["SIGNIN_LIMIT", d.counts.SIGNIN_LIMIT], ["PROBE", d.counts.PROBE]];
    $("sec-tiles").innerHTML = tiles.map(function (t) {
      return '<button type="button" class="sec-tile tone-' + TONE[t[0]] + '" data-kind="' + t[0] + '"><b>' + Number(t[1] || 0).toLocaleString() + "</b><span>" +
        esc(SHORT[t[0]]) + "</span><small>" + esc(kinds[t[0]] || "") + "</small></button>";
    }).join("");
    $("sec-ips").innerHTML = d.top_ips.map(function (x) {
      return "<tr><td>" + ipLink(x.ip) + '</td><td class="num">' + x.events + '</td><td class="num">' + x.failed_signins + "</td><td>" + esc(when(x.last_seen)) + "</td></tr>";
    }).join("");
    $("sec-ips-empty").hidden = d.top_ips.length > 0;
    $("sec-accounts").innerHTML = d.top_accounts.map(function (x) {
      return '<tr><td><button type="button" class="link-btn" data-user="' + esc(x.username) + '">' + esc(x.username) + '</button></td><td class="num">' + x.failed +
        '</td><td class="num">' + x.addresses + "</td><td>" + esc(when(x.last_seen)) + "</td></tr>";
    }).join("");
    $("sec-accounts-empty").hidden = d.top_accounts.length > 0;
  }

  async function loadEvents() {
    const p = new URLSearchParams({ days: $("sec-days").value });
    if ($("sec-kind").value) p.set("kind", $("sec-kind").value);
    if ($("sec-ip").value.trim()) p.set("ip", $("sec-ip").value.trim());
    if ($("sec-q").value.trim()) p.set("q", $("sec-q").value.trim());
    const r = await VA.fetchJson(API + "/events?" + p.toString());
    if (!r.ok) return VA.toast((r.body && r.body.error && r.body.error.message) || "Could not load the attempts.", "error");
    $("sec-events").innerHTML = r.body.map(function (e) {
      const who = e.person ? esc(e.person) : (e.username ? esc(e.username) + (e.kind === "FAILED_SIGNIN" ? "" : " <small>(not signed in)</small>") : "<small>Not signed in</small>");
      return "<tr><td>" + esc(when(e.at)) + '</td><td><span class="sec-pill tone-' + (TONE[e.kind] || "teal") + '">' + esc(SHORT[e.kind] || e.kind) + "</span></td><td>" + who +
        "</td><td>" + ipLink(e.ip) + (e.user_agent ? '<small class="sec-ua">' + esc(e.user_agent.slice(0, 70)) + "</small>" : "") +
        "</td><td><code>" + esc((e.method || "") + " " + (e.path || "")) + '</code></td><td class="sec-detail">' + esc(e.detail || "") + "</td></tr>";
    }).join("");
    $("sec-empty").hidden = r.body.length > 0;
    $("sec-count").textContent = r.body.length + (r.body.length === 200 ? "+ (newest 200)" : "") + " attempt" + (r.body.length === 1 ? "" : "s");
  }

  function loadAll() { loaded = true; loadSummary(); loadEvents(); }

  const panel = document.querySelector('[data-tab-panel="security"]');
  if (!panel) return;
  panel.addEventListener("click", function (e) {
    const ip = e.target.closest("[data-ip]"), user = e.target.closest("[data-user]"), tile = e.target.closest("[data-kind]");
    if (ip) { $("sec-ip").value = ip.getAttribute("data-ip"); $("sec-kind").value = ""; $("sec-q").value = ""; }
    else if (user) { $("sec-q").value = user.getAttribute("data-user"); $("sec-ip").value = ""; $("sec-kind").value = "FAILED_SIGNIN"; }
    else if (tile) { $("sec-kind").value = tile.getAttribute("data-kind"); }
    else return;
    loadEvents();
    $("sec-ev-h").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  $("sec-form").addEventListener("submit", function (e) { e.preventDefault(); loadEvents(); });
  $("sec-days").addEventListener("change", loadAll);
  // Load only when the tab is opened (or the page was opened at #security).
  const tab = document.querySelector('.portal-tab[data-tab="security"]');
  if (tab) tab.addEventListener("click", function () { if (!loaded) loadAll(); });
  document.addEventListener("vistadmin:ready", function () { if (!panel.hidden && !loaded) loadAll(); });
  if (location.hash === "#security") setTimeout(function () { if (!loaded) loadAll(); }, 0);
})();