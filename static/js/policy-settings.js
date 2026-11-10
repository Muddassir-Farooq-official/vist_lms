(function () {
  "use strict";

  // Settings > Rules (GET/PUT /api/v1/policy). Every rule is drawn with a proper control (switch, dropdown, number with
  // its unit, or "limit on/off + amount"), grouped into sections that appear in the Settings side menu. All sections are
  // drawn once and shown one at a time, so unsaved edits survive moving between sections. Saving is done by the page's
  // save bar (settings.js) through window.SettingsPolicy.
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const holder = document.getElementById("policy-groups");
  if (!holder) return;
  let items = [], sections = [], current = null;

  function num(v) { return Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }); }
  function ui(p) { return p.ui || { control: "number", label: p.label, section: p.group }; }
  function withUnit(u, v) { return (u.before ? u.before + " " : "") + num(v) + (u.after ? " " + u.after : ""); }

  function describe(p, v) {
    const u = ui(p);
    v = Number(v);
    if (u.control === "switch") return v ? "On" : "Off";
    if (u.control === "choice") {
      const c = (u.choices || []).find(function (x) { return Number(x[0]) === v; });
      return c ? c[1] : (u.custom ? "Every " + num(v) + " " + u.custom : num(v));
    }
    if (u.control === "optional") return v ? withUnit(u, v) : u.off;
    return withUnit(u, v);
  }

  function unitBox(p, id, value, disabled) {
    const u = ui(p);
    return '<span class="st-unit' + (disabled ? " is-off" : "") + '">' + (u.before ? "<b>" + esc(u.before) + "</b>" : "") +
      '<input id="' + id + '" type="number" inputmode="decimal" data-key="' + esc(p.key) + '" min="' + p.min + '" max="' + p.max + '" step="' +
      (p.type === "int" ? "1" : (p.max <= 10 ? "0.01" : "0.5")) + '" value="' + (value === "" ? "" : esc(String(value))) + '"' + (disabled ? " disabled" : "") +
      ' aria-describedby="h-' + esc(p.key) + '">' + (u.after ? "<b>" + esc(u.after) + "</b>" : "") + "</span>";
  }

  function control(p) {
    const u = ui(p), id = "policy-" + p.key, v = Number(p.value);
    if (u.control === "switch") {
      return '<label class="st-switch"><input id="' + id + '" type="checkbox" role="switch" data-key="' + esc(p.key) + '"' + (v ? " checked" : "") +
        ' aria-describedby="h-' + esc(p.key) + '"><span aria-hidden="true"></span></label>';
    }
    if (u.control === "choice") {
      const known = (u.choices || []).some(function (c) { return Number(c[0]) === v; });
      let html = '<select id="' + id + '" class="st-select" data-key="' + esc(p.key) + '" aria-describedby="h-' + esc(p.key) + '">' +
        u.choices.map(function (c) { return '<option value="' + c[0] + '"' + (Number(c[0]) === v ? " selected" : "") + ">" + esc(c[1]) + "</option>"; }).join("") +
        (u.custom ? '<option value="custom"' + (known ? "" : " selected") + ">Custom…</option>" : "") + "</select>";
      if (u.preview === "sound") {
        html += '<button type="button" class="role-btn small-btn secondary-btn st-play" data-play-for="' + esc(p.key) + '" aria-label="Play this tone">\u25B6 Play</button>';
      }
      if (u.custom) {
        html += '<span class="st-unit st-custom"' + (known ? " hidden" : "") + '><b>Every</b><input type="number" min="1" max="' + p.max + '" step="1" data-custom-for="' +
          esc(p.key) + '" aria-label="' + esc(u.label) + ': number of ' + esc(u.custom) + '" value="' + (known ? "" : v) + '"><b>' + esc(u.custom) + "</b></span>";
      }
      return html;
    }
    if (u.control === "optional") {
      const on = v > 0;
      return '<span class="st-optional"><label class="st-switch"><input type="checkbox" role="switch" data-opt-for="' + esc(p.key) + '"' + (on ? " checked" : "") +
        ' aria-label="' + esc(u.on || "Limit it") + '"><span aria-hidden="true"></span></label><span class="st-opt-label" data-opt-label="' + esc(p.key) + '">' +
        esc(on ? (u.on || "On") : u.off) + "</span></span>" + unitBox(p, id, on ? v : "", !on);
    }
    return unitBox(p, id, p.value, false);
  }

  function row(p) {
    const u = ui(p), id = "policy-" + p.key;
    const labelTag = u.control === "optional" ? '<span class="st-row-label" id="l-' + esc(p.key) + '">' : '<label class="st-row-label" for="' + id + '">';
    const labelEnd = u.control === "optional" ? "</span>" : "</label>";
    return '<div class="st-row" data-row="' + esc(p.key) + '" data-find="' + esc((u.label + " " + p.help + " " + u.section + " " + (u.sub || "")).toLowerCase()) + '">' +
      '<div class="st-row-text">' + labelTag + esc(u.label) + labelEnd +
      (p.overridden ? ' <span class="st-changed">Changed</span>' : "") +
      '<small id="h-' + esc(p.key) + '">' + esc(p.help) + "</small>" +
      '<small class="st-default">Default: ' + esc(describe(p, p.default)) + "</small></div>" +
      '<div class="st-row-ctl">' + control(p) +
      (p.overridden ? '<button type="button" class="st-reset" data-reset="' + esc(p.key) + '">Use default</button>' : "") + "</div></div>";
  }

  function render() {
    const bySec = {};
    items.forEach(function (p) { (bySec[ui(p).section] = bySec[ui(p).section] || []).push(p); });
    const order = sections.map(function (s) { return s.name; }).concat(Object.keys(bySec).filter(function (n) { return !sections.some(function (s) { return s.name === n; }); }));
    holder.innerHTML = order.filter(function (n) { return bySec[n]; }).map(function (n) {
      const subs = {}, subOrder = [];
      bySec[n].forEach(function (p) { const s = ui(p).sub || ""; if (!subs[s]) { subs[s] = []; subOrder.push(s); } subs[s].push(p); });
      return '<div class="st-sec" data-sec="' + esc(n) + '">' + subOrder.map(function (s) {
        return '<section class="st-card">' + (s ? "<h3>" + esc(s) + "</h3>" : '<h3 class="st-search-only">' + esc(n) + "</h3>") + subs[s].map(row).join("") + "</section>";
      }).join("") + "</div>";
    }).join("");
    buildNav(order.filter(function (n) { return bySec[n]; }), bySec);
    show();
  }

  function buildNav(names, bySec) {
    const box = document.getElementById("st-nav-rules");
    if (!box) return;
    document.getElementById("st-nav-rules-head").hidden = !names.length;
    box.innerHTML = names.map(function (n) {
      const changedHere = bySec[n].filter(function (p) { return p.overridden; }).length;
      return '<button type="button" class="st-nav-item" data-go="rules" data-section="' + esc(n) + '"><span>' + esc(n) + "</span><small>" +
        bySec[n].length + "</small></button>";
    }).join("");
    document.dispatchEvent(new CustomEvent("settings:nav"));
  }

  // One section at a time; while searching, every matching rule from every section.
  function show() {
    const q = (document.getElementById("policy-search").value || "").trim().toLowerCase();
    let any = false;
    holder.querySelectorAll(".st-sec").forEach(function (sec) {
      let secHas = false;
      sec.querySelectorAll(".st-card").forEach(function (card) {
        let cardHas = false;
        card.querySelectorAll(".st-row").forEach(function (r) {
          const hit = q ? r.getAttribute("data-find").indexOf(q) !== -1 : true;
          r.hidden = !hit; cardHas = cardHas || hit;
        });
        card.hidden = !cardHas; secHas = secHas || cardHas;
      });
      sec.hidden = q ? !secHas : sec.getAttribute("data-sec") !== current;
      any = any || !sec.hidden;
    });
    holder.classList.toggle("is-searching", !!q);
    document.getElementById("policy-none").hidden = any;
    const s = sections.find(function (x) { return x.name === current; });
    document.getElementById("policy-h").textContent = q ? "Search results" : (current || "Rules");
    document.getElementById("policy-about").textContent = q ? "Rules matching “" + q + "” in every section." : (s ? s.about : "");
  }

  function valueOf(p) {
    const u = ui(p), key = p.key;
    const el = holder.querySelector('[data-key="' + key + '"]');
    if (!el) return null;
    if (u.control === "switch") return el.checked ? 1 : 0;
    if (u.control === "choice") {
      if (el.value !== "custom") return Number(el.value);
      const c = holder.querySelector('[data-custom-for="' + key + '"]');
      return c && c.value.trim() !== "" ? Number(c.value) : null;
    }
    if (u.control === "optional") {
      const sw = holder.querySelector('[data-opt-for="' + key + '"]');
      if (!sw.checked) return 0;
      return el.value.trim() === "" ? null : Number(el.value);
    }
    return el.value.trim() === "" ? null : Number(el.value);
  }

  function changes() {
    const values = {};
    items.forEach(function (p) {
      const row = holder.querySelector('[data-row="' + p.key + '"]');
      if (row && row.dataset.reset === "1") { values[p.key] = null; return; }
      const v = valueOf(p);
      if (v !== null && Number(v) !== Number(p.value)) values[p.key] = v;
    });
    return values;
  }

  function changedLabels() {
    const c = changes();
    return items.filter(function (p) { return p.key in c; }).map(function (p) { return ui(p).label; });
  }

  function changed() { document.dispatchEvent(new CustomEvent("settings:changed")); }

  holder.addEventListener("change", function (e) {
    const t = e.target;
    if (t.matches("select[data-key]") && holder.querySelector('[data-custom-for="' + t.getAttribute("data-key") + '"]')) {
      const box = holder.querySelector('[data-custom-for="' + t.getAttribute("data-key") + '"]').closest(".st-custom");
      box.hidden = t.value !== "custom";
      if (t.value === "custom") box.querySelector("input").focus();
    }
    if (t.matches("[data-opt-for]")) {
      const key = t.getAttribute("data-opt-for"), p = items.find(function (x) { return x.key === key; });
      const input = holder.querySelector('[data-key="' + key + '"]');
      input.disabled = !t.checked;
      input.closest(".st-unit").classList.toggle("is-off", !t.checked);
      holder.querySelector('[data-opt-label="' + key + '"]').textContent = t.checked ? (ui(p).on || "On") : ui(p).off;
      if (t.checked) {
        if (!input.value) input.value = Number(p.value) || Number(p.default) || "";
        input.focus();
      }
    }
    changed();
  });
  holder.addEventListener("input", changed);
  holder.addEventListener("click", function (e) {
    const b = e.target.closest("[data-reset]");
    if (!b) return;
    const key = b.getAttribute("data-reset"), p = items.find(function (x) { return x.key === key; });
    const row = holder.querySelector('[data-row="' + key + '"]');
    const saved = p.value; p.value = p.default;                      // draw the control at its default, keep "use default" pending
    row.querySelector(".st-row-ctl").innerHTML = control(p);
    p.value = saved;
    row.dataset.reset = "1";
    changed();
  });
  document.getElementById("policy-search").addEventListener("input", function () {
    if (window.SettingsNav && this.value.trim()) window.SettingsNav.go("rules", current, true);
    show();
  });

  async function load() {
    const [r, s] = await Promise.all([VA.fetchJson("/api/v1/policy", { quiet: true }), VA.fetchJson("/api/v1/policy/sections", { quiet: true })]);
    if (!r.ok) { const pp = document.getElementById("policy-panel"); if (pp) pp.hidden = true; return false; }
    items = r.body; sections = s.ok ? s.body : [];
    if (!current) current = (sections[0] && sections[0].name) || null;
    render(); return true;
  }

  async function save() {
    const c = changes();
    const missing = items.filter(function (p) {
      const u = ui(p);
      if (u.control === "optional") { const sw = holder.querySelector('[data-opt-for="' + p.key + '"]'); return sw && sw.checked && valueOf(p) === null; }
      if (u.control === "choice" && u.custom) { const el = holder.querySelector('[data-key="' + p.key + '"]'); return el && el.value === "custom" && valueOf(p) === null; }
      return false;
    });
    if (missing.length) return { ok: false, message: "Enter the amount for: " + missing.map(function (p) { return ui(p).label; }).join(", ") + "." };
    if (!Object.keys(c).length) return { ok: true, count: 0 };
    const r = await VA.fetchJson("/api/v1/policy", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values: c }) });
    if (!r.ok) return { ok: false, message: (r.body && r.body.error && r.body.error.message) || "Could not save the rules." };
    items = r.body; render();
    return { ok: true, count: Object.keys(c).length };
  }

  function showSection(name) { if (name) current = name; show(); }
  function clearSearch() { const s = document.getElementById("policy-search"); if (s.value) { s.value = ""; } show(); }

  window.SettingsPolicy = { load: load, changes: changes, changedLabels: changedLabels, save: save, discard: render, showSection: showSection,
    clearSearch: clearSearch, current: function () { return current; } };

  // ▶ Play next to a notification tone: hear it with the volume and repeats chosen on the page
  document.addEventListener("click", function (e) {
    const b = e.target.closest && e.target.closest(".st-play");
    if (!b) return;
    const sel = document.querySelector('[data-key="' + b.getAttribute("data-play-for") + '"]');
    const num = function (k, d) { const x = document.querySelector('[data-key="' + k + '"]'); return x && x.value !== "" ? Number(x.value) : d; };
    const go = function () { window.VistSound.preview(Number(sel.value), { volume: num("notify_sound_volume", 70), repeat: num("notify_sound_repeat", 1) }); };
    if (window.VistSound) { go(); return; }
    const tag = document.createElement("script");
    tag.src = "/static/js/notify-sound.js"; tag.onload = go;
    document.head.appendChild(tag);
  });
})();
