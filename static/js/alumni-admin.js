(function () {
  "use strict";
  // Graduation & alumni (administration): API /api/v1/alumni
  const VA = window.VistAdmin;
  const API = "/api/v1/alumni";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const send = function (url, method, body) { return VA.fetchJson(url, { method: method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); };
  const day = function (v) { return new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }); };
  let current = null, timer = null;

  // ---- graduation lists
  async function programs() {
    const r = await VA.fetchJson("/api/v1/programs?page_size=200", { quiet: true });
    const rows = r.ok ? (r.body.items || r.body) : [];
    const opts = rows.map(function (p) { return '<option value="' + p.id + '">' + esc(p.code + " — " + p.name) + "</option>"; }).join("");
    $("gl-program").innerHTML = opts;
    $("ad-program").insertAdjacentHTML("beforeend", opts);
  }
  async function lists() {
    const r = await VA.fetchJson(API + "/graduation");
    $("gl-list").innerHTML = (r.ok ? r.body : []).map(function (g) {
      return '<tr data-id="' + g.id + '"><td><strong>' + esc(g.title) + "</strong></td><td>" + esc(g.program) + "</td><td>" + esc(day(g.graduation_date)) + "</td><td>" + g.included + "</td><td>" +
        (g.status === "DRAFT" ? "Being prepared" : "Confirmed") + '</td><td><button type="button" class="row-action gl-open">Open</button></td></tr>';
    }).join("") || '<tr><td colspan="6" class="sd-dim">No lists yet.</td></tr>';
  }
  function show(d) {
    current = d;
    const draft = d.status === "DRAFT";
    $("gl-d-title").textContent = d.title + " — " + d.program + " (" + (draft ? "being prepared" : "confirmed") + ")";
    $("gl-entries").innerHTML = d.entries.map(function (e) {
      const tick = draft ? '<input type="checkbox" class="gl-tick"' + (e.included ? " checked" : "") + ' aria-label="Graduates">' : (e.result === "GRADUATED" ? "✓ Graduated" : e.result === "SKIPPED" ? "Skipped" : "—");
      const checks = e.problems.length ? '<span class="sd-dim">' + e.problems.map(esc).join("<br>") + "</span>" : "Clear";
      return '<tr data-id="' + e.id + '"><td>' + tick + "</td><td>" + esc(e.roll_number) + "</td><td>" + esc(e.name) + "</td><td>" + (e.cgpa == null ? "—" : e.cgpa.toFixed(2)) + "</td><td>" + checks +
        (e.note ? "<small>" + esc(e.note) + "</small>" : "") + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">No current students in this program.</td></tr>';
    $("gl-pdf").href = API + "/graduation/" + d.id + "/pdf";
    $("gl-refresh").hidden = $("gl-confirm").hidden = $("gl-del").hidden = !draft;
    $("gl-detail").hidden = false;
  }
  async function open(id) { const r = await VA.fetchJson(API + "/graduation/" + id); if (r.ok) show(r.body); }
  $("gl-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    VA.toast("Checking every student… this can take a moment.");
    const r = await send(API + "/graduation", "POST", { program_id: $("gl-program").value, title: $("gl-title").value, graduation_date: $("gl-date").value });
    if (!r.ok) return VA.toast(err(r, "Not drawn up."), "error");
    show(r.body); lists();
  });
  $("gl-list").addEventListener("click", function (e) { if (e.target.closest(".gl-open")) open(e.target.closest("tr").getAttribute("data-id")); });
  $("gl-entries").addEventListener("change", async function (e) {
    const box = e.target.closest(".gl-tick");
    if (!box) return;
    const id = box.closest("tr").getAttribute("data-id"), entry = current.entries.find(function (x) { return x.id === id; });
    let note = null;
    if (box.checked && entry.problems.length) {
      note = await VA.ask({ title: "Include a student who is not clear", label: "Reason (at least 10 letters; it is recorded)", min: 10 });
      if (!note) { box.checked = false; return; }
    }
    const r = await send(API + "/graduation/" + current.id + "/entries/" + id, "POST", { included: box.checked, note: note });
    if (!r.ok) { box.checked = !box.checked; return VA.toast(err(r, "Not saved."), "error"); }
    open(current.id); lists();
  });
  $("gl-refresh").addEventListener("click", async function () { const r = await send(API + "/graduation/" + current.id + "/refresh", "POST"); if (r.ok) show(r.body); });
  $("gl-confirm").addEventListener("click", async function () {
    const n = current.entries.filter(function (x) { return x.included; }).length;
    if (!(await VA.confirm("Mark " + n + " student(s) as graduated? Their student login becomes an alumni login. This cannot be undone."))) return;
    const r = await send(API + "/graduation/" + current.id + "/confirm", "POST");
    if (!r.ok) return VA.toast(err(r, "Not done."), "error");
    VA.toast(r.body.graduated + " graduated" + (r.body.skipped ? ", " + r.body.skipped + " skipped (see why)" : "") + ".");
    show(r.body); lists();
  });
  $("gl-del").addEventListener("click", async function () {
    if (!(await VA.confirm("Delete this list? Nobody has been graduated by it."))) return;
    const r = await send(API + "/graduation/" + current.id, "DELETE");
    if (r.ok) { $("gl-detail").hidden = true; lists(); }
  });

  // ---- directory
  async function directory() {
    const qs = new URLSearchParams();
    if ($("ad-q").value.trim()) qs.set("q", $("ad-q").value.trim());
    if ($("ad-program").value) qs.set("program_id", $("ad-program").value);
    $("ad-csv").href = API + "/directory/all.csv" + ($("ad-program").value ? "?program_id=" + $("ad-program").value : "");
    const r = await VA.fetchJson(API + "/directory/all?" + qs.toString());
    $("ad-list").innerHTML = (r.ok ? r.body : []).map(function (a) {
      return '<tr data-id="' + a.student_id + '"><td><strong>' + esc(a.name) + "</strong><small>" + esc(a.roll_number || "") + (a.in_directory ? " · listed" : "") + "</small></td><td>" + esc(a.program || "") + (a.year ? " · " + a.year : "") +
        "</td><td>" + esc([a.job_title, a.employer].filter(Boolean).join(", ") || a.further_study || "—") + "</td><td>" + esc([a.city, a.country].filter(Boolean).join(", ") || "—") + "</td><td>" +
        esc([a.email, a.phone].filter(Boolean).join(" · ") || "—") + "</td><td>" + (a.has_login ? "" : "<small>no login</small>") +
        '<button type="button" class="row-action ad-login">Alumni login</button></td></tr>';
    }).join("") || '<tr><td colspan="6" class="sd-dim">No graduates found.</td></tr>';
  }
  $("ad-q").addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(directory, 300); });
  $("ad-program").addEventListener("change", directory);
  $("ad-list").addEventListener("click", async function (e) {
    if (!e.target.closest(".ad-login")) return;
    if (!(await VA.confirm("Switch this graduate's old login back on as an alumni login?"))) return;
    const r = await send(API + "/students/" + e.target.closest("tr").getAttribute("data-id") + "/login", "POST");
    if (!r.ok) return VA.toast(err(r, "Not done."), "error");
    VA.toast(r.body.has_login ? "Alumni login switched on." : "This graduate never had a login.");
  });

  // ---- surveys
  function parse(text) {
    return text.split("\n").map(function (l) { return l.trim(); }).filter(Boolean).map(function (l) {
      const m = /^([RTC]):\s*(.*)$/i.exec(l);
      const kind = m ? { R: "RATING", T: "TEXT", C: "CHOICE" }[m[1].toUpperCase()] : "TEXT", rest = m ? m[2] : l;
      if (kind === "CHOICE") { const parts = rest.split("|").map(function (x) { return x.trim(); }); return { text: parts[0], kind: kind, choices: parts.slice(1) }; }
      return { text: rest, kind: kind };
    });
  }
  async function surveys() {
    const r = await VA.fetchJson(API + "/surveys");
    $("sv-list").innerHTML = (r.ok ? r.body : []).map(function (s) {
      return '<tr data-id="' + s.id + '" data-title="' + esc(s.title) + '"><td><strong>' + esc(s.title) + "</strong><small>" + s.questions.length + " question(s)</small></td><td>" + s.responses + "</td><td>" + (s.is_open ? "Open" : "Closed") +
        '</td><td><button type="button" class="row-action sv-toggle" data-open="' + (s.is_open ? "0" : "1") + '">' + (s.is_open ? "Close" : "Open (tells every alumnus)") + '</button> <button type="button" class="row-action sv-res">Results</button></td></tr>';
    }).join("") || '<tr><td colspan="4" class="sd-dim">No surveys yet.</td></tr>';
  }
  $("sv-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const r = await send(API + "/surveys", "POST", { title: $("sv-title").value, intro: $("sv-intro").value || null, questions: parse($("sv-qs").value) });
    if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
    VA.toast("Saved. Open it when ready."); $("sv-title").value = ""; surveys();
  });
  $("sv-list").addEventListener("click", async function (e) {
    const tr = e.target.closest("tr");
    if (e.target.closest(".sv-toggle")) {
      const r = await send(API + "/surveys/" + tr.getAttribute("data-id"), "PUT", { title: tr.getAttribute("data-title"), is_open: e.target.getAttribute("data-open") === "1" });
      if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
      surveys();
    } else if (e.target.closest(".sv-res")) {
      const r = await VA.fetchJson(API + "/surveys/" + tr.getAttribute("data-id") + "/results");
      if (!r.ok) return;
      $("sv-r-title").textContent = r.body.title + " — " + r.body.responses + " answer(s)";
      $("sv-r-body").innerHTML = r.body.questions.map(function (q) {
        let inner = "";
        if (q.kind === "RATING") inner = "Average " + (q.average == null ? "—" : q.average) + " of 5 · " + Object.keys(q.counts).map(function (k) { return k + ": " + q.counts[k]; }).join(", ");
        else if (q.kind === "CHOICE") inner = Object.keys(q.counts).map(function (k) { return esc(k) + ": " + q.counts[k]; }).join(" · ");
        else inner = "<ul>" + q.texts.map(function (t) { return "<li>" + esc(t) + "</li>"; }).join("") + "</ul>";
        return "<h3>" + esc(q.text) + "</h3><p>" + inner + "</p>";
      }).join("");
      $("sv-results").hidden = false;
    }
  });

  programs(); lists(); directory(); surveys();
})();
