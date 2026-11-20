(function () {
  "use strict";
  // Students -> Import -> Field rules: tick which details must be filled when a student is added or imported.
  const VA = window.VistAdmin;
  const list = document.getElementById("fr-list");
  if (!list) return;
  const msg = document.getElementById("fr-msg");
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  let fields = [];

  function say(text, bad) { msg.textContent = text; msg.hidden = !text; msg.classList.toggle("dash-alert", !!bad); msg.classList.toggle("error", !!bad); }

  function draw() {
    const groups = [];
    fields.forEach(function (f) { if (groups.indexOf(f.group) < 0) groups.push(f.group); });
    list.innerHTML = groups.map(function (g) {
      return '<fieldset class="fr-group"><legend>' + esc(g) + "</legend>" + fields.filter(function (f) { return f.group === g; }).map(function (f) {
        return '<label class="fr-item' + (f.locked ? " locked" : "") + '"><input type="checkbox" data-key="' + esc(f.key) + '"' + (f.required ? " checked" : "") +
          (f.locked ? " disabled" : "") + " /> <span>" + esc(f.label) + (f.locked ? ' <small class="sd-dim">(always)</small>' : "") + "</span>" +
          (f.note ? '<small class="fr-note">' + esc(f.note) + "</small>" : "") + "</label>";
      }).join("") + "</fieldset>";
    }).join("");
    const req = fields.filter(function (f) { return f.required; }).length;
    document.getElementById("fr-summary").textContent = "(" + req + " required, " + (fields.length - req) + " optional)";
  }

  async function load() {
    const r = await VA.fetchJson("/api/v1/students/field-rules");
    if (!r.ok) { list.innerHTML = '<span class="sd-dim">Could not load the rules.</span>'; return; }
    fields = r.body;
    draw();
    if (location.hash === "#field-rules") document.getElementById("field-rules").open = true;
  }

  async function save(rules) {
    say("");
    const r = await VA.fetchJson("/api/v1/students/field-rules", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rules: rules }) });
    if (!r.ok) { say((r.body && r.body.error && r.body.error.message) || "Could not save the rules.", true); return; }
    fields = r.body;
    draw();
    say("Saved. Download the template again so it shows these rules.");
    VA.toast("Field rules saved.");
  }

  document.getElementById("fr-save").addEventListener("click", async function () {
    const rules = {};
    list.querySelectorAll("input[data-key]").forEach(function (i) { if (!i.disabled) rules[i.getAttribute("data-key")] = i.checked; });
    const off = fields.filter(function (f) { return f.key === "guardian_cnic" && f.required; }).length && rules.guardian_cnic === false;
    if (off && !(await VA.confirm("Without the guardian's CNIC a student has no parent portal and is not linked to brothers/sisters. Make it optional anyway?", "Make optional"))) return;
    save(rules);
  });
  document.getElementById("fr-reset").addEventListener("click", function () {
    const rules = {};
    fields.forEach(function (f) { if (!f.locked) rules[f.key] = f.default_required; });
    save(rules);
  });
  load();
})();
