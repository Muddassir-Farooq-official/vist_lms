// Timetable wizard (admin/timetable.html, also the exam department's Timetable): the batch terms chosen in step 1 are
// carried into every step's own term list, each step shows whether it is ready (API .../smart/readiness), the
// optional "start again" clear (API .../smart/clear-preview and /clear), Back / Next, and the guided tour.
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const API = "/api/v1/academics/timetable/smart";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const day = function (v) { return v ? new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—"; };
  const iso = function (d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); };
  const KEY = "vist.timetable.terms", TOUR = "vist.timetable.tour.v1";
  const PICKERS = ["st-terms", "ss-terms", "wl-terms", "fr-terms", "pt-terms"];
  const SHOW = { sections: "ss-show", workload: "wl-show" };
  let chosen = [], ready = null, preview = null, refreshTimer = null;

  const store = {
    get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  };

  // ------------------------------------------------------------------ step 1: the batches, carried everywhere
  async function loadTerms() {
    const r = await VA.fetchJson("/api/v1/academics/terms");
    const terms = r.ok ? r.body : [];
    try { chosen = JSON.parse(store.get(KEY) || "[]").filter(function (t) { return terms.indexOf(t) >= 0; }); } catch (e) { chosen = []; }
    $("tw-terms").innerHTML = terms.length ? terms.map(function (t) {
      return '<label><input type="checkbox" value="' + esc(t) + '"' + (chosen.indexOf(t) >= 0 ? " checked" : "") + " /> " + esc(t) + "</label>";
    }).join("") : '<span class="sd-dim">No terms yet. Add them on Courses → Academic terms, with their course sections.</span>';
    applyEverywhere();
    refresh();
  }

  function readChosen() {
    chosen = Array.prototype.map.call($("tw-terms").querySelectorAll("input[type=checkbox]:checked"), function (c) { return c.value; });
    store.set(KEY, JSON.stringify(chosen));
  }

  function applyTo(box) {
    if (!box || !chosen.length) return;
    const sig = chosen.join("\u0001") + "|" + box.querySelectorAll("input[type=checkbox]").length;
    if (box.dataset.twSig === sig) return;
    let changed = null;
    box.querySelectorAll("input[type=checkbox][value]").forEach(function (c) {
      const want = chosen.indexOf(c.value) >= 0;
      if (c.checked !== want) { c.checked = want; changed = c; }
    });
    box.dataset.twSig = sig;
    // let the step's own script know, as if the person had ticked it
    if (changed) changed.dispatchEvent(new Event("change", { bubbles: true }));
  }
  function applyEverywhere() { PICKERS.forEach(function (id) { applyTo($(id)); }); }

  // the other steps draw their own term lists when they load: tick ours as soon as they appear
  PICKERS.forEach(function (id) {
    const box = $(id);
    if (box) new MutationObserver(function () { applyTo(box); }).observe(box, { childList: true, subtree: true });
  });

  $("tw-terms").addEventListener("change", function () {
    readChosen();
    PICKERS.forEach(function (id) { const b = $(id); if (b) delete b.dataset.twSig; });
    applyEverywhere();
    $("tw-clear-result").innerHTML = "";
    // "Tick all" fires one change per box: check once they settle
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refresh, 200);
  });

  // ------------------------------------------------------------------ where things stand
  function mark(step, state, title) {
    const b = document.querySelector('[data-badge="' + step + '"]');
    if (!b) return;
    b.className = "tw-badge" + (state ? " tw-" + state : "");
    b.textContent = state === "ok" ? "✓" : state === "warn" ? "!" : "";
    b.title = title || "";
    const note = document.querySelector('[data-note-for="' + step + '"]');
    if (note) note.textContent = title || "";
  }

  async function refresh() {
    const box = $("tw-ready");
    if (!chosen.length) {
      box.innerHTML = '<p class="tw-empty">Tick at least one batch term above to begin.</p>';
      ["choose", "periods", "workload", "sections", "teachers", "draft"].forEach(function (s) { mark(s, "", ""); });
      return;
    }
    const r = await VA.fetchJson(API + "/readiness?terms=" + encodeURIComponent(chosen.join("||")), { quiet: true });
    if (!r.ok) { box.innerHTML = '<p class="dash-alert error">' + esc(err(r, "Could not check.")) + "</p>"; return; }
    ready = r.body;
    const d = ready, sec = d.sections;
    const rows = [
      ["choose", sec.total > 0, sec.total + " course section" + (sec.total === 1 ? "" : "s") + " in " + d.terms.length + " batch term" + (d.terms.length === 1 ? "" : "s") + (sec.labs ? " (" + sec.labs + " labs)" : ""),
        sec.total ? "" : "These batches have no course sections yet: add them on Courses first."],
      ["periods", d.periods["class"] > 0, d.periods["class"] + " class periods a day, " + d.periods.breaks + " breaks; " + d.rooms + " rooms listed",
        d.periods["class"] ? (d.rooms ? "" : "No rooms listed: any room name is accepted.") : "Add the daily class periods."],
      ["workload", sec.without_teacher === 0 && sec.total > 0, sec.without_teacher ? sec.without_teacher + " section(s) have no teacher" : "Every section has a teacher",
        sec.without_teacher ? "Give them a teacher (or they are placed without one)." : ""],
      ["sections", sec.total > 0, "Classes a week start at each course's credit hours" + (sec.labs ? "; " + sec.labs + " marked as labs" : ""), "Check labs and anything that differs."],
      ["teachers", true, d.teachers.with_times + " of " + d.teachers.total + " teachers have marked times they cannot teach", "Optional."],
      ["draft", !!(d.draft && d.draft.status === "PUBLISHED"), d.draft ? (d.draft.status === "PUBLISHED" ? "Published from " + day(d.draft.published_from) : "A draft is waiting to be checked and published") :
        (d.live_weekly_patterns ? "Live timetable: " + sec.with_timetable + " of " + sec.total + " sections have weekly classes" : "No timetable yet"), ""]
    ];
    box.innerHTML = '<ol class="tw-check">' + rows.map(function (x) {
      const ok = x[1];
      return '<li class="' + (ok ? "ok" : "warn") + '"><button type="button" class="link-btn tw-go" data-go="' + x[0] + '">' + esc(x[2]) + "</button>" + (x[3] ? '<small>' + esc(x[3]) + "</small>" : "") + "</li>";
    }).join("") + "</ol>";
    mark("choose", sec.total ? "ok" : "warn", rows[0][2]);
    mark("periods", d.periods["class"] ? "ok" : "warn", rows[1][2]);
    mark("workload", sec.without_teacher ? "warn" : "ok", rows[2][2]);
    mark("sections", sec.total ? "ok" : "", rows[3][2]);
    mark("teachers", "", rows[4][2]);
    mark("draft", d.draft && d.draft.status === "PUBLISHED" ? "ok" : d.draft ? "warn" : "", rows[5][2]);
  }

  // ------------------------------------------------------------------ moving between steps
  function go(step) {
    const b = document.querySelector('[data-auto-tabs] [data-tab="' + step + '"]');
    if (b) { b.click(); window.scrollTo({ top: 0, behavior: "smooth" }); }
  }
  document.addEventListener("click", function (e) {
    const g = e.target.closest(".tw-go");
    if (g) { e.preventDefault(); go(g.getAttribute("data-go")); }
  });
  document.querySelectorAll("[data-auto-tabs] [data-tab]").forEach(function (b) {
    b.addEventListener("click", function () {
      const step = b.getAttribute("data-tab");
      applyEverywhere();
      // load the step's list straight away for the chosen batches
      if (SHOW[step] && chosen.length && !b.dataset.twLoaded) {
        b.dataset.twLoaded = "1";
        setTimeout(function () { const s = $(SHOW[step]); if (s) s.click(); }, 150);
      }
      if (step === "choose") refresh();
    });
  });

  // ------------------------------------------------------------------ start again: clear
  $("tw-clear-date").value = iso(new Date());
  $("tw-clear-date").min = iso(new Date());
  $("tw-clear-preview").addEventListener("click", async function () {
    const out = $("tw-clear-result");
    if (!chosen.length) { out.innerHTML = '<p class="dash-alert error">Tick the batch terms first.</p>'; return; }
    out.innerHTML = '<p class="sd-dim">Checking…</p>';
    const r = await VA.fetchJson(API + "/clear-preview?terms=" + encodeURIComponent(chosen.join("||")) + "&from_date=" + $("tw-clear-date").value);
    if (!r.ok) { out.innerHTML = '<p class="dash-alert error">' + esc(err(r, "Could not check.")) + "</p>"; return; }
    preview = r.body;
    const t = preview.totals;
    out.innerHTML = '<div class="table-scroll"><table class="dash-table tw-clear-table"><thead><tr><th>Batch term</th><th>Cleared from</th><th>Until</th><th class="num">Weekly classes</th>' +
      '<th class="num" title="Upcoming classes with nothing in them">Removed</th><th class="num" title="Attendance already marked: never changed">Kept: attendance</th>' +
      '<th class="num" title="A topic, files or marks: kept as cancelled">Kept: cancelled</th><th class="num" title="Classes before the date: never changed">Before the date</th></tr></thead><tbody>' +
      preview.terms.map(function (x) {
        return "<tr><td><strong>" + esc(x.term) + "</strong>" + (x.chosen ? "" : '<small>combined class with ' + esc(x.combined_from.join(", ")) + "</small>") + "</td><td>" + esc(day(x.from)) + "</td><td>" + esc(day(x.until)) +
          '</td><td class="num">' + x.slots + '</td><td class="num">' + x.removed + '</td><td class="num">' + x.kept_attendance + '</td><td class="num">' + x.kept_cancelled + '</td><td class="num">' + x.before + "</td></tr>";
      }).join("") + '</tbody><tfoot><tr><th colspan="3">Total</th><th class="num">' + t.slots + '</th><th class="num">' + t.removed + '</th><th class="num">' + t.kept_attendance + '</th><th class="num">' + t.kept_cancelled + '</th><th class="num">' + t.before + "</th></tr></tfoot></table></div>" +
      (preview.ended.length ? '<p class="sd-dim">Finished, not touched: ' + esc(preview.ended.join(", ")) + ".</p>" : "") +
      (t.slots ? '<div class="form-actions"><button type="button" class="danger-btn" id="tw-clear-open">Clear these weekly classes…</button></div>' : '<p class="sd-dim">There is nothing to clear for these batches from that date.</p>');
    const open = $("tw-clear-open");
    if (open) open.addEventListener("click", function () {
      $("tw-clear-summary").innerHTML = "<p><strong>" + t.slots + "</strong> weekly classes of " + esc(chosen.join(", ")) + " come off; <strong>" + t.removed + "</strong> empty classes are removed. " +
        "Kept: <strong>" + t.kept_attendance + "</strong> with attendance, <strong>" + t.kept_cancelled + "</strong> with a topic or files (cancelled), and every class before each batch's date.</p>" +
        "<p>Then make the new timetable in steps 2–6. This cannot be undone, but it is recorded.</p>";
      $("tw-clear-word").value = "";
      $("tw-clear-ok").disabled = true;
      $("tw-clear-err").hidden = true;
      $("tw-clear-dlg").showModal();
      $("tw-clear-word").focus();
    });
  });
  $("tw-clear-word").addEventListener("input", function () { $("tw-clear-ok").disabled = $("tw-clear-word").value.trim().toUpperCase() !== "CLEAR"; });
  $("tw-clear-cancel").addEventListener("click", function () { $("tw-clear-dlg").close(); });
  $("tw-clear-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const notify = Array.prototype.map.call($("tw-clear-form").querySelectorAll(".stt-notify input:checked"), function (c) { return c.value; });
    $("tw-clear-ok").disabled = true;
    const r = await VA.fetchJson(API + "/clear", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ terms: chosen, from_date: $("tw-clear-date").value, confirm: $("tw-clear-word").value, notify: notify }) });
    if (!r.ok) { $("tw-clear-err").textContent = err(r, "Not cleared."); $("tw-clear-err").hidden = false; $("tw-clear-ok").disabled = false; return; }
    $("tw-clear-dlg").close();
    $("tw-clear-result").innerHTML = '<p class="dash-alert success-text">Cleared: ' + r.body.weekly_patterns_removed + " weekly classes taken off, " + r.body.totals.removed +
      " empty classes removed, " + r.body.totals.kept_attendance + " classes with attendance kept. Now go through steps 2–6 to make the new timetable.</p>";
    VA.toast("Timetable cleared. Make the new one in the next steps.");
    refresh();
  });

  // ------------------------------------------------------------------ the guided tour
  function tour() {
    window.VistTour.start([
      { el: ".tw-steps", title: "Six steps, in order", text: "Make the timetable from left to right. Each step shows a ✓ when it is ready, or ! when something needs a look. The tabs on the right are for looking at a timetable, not for making it.", before: function () { go("choose"); } },
      { el: "#tw-terms", title: "1. Batches", text: "Tick the batch terms you are making the timetable for, usually every batch of one session. Each batch keeps its own dates. Your choice is carried into every step.", before: function () { go("choose"); } },
      { el: "#tw-ready", title: "What is ready", text: "This checklist says what is in place for the chosen batches. Click a line to go to that step." },
      { el: "#tw-clear-panel", title: "Start again (optional)", text: "To make a new timetable from scratch, clear the current one from a date. Classes already held and classes with attendance are never touched. You always see a preview first and must type CLEAR." },
      { el: "#periods-table", title: "2. Periods & rooms", text: "The periods of the day (classes, breaks, assembly) and the rooms with their seats. Set these once; every timetable uses them.", before: function () { go("periods"); } },
      { el: "#wl-table, #wl-show", title: "3. Teachers", text: "Each teacher's weekly load. Sections without a teacher are listed under the table, with the best teachers proposed.", before: function () { go("workload"); } },
      { el: "#ss-table, #ss-show", title: "4. Classes & labs", text: "How many classes a week each section gets (its credit hours by default), whether it is a double lab period, and its home room.", before: function () { go("sections"); } },
      { el: "#tt-teachers", title: "5. Free times", text: "Mark the periods a teacher cannot teach, such as visiting faculty. Teachers can mark their own too.", before: function () { go("teachers"); } },
      { el: "#st-generate", title: "6. Make & publish", text: "Generate a draft, check it by class, teacher or room, move anything by hand, then Publish it from a date. Teachers, students and parents can be told.", before: function () { go("draft"); } },
      { el: "#tw-tour-btn", title: "That's it", text: "Take this tour again any time from here. The Help & manual page has the full guide.", before: function () { go("choose"); } }
    ], { onEnd: function () { store.set(TOUR, "1"); } });
  }
  $("tw-tour-btn").addEventListener("click", tour);

  // first visit: start the tour once nothing else (e.g. the "new version" message) is open
  function tourWhenFree(tries) {
    if (document.querySelector("dialog[open]")) { if (tries < 120) setTimeout(function () { tourWhenFree(tries + 1); }, 500); return; }
    tour();
  }
  loadTerms().then(function () { if (!store.get(TOUR)) setTimeout(function () { tourWhenFree(0); }, 800); });
})();
