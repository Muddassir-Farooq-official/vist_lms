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
        '<td><button type="button" class="row-action rn-save" hidden>Save</button><span class="rn-ok sd-dim"></span>' +
        (s.reg_number ? "" : ' <button type="button" class="row-action rn-exempt" data-name="' + esc(s.name + " (" + (s.roll_number || "") + ")") + '">Exempt\u2026</button>') + "</td></tr>";
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

  // ---------------------------------------------------------------- deadlines
  function fmtDate(v) { return v ? new Date(v + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "\u2014"; }
  let dlLoaded = false;
  async function loadDeadlines() {
    const r = await VA.fetchJson("/api/v1/students/reg-numbers/deadlines");
    if (!r.ok) { $("rn-dl-msg").textContent = err(r.body, "Could not load the deadlines."); return; }
    const d = r.body;
    const terms = d.terms.filter(function (t) { return t.affiliated !== false; });
    if (!dlLoaded) {
      dlLoaded = true;
      $("rn-dl-term").innerHTML = terms.map(function (t) { return '<option value="' + t.term_id + '">' + esc(t.label) + "</option>"; }).join("") ||
        '<option value="">No open term</option>';
      $("rn-dl-session").innerHTML = d.sessions.map(function (x) { return '<option value="' + x.id + '">' + esc(x.name) + "</option>"; }).join("") ||
        '<option value="">No session</option>';
    }
    if (!d.affiliated_programs) $("rn-dl-msg").textContent = "No program is affiliated (Exam office \u2192 the program's exams are held by a university), so no deadline is needed.";
    const set = terms.filter(function (t) { return t.reg_deadline; });
    $("rn-dl-tbody").innerHTML = set.map(function (t) {
      const left = t.days_left;
      const when = left < 0 ? '<span class="rn-ok bad">passed: admit cards held</span>' : left === 0 ? "today" : left + " day(s) left";
      return "<tr><td><strong>" + esc(t.label) + "</strong></td><td>" + esc(t.session || "\u2014") + "</td><td>" + fmtDate(t.reg_deadline) +
        ' <small class="sd-dim">' + when + "</small></td><td>" + t.students + "</td><td>" + (t.missing ? "<strong>" + t.missing + "</strong>" +
        (t.missing_exempt ? ' <small class="sd-dim">(' + t.missing_exempt + " exempted)</small>" : "") +
        '<br><small class="sd-dim">' + esc(t.missing_roll_numbers.join(", ")) + (t.missing > t.missing_roll_numbers.length ? " \u2026" : "") + "</small>" : "none") + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">No deadline set.</td></tr>';
  }
  $("rn-dl-scope").addEventListener("change", function () {
    const bySession = this.value === "session";
    $("rn-dl-term").hidden = bySession; $("rn-dl-session").hidden = !bySession;
  });
  async function saveDeadline(clear) {
    const body = { reg_deadline: clear ? null : ($("rn-dl-date").value || null) };
    if ($("rn-dl-scope").value === "session") body.session_id = $("rn-dl-session").value || null; else body.term_id = $("rn-dl-term").value || null;
    if (!body.term_id && !body.session_id) { $("rn-dl-msg").textContent = "Choose the term or session first."; return; }
    if (!clear && !body.reg_deadline) { $("rn-dl-msg").textContent = "Choose the last day."; return; }
    const r = await VA.fetchJson("/api/v1/students/reg-numbers/deadlines", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { $("rn-dl-msg").textContent = err(r.body, "Not saved."); return; }
    const c = r.body.changed.length;
    $("rn-dl-msg").textContent = (c ? (clear ? "Removed for " : "Set for ") + r.body.changed.join(", ") + "." : "Nothing changed.") +
      (r.body.skipped.length ? " Skipped (closed, or not an affiliated program): " + r.body.skipped.join(", ") + "." : "");
    loadDeadlines();
  }
  $("rn-dl-save").addEventListener("click", function () { saveDeadline(false); });
  $("rn-dl-clear").addEventListener("click", function () { saveDeadline(true); });

  // ---------------------------------------------------------------- exemptions
  async function loadExemptions() {
    const r = await VA.fetchJson("/api/v1/students/reg-numbers/exemptions" + ($("rn-ex-old").checked ? "?include_old=true" : ""));
    if (!r.ok) { $("rn-ex-tbody").innerHTML = '<tr><td colspan="5" class="sd-dim">' + esc(err(r.body, "Could not load.")) + "</td></tr>"; return; }
    $("rn-ex-tbody").innerHTML = r.body.map(function (e) {
      return "<tr><td><strong>" + esc(e.name) + '</strong><small class="sd-dim">' + esc(e.roll_number || "") + "</small></td><td>" + esc(e.reason) + "</td><td>" +
        (e.until ? fmtDate(e.until) : "until revoked") + "</td><td>" + esc(e.created_by || "\u2014") + "</td><td>" +
        (e.active ? '<button type="button" class="row-action rn-revoke" data-id="' + e.id + '">Revoke</button>'
                  : '<span class="sd-dim">' + (e.revoked_at ? "revoked" + (e.revoked_by ? " by " + esc(e.revoked_by) : "") : "expired") + "</span>") + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">None.</td></tr>';
  }
  $("rn-ex-old").addEventListener("change", loadExemptions);
  $("rn-ex-tbody").addEventListener("click", async function (e) {
    const b = e.target.closest(".rn-revoke");
    if (!b) return;
    if (!window.confirm("Revoke this exemption? The admit card is held again while the Reg No is missing after the deadline.")) return;
    b.disabled = true;
    const r = await VA.fetchJson("/api/v1/students/reg-numbers/exemptions/" + b.getAttribute("data-id") + "/revoke", { method: "POST" });
    if (!r.ok) { VA.toast(err(r.body, "Not revoked."), "error"); b.disabled = false; return; }
    VA.toast("Revoked.");
    loadExemptions(); loadDeadlines();
  });
  let exStudent = null;
  $("rn-tbody").addEventListener("click", function (e) {
    const b = e.target.closest(".rn-exempt");
    if (!b) return;
    exStudent = b.closest("tr").getAttribute("data-id");
    $("rn-ex-who").textContent = b.getAttribute("data-name");
    $("rn-ex-reason").value = ""; $("rn-ex-until").value = ""; $("rn-ex-error").hidden = true;
    $("rn-ex-modal").showModal();
    $("rn-ex-reason").focus();
  });
  $("rn-ex-cancel").addEventListener("click", function () { $("rn-ex-modal").close(); });
  $("rn-ex-save").addEventListener("click", async function () {
    const body = { student_id: exStudent, reason: $("rn-ex-reason").value.trim(), until: $("rn-ex-until").value || null };
    const r = await VA.fetchJson("/api/v1/students/reg-numbers/exemptions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { $("rn-ex-error").textContent = err(r.body, "Not saved."); $("rn-ex-error").hidden = false; return; }
    $("rn-ex-modal").close();
    VA.toast("Exempted: the admit card can be printed.");
    loadExemptions(); loadDeadlines();
  });

  ["rn-program", "rn-semester", "rn-missing"].forEach(function (id) { $(id).addEventListener("change", load); });
  ["rn-section", "rn-q"].forEach(function (id) { $(id).addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(load, 300); }); });
  load();
  loadDeadlines();
  loadExemptions();
})();
