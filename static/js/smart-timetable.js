// Admin > Smart timetable (app/timetabling): generate a draft for whole terms, check and adjust it, publish it;
// per-section classes a week / labs / home room; teachers' unavailable times.
(function () {
  "use strict";

  const VA = window.VistAdmin;
  const API = "/api/v1/academics/timetable/smart";
  const DAY = { MONDAY: "Mon", TUESDAY: "Tue", WEDNESDAY: "Wed", THURSDAY: "Thu", FRIDAY: "Fri", SATURDAY: "Sat", SUNDAY: "Sun" };
  const DAYFULL = { MONDAY: "Monday", TUESDAY: "Tuesday", WEDNESDAY: "Wednesday", THURSDAY: "Thursday", FRIDAY: "Friday", SATURDAY: "Saturday", SUNDAY: "Sunday" };
  const $ = function (id) { return document.getElementById(id); };
  function esc(v) { return VA.escapeHtml(v == null ? "" : String(v)); }
  function msg(r, fb) { return (r && r.body && r.body.error && r.body.error.message) || fb; }
  function checked(box) { return Array.prototype.map.call(box.querySelectorAll("input:checked"), function (i) { return i.value; }); }
  function json(method, body) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }; }

  let draft = null, nextMonday = null, terms = [];

  // ================================================================== terms
  async function loadTerms() {
    const r = await VA.fetchJson("/api/v1/academics/terms");
    terms = r.ok ? r.body : [];
    ["st-terms", "ss-terms"].forEach(function (id) {
      const box = $(id);
      box.innerHTML = terms.length ? terms.map(function (t, i) {
        return '<label><input type="checkbox" value="' + esc(t) + '"' + (i === 0 ? " checked" : "") + " /> " + esc(t) + "</label>";
      }).join("") : '<span class="sd-dim">No terms yet. Add course sections for a term first.</span>';
    });
  }

  // ================================================================== draft
  function showError(text) { $("st-error").textContent = text || ""; $("st-error").hidden = !text; }

  async function generate(keepPinned) {
    const t = checked($("st-terms")), days = checked($("st-days"));
    if (!t.length) { showError("Tick at least one term."); return; }
    if (!days.length) { showError("Tick at least one day."); return; }
    showError("");
    const btns = [$("st-generate"), $("st-regenerate")];
    btns.forEach(function (b) { b.disabled = true; });
    $("st-gen-note").textContent = "Working… this can take up to about 20 seconds.";
    const num = function (id) { const v = $(id).value.trim(); return v ? parseInt(v, 10) : null; };
    const body = { terms: t, days: days, max_per_day: num("st-maxday"), max_in_row: num("st-maxrow") };
    if (keepPinned && draft && draft.status === "DRAFT") body.keep_pinned_from = draft.id;
    const r = await VA.fetchJson(API + "/drafts", json("POST", body));
    btns.forEach(function (b) { b.disabled = false; });
    $("st-gen-note").textContent = "Takes up to about 20 seconds.";
    if (!r.ok) { showError(msg(r, "Could not generate the draft.")); return; }
    draw(r.body);
    $("st-draft").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function draw(d) {
    draft = d;
    $("st-draft").hidden = false;
    const live = d.status === "DRAFT";
    $("st-draft-title").textContent = live ? "2. Check the draft" : "Published " + (d.published_from ? "from " + VA.formatDateLong(d.published_from) : "");
    $("st-draft-actions").hidden = !live;
    const s = d.stats || {};
    const missing = (d.problems || []).filter(function (p) { return !p.warning; }).reduce(function (a, p) { return a + (p.missing || 0); }, 0);
    $("st-summary").innerHTML =
      '<div class="pt-chip"><strong>' + esc(d.terms.join(", ")) + "</strong> · " + esc(d.days.map(function (x) { return DAY[x]; }).join(" ")) + "</div>" +
      '<div class="pt-chip">' + (s.classes_placed || 0) + " of " + (s.classes_needed || 0) + " weekly classes placed · " + (s.sections || 0) + " sections</div>" +
      '<div class="pt-chip">' + (missing ? '<span class="pt-bad">' + missing + " class" + (missing === 1 ? "" : "es") + " could not be placed</span>" : '<span class="pt-good">Everything placed</span>') + "</div>";
    const probs = d.problems || [];
    $("st-problems").innerHTML = probs.length ? '<h3 class="tt-sub">Needs your attention</h3><table class="dash-table"><thead><tr><th>Course</th><th>Not placed</th><th>Why</th><th></th></tr></thead><tbody>' +
      probs.map(function (p) {
        const canPlace = live && !p.warning && p.missing > 0 && d.courses.some(function (c) { return c.offering_id === p.offering_id; });
        return "<tr><td>" + esc(p.label) + "</td><td>" + (p.warning ? '<span class="sd-dim">note</span>' : p.missing) + "</td><td>" + esc(p.reason) + "</td><td>" +
          (canPlace ? '<button type="button" class="link-btn" data-place="' + esc(p.offering_id) + '">Place by hand…</button>' : "") + "</td></tr>";
      }).join("") + "</tbody></table>" : "";
    fillViewOf();
    drawGrid();
  }

  function fillViewOf() {
    const kind = $("st-view-kind").value, sel = $("st-view-of"), keep = sel.value;
    let opts = [];
    if (kind === "class") opts = draft.classes;
    if (kind === "teacher") opts = draft.teachers;
    if (kind === "room") opts = draft.rooms.map(function (r) { return r.name; });
    sel.innerHTML = opts.map(function (o) { return '<option value="' + esc(o) + '">' + esc(o) + "</option>"; }).join("") || '<option value="">(none)</option>';
    if (opts.indexOf(keep) >= 0) sel.value = keep;
  }

  function matches(s) {
    const kind = $("st-view-kind").value, of = $("st-view-of").value;
    if (kind === "class") return s.classes.indexOf(of) >= 0;
    if (kind === "teacher") return s.teacher === of;
    return (s.room || "").replace(/\s+/g, "").toLowerCase() === of.replace(/\s+/g, "").toLowerCase();
  }

  function drawGrid() {
    const d = draft, grid = $("stt-grid");
    const cells = {};
    d.slots.filter(matches).forEach(function (s) {
      s.periods.forEach(function (pid, i) { (cells[s.day + "|" + pid] = cells[s.day + "|" + pid] || []).push({ s: s, first: i === 0 }); });
    });
    let html = '<thead><tr><th class="pt-when">Period</th>' + d.days.map(function (x) { return "<th>" + esc(DAYFULL[x]) + "</th>"; }).join("") + "</tr></thead><tbody>";
    d.periods.forEach(function (p) {
      html += '<tr><td class="pt-when"><strong>P' + esc(p.number) + '</strong><br><span class="sd-dim">' + esc(p.start) + "–" + esc(p.end) + "</span></td>";
      d.days.forEach(function (day) {
        if (p.days.indexOf(day) < 0) { html += '<td class="pt-na">—</td>'; return; }
        const items = cells[day + "|" + p.id] || [];
        html += '<td class="' + (items.length > 1 ? "pt-clash" : "") + '">' + items.map(function (x) {
          const s = x.s;
          return '<button type="button" class="stt-cls' + (s.pinned ? " stt-pinned" : "") + '" data-slot="' + esc(s.id) + '"' + (d.status === "DRAFT" ? "" : " disabled") + ">" +
            "<strong>" + esc(s.code) + " " + esc(s.section) + "</strong>" + (s.span > 1 ? ' <span class="pt-comb">' + (x.first ? "lab ⇣" : "lab") + "</span>" : "") +
            (s.combined.length ? ' <span class="pt-comb" title="' + esc(s.combined.join(", ")) + '">combined</span>' : "") +
            "<small>" + esc(s.teacher || "No teacher") + " · " + esc(s.room) + "</small>" +
            ($("st-view-kind").value !== "class" ? "<small>" + esc(s.classes.join("; ")) + "</small>" : "") + "</button>";
        }).join("") + "</td>";
      });
      html += "</tr>";
    });
    grid.innerHTML = html + "</tbody>";
  }

  // ------------------------------------------------------------------ move / place by hand
  let editing = null;      // {slot} or {offering_id}
  function openPlace(target) {
    editing = target;
    const d = draft;
    const s = target.slot;
    const course = d.courses.find(function (c) { return c.offering_id === (s ? s.offering_id : target.offering_id); });
    $("st-move-title").textContent = s ? "Move class" : "Place a class by hand";
    $("st-move-what").textContent = course ? course.code + " " + course.section + " · " + course.title + " · " + (course.teacher || "No teacher") +
      " · " + course.have + " of " + course.per_week + " a week placed" : "";
    $("st-move-day").innerHTML = d.days.map(function (x) { return '<option value="' + x + '">' + DAYFULL[x] + "</option>"; }).join("");
    $("st-move-room").innerHTML = d.rooms.map(function (r) {
      return '<option value="' + esc(r.name) + '">' + esc(r.name) + (r.seats ? " (" + r.seats + " seats)" : "") + (r.kind === "LAB" ? " · lab" : "") + "</option>";
    }).join("");
    if (s) { $("st-move-day").value = s.day; $("st-move-room").value = s.room; }
    fillPeriods(s ? s.period_id : null);
    $("st-move-remove").hidden = !s;
    $("st-move-ok").textContent = s ? "Move" : "Place";
    $("st-move-err").hidden = true;
    $("st-move").showModal();
  }
  function fillPeriods(keep) {
    const day = $("st-move-day").value;
    $("st-move-period").innerHTML = draft.periods.filter(function (p) { return p.days.indexOf(day) >= 0; }).map(function (p) {
      return '<option value="' + p.id + '">P' + esc(p.number) + " · " + esc(p.start) + "–" + esc(p.end) + "</option>";
    }).join("");
    if (keep) $("st-move-period").value = keep;
  }
  $("st-move-day").addEventListener("change", function () { fillPeriods($("st-move-period").value); });
  $("st-move-cancel").addEventListener("click", function () { $("st-move").close(); });
  $("st-move-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const body = { day: $("st-move-day").value, period_id: $("st-move-period").value, room: $("st-move-room").value };
    let r;
    if (editing.slot) r = await VA.fetchJson(API + "/drafts/" + draft.id + "/slots/" + editing.slot.id, json("PATCH", body));
    else { body.offering_id = editing.offering_id; r = await VA.fetchJson(API + "/drafts/" + draft.id + "/slots", json("POST", body)); }
    if (!r.ok) { $("st-move-err").textContent = msg(r, "Could not do that."); $("st-move-err").hidden = false; return; }
    $("st-move").close();
    draw(r.body);
    VA.toast(editing.slot ? "Moved and pinned." : "Placed and pinned.");
  });
  $("st-move-remove").addEventListener("click", async function () {
    const r = await VA.fetchJson(API + "/drafts/" + draft.id + "/slots/" + editing.slot.id, { method: "DELETE" });
    if (!r.ok) { $("st-move-err").textContent = msg(r, "Could not remove it."); $("st-move-err").hidden = false; return; }
    $("st-move").close();
    draw(r.body);
  });
  $("stt-grid").addEventListener("click", function (e) {
    const b = e.target.closest("[data-slot]");
    if (!b || !draft || draft.status !== "DRAFT") return;
    const s = draft.slots.find(function (x) { return x.id === b.dataset.slot; });
    if (s) openPlace({ slot: s });
  });
  $("st-problems").addEventListener("click", function (e) {
    const b = e.target.closest("[data-place]");
    if (b) openPlace({ offering_id: b.dataset.place });
  });

  // ------------------------------------------------------------------ publish / discard
  async function preview() {
    const day = $("st-pub-date").value;
    const box = $("st-pub-preview");
    if (!day) { box.textContent = ""; return; }
    box.textContent = "Checking…";
    const r = await VA.fetchJson(API + "/drafts/" + draft.id + "/publish-preview?from_date=" + encodeURIComponent(day));
    if (!r.ok) { box.textContent = msg(r, "Could not check."); return; }
    const p = r.body;
    box.innerHTML = "<p>From <strong>" + esc(VA.formatDateLong(p.from_date)) + "</strong> the draft's <strong>" + p.draft_classes + "</strong> weekly classes replace the current timetable of these courses (" +
      p.live_slots + " weekly slots now, " + p.draft_slots + " after; slots count each section of a combined class and each period of a lab).</p><p>Classes before that date, and all attendance already taken" +
      (p.classes_held_before ? " (" + p.classes_held_before + " classes held)" : "") + ", stay exactly as they are. " +
      p.upcoming_classes_replaced + " upcoming class(es) come off the timetable" +
      (p.upcoming_with_work_kept ? " (" + p.upcoming_with_work_kept + " already have a topic or files, so they are kept as cancelled)" : "") + ".</p>" +
      (p.unplaced ? '<p class="pt-bad">' + p.unplaced + " class(es) are still not placed; they will be missing from the timetable.</p>" : "");
  }
  $("st-publish").addEventListener("click", function () {
    $("st-pub-date").value = nextMonday || "";
    $("st-pub-err").hidden = true;
    $("st-pub").showModal();
    preview();
  });
  $("st-pub-date").addEventListener("change", preview);
  $("st-pub-cancel").addEventListener("click", function () { $("st-pub").close(); });
  $("st-pub-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const ok = $("st-pub-ok");
    ok.disabled = true; ok.textContent = "Publishing…";
    const notify = checked(document.querySelector("#st-pub .stt-notify"));
    const r = await VA.fetchJson(API + "/drafts/" + draft.id + "/publish", json("POST", { from_date: $("st-pub-date").value, notify: notify }));
    ok.disabled = false; ok.textContent = "Publish";
    if (!r.ok) { $("st-pub-err").textContent = msg(r, "Could not publish."); $("st-pub-err").hidden = false; return; }
    $("st-pub").close();
    if (r.body.pending_approval) { VA.toast(r.body.message || "Sent for approval.", "success"); return; }
    const t = r.body.told || {};
    VA.toast("Published. " + r.body.class_sessions_created + " classes created" + (Object.keys(t).length ? "; told " + Object.keys(t).map(function (k) { return t[k] + " " + k; }).join(", ") : "") + ".");
    const v = await VA.fetchJson(API + "/drafts/" + draft.id);
    if (v.ok) draw(v.body);
  });
  $("st-discard").addEventListener("click", async function () {
    const ok = await VA.ask({ title: "Discard this draft?", intro: "The live timetable is not touched.", ok: "Discard", input: "none" });
    if (!ok) return;
    const r = await VA.fetchJson(API + "/drafts/" + draft.id + "/discard", { method: "POST" });
    if (!r.ok) { VA.toast(msg(r, "Could not discard."), "error"); return; }
    draft = null;
    $("st-draft").hidden = true;
  });
  $("st-generate").addEventListener("click", function () { generate(false); });
  $("st-regenerate").addEventListener("click", function () { generate(true); });
  $("st-view-kind").addEventListener("change", function () { fillViewOf(); drawGrid(); });
  $("st-view-of").addEventListener("change", drawGrid);

  // ================================================================== sections
  let rooms = [];
  // Labs a week: none, some (that many of the classes are double periods, the rest single theory periods) or all
  function labsSelect(s) {
    const cat = s.lab_credit_hours || 0;
    const cur = s.double_period ? "all" : s.lab_classes_per_week === null || s.lab_classes_per_week === undefined ? "" : String(s.lab_classes_per_week);
    const opts = [["", "As catalog (" + (cat ? cat + (cat === 1 ? " lab" : " labs") : "none") + ")"], ["0", "None"]];
    for (let i = 1; i <= 6; i++) opts.push([String(i), i + (i === 1 ? " lab" : " labs")]);
    opts.push(["all", "All (every class)"]);
    return '<select class="select-input stt-labs" data-f="labs" aria-label="Labs a week (double periods)" title="How many of the weekly classes are labs: two back-to-back periods in a lab">' +
      opts.map(function (o) { return '<option value="' + o[0] + '"' + (o[0] === cur ? " selected" : "") + ">" + o[1] + "</option>"; }).join("") + "</select>" +
      (s.suggest ? '<small class="stt-suggest">' + esc(s.suggest.text) + ' <button type="button" class="link-btn stt-use-catalog">Use the catalog</button></small>' : "");
  }

  async function showSections() {
    const t = checked($("ss-terms"));
    const err = $("ss-error");
    if (!t.length) { err.textContent = "Tick at least one term."; err.hidden = false; return; }
    err.hidden = true;
    const [r, rr] = await Promise.all([VA.fetchJson(API + "/sections?terms=" + encodeURIComponent(t.join("||"))), VA.fetchJson("/api/v1/academics/rooms")]);
    if (!r.ok) { err.textContent = msg(r, "Could not load the sections."); err.hidden = false; return; }
    rooms = rr.ok ? (Array.isArray(rr.body) ? rr.body : rr.body.items || []).filter(function (x) { return x.is_active !== false; }) : [];
    $("ss-tbody").innerHTML = r.body.map(function (s) {
      const together = s.combined
        ? '<span class="pt-comb">combined</span> <small>with ' + esc((s.combined_with || []).join(", ")) + '</small> <button type="button" class="link-btn co-sep" data-sep="' + esc(s.id) + '">Separate</button>'
        : s.can_combine && window.VistCombine
          ? '<small>Same course in ' + esc(s.can_combine.with.join(", ")) + "</small><br>" + window.VistCombine.controls(s.can_combine)
          : '<span class="sd-dim">—</span>';
      return '<tr data-id="' + esc(s.id) + '"><td><strong>' + esc(s.code) + "</strong> " + esc(s.title) + "</td><td>" + esc(s.section) +
        "</td><td>" + esc(s.term) + '</td><td class="co-cell">' + together + '</td><td class="num">' + esc(s.credit_hours) + (s.lab_credit_hours ? '<small class="crs-split">' + esc(s.credit_hours - s.lab_credit_hours) + "+" + esc(s.lab_credit_hours) + "</small>" : "") + "</td>" +
        '<td><input type="number" class="text-input stt-num" min="1" max="7" data-f="classes_per_week" value="' + esc(s.classes_per_week || "") + '" placeholder="' + esc(s.per_week) + '" aria-label="Classes a week" /></td>' +
        '<td>' + labsSelect(s) + '</td>' +
        '<td><select class="select-input" data-f="home_room" aria-label="Home room"><option value="">(any)</option>' + rooms.map(function (x) {
          return '<option' + (x.name === s.home_room ? " selected" : "") + ">" + esc(x.name) + "</option>";
        }).join("") + "</select></td><td class=\"sd-dim\" data-status></td></tr>";
    }).join("");
    $("ss-table").hidden = false;
  }
  $("ss-show").addEventListener("click", showSections);
  // taught together: combine sections of the same course (other batches too), or separate one again
  function afterCombine() { showSections(); document.dispatchEvent(new CustomEvent("tw:refresh")); }
  if (window.VistCombine) window.VistCombine.wire($("ss-tbody"), afterCombine);
  $("ss-tbody").addEventListener("click", async function (e) {
    const b = e.target.closest(".co-sep");
    if (!b) return;
    if (!window.confirm("Take this section out of the combined class? It keeps its timetable, which you can then change on its own.")) return;
    if (await window.VistCombine.separate(b.getAttribute("data-sep"))) afterCombine();
  });
  $("ss-tbody").addEventListener("change", async function (e) {
    const tr = e.target.closest("tr[data-id]");
    if (!tr || e.target.classList.contains("co-teacher")) return;
    const get = function (f) { return tr.querySelector('[data-f="' + f + '"]'); };
    const n = get("classes_per_week").value.trim();
    const labs = get("labs").value;
    const body = { classes_per_week: n ? parseInt(n, 10) : null, double_period: labs === "all", labs_as_catalog: labs === "",
                   lab_classes_per_week: labs === "all" || labs === "" ? null : parseInt(labs, 10), home_room: get("home_room").value || null };
    const st = tr.querySelector("[data-status]");
    st.textContent = "Saving…";
    const r = await VA.fetchJson(API + "/sections/" + tr.dataset.id, json("PATCH", body));
    st.textContent = r.ok ? "Saved" : msg(r, "Not saved");
    if (r.ok) await refreshRow(tr.dataset.id);          // the suggestion (if any) follows the new setting
  });
  async function refreshRow(id) {
    await showSections();
    const row = $("ss-tbody").querySelector('tr[data-id="' + id + '"] [data-status]');
    if (row) row.textContent = "Saved";
  }
  // the course catalog says e.g. 3+1 and this section differs: one tap sets it as the catalog says
  $("ss-tbody").addEventListener("click", async function (e) {
    const b = e.target.closest(".stt-use-catalog");
    if (!b) return;
    const tr = b.closest("tr[data-id]");
    const st = tr.querySelector("[data-status]");
    st.textContent = "Saving…";
    const r = await VA.fetchJson(API + "/sections/" + tr.dataset.id, json("PATCH", { classes_per_week: null, double_period: false, labs_as_catalog: true,
      home_room: tr.querySelector('[data-f="home_room"]').value || null }));
    st.textContent = r.ok ? "Saved" : msg(r, "Not saved");
    if (r.ok) await refreshRow(tr.dataset.id);
  });

  // ================================================================== teachers
  async function loadTeachers() {
    const r = await VA.fetchJson(API + "/teachers");
    if (!r.ok) { $("tt-teachers-tbody").innerHTML = '<tr><td colspan="5">' + esc(msg(r, "Could not load the teachers.")) + "</td></tr>"; return; }
    $("tt-teachers-tbody").innerHTML = r.body.map(function (t) {
      const lim = [t.max_per_day ? "≤ " + t.max_per_day + " a day" : "", t.max_in_row ? "≤ " + t.max_in_row + " in a row" : ""].filter(Boolean).join(", ");
      return "<tr><td>" + esc(t.name) + ' <span class="sd-dim">' + esc(t.code) + "</span>" + (t.locked ? ' <span class="pt-comb">fixed</span>' : "") + "</td><td>" + esc(t.department || "") +
        '</td><td class="num">' + (t.blocked || "—") + "</td><td>" + esc(lim || "—") + '</td><td><button type="button" class="link-btn" data-teacher="' + esc(t.id) + '" data-name="' + esc(t.name) + '">Edit…</button></td></tr>';
    }).join("") || '<tr><td colspan="5">No teachers found.</td></tr>';
  }
  $("tt-teachers-tbody").addEventListener("click", async function (e) {
    const b = e.target.closest("[data-teacher]");
    if (!b) return;
    const id = b.dataset.teacher;
    const r = await VA.fetchJson(API + "/teachers/" + id + "/availability");
    if (!r.ok) { VA.toast(msg(r, "Could not load."), "error"); return; }
    const box = $("tt-editor");
    box.hidden = false;
    window.AvailabilityGrid.render(box, r.body, {
      admin: true, title: b.dataset.name,
      onSave: function (payload) { return VA.fetchJson(API + "/teachers/" + id + "/availability", json("PUT", payload)); },
      onSaved: loadTeachers,
    });
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  // ================================================================== start
  (async function start() {
    await loadTerms();
    loadTeachers();
    const r = await VA.fetchJson(API + "/latest");
    if (!r.ok) return;
    nextMonday = r.body.next_monday;
    if (r.body.id) {
      const v = await VA.fetchJson(API + "/drafts/" + r.body.id);
      if (v.ok) draw(v.body);
    }
  })();
})();
