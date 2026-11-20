(function () {
  "use strict";
  // University Reg Nos: fill in or correct each student's Reg No, one by one or from a CSV (admin and exam office).
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (b, fb) { return (b && b.error && b.error.message) || fb; };
  let timer = null;

  function filters() {
    const p = new URLSearchParams();
    if ($("rn-program").value) p.set("program_id", $("rn-program").value);
    if ($("rn-semester").value) p.set("semester", $("rn-semester").value);
    if ($("rn-section").value.trim()) p.set("section", $("rn-section").value.trim());
    return p;
  }

  let programsShown = false;
  function fillPrograms(list) {
    if (programsShown) return;
    programsShown = true;
    list.forEach(function (p) {
      const o = document.createElement("option");
      o.value = p.id; o.textContent = p.code + " \u2014 " + p.name;
      $("rn-program").appendChild(o);
    });
  }

  async function load() {
    const p = filters();
    if ($("rn-missing").checked) p.set("missing_only", "true");
    if ($("rn-q").value.trim()) p.set("q", $("rn-q").value.trim());
    $("rn-download").href = "/api/v1/students/reg-numbers/template.csv?" + filters().toString();
    const r = await VA.fetchJson("/api/v1/students/reg-numbers?" + p.toString());
    if (!r.ok) { $("rn-count").textContent = err(r.body, "Could not load the students."); return; }
    const d = r.body;
    fillPrograms(d.programs || []);
    $("rn-count").textContent = d.students.length + " student(s)" + (d.missing ? ", " + d.missing + " without a Reg No" : "") + (d.truncated ? " (first 1000; narrow the filters)" : "") + ".";
    $("rn-tbody").innerHTML = d.students.map(function (s) {
      return '<tr data-id="' + s.id + '"><td>' + esc(s.roll_number) + "</td><td><strong>" + esc(s.name) + "</strong></td><td>" +
        esc([s.program_code, s.current_semester ? "Sem " + s.current_semester : "", s.section_label].filter(Boolean).join(" · ")) + "</td>" +
        '<td><input type="text" class="text-input rn-input" maxlength="40" value="' + esc(s.reg_number || "") + '" aria-label="Reg No of ' + esc(s.name) + '" /></td>' +
        '<td><button type="button" class="row-action rn-save" hidden>Save</button><span class="rn-ok sd-dim"></span></td></tr>';
    }).join("") || '<tr><td colspan="5" class="sd-dim">No students match.</td></tr>';
  }

  $("rn-tbody").addEventListener("input", function (e) {
    if (!e.target.classList.contains("rn-input")) return;
    const tr = e.target.closest("tr");
    tr.querySelector(".rn-save").hidden = false;
    tr.querySelector(".rn-ok").textContent = "";
  });
  $("rn-tbody").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && e.target.classList.contains("rn-input")) { e.preventDefault(); e.target.closest("tr").querySelector(".rn-save").click(); }
  });
  $("rn-tbody").addEventListener("click", async function (e) {
    if (!e.target.classList.contains("rn-save")) return;
    const tr = e.target.closest("tr");
    const value = tr.querySelector(".rn-input").value.trim();
    const r = await VA.fetchJson("/api/v1/students/" + tr.getAttribute("data-id") + "/reg-number", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reg_number: value || null }),
    });
    if (!r.ok) { tr.querySelector(".rn-ok").textContent = err(r.body, "Not saved."); tr.querySelector(".rn-ok").classList.add("bad"); return; }
    tr.querySelector(".rn-input").value = r.body.reg_number || "";
    e.target.hidden = true;
    tr.querySelector(".rn-ok").classList.remove("bad");
    tr.querySelector(".rn-ok").textContent = "Saved";
  });

  async function upload(dry) {
    $("rn-error").hidden = true;
    const f = $("rn-file").files[0];
    if (!f) { $("rn-error").textContent = "Choose the CSV file first."; $("rn-error").hidden = false; return null; }
    const fd = new FormData(); fd.append("file", f);
    const r = await VA.fetchJson("/api/v1/students/reg-numbers/import?dry_run=" + (dry ? "true" : "false"), { method: "POST", body: fd });
    if (!r.ok) { $("rn-error").textContent = err(r.body, "Could not read the file."); $("rn-error").hidden = false; return null; }
    return r.body;
  }

  $("rn-check").addEventListener("click", async function () {
    const d = await upload(true);
    if (!d) return;
    const out = $("rn-result");
    if (d.error_count) {
      out.innerHTML = '<p class="dash-alert error">' + d.error_count + " problem(s); nothing was saved. Fix the file and check it again.</p>" +
        '<table class="dash-table"><thead><tr><th>Line</th><th>Roll no.</th><th>Problem</th></tr></thead><tbody>' +
        d.errors.map(function (x) { return "<tr><td>" + esc(x.line) + "</td><td>" + esc(x.roll_number) + "</td><td>" + esc(x.error) + "</td></tr>"; }).join("") + "</tbody></table>";
      return;
    }
    if (!d.change_count) { out.innerHTML = '<p class="panel-hint">The file matches what is saved: nothing to change.</p>'; return; }
    out.innerHTML = "<p><strong>" + d.change_count + " change(s)</strong> from " + d.rows + " row(s):</p>" +
      '<table class="dash-table"><thead><tr><th>Roll no.</th><th>Student</th><th>Now</th><th>Will be</th></tr></thead><tbody>' +
      d.changes.map(function (c) { return "<tr><td>" + esc(c.roll_number) + "</td><td>" + esc(c.name) + '</td><td class="sd-dim">' + esc(c.old || "—") + "</td><td><strong>" + esc(c.new || "(cleared)") + "</strong></td></tr>"; }).join("") +
      '</tbody></table><div class="att-go"><button type="button" class="role-btn" id="rn-apply">Save these ' + d.change_count + " change(s)</button></div>";
    $("rn-apply").addEventListener("click", async function () {
      this.disabled = true;
      const res = await upload(false);
      if (!res) return;
      if (res.error_count) { out.innerHTML = '<p class="dash-alert error">The file changed meanwhile: check it again.</p>'; return; }
      out.innerHTML = '<p class="panel-hint">' + res.applied + " Reg No(s) saved.</p>";
      VA.toast(res.applied + " Reg No(s) saved.");
      load();
    });
  });

  ["rn-program", "rn-semester", "rn-missing"].forEach(function (id) { $(id).addEventListener("change", load); });
  ["rn-section", "rn-q"].forEach(function (id) { $(id).addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(load, 300); }); });
  load();
})();
