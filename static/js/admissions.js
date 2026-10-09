(function () {
  "use strict";

  // Admissions office: /api/v1/admissions/...
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/admissions";
  const STATUS = { SUBMITTED: "To verify", VERIFIED: "Verified", OFFERED: "Offered", ADMITTED: "Admitted", REJECTED: "Rejected", EXPIRED: "Offer expired", WITHDRAWN: "Withdrawn" };
  const FIELDS = [["first_name", "First name"], ["last_name", "Last name"], ["date_of_birth", "Date of birth", "date"], ["gender", "Gender (Male/Female)"],
    ["cnic", "CNIC / B-Form 12345-1234567-1"], ["contact_no", "Mobile"], ["email", "Email (optional)"], ["marital_status", "Marital status"], ["religion", "Religion"],
    ["nationality", "Nationality"], ["guardian_name", "Father / guardian"], ["guardian_cnic", "Guardian CNIC"], ["guardian_contact_no", "Guardian mobile"],
    ["relation_with_guardian", "Relation"], ["emergency_contact_no", "Emergency mobile"], ["present_address", "Address"], ["province", "Province"],
    ["district", "District"], ["city", "City"], ["matric_obtained", "Matric marks", "number"], ["matric_total", "Matric total", "number"],
    ["inter_obtained", "Inter marks", "number"], ["inter_total", "Inter total", "number"], ["test_obtained", "Test marks", "number"], ["test_total", "Test total", "number"]];
  let current = null;

  function say(t) { const el = document.getElementById("ad-msg"); el.textContent = t; el.hidden = !t; el.scrollIntoView({ block: "nearest" }); }
  function errText(r) { return (r.body && r.body.error && r.body.error.message) || "That did not work."; }
  function postJson(url, body) { return VA.fetchJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }); }
  const pct = function (v) { return v === null || v === undefined ? "—" : Number(v).toFixed(1); };

  async function loadPrograms() {
    const r = await VA.fetchJson("/api/v1/programs");
    const items = r.ok ? (Array.isArray(r.body) ? r.body : r.body.items || []) : [];
    document.getElementById("ar-program").innerHTML = '<option value="">Program…</option>' + items.map(function (p) {
      return '<option value="' + p.id + '">' + esc(p.code + " — " + p.name) + "</option>";
    }).join("");
  }

  async function loadRounds() {
    const r = await VA.fetchJson(API + "/rounds");
    if (!r.ok) return say(errText(r));
    document.getElementById("ar-list").innerHTML = r.body.map(function (x) {
      const total = Object.keys(x.counts).reduce(function (s, k) { return s + x.counts[k]; }, 0);
      return "<tr><td>" + esc(x.term_label) + "</td><td>" + esc(x.program) + '</td><td class="num">' + x.seats + '</td><td class="num">' + x.seats_free +
        "</td><td>" + total + " (" + (x.counts.ADMITTED || 0) + " admitted)</td><td>" + esc(x.status === "OPEN" ? "Open" : "Closed") +
        '</td><td><button type="button" class="row-action" data-open="' + x.id + '">Open</button></td></tr>';
    }).join("") || '<tr><td colspan="7">No admission rounds yet.</td></tr>';
    document.querySelectorAll("[data-open]").forEach(function (b) {
      b.addEventListener("click", function () { current = r.body.find(function (x) { return x.id === b.dataset.open; }); showRound(); });
    });
    if (current) current = r.body.find(function (x) { return x.id === current.id; }) || null;
    return r.body;
  }

  async function showRound() {
    if (!current) return;
    document.getElementById("ad-round").hidden = false;
    const w = current.weights;
    document.getElementById("ad-title").textContent = current.program + " — " + current.term_label + " · " + current.seats + " seats (" + current.seats_free +
      " free) · merit: matric " + w.matric + "% + inter " + w.inter + "% + test " + w.test + "%" + (current.status === "CLOSED" ? " · CLOSED" : "");
    let links = "";
    for (let i = 1; i <= current.merit_lists_published; i += 1) {
      links += '<a href="' + API + "/rounds/" + current.id + "/merit-lists/" + i + '.pdf" target="_blank" rel="noopener">Merit list ' + i + "</a> ";
    }
    document.getElementById("ad-lists").innerHTML = links;
    const f = document.getElementById("ad-filter").value;
    const r = await VA.fetchJson(API + "/rounds/" + current.id + "/applicants" + (f ? "?status=" + f : ""));
    if (!r.ok) return say(errText(r));
    document.getElementById("ad-tbody").innerHTML = r.body.map(function (a) {
      let act = "";
      if (a.status === "SUBMITTED" || a.status === "REJECTED") act += '<button type="button" class="row-action" data-st="VERIFIED" data-id="' + a.id + '">Verify</button> ';
      if (a.status === "SUBMITTED" || a.status === "VERIFIED") act += '<button type="button" class="row-action" data-st="REJECTED" data-id="' + a.id + '">Reject</button> ';
      if (a.status === "OFFERED") act += '<button type="button" class="row-action" data-admit="' + a.id + '">Admit</button> ';
      if (["SUBMITTED", "VERIFIED", "OFFERED"].indexOf(a.status) !== -1) act += '<button type="button" class="row-action" data-st="WITHDRAWN" data-id="' + a.id + '">Withdrawn</button>';
      const st = STATUS[a.status] + (a.merit_list_no ? " (list " + a.merit_list_no + ", #" + a.merit_rank + (a.offer_expires_on && a.status === "OFFERED" ? ", by " + a.offer_expires_on : "") + ")" : "");
      return "<tr><td>" + esc(a.application_no) + "</td><td>" + esc(a.name) + "<br><small>" + esc(a.cnic) + "</small></td><td class=\"num\">" + pct(a.matric_percent) +
        '</td><td class="num">' + pct(a.inter_percent) + '</td><td class="num">' + pct(a.test_percent) + '</td><td class="num">' + Number(a.aggregate).toFixed(3) +
        "</td><td>" + esc(st) + (a.remarks ? "<br><small>" + esc(a.remarks) + "</small>" : "") + "</td><td>" + act + "</td></tr>";
    }).join("") || '<tr><td colspan="8">No applications.</td></tr>';
    document.querySelectorAll("[data-st]").forEach(function (b) {
      b.addEventListener("click", async function () {
        let remarks = null;
        if (b.dataset.st === "REJECTED") { remarks = window.prompt("Reason for rejecting:"); if (!remarks) return; }
        const res = await postJson(API + "/applicants/" + b.dataset.id + "/status", { status: b.dataset.st, remarks: remarks });
        if (!res.ok) return say(errText(res));
        refresh();
      });
    });
    document.querySelectorAll("[data-admit]").forEach(function (b) {
      b.addEventListener("click", async function () {
        if (!window.confirm("Admit this applicant? This creates their student record and login.")) return;
        const res = await postJson(API + "/applicants/" + b.dataset.admit + "/admit");
        if (!res.ok) return say(errText(res));
        say("Admitted: roll number " + res.body.roll_number + ", username " + res.body.username + ". They sign in with the institute's default password and must change it.");
        refresh();
      });
    });
  }

  async function refresh() { await loadRounds(); showRound(); }

  document.getElementById("ar-create").addEventListener("click", async function () {
    const body = { term_label: document.getElementById("ar-term").value, program_id: document.getElementById("ar-program").value,
      seats: Number(document.getElementById("ar-seats").value), weight_matric: Number(document.getElementById("ar-wm").value),
      weight_inter: Number(document.getElementById("ar-wi").value), weight_test: Number(document.getElementById("ar-wt").value),
      min_inter_percent: Number(document.getElementById("ar-min").value), closes_on: document.getElementById("ar-closes").value || null };
    if (!body.term_label || !body.program_id || !body.seats) return say("Choose the term and program and give the number of seats.");
    const r = await postJson(API + "/rounds", body);
    if (!r.ok) return say(errText(r));
    say("Round opened.");
    const all = await loadRounds();
    current = all.find(function (x) { return x.id === r.body.id; });
    showRound();
  });

  document.getElementById("ad-merit").addEventListener("click", async function () {
    const days = window.prompt("Days for selected applicants to complete admission:", "7");
    if (!days) return;
    const r = await postJson(API + "/rounds/" + current.id + "/merit-lists", { offer_days: Number(days) });
    if (!r.ok) return say(errText(r));
    say("Merit list " + r.body.merit_list_no + " published: " + r.body.offered + " offered (closing aggregate " + r.body.lowest_aggregate_offered +
      "), deadline " + r.body.offer_deadline + ". " + r.body.still_waiting + " still waiting.");
    refresh();
  });
  document.getElementById("ad-expire").addEventListener("click", async function () {
    const r = await postJson(API + "/rounds/" + current.id + "/expire-offers");
    if (!r.ok) return say(errText(r));
    say(r.body.expired + " offer(s) expired; their seats are free for the next merit list.");
    refresh();
  });
  document.getElementById("ad-close").addEventListener("click", async function () {
    if (!window.confirm("Close this round? No more applications or merit lists.")) return;
    const r = await postJson(API + "/rounds/" + current.id + "/close");
    if (!r.ok) return say(errText(r));
    refresh();
  });

  async function upload(dry) {
    const f = document.getElementById("ad-file").files[0];
    if (!f) return say("Choose the CSV file.");
    const fd = new FormData();
    fd.append("file", f);
    const r = await VA.fetchJson(API + "/rounds/" + current.id + "/applicants/import?dry_run=" + dry, { method: "POST", body: fd });
    if (!r.ok) return say(errText(r));
    const e = r.body.errors.map(function (x) { return "row " + x.row + ": " + x.message; }).join(" | ");
    say((dry ? r.body.ok_rows + " row(s) are fine." : r.body.ok_rows + " application(s) imported.") + (e ? " Problems — " + e : ""));
    if (!dry) refresh();
  }
  document.getElementById("ad-check").addEventListener("click", function () { upload(true); });
  document.getElementById("ad-import").addEventListener("click", function () { upload(false); });

  document.getElementById("ad-one").addEventListener("click", function () {
    const holder = document.getElementById("ad-fields");
    if (!holder.innerHTML) {
      holder.innerHTML = FIELDS.map(function (f) {
        return '<input class="text-input small-input" data-f="' + f[0] + '" type="' + (f[2] || "text") + '" placeholder="' + esc(f[1]) + '" aria-label="' + esc(f[1]) + '" />';
      }).join("");
    }
    document.getElementById("ad-form").hidden = false;
  });
  document.getElementById("ad-save").addEventListener("click", async function () {
    const body = {};
    document.querySelectorAll("#ad-fields [data-f]").forEach(function (i) {
      const v = i.value.trim();
      if (v !== "") body[i.dataset.f] = i.type === "number" ? Number(v) : v;
    });
    const r = await postJson(API + "/rounds/" + current.id + "/applicants", body);
    if (!r.ok) return say(errText(r));
    say("Saved: " + r.body.application_no + (r.body.status === "REJECTED" ? " — rejected automatically: " + r.body.remarks : "."));
    document.querySelectorAll("#ad-fields [data-f]").forEach(function (i) { i.value = ""; });
    refresh();
  });

  document.getElementById("ad-filter").addEventListener("change", showRound);
  document.addEventListener("vistadmin:ready", function () { loadPrograms(); loadRounds(); });
})();