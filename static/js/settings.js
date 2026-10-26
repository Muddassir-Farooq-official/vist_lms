(function () {
  "use strict";

  // Settings: Institute (profile + logos), Look & documents (colour, footer, bank, signature), Rules (policy-settings.js),
  // My account (password). One save bar saves the profile and the rules together; leaving with unsaved changes warns.
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/institute";
  const SWATCHES = ["#2454d6", "#0b6b5d", "#7a1f3d", "#5b2a86", "#b45309", "#0f5132", "#1e3a5f", "#9b1c1c"];
  let profile = null, isAdmin = false;

  function msg(r, fallback) { return (r.body && r.body.error && r.body.error.message) || fallback; }
  function fields() { return Array.prototype.slice.call(document.querySelectorAll("[data-field]")); }

  // ------------------------------------------------------------------ profile
  function fill(p) {
    profile = p;
    fields().forEach(function (el) { const v = p[el.getAttribute("data-field")]; el.value = v == null ? "" : v; el.classList.remove("st-invalid"); });
    $("pf-color-pick").value = p.primary_color || "#2454d6";
    ["logo", "logo_dark", "signature"].forEach(function (kind) {
      const img = $("img-" + kind), has = p["has_" + kind];
      const url = kind === "signature" ? API + "/signature.png?v=" + p.version : (kind === "logo" ? "/branding/logo.png?v=" : "/branding/logo-dark.png?v=") + p.version;
      img.src = has || kind === "logo" ? url : ""; img.hidden = !(has || kind === "logo");
      const box = img.closest(".st-image"); box.classList.toggle("empty", !has);
      box.querySelector("[data-remove]").hidden = !has;
    });
    preview(); dirty();
  }

  function profileChanges() {
    if (!profile) return {};
    const out = {};
    fields().forEach(function (el) {
      const k = el.getAttribute("data-field"), v = el.value.trim(), old = profile[k] == null ? "" : String(profile[k]);
      if (v !== old) out[k] = v;
    });
    return out;
  }

  // ------------------------------------------------------------------ colour and preview
  function luminance(hex) {
    const c = [1, 3, 5].map(function (i) { const v = parseInt(hex.substr(i, 2), 16) / 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }
  function mix(hex, to, t) {
    const a = [1, 3, 5].map(function (i) { return parseInt(hex.substr(i, 2), 16); });
    return "#" + a.map(function (v, i) { return Math.round(v + (to[i] - v) * t).toString(16).padStart(2, "0"); }).join("");
  }
  function validColor(v) { return /^#[0-9a-fA-F]{6}$/.test(v); }

  function preview() {
    const name = $("pf-name").value.trim() || "Institute name", short = $("pf-short").value.trim() || name;
    const color = validColor($("pf-color").value.trim()) ? $("pf-color").value.trim() : "#2454d6";
    const box = $("st-preview");
    box.style.setProperty("--pv", color);
    box.style.setProperty("--pv-dark", mix(color, [0, 0, 0], 0.62));
    box.style.setProperty("--pv-soft", mix(color, [255, 255, 255], 0.9));
    $("pv-short").textContent = short;
    $("pv-name").textContent = name;
    $("pv-address").textContent = [$("pf-address").value.trim(), $("pf-city").value.trim()].filter(Boolean).join(", ");
    $("pv-contact").textContent = [$("pf-phone").value.trim(), $("pf-email").value.trim(), $("pf-website").value.trim()].filter(Boolean).join("  ·  ");
    $("pv-footer").textContent = $("pf-footer").value.trim();
    const hasLogo = profile && profile.has_logo;
    $("pv-logo").hidden = !hasLogo;
    if (hasLogo) $("pv-logo").src = "/branding/logo.png?v=" + profile.version;
    const mark = $("pv-mark");
    mark.innerHTML = hasLogo ? '<img src="' + (profile.has_logo_dark ? "/branding/logo-dark.png?v=" : "/branding/logo.png?v=") + profile.version + '" alt="">' : VA.escapeHtml((short.match(/[A-Za-z0-9]/) || ["I"])[0].toUpperCase());
    const warn = $("pf-contrast");
    // White text on the colour (buttons, menu) must stay readable: WCAG contrast 3:1 at least.
    const contrast = (1.05) / (luminance(color) + 0.05);
    warn.hidden = contrast >= 3;
    warn.textContent = "This colour is light: white text on it is hard to read (contrast " + contrast.toFixed(1) + ":1). Choose a darker shade.";
  }

  $("pf-color-pick").addEventListener("input", function () { $("pf-color").value = $("pf-color-pick").value; preview(); dirty(); });
  $("pf-color-clear").addEventListener("click", function () { $("pf-color").value = ""; $("pf-color-pick").value = "#2454d6"; preview(); dirty(); });
  $("pf-swatches").innerHTML = SWATCHES.map(function (c) { return '<button type="button" class="st-swatch" style="background:' + c + '" data-color="' + c + '" aria-label="Colour ' + c + '"></button>'; }).join("");
  $("pf-swatches").addEventListener("click", function (e) {
    const b = e.target.closest("[data-color]"); if (!b) return;
    $("pf-color").value = b.getAttribute("data-color"); $("pf-color-pick").value = b.getAttribute("data-color"); preview(); dirty();
  });
  fields().forEach(function (el) { el.addEventListener("input", function () { el.classList.remove("st-invalid"); preview(); dirty(); }); });

  // ------------------------------------------------------------------ images
  document.querySelectorAll("[data-upload]").forEach(function (input) {
    input.addEventListener("change", async function () {
      const f = input.files[0]; input.value = "";
      if (!f) return;
      if (f.size > 2 * 1024 * 1024) return VA.toast("The image is too large: at most 2 MB.", "error");
      if (!/^image\/(png|jpeg)$/.test(f.type)) return VA.toast("Upload a PNG or JPG image.", "error");
      const fd = new FormData(); fd.append("file", f);
      const r = await VA.fetchJson(API + "/images/" + input.getAttribute("data-upload"), { method: "POST", body: fd });
      if (!r.ok) return VA.toast(msg(r, "Could not upload the image."), "error");
      const pending = profileChanges(); fill(r.body);
      Object.keys(pending).forEach(function (k) { const el = document.querySelector('[data-field="' + k + '"]'); if (el) el.value = pending[k]; });
      preview(); dirty();
      VA.toast("Uploaded. Pages and new documents use it now (other screens within a minute).");
    });
  });
  document.querySelectorAll("[data-remove]").forEach(function (b) {
    b.addEventListener("click", async function () {
      if (!(await VA.confirm("Remove this image?", "Remove"))) return;
      const r = await VA.fetchJson(API + "/images/" + b.getAttribute("data-remove"), { method: "DELETE" });
      if (!r.ok) return VA.toast(msg(r, "Could not remove it."), "error");
      fill(r.body); VA.toast("Removed.");
    });
  });

  // ------------------------------------------------------------------ save bar (profile + rules together)
  function pendingCount() {
    const a = Object.keys(profileChanges()).length;
    const b = window.SettingsPolicy ? Object.keys(window.SettingsPolicy.changes()).length : 0;
    return a + b;
  }
  function dirty() {
    const n = pendingCount();
    $("st-savebar").hidden = n === 0;
    document.body.classList.toggle("has-savebar", n > 0);
    $("st-savebar-text").textContent = n + " unsaved change" + (n === 1 ? "" : "s");
  }
  document.addEventListener("settings:changed", dirty);
  window.addEventListener("beforeunload", function (e) { if (pendingCount()) { e.preventDefault(); e.returnValue = ""; } });

  $("st-discard").addEventListener("click", function () {
    if (profile) fill(profile);
    if (window.SettingsPolicy) window.SettingsPolicy.discard();
    dirty();
  });

  $("st-save").addEventListener("click", async function () {
    const btn = $("st-save"); btn.disabled = true;
    const ch = profileChanges();
    let saved = 0;
    if (Object.keys(ch).length) {
      const color = ch.primary_color;
      if (color && !validColor(color)) { btn.disabled = false; $("pf-color").classList.add("st-invalid"); return VA.toast("Main colour must look like #0b6b5d.", "error"); }
      const r = await VA.fetchJson(API + "/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(ch) });
      if (!r.ok) { btn.disabled = false; return VA.toast(msg(r, "Could not save the institute details."), "error"); }
      fill(r.body); saved += Object.keys(ch).length;
      if ("primary_color" in ch || "short_name" in ch || "name" in ch) applyLive(r.body);
    }
    if (window.SettingsPolicy) {
      const pr = await window.SettingsPolicy.save();
      if (!pr.ok) { btn.disabled = false; dirty(); return VA.toast(pr.message, "error"); }
      saved += pr.count;
    }
    btn.disabled = false; dirty();
    VA.toast(saved ? "Saved. It shows on every page and new document (other screens within a minute)." : "Nothing to save.");
  });

  // The colour and name change on this page at once (other pages pick them up when opened).
  function applyLive(p) {
    let style = document.getElementById("brand-theme");
    if (!p.primary_color) { if (style) style.remove(); }
    else {
      if (!style) { style = document.createElement("style"); style.id = "brand-theme"; document.head.appendChild(style); }
      const c = p.primary_color;
      style.textContent = ":root{--blue-600:" + c + ";--blue-700:" + mix(c, [0, 0, 0], 0.2) + ";--blue-500:" + mix(c, [255, 255, 255], 0.15) + ";--blue-100:" + mix(c, [255, 255, 255], 0.82) +
        ";--blue-50:" + mix(c, [255, 255, 255], 0.92) + ";--sidebar-bg:" + mix(c, [0, 0, 0], 0.62) + ";--sidebar-bg-alt:" + mix(c, [0, 0, 0], 0.5) + ";}";
    }
    const b = document.querySelector(".drawer-brand .brand-text b"); if (b) b.textContent = p.short_name;
  }

  // ------------------------------------------------------------------ my account
  $("change-password-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    const btn = $("change-password-btn"); btn.disabled = true;
    $("password-error").hidden = true; $("password-success").hidden = true;
    const r = await VA.fetchJson("/api/v1/auth/change-password", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: $("current-password").value, new_password: $("new-password").value }),
    });
    btn.disabled = false;
    if (!r.ok) { $("password-error").textContent = msg(r, "Could not change password."); $("password-error").hidden = false; return; }
    $("change-password-form").reset();
    $("password-success").textContent = "Password updated."; $("password-success").hidden = false;
  });

  document.addEventListener("vistadmin:ready", async function (event) {
    const me = event.detail.me;
    $("acct-username").textContent = me.username;
    $("acct-roles").textContent = (me.roles || []).join(", ");
    isAdmin = (me.permissions || []).indexOf("institute.profile.manage") !== -1;
    if (!isAdmin) {
      document.querySelectorAll("[data-admin-only]").forEach(function (el) { el.hidden = true; });
      const t = document.querySelector('.portal-tab[data-tab="account"]'); if (t) t.click();
    } else {
      const r = await VA.fetchJson(API + "/profile");
      if (r.ok) fill(r.body);
    }
    if (window.SettingsPolicy) await window.SettingsPolicy.load();
    const want = (location.hash || "").replace("#", "");
    const tab = want && document.querySelector('.portal-tab[data-tab="' + want + '"]');
    if (tab && !tab.hidden) tab.click();
  });
})();
