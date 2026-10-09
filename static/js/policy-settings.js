(function () {
  "use strict";

  // Admin Settings > Institute Policy: GET/PUT /api/v1/policy.
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const holder = document.getElementById("policy-groups");
  if (!holder) return;
  const errBox = document.getElementById("policy-error");
  const okBox = document.getElementById("policy-success");
  const saveBtn = document.getElementById("policy-save-btn");
  let items = [];

  function msg(body, fallback) {
    return body && body.error && body.error.message ? body.error.message : fallback;
  }

  function render() {
    const groups = {};
    items.forEach(function (p) { (groups[p.group] = groups[p.group] || []).push(p); });
    holder.innerHTML = Object.keys(groups).map(function (g) {
      return "<h3>" + esc(g) + "</h3>" + groups[g].map(function (p) {
        const id = "policy-" + p.key;
        return '<div class="form-field"><label for="' + id + '">' + esc(p.label) + "</label>" +
          '<input id="' + id + '" data-key="' + esc(p.key) + '" class="text-input small-input" type="number" min="' + p.min + '" max="' + p.max + '" step="' + (p.type === "int" ? "1" : "0.01") + '"' +
          (p.overridden ? ' value="' + esc(String(p.value)) + '"' : "") + ' placeholder="' + esc(String(p.default)) + ' (default)" />' +
          '<small class="panel-hint">' + esc(p.help) + "</small></div>";
      }).join("");
    }).join("");
  }

  async function load() {
    const r = await VA.fetchJson("/api/v1/policy");
    if (!r.ok) {
      document.getElementById("policy-panel").hidden = true;   // not an administrator
      return;
    }
    items = r.body;
    render();
  }

  saveBtn.addEventListener("click", async function () {
    errBox.hidden = true;
    okBox.hidden = true;
    const values = {};
    holder.querySelectorAll("input[data-key]").forEach(function (input) {
      const p = items.find(function (x) { return x.key === input.getAttribute("data-key"); });
      const raw = input.value.trim();
      if (raw === "") {
        if (p.overridden) values[p.key] = null;            // back to default
      } else if (Number(raw) !== Number(p.value) || !p.overridden) {
        values[p.key] = Number(raw);
      }
    });
    if (!Object.keys(values).length) {
      okBox.textContent = "Nothing changed.";
      okBox.hidden = false;
      return;
    }
    if (!window.confirm("Save " + Object.keys(values).length + " policy change(s)? They apply to everyone straight away.")) return;
    const r = await VA.fetchJson("/api/v1/policy", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values: values }),
    });
    if (!r.ok) {
      errBox.textContent = msg(r.body, "Could not save the policy.");
      errBox.hidden = false;
      return;
    }
    items = r.body;
    render();
    okBox.textContent = "Policy saved.";
    okBox.hidden = false;
  });

  document.addEventListener("vistadmin:ready", load);
})();