// First run of a new institute (/dashboard/admin/welcome): its name and contact details, logo and colour, and kind
// (university, affiliated college or school), then the dashboard's "Set up your LMS". Saved through the same APIs as
// Settings -> Institute (/api/v1/institute/profile, /images/logo) and Settings -> Rules (/api/v1/policy, school_mode).
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/institute";
  const SWATCHES = [["#1d4ed8", "Royal blue"], ["#b91c1c", "Royal red"], ["#047857", "Emerald green"], ["#6d28d9", "Royal purple"],
    ["#0e7490", "Teal"], ["#c2410c", "Burnt orange"], ["#7a1f3d", "Maroon"], ["#1e3a5f", "Navy"], ["#be185d", "Magenta"], ["#3f3f46", "Graphite"]];
  let step = 1, profile = {}, saved = {};
  const fields = function () { return Array.prototype.slice.call(document.querySelectorAll("[data-field]")); };

  function error(text) { $("wz-error").textContent = text || ""; $("wz-error").hidden = !text; }
  function msg(r, fallback) { return (r.body && r.body.error && r.body.error.message) || fallback; }

  function show(n) {
    step = n;
    document.querySelectorAll(".wz-step").forEach(function (el) { el.hidden = Number(el.getAttribute("data-step")) !== n; });
    document.querySelectorAll("[data-dot]").forEach(function (el) {
      const d = Number(el.getAttribute("data-dot"));
      el.className = d < n ? "done" : (d === n ? "now" : "");
    });
    $("wz-back").hidden = n === 1 || n === 4;
    $("wz-skip").hidden = n === 4;
    $("wz-next").textContent = n === 4 ? "Go to the dashboard" : (n === 3 ? "Finish" : "Next");
    error("");
    const first = document.querySelector('.wz-step[data-step="' + n + '"] input:not([type=file])');
    if (first) first.focus();
  }

  function changes() {
    const out = {};
    fields().forEach(function (el) {
      const k = el.getAttribute("data-field"), v = el.value.trim(), old = saved[k] == null ? "" : String(saved[k]);
      if (v !== old || !saved.__stored) out[k] = v;
    });
    return out;
  }

  async function saveProfile() {
    const ch = changes();
    Object.keys(ch).forEach(function (k) { if (ch[k] === "" && (saved[k] == null || saved[k] === "")) delete ch[k]; });
    if (!Object.keys(ch).length && saved.__stored) return true;
    const r = await VA.fetchJson(API + "/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(ch) });
    if (!r.ok) { error(msg(r, "Could not save. Check the details and try again.")); return false; }
    saved = Object.assign({}, r.body, { __stored: true });
    return true;
  }

  async function next() {
    if (step === 1) {
      if (!$("wz-name").value.trim() || !$("wz-short").value.trim()) { error("Enter the full name and the short name."); return; }
      if (await saveProfile()) show(2);
    } else if (step === 2) {
      const c = $("wz-color").value.trim();
      if (c && !/^#[0-9a-fA-F]{6}$/.test(c)) { error("The colour should look like #1d4ed8, or pick one above."); return; }
      if (await saveProfile()) show(3);
    } else if (step === 3) {
      const pick = document.querySelector('input[name="wz-kind"]:checked');
      if (!pick) { error("Choose the kind of institute."); return; }
      const r = await VA.fetchJson("/api/v1/policy", { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values: { school_mode: pick.value === "school" ? 1 : 0 } }) });
      if (!r.ok) { error(msg(r, "Could not save the kind of institute.")); return; }
      show(4);
    } else {
      location.href = "/dashboard/admin";
    }
  }

  function swatches() {
    $("wz-swatches").innerHTML = SWATCHES.map(function (c) {
      return '<button type="button" class="wz-swatch" data-color="' + c[0] + '" title="' + c[1] + '" aria-label="' + c[1] + '" style="background:' + c[0] + '"></button>';
    }).join("");
    $("wz-swatches").addEventListener("click", function (e) {
      const b = e.target.closest("[data-color]"); if (!b) return;
      $("wz-color").value = b.getAttribute("data-color"); mark();
    });
    $("wz-color").addEventListener("input", mark);
  }
  function mark() {
    const v = $("wz-color").value.trim().toLowerCase();
    document.querySelectorAll(".wz-swatch").forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-color") === v); });
  }

  async function uploadLogo() {
    const file = $("wz-logo").files[0]; if (!file) return;
    $("wz-logo-msg").textContent = "Uploading…";
    const fd = new FormData(); fd.append("file", file);
    const r = await VA.fetchJson(API + "/images/logo", { method: "POST", body: fd });
    $("wz-logo").value = "";
    if (!r.ok) { $("wz-logo-msg").textContent = msg(r, "Could not upload the logo."); return; }
    saved.__stored = true;
    $("wz-logo-msg").textContent = "Logo saved.";
    $("wz-logo-img").src = "/branding/logo.png?v=" + (r.body.version || Date.now());
  }

  async function start() {
    swatches();
    const r = await VA.fetchJson(API + "/profile");
    if (r.ok) {
      profile = r.body; saved = Object.assign({}, profile);
      fields().forEach(function (el) { const v = profile[el.getAttribute("data-field")]; el.value = v == null ? "" : v; });
      $("wz-logo-img").src = "/branding/logo.png?v=" + (profile.version || 0);
      mark();
    }
    const p = await VA.fetchJson("/api/v1/policy", { quiet: true });
    const sm = p.ok && Array.isArray(p.body) ? p.body.filter(function (x) { return x.key === "school_mode"; })[0] : null;
    if (sm && Number(sm.value) === 1) document.querySelector('input[name="wz-kind"][value="school"]').checked = true;
    $("wz-next").addEventListener("click", next);
    $("wz-back").addEventListener("click", function () { show(Math.max(1, step - 1)); });
    $("wz-skip").addEventListener("click", function () {
      try { sessionStorage.setItem("vist.firstrun.skip", "1"); } catch (e) { /* private mode */ }
      location.href = "/dashboard/admin";
    });
    $("wz-logo").addEventListener("change", uploadLogo);
    document.getElementById("wz").addEventListener("keydown", function (e) {
      if (e.key === "Enter" && e.target.tagName === "INPUT" && e.target.type !== "radio") { e.preventDefault(); next(); }
    });
    show(1);
  }

  document.addEventListener("vistadmin:ready", start, { once: true });
})();
