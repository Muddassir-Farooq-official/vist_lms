(function () {
  "use strict";

  // Settings > Rules: GET/PUT /api/v1/policy. Changes are collected here and saved by the page's save bar (settings.js).
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const holder = document.getElementById("policy-groups");
  if (!holder) return;
  let items = [];

  // Rules that are a choice rather than a number: shown as a list (the stored value is the position).
  const CHOICES = { amount_display: ["Full amount (Rs. 4,045,000)", "K / M (Rs. 4.05M)", "Lakh / crore (Rs. 40.45 lakh)"] };

  function fmt(v) { return Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }); }

  function render() {
    const q = (document.getElementById("policy-search").value || "").trim().toLowerCase();
    const groups = {};
    items.forEach(function (p) {
      if (q && (p.label + " " + p.help + " " + p.group).toLowerCase().indexOf(q) === -1) return;
      (groups[p.group] = groups[p.group] || []).push(p);
    });
    const names = Object.keys(groups);
    document.getElementById("policy-none").hidden = names.length > 0;
    holder.innerHTML = names.map(function (g) {
      return '<details class="st-rules" open><summary><h3>' + esc(g) + "</h3><small>" + groups[g].length + " rule" + (groups[g].length === 1 ? "" : "s") + "</small></summary>" +
        groups[g].map(function (p) {
          const id = "policy-" + p.key;
          return '<div class="st-rule"><div class="st-rule-text"><label for="' + id + '">' + esc(p.label) + (p.overridden ? ' <span class="status-pill pending">changed</span>' : "") + "</label>" +
            "<small>" + esc(p.help) + "</small><small class=\"st-rule-default\">Default " + (CHOICES[p.key] ? esc(CHOICES[p.key][Number(p.default)] || "") :
              esc(fmt(p.default)) + " · allowed " + esc(fmt(p.min)) + " to " + esc(fmt(p.max))) + "</small></div>" +
            '<div class="st-rule-input">' + (CHOICES[p.key] ?
              '<select id="' + id + '" data-key="' + esc(p.key) + '" class="select-input">' + CHOICES[p.key].map(function (c, i) {
                return '<option value="' + i + '"' + (Number(p.value) === i ? " selected" : "") + ">" + esc(c) + "</option>"; }).join("") + "</select>" :
              '<input id="' + id + '" data-key="' + esc(p.key) + '" class="text-input small-input" type="number" inputmode="decimal" min="' + p.min + '" max="' + p.max +
            '" step="' + (p.type === "int" ? "1" : "0.01") + '" value="' + esc(String(p.value)) + '" />') +
            (p.overridden ? '<button type="button" class="row-action" data-reset="' + esc(p.key) + '">Use default</button>' : "") + "</div></div>";
        }).join("") + "</details>";
    }).join("");
  }

  function changes() {
    const values = {};
    holder.querySelectorAll("[data-key]").forEach(function (input) {
      const p = items.find(function (x) { return x.key === input.getAttribute("data-key"); });
      if (input.dataset.reset === "1") { values[p.key] = null; return; }
      const raw = input.value.trim();
      if (raw === "") return;
      if (Number(raw) !== Number(p.value)) values[p.key] = Number(raw);
    });
    return values;
  }

  function changed() { document.dispatchEvent(new CustomEvent("settings:changed")); }
  holder.addEventListener("input", changed);
  holder.addEventListener("change", changed);
  holder.addEventListener("click", function (e) {
    const b = e.target.closest("[data-reset]");
    if (!b) return;
    const input = document.getElementById("policy-" + b.getAttribute("data-reset"));
    const p = items.find(function (x) { return x.key === b.getAttribute("data-reset"); });
    input.value = p.default; input.dataset.reset = "1"; b.remove(); changed();
  });
  document.getElementById("policy-search").addEventListener("input", function () {
    const pending = changes(); render();
    Object.keys(pending).forEach(function (k) { const el = document.getElementById("policy-" + k); if (el && pending[k] !== null) el.value = pending[k]; });
  });

  async function load() {
    const r = await VA.fetchJson("/api/v1/policy", { quiet: true });
    if (!r.ok) { document.getElementById("policy-panel").hidden = true; return false; }
    items = r.body; render(); return true;
  }

  async function save() {
    const values = changes();
    if (!Object.keys(values).length) return { ok: true, count: 0 };
    const r = await VA.fetchJson("/api/v1/policy", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values: values }) });
    if (!r.ok) return { ok: false, message: (r.body && r.body.error && r.body.error.message) || "Could not save the rules." };
    items = r.body; render();
    return { ok: true, count: Object.keys(values).length };
  }

  window.SettingsPolicy = { load: load, changes: changes, save: save, discard: render };
})();