(function () {
  "use strict";
  // Semester rollover wizard (API: /api/v1/rollover/{session}). Every step: preview first, then one all-or-nothing action.
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const post = function (url, body) { return VA.fetchJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }); };
  const day = function (v) { return v ? new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—"; };
  let sid = "";
  const api = function (p) { return "/api/v1/rollover/" + sid + (p || ""); };

  async function loadSessions() {
    const r = await VA.fetchJson("/api/v1/academics/sessions");
    if (!r.ok) { $("ro-status").textContent = err(r, "Could not load the sessions."); return; }
    const list = r.body.sessions || [];
    $("ro-session").innerHTML = list.map(function (s) { return '<option value="' + s.id + '">' + esc(s.name) + " (" + esc(s.when) + ")</option>"; }).join("") ||
      '<option value="">No session yet</option>';
    sid = $("ro-session").value;
    if (!list.length) { $("ro-status").textContent = "Group the batch terms into a session first (Courses → Academic sessions)."; return; }
    refresh();
  }

  async function refresh() {
    if (!sid) return;
    const o = await VA.fetchJson(api(""));
    if (o.ok) $("ro-status").textContent = o.body.session.name + ": " + o.body.terms + " batch term(s), " + o.body.closed + " closed.";
    loadClose(); loadPromote();
    if ($("ro-next").value.trim()) { loadNext(); loadFees(); }
  }

  // ---------------------------------------------------------------- 1. close
  async function loadClose() {
    const r = await VA.fetchJson(api("/close"));
    if (!r.ok) { $("ro-c-body").innerHTML = '<tr><td colspan="6">' + esc(err(r, "Could not load.")) + "</td></tr>"; return; }
    $("ro-c-body").innerHTML = r.body.terms.map(function (t) {
      const open = t.status === "OPEN";
      return "<tr><td>" + (open ? '<input type="checkbox" class="ro-c-pick" value="' + t.term_id + '"' + (t.ready ? " checked" : "") + ' aria-label="Close ' + esc(t.label) + '">' : "") + "</td>" +
        "<td><strong>" + esc(t.label) + "</strong><small>ends " + esc(day(t.end_date)) + "</small></td><td>" + (open ? "Open" : "Closed") + "</td><td>" + t.subjects + "</td><td>" + t.enrollments_open + "</td>" +
        "<td>" + (t.unfinished.length ? '<span class="ro-bad">' + esc(t.unfinished.slice(0, 4).join("; ")) + (t.unfinished.length > 4 ? " …" : "") + "</span>" : (open ? "ready" : "")) + "</td></tr>";
    }).join("") || '<tr><td colspan="6" class="sd-dim">No terms in this session.</td></tr>';
  }
  $("ro-c-all").addEventListener("change", function () { const on = this.checked; document.querySelectorAll(".ro-c-pick").forEach(function (c) { c.checked = on; }); });
  $("ro-c-go").addEventListener("click", async function () {
    const ids = Array.prototype.map.call(document.querySelectorAll(".ro-c-pick:checked"), function (c) { return c.value; });
    if (!ids.length) { $("ro-c-msg").textContent = "Tick the terms to close."; return; }
    if (!window.confirm("Close " + ids.length + " term(s)? Open enrollments are completed and the terms stop taking new records.")) return;
    this.disabled = true;
    const r = await post(api("/close"), { term_ids: ids, reason: $("ro-c-reason").value.trim() || null });
    this.disabled = false;
    $("ro-c-msg").textContent = r.ok ? "Closed: " + r.body.closed.map(function (c) { return c.label; }).join(", ") + "." : err(r, "Not closed.");
    refresh();
  });

  // ---------------------------------------------------------------- 2. promote
  async function loadPromote() {
    const r = await VA.fetchJson(api("/promote"));
    if (!r.ok) { $("ro-p-body").innerHTML = '<p class="panel-hint">' + esc(err(r, "Could not load.")) + "</p>"; return; }
    $("ro-p-body").innerHTML = '<p class="panel-hint">' + r.body.will_promote + " student(s) to promote; " + r.body.carry_over + " with carry-over subjects.</p>" +
      r.body.batches.map(function (b) {
        const head = "<h3>" + esc(b.label) + (b.status === "PROMOTE" ? " → semester " + (b.semester + 1) : "") + " <small class=\"sd-dim\">" + b.students.length + " student(s)</small></h3>";
        if (b.status !== "PROMOTE") return '<div class="ro-batch">' + head + '<p class="panel-hint">' + esc(b.note || "") + "</p></div>";
        return '<div class="ro-batch">' + head + '<details><summary>Students (tick to hold back)</summary><div class="table-scroll"><table class="dash-table"><tbody>' +
          b.students.map(function (s) {
            return '<tr data-id="' + s.id + '"><td><label><input type="checkbox" class="ro-hold"> Hold back</label></td><td><strong>' + esc(s.name) + "</strong><small>" + esc(s.roll_number) + "</small></td>" +
              "<td>" + (s.carry_over.length ? "Carry-over: " + esc(s.carry_over.join(", ")) : "") + '</td><td><input class="text-input ro-hold-reason" maxlength="200" placeholder="Why held back" hidden></td></tr>';
          }).join("") + "</tbody></table></div></details></div>";
      }).join("");
    loadRuns();
  }
  $("ro-p-body").addEventListener("change", function (e) {
    if (!e.target.classList.contains("ro-hold")) return;
    const inp = e.target.closest("tr").querySelector(".ro-hold-reason");
    inp.hidden = !e.target.checked;
    if (e.target.checked) inp.focus();
  });
  $("ro-p-go").addEventListener("click", async function () {
    const hold = [];
    let missing = false;
    document.querySelectorAll(".ro-hold:checked").forEach(function (c) {
      const tr = c.closest("tr"), why = tr.querySelector(".ro-hold-reason").value.trim();
      if (why.length < 3) missing = true;
      hold.push({ student_id: tr.getAttribute("data-id"), reason: why });
    });
    if (missing) { $("ro-p-msg").textContent = "Write why each held-back student stays."; return; }
    if (!window.confirm("Promote everyone in this session except " + hold.length + " held back? You can undo within 7 days.")) return;
    this.disabled = true;
    const r = await post(api("/promote"), { hold: hold });
    this.disabled = false;
    $("ro-p-msg").textContent = r.ok ? r.body.promoted + " promoted, " + r.body.held.length + " held back" + (r.body.fees_charged ? "; " + r.body.fees_charged + " fee(s) charged for their new semester" : "") + "." : err(r, "Not promoted.");
    refresh();
  });
  async function loadRuns() {
    const r = await VA.fetchJson(api(""));
    if (!r.ok) return;
    $("ro-p-runs").innerHTML = r.body.promotions.map(function (p) {
      return '<p class="panel-hint">Promoted ' + p.students + " on " + esc(day(p.created_at)) + (p.held ? " (" + p.held + " held back)" : "") +
        (p.undone_at ? " — undone " + esc(day(p.undone_at)) : (p.can_undo ? ' <button type="button" class="row-action ro-undo" data-id="' + p.id + '">Undo</button> (until ' + esc(day(p.undo_until)) + ")" : "")) + "</p>";
    }).join("");
  }
  $("ro-p-runs").addEventListener("click", async function (e) {
    const b = e.target.closest(".ro-undo");
    if (!b || !window.confirm("Put these students back to their previous semester? Fees already charged are kept.")) return;
    const r = await post("/api/v1/rollover/runs/" + b.getAttribute("data-id") + "/undo");
    $("ro-p-msg").textContent = r.ok ? r.body.put_back + " put back." + (r.body.left_as_is.length ? " Left as they are (changed since): " + r.body.left_as_is.join(", ") + "." : "") + (r.body.note ? " " + r.body.note : "") : err(r, "Not undone.");
    refresh();
  });

  // ---------------------------------------------------------------- 3. next session
  async function loadNext() {
    const name = $("ro-next").value.trim();
    $("ro-n-go").disabled = true;
    if (!name) { $("ro-n-msg").textContent = "Write the next session's name at the top."; return; }
    const r = await VA.fetchJson(api("/next?new_name=" + encodeURIComponent(name)));
    if (!r.ok) { $("ro-n-body").innerHTML = '<tr><td colspan="6">' + esc(err(r, "Could not prepare it.")) + "</td></tr>"; return; }
    $("ro-n-body").innerHTML = r.body.rows.map(function (x) {
      return "<tr><td><strong>" + esc(x.label) + "</strong></td><td>" + esc(x.new_label || "—") + (x.status === "EXISTS" ? "<small>already made</small>" : "") + "</td><td>" + (x.courses ? x.courses.length : 0) +
        "</td><td>" + esc((x.sections || []).join(", ")) + "</td><td>" + (x.promoted == null ? "" : x.promoted) + "</td><td>" + esc(x.note || "") + "</td></tr>";
    }).join("");
    $("ro-n-msg").textContent = r.body.will_create_terms + " term(s) to create, " + r.body.will_open_sections + " subject section(s) to open.";
    $("ro-n-go").disabled = !!r.body.blocked.length;
  }
  $("ro-n-check").addEventListener("click", loadNext);
  $("ro-n-go").addEventListener("click", async function () {
    const body = { new_name: $("ro-next").value.trim(), start_date: $("ro-n-start").value || null, end_date: $("ro-n-end").value || null,
      enroll: $("ro-n-enroll").checked, same_teachers: $("ro-n-teach").checked };
    if (!body.start_date || !body.end_date) { $("ro-n-msg").textContent = "Give the next session's start and end dates."; return; }
    this.disabled = true;
    const r = await post(api("/next"), body);
    $("ro-n-msg").textContent = r.ok ? r.body.session + ": " + r.body.terms_created + " term(s), " + r.body.sections_opened + " section(s), " + r.body.enrolled + " enrollment(s), " + r.body.teachers_set + " teacher(s) kept." : err(r, "Not started.");
    this.disabled = false;
    loadFees();
  });

  // ---------------------------------------------------------------- 4. fees
  async function loadFees() {
    const name = $("ro-next").value.trim();
    $("ro-f-go").disabled = true;
    if (!name) return;
    const r = await VA.fetchJson(api("/fees?new_name=" + encodeURIComponent(name)));
    if (!r.ok) { $("ro-f-body").innerHTML = '<tr><td colspan="5">' + esc(err(r, "Could not prepare it.")) + "</td></tr>"; return; }
    const why = { EXISTS: "already has this fee", NO_TERM: "start the next session first" };
    $("ro-f-body").innerHTML = r.body.structures.map(function (x) {
      return "<tr><td>" + esc(x.name) + '</td><td class="num">' + VA.formatMoney(x.amount) + "</td><td>" + esc(x.from_term) + "</td><td>" + esc(x.to_term) + "</td><td>" + (x.status === "COPY" ? "will copy" : '<span class="sd-dim">' + why[x.status] + "</span>") + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">No active fee structure on these batch terms.</td></tr>';
    $("ro-f-go").disabled = !r.body.will_copy;
  }
  $("ro-f-check").addEventListener("click", loadFees);
  $("ro-f-go").addEventListener("click", async function () {
    const body = { new_name: $("ro-next").value.trim(), percentage_change: Number($("ro-f-pct").value || 0), effective_from: $("ro-f-from").value || null };
    if (!body.effective_from) { $("ro-f-msg").textContent = "Give the first due date."; return; }
    this.disabled = true;
    const r = await post(api("/fees"), body);
    $("ro-f-msg").textContent = r.ok ? r.body.drafts.length + " draft(s) made. Check and activate them in Fees." : err(r, "Not copied.");
    loadFees();
  });

  $("ro-session").addEventListener("change", function () { sid = this.value; refresh(); });
  $("ro-next").addEventListener("change", function () { loadNext(); loadFees(); });
  loadSessions();
})();
