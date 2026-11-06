(function () {
  "use strict";

  // Attendance -> Teachers: the day's classes with check-ins (missed / needs cover), punctuality, and class covers.
  const VA = window.VistAdmin, esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/teaching";
  const panel = document.querySelector('[data-tab-panel="teachers"]');
  if (!panel) return;
  let loaded = false, current = null, picked = null;

  function iso(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
  function hm(t) { return t ? String(t).slice(0, 5) : ""; }
  function money(v) { return VA.formatMoney ? VA.formatMoney(v) : "Rs. " + Number(v || 0).toLocaleString(); }
  function msg(body, fallback) { return (body && body.error && body.error.message) || fallback; }
  function shortDate(s) { const d = new Date(s + "T00:00:00"); return d.toLocaleDateString(undefined, { day: "2-digit", month: "short" }); }

  const STATE = {
    ON_TIME: ["On time", "tone-green"], LATE: ["After the window", "tone-orange"], MISSED: ["Not taken", "tone-red"],
    OPEN: ["Marking open", "tone-teal"], UPCOMING: ["Not started", "tone-grey"],
  };
  function statePill(r) {
    const s = STATE[r.state] || [r.state, "tone-grey"];
    let extra = "";
    if (r.taken_at) extra = '<small class="list-sub">at ' + esc(String(r.taken_at).slice(11, 16)) + " (" + r.minutes_after_start + " min after start)</small>";
    if (r.late_note) extra += '<small class="list-sub">' + esc(r.late_note) + "</small>";
    return '<span class="sec-pill ' + s[1] + '">' + esc(s[0]) + "</span>" + extra;
  }

  // ------------------------------------------------------------------ the day
  async function loadDay() {
    const day = $("tc-day").value;
    $("tc-tbody").innerHTML = VA.loadingRowHtml ? VA.loadingRowHtml(6) : "";
    const r = await VA.fetchJson(API + "/day?on=" + encodeURIComponent(day));
    if (!r.ok) { $("tc-tbody").innerHTML = ""; return VA.toast(msg(r.body, "Could not load the classes."), "error"); }
    const d = r.body, c = d.counts;
    const chips = [["Classes", c.total, ""], ["On time", c.on_time, "tone-green"], ["After the window", c.late, "tone-orange"], ["Not taken", c.missed, "tone-red"],
      ["Checked in but not taken", c.missed_while_in, "tone-red"], ["Need cover", c.needs_cover, "tone-orange"]];
    $("tc-chips").innerHTML = chips.map(function (x) { return '<span class="tc-chip ' + x[2] + '"><b>' + x[1] + "</b> " + esc(x[0]) + "</span>"; }).join("");
    const cnt = $("att-teach-count"); if (cnt && day === iso(new Date())) cnt.textContent = (c.missed_while_in + c.needs_cover) > 0 ? String(c.missed_while_in + c.needs_cover) : "";
    $("tc-absent").hidden = d.absent_teachers.length === 0;
    $("tc-absent").innerHTML = d.absent_teachers.length ? "<b>Not on campus with classes still to teach:</b> " + d.absent_teachers.map(function (a) {
      return esc(a.name) + " (" + esc(a.presence_label) + ", " + a.classes + " class" + (a.classes === 1 ? "" : "es") + ")"; }).join("; ") : "";
    $("tc-empty").hidden = d.classes.length > 0;
    $("tc-table").hidden = d.classes.length === 0;
    const focus = new URLSearchParams(location.search).get("class");
    $("tc-tbody").innerHTML = d.classes.map(function (r) {
      const cls = r.missed_while_in ? "tc-row-red" : r.needs_cover ? "tc-row-amber" : "";
      const cover = r.cover ? "<b>" + esc(r.cover.substitute_name || "") + '</b><small class="list-sub">' + money(r.cover.amount) + "</small>" : '<span class="sd-dim">—</span>';
      const canCover = !r.taken_at && (r.needs_cover || r.cover || r.state === "UPCOMING" || r.state === "OPEN");
      const rec = !r.taken_at && r.state === "MISSED" && window.VistOpenForMarking
        ? '<button type="button" class="link-btn" data-record="' + r.class_session_id + '" data-offering="' + r.course_offering_id + '">Record attendance</button>' : "";
      const btn = rec + (canCover ? '<button type="button" class="role-btn small-btn" data-cover="' + r.class_session_id + '">' + (r.cover ? "Change cover" : "Find cover") + "</button>" : "");
      return '<tr class="' + cls + (focus === r.class_session_id ? " tc-focus" : "") + '"><td>' + hm(r.start_time) + "–" + hm(r.end_time) + '<small class="list-sub">' + esc(r.room) + "</small></td>" +
        '<td><span class="crs-code">' + esc(r.course_code) + "</span> " + esc(r.course_title) + '<small class="list-sub">Section ' + esc(r.section) + " · " + esc(r.term_label) + "</small></td>" +
        "<td>" + (r.teacher_name ? esc(r.teacher_name) : '<span class="sd-dim">No teacher</span>') + '<small class="list-sub">' + esc(r.presence_label) + "</small>" +
        (r.missed_while_in ? '<small class="tc-flag">On campus, class not taken</small>' : "") + "</td>" +
        "<td>" + statePill(r) + "</td><td>" + cover + "</td><td>" + btn + "</td></tr>";
    }).join("");
    const f = $("tc-tbody").querySelector(".tc-focus"); if (f) f.scrollIntoView({ block: "center" });
  }

  function shiftDay(n) { const d = new Date($("tc-day").value + "T00:00:00"); d.setDate(d.getDate() + n); $("tc-day").value = iso(d); loadDay(); }

  // ------------------------------------------------------------------ punctuality + covers
  function rangeQs() { return "first=" + encodeURIComponent($("tc-from").value) + "&last=" + encodeURIComponent($("tc-to").value); }

  async function loadPunct() {
    $("tc-csv").href = API + "/punctuality.csv?" + rangeQs();
    const r = await VA.fetchJson(API + "/punctuality?" + rangeQs());
    if (!r.ok) return VA.toast(msg(r.body, "Could not load the report."), "error");
    const t = r.body.totals;
    $("tc-totals").textContent = t.classes_due ? (t.classes_due + " classes due: " + t.on_time + " on time, " + t.late + " late, " + t.missed + " never taken" +
      (t.on_time_percent != null ? " (" + t.on_time_percent + "% on time)." : ".")) + " Marking window: " + r.body.window_minutes + " minutes." : "";
    $("tc-punct-empty").hidden = r.body.teachers.length > 0;
    $("tc-punct-tbody").innerHTML = r.body.teachers.map(function (x) {
      const tone = x.verdict === "On time" ? "tone-green" : x.verdict === "Sometimes late" ? "tone-orange" : "tone-red";
      return '<tr class="tc-click" data-teacher="' + x.employee_id + '" tabindex="0"><td><b>' + esc(x.name) + '</b><small class="list-sub">' + esc(x.code) +
        (x.covered ? " · " + x.covered + " covered" : "") + '</small></td><td class="num">' + x.classes_due + '</td><td class="num">' + x.on_time + '</td><td class="num">' + x.late +
        '</td><td class="num">' + x.missed + '</td><td class="num">' + (x.on_time_percent == null ? "—" : x.on_time_percent + "%") + '</td><td class="num">' +
        (x.average_minutes_after_start == null ? "—" : x.average_minutes_after_start + " min") + '</td><td><span class="sec-pill ' + tone + '">' + esc(x.verdict) + "</span></td></tr>";
    }).join("");
  }

  async function teacherDetail(row) {
    const next = row.nextElementSibling;
    if (next && next.classList.contains("tc-detail")) { next.remove(); return; }
    const r = await VA.fetchJson(API + "/teachers/" + row.getAttribute("data-teacher") + "/classes?" + rangeQs());
    if (!r.ok) return VA.toast(msg(r.body, "Could not load the classes."), "error");
    const tr = document.createElement("tr");
    tr.className = "tc-detail";
    tr.innerHTML = '<td colspan="8"><div class="table-scroll"><table class="dash-table"><thead><tr><th>Date</th><th>Class</th><th>Check-in</th><th>Attendance</th></tr></thead><tbody>' +
      (r.body.map(function (c) {
        return "<tr><td>" + esc(shortDate(c.date)) + " " + hm(c.start_time) + "</td><td>" + esc(c.course_code) + " (" + esc(c.section) + ")" + (c.covering ? ' <small class="list-sub">covering</small>' : "") +
          "</td><td>" + esc(c.presence_label) + "</td><td>" + statePill(c) + "</td></tr>";
      }).join("") || '<tr><td colspan="4" class="sd-dim">No classes.</td></tr>') + "</tbody></table></div></td>";
    row.after(tr);
  }

  async function loadCovers() {
    // the report's period, plus the coming month (covers are usually given for today or the next days)
    const ahead = new Date(); ahead.setDate(ahead.getDate() + 30);
    const to = $("tc-to").value && $("tc-to").value > iso(ahead) ? $("tc-to").value : iso(ahead);
    const r = await VA.fetchJson(API + "/covers?first=" + encodeURIComponent($("tc-from").value) + "&last=" + encodeURIComponent(to));
    if (!r.ok) return;
    $("tc-covers-empty").hidden = r.body.length > 0;
    $("tc-covers-table").hidden = r.body.length === 0;
    $("tc-covers-tbody").innerHTML = r.body.map(function (c) {
      const tone = c.status === "Taken" ? "tone-green" : c.status === "Waiting" ? "tone-teal" : "tone-red";
      const record = c.active && c.status === "Not taken" && window.VistOpenForMarking
        ? '<button type="button" class="role-btn small-btn" data-record="' + c.class_session_id + '" data-offering="' + c.course_offering_id + '">Record attendance</button> ' : "";
      const acts = record + (c.active && !c.payroll ? '<button type="button" class="link-btn" data-amount="' + c.id + '" data-value="' + c.amount + '">Change pay</button> ' +
        '<button type="button" class="link-btn" data-cancel="' + c.id + '">Take back</button>' : "");
      return "<tr><td>" + esc(shortDate(c.date)) + "</td><td>" + esc(c.description) + "</td><td>" + esc(c.substitute_name || "") + '</td><td class="num">' + money(c.amount) +
        (c.amount_note ? '<small class="list-sub">' + esc(c.amount_note) + "</small>" : "") + '</td><td><span class="sec-pill ' + tone + '">' + esc(c.status) + "</span>" +
        (c.active && c.status === "Not taken" ? '<small class="list-sub">window closed: record its attendance to pay the cover</small>' : c.active && c.status !== "Taken" ? '<small class="list-sub">paid only once the class is held</small>' : "") + "</td><td>" + (c.payroll ? esc(c.payroll) : '<span class="sd-dim">—</span>') +
        "</td><td>" + acts + "</td></tr>";
    }).join("");
  }

  // ------------------------------------------------------------------ the cover dialog
  async function openCover(id) {
    const r = await VA.fetchJson(API + "/classes/" + id + "/cover-options");
    if (!r.ok) return VA.toast(msg(r.body, "Could not load who is free."), "error");
    current = r.body; picked = current.cover ? current.cover.substitute_id : null;
    const k = current.class;
    $("tc-d-class").textContent = k.course_code + " (" + k.section + ") " + k.course_title + " · " + shortDate(k.date) + " " + hm(k.start_time) + "–" + hm(k.end_time) +
      " · room " + k.room + (k.teacher_name ? " · teacher: " + k.teacher_name : "");
    $("tc-d-none").hidden = current.people.length > 0;
    $("tc-d-people").innerHTML = current.people.map(function (p) {
      return '<tr><td><input type="radio" name="tc-pick" value="' + p.employee_id + '" aria-label="' + esc(p.name) + '"' + (picked === p.employee_id ? " checked" : "") + "></td><td><b>" +
        esc(p.name) + "</b>" + (p.recommended ? ' <span class="sec-pill tone-green">Recommended</span>' : "") + '<small class="list-sub">' + esc(p.job) + (p.department ? " · " + esc(p.department) : "") +
        "</small></td><td>" + esc(p.reasons.join(", ")) + "</td></tr>";
    }).join("");
    $("tc-d-amount").value = current.cover ? current.cover.amount : current.suggested_amount;
    $("tc-d-note").textContent = "Suggested " + money(current.suggested_amount) + ": " + current.amount_note + ".";
    $("tc-d-error").hidden = true;
    $("tc-dialog").showModal();
  }

  async function saveCover() {
    const sel = $("tc-d-people").querySelector('input[name="tc-pick"]:checked');
    const err = $("tc-d-error");
    if (!sel) { err.textContent = "Choose who will take the class."; err.hidden = false; return; }
    const amount = $("tc-d-amount").value.trim();
    const body = { substitute_id: sel.value };
    if (amount !== "" && Number(amount) !== Number(current.suggested_amount)) body.amount = Number(amount);
    $("tc-d-save").disabled = true;
    const r = await VA.fetchJson(API + "/classes/" + current.class.class_session_id + "/cover", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    $("tc-d-save").disabled = false;
    if (!r.ok) { err.textContent = msg(r.body, "Could not give this class."); err.hidden = false; return; }
    $("tc-dialog").close();
    VA.toast(r.body.substitute_name + " will take " + current.class.course_code + ". They have been notified.");
    loadDay(); loadCovers();
  }

  panel.addEventListener("click", async function (e) {
    const cov = e.target.closest("[data-cover]"), amt = e.target.closest("[data-amount]"), can = e.target.closest("[data-cancel]"), row = e.target.closest("[data-teacher]");
    const rec = e.target.closest("[data-record]");
    if (rec) {
      // a class whose marking window has closed: the office records its attendance (with a note) in "Mark attendance";
      // once recorded, a cover on it shows "Taken" and is paid
      return window.VistOpenForMarking(rec.getAttribute("data-offering"), rec.getAttribute("data-record"));
    }
    if (cov) return openCover(cov.getAttribute("data-cover"));
    if (amt) {
      const v = await VA.ask({ title: "Change cover pay", label: "Cover pay (Rs.)", input: "number", value: amt.getAttribute("data-value"), ok: "Save" });
      if (v == null) return;
      const r = await VA.fetchJson(API + "/covers/" + amt.getAttribute("data-amount"), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ amount: Number(v) }) });
      if (!r.ok) return VA.toast(msg(r.body, "Could not change it."), "error");
      VA.toast("Cover pay changed."); return loadCovers();
    }
    if (can) {
      const v = await VA.ask({ title: "Take this cover back?", label: "Reason (kept on record)", ok: "Take back", danger: true, required: false, min: 0 });
      if (v == null) return;
      const r = await VA.fetchJson(API + "/covers/" + can.getAttribute("data-cancel") + "/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: v === true ? "" : v }) });
      if (!r.ok && r.status !== 204) return VA.toast(msg(r.body, "Could not take it back."), "error");
      VA.toast("Cover taken back."); loadDay(); return loadCovers();
    }
    if (row && !e.target.closest("a,button")) return teacherDetail(row);
  });
  panel.addEventListener("keydown", function (e) { const row = e.target.closest("[data-teacher]"); if (row && e.key === "Enter") teacherDetail(row); });
  $("tc-d-save").addEventListener("click", saveCover);
  $("tc-d-close").addEventListener("click", function () { $("tc-dialog").close(); });
  $("tc-day").addEventListener("change", loadDay);
  $("tc-prev").addEventListener("click", function () { shiftDay(-1); });
  $("tc-next").addEventListener("click", function () { shiftDay(1); });
  $("tc-today").addEventListener("click", function () { $("tc-day").value = iso(new Date()); loadDay(); });
  $("tc-load").addEventListener("click", function () { loadPunct(); loadCovers(); });

  function loadAll() {
    if (loaded) return;
    loaded = true;
    const qs = new URLSearchParams(location.search), now = new Date();
    $("tc-day").value = qs.get("day") || iso(now);
    $("tc-from").value = iso(new Date(now.getFullYear(), now.getMonth(), 1));
    $("tc-to").value = iso(now);
    loadDay(); loadPunct(); loadCovers();
  }
  const tab = document.querySelector('.portal-tab[data-tab="teachers"]');
  if (tab) tab.addEventListener("click", loadAll);
  document.addEventListener("vistadmin:ready", function () {
    if (!panel.hidden) return loadAll();
    // the count on the tab: classes that need the administration today
    VA.fetchJson(API + "/day").then(function (r) {
      if (r.ok) { const n = r.body.counts.missed_while_in + r.body.counts.needs_cover; const el = $("att-teach-count"); if (el) el.textContent = n > 0 ? String(n) : ""; }
    });
  });
  if (location.hash === "#teachers") setTimeout(loadAll, 0);
})();