/* Admin / principal -> Warnings (/api/v1/discipline). Issuing and the violation list only show for the admin. */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/discipline";
  const canIssue = !!$("wr-form");
  let view = "pending", chosen = null, current = null, meta = null, timer = null;

  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function day(d) { return d ? new Date(String(d).length > 10 ? d : d + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : ""; }
  function err(r, f) { return (r.body && r.body.error && r.body.error.message) || f; }
  function kind() { const r = document.querySelector('input[name="wr-kind"]:checked'); return r ? r.value : "STUDENT"; }
  function json(method, body) { return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }; }
  function badge(w) {
    if (w.status === "WITHDRAWN") return el("span", "status-pill", "Withdrawn");
    if (w.is_final) return el("span", "status-pill dropped", "Final warning");
    return el("span", "status-pill pending", "Warning " + w.number + " of " + meta.limit);
  }

  /* ---------------- issuing (admin) ---------------- */
  async function loadTypes() {
    const r = await VA.fetchJson(API + "/violation-types?kind=" + kind());
    $("wr-type").innerHTML = "";
    (r.ok ? r.body : []).forEach(function (t) { const o = el("option", null, t.name); o.value = t.id; $("wr-type").appendChild(o); });
  }

  function choose(p) {
    chosen = p;
    $("wr-results").hidden = true;
    $("wr-q").value = "";
    const box = $("wr-chosen");
    box.textContent = "";
    box.appendChild(el("strong", null, p.name));
    box.appendChild(el("span", "cc-meta", " · " + p.code + " · " + p.counting + " of 3 warnings count now"));
    $("wr-final-note").hidden = p.counting < 2;
    $("wr-final-note").textContent = "This will be " + p.name + "'s FINAL warning. The admin or principal will then decide the action (final chance, suspension, a fine or " +
      (p.kind === "STUDENT" ? "expulsion" : "termination") + ").";
    $("wr-form").hidden = false;
    $("wr-note").focus();
  }

  async function search() {
    const q = $("wr-q").value.trim();
    const ul = $("wr-results");
    if (q.length < 2) { ul.hidden = true; return; }
    const r = await VA.fetchJson(API + "/people?kind=" + kind() + "&q=" + encodeURIComponent(q));
    ul.textContent = "";
    const list = r.ok ? r.body : [];
    if (!list.length) ul.appendChild(el("li", "cc-meta", "No one found."));
    list.forEach(function (p) {
      const li = el("li");
      const b = el("button", "wr-person"); b.type = "button";
      b.appendChild(el("strong", null, p.name));
      b.appendChild(el("span", "cc-meta", p.code + " · " + p.status.toLowerCase().replace("_", " ") + (p.counting ? " · " + p.counting + " of 3 count" : "")));
      b.addEventListener("click", function () { choose(p); });
      li.appendChild(b); ul.appendChild(li);
    });
    ul.hidden = false;
  }

  async function issue(e) {
    e.preventDefault();
    if (!chosen) return;
    const note = $("wr-note").value.trim();
    if (note.length < 10) { $("wr-error").textContent = "Write what happened (at least 10 characters)."; $("wr-error").hidden = false; return; }
    $("wr-send").disabled = true;
    const r = await VA.fetchJson(API + "/warnings", json("POST", { kind: chosen.kind, person_id: chosen.id, violation_type_id: $("wr-type").value,
      note: note, incident_date: $("wr-date").value || null }));
    $("wr-send").disabled = false;
    if (!r.ok) { $("wr-error").textContent = err(r, "Could not issue the warning."); $("wr-error").hidden = false; return; }
    VA.toast(r.body.is_final ? "Final warning issued. Decide the action under 'Needs a decision'." : "Warning " + r.body.number + " of 3 issued. " + chosen.name + " has been told.", "success");
    clear();
    load();
  }

  function clear() {
    chosen = null;
    $("wr-form").hidden = true;
    $("wr-note").value = "";
    $("wr-error").hidden = true;
  }

  async function loadTypeList() {
    const r = await VA.fetchJson(API + "/violation-types?all=1");
    ["STUDENT", "EMPLOYEE"].forEach(function (k) { $("wr-types-" + k).textContent = ""; });
    (r.ok ? r.body : []).forEach(function (t) {
      (t.applies_to === "BOTH" ? ["STUDENT", "EMPLOYEE"] : [t.applies_to]).forEach(function (k) {
        const li = el("li", t.is_active ? "" : "wr-off");
        li.appendChild(el("span", null, t.name));
        const b = el("button", "link-btn", t.is_active ? "Turn off" : "Turn on"); b.type = "button";
        b.addEventListener("click", async function () {
          const x = await VA.fetchJson(API + "/violation-types/" + t.id, json("PATCH", { is_active: !t.is_active }));
          if (!x.ok) { VA.toast(err(x, "Could not save."), "error"); return; }
          loadTypeList(); loadTypes();
        });
        li.appendChild(b);
        $("wr-types-" + k).appendChild(li);
      });
    });
  }

  /* ---------------- the list ---------------- */
  function card(w) {
    const c = el("article", "wr-card" + (w.is_final && w.status === "ACTIVE" ? " wr-final" : "") + (w.status === "WITHDRAWN" ? " wr-withdrawn" : ""));
    const head = el("div", "wr-head");
    const who = el("div");
    who.appendChild(el("strong", null, w.person.name));
    who.appendChild(el("span", "cc-meta", " · " + w.person.code + " · " + (w.kind === "STUDENT" ? "Student" : "Staff") + " · " + w.person.counting + " of " + meta.limit + " count now"));
    head.appendChild(who);
    head.appendChild(badge(w));
    c.appendChild(head);
    c.appendChild(el("p", "wr-violation", w.violation));
    c.appendChild(el("p", "cc-meta", "Happened on " + day(w.incident_date) + " · issued " + day(w.issued_on) + " · " + w.reference +
      (w.status === "ACTIVE" ? (w.counts ? " · counts until " + day(w.counts_until) : " · no longer counts") : "")));
    c.appendChild(el("p", "wr-note", w.note));
    if (w.status === "ACTIVE") c.appendChild(el("p", w.acknowledged_at ? "wr-read" : "cc-meta", w.acknowledged_at ? "✓ Read on " + day(w.acknowledged_at) : "Not read yet"));
    if (w.response) {
      const r = el("blockquote", "wr-reply");
      r.appendChild(el("strong", null, "Reply (" + day(w.responded_at) + "): "));
      r.appendChild(document.createTextNode(w.response));
      c.appendChild(r);
    }
    if (w.action && w.action !== "PENDING") c.appendChild(el("p", "wr-decision", "Decision: " + w.action_text +
      (w.fine_amount ? " Rs. " + Math.round(w.fine_amount).toLocaleString("en-PK") : "") + (w.action_note ? " — " + w.action_note : "")));
    if (w.lifted_at) c.appendChild(el("p", "cc-meta", "Suspension ended " + day(w.lifted_at) + (w.lift_note ? ": " + w.lift_note : "")));
    if (w.status === "WITHDRAWN") c.appendChild(el("p", "cc-meta", "Withdrawn " + day(w.withdrawn_at) + ": " + (w.withdraw_reason || "")));
    const act = el("div", "wr-actions-row");
    const a = el("a", "role-btn small-btn secondary-btn", "Letter");
    a.href = API + "/warnings/" + encodeURIComponent(w.id) + "/letter"; a.target = "_blank"; a.rel = "noopener";
    act.appendChild(a);
    if (w.action === "PENDING" && w.status === "ACTIVE") {
      const d = el("button", "role-btn small-btn", "Decide the action"); d.type = "button";
      d.addEventListener("click", function () { open(w, "decide"); });
      act.appendChild(d);
    }
    if (w.can_lift) {
      const l = el("button", "role-btn small-btn", "End suspension"); l.type = "button";
      l.addEventListener("click", function () { open(w, "lift"); });
      act.appendChild(l);
    }
    if (w.status === "ACTIVE" && (!w.decided_at || w.action === "NONE")) {
      const x = el("button", "role-btn small-btn secondary-btn", "Withdraw"); x.type = "button";
      x.addEventListener("click", function () { open(w, "withdraw"); });
      act.appendChild(x);
    }
    c.appendChild(act);
    return c;
  }

  function open(w, mode) {
    current = { w: w, mode: mode };
    $("wr-d-title").textContent = mode === "decide" ? "Decide the action" : mode === "lift" ? "End the suspension" : "Withdraw this warning";
    $("wr-d-who").textContent = w.person.name + " (" + w.person.code + ") · " + w.violation;
    const fs = $("wr-d-actions");
    fs.hidden = mode !== "decide";
    fs.querySelectorAll("label").forEach(function (l) { l.remove(); });
    if (mode === "decide") {
      meta.actions[w.kind].forEach(function (a, i) {
        const l = el("label", "wr-action");
        const r = el("input"); r.type = "radio"; r.name = "wr-action"; r.value = a; r.checked = i === 0;
        l.appendChild(r);
        const label = { NONE: "Final chance", SUSPEND: "Suspend", EXPEL: "Expel", TERMINATE: "Terminate", FINE: "Fine" }[a] || meta.action_text[a];
        const hint = a === "SUSPEND" ? "cannot sign in until you end it" + (w.kind === "STUDENT" ? " (status: Suspended)" : " (status: On leave)") :
          a === "EXPEL" ? "status: Dropped; cannot sign in" : a === "TERMINATE" ? "status: Terminated; cannot sign in" :
          a === "FINE" ? (w.kind === "STUDENT" ? "added to the student's fines" : "a fine on the staff member, like other staff fines") : "no action now; the next warning is final again";
        const sp = el("span", null, " ");
        sp.appendChild(el("strong", null, label));
        sp.appendChild(document.createTextNode(" — " + hint));
        l.appendChild(sp);
        r.addEventListener("change", function () { $("wr-d-amount-l").hidden = r.value !== "FINE" || !r.checked; });
        fs.appendChild(l);
      });
    }
    $("wr-d-amount-l").hidden = true;
    $("wr-d-amount").value = "";
    $("wr-d-note-label").textContent = mode === "decide" ? "Decision (the person reads it)" : mode === "lift" ? "Why does the suspension end?" : "Why is it withdrawn?";
    $("wr-d-note").value = "";
    $("wr-d-error").hidden = true;
    $("wr-d-ok").textContent = mode === "decide" ? "Apply decision" : mode === "lift" ? "End suspension" : "Withdraw";
    $("wr-dialog").showModal();
    $("wr-d-note").focus();
  }

  async function submitDialog(e) {
    e.preventDefault();
    const note = $("wr-d-note").value.trim();
    const w = current.w;
    let r;
    if (current.mode === "decide") {
      const a = document.querySelector('input[name="wr-action"]:checked').value;
      if (note.length < 10) { $("wr-d-error").textContent = "Write the decision (at least 10 characters)."; $("wr-d-error").hidden = false; return; }
      const amount = a === "FINE" ? Number($("wr-d-amount").value) : null;
      if (a === "FINE" && !(amount > 0)) { $("wr-d-error").textContent = "Enter the fine in rupees."; $("wr-d-error").hidden = false; $("wr-d-amount").focus(); return; }
      if (a !== "NONE" && !window.confirm(meta.action_text[a] + (amount ? " Rs. " + amount : "") + ": " + w.person.name + ". This changes their record. Continue?")) return;
      $("wr-d-ok").disabled = true;
      r = await VA.fetchJson(API + "/warnings/" + w.id + "/decide", json("POST", { action: a, note: note, amount: amount }));
    } else if (current.mode === "lift") {
      if (note.length < 5) { $("wr-d-error").textContent = "Give a short reason."; $("wr-d-error").hidden = false; return; }
      $("wr-d-ok").disabled = true;
      r = await VA.fetchJson(API + "/warnings/" + w.id + "/lift", json("POST", { note: note }));
    } else {
      if (note.length < 5) { $("wr-d-error").textContent = "Give a short reason."; $("wr-d-error").hidden = false; return; }
      $("wr-d-ok").disabled = true;
      r = await VA.fetchJson(API + "/warnings/" + w.id + "/withdraw", json("POST", { note: note }));
    }
    $("wr-d-ok").disabled = false;
    if (!r.ok) { $("wr-d-error").textContent = err(r, "Could not save."); $("wr-d-error").hidden = false; return; }
    $("wr-dialog").close();
    VA.toast(current.mode === "decide" ? "Decision applied. " + w.person.name + " has been told." : current.mode === "lift" ? "Suspension ended. " + w.person.name + " can sign in again."
      : "Withdrawn. It no longer counts.", "success");
    load();
  }

  async function load() {
    const k = $("wr-filter").value;
    const r = await VA.fetchJson(API + "/warnings?view=" + view + (k ? "&kind=" + k : ""));
    const list = $("wr-list");
    if (!r.ok) { list.textContent = err(r, "Could not load the warnings."); return; }
    meta = r.body;
    $("wr-c-pending").textContent = meta.pending || "";
    $("wr-c-replies").textContent = meta.replies || "";
    list.textContent = "";
    meta.warnings.forEach(function (w) { list.appendChild(card(w)); });
    $("wr-empty").hidden = meta.warnings.length > 0;
    $("wr-empty").textContent = view === "pending" ? "No final warning is waiting for a decision." : view === "replies" ? "No unanswered replies." : "No warnings yet.";
  }

  document.addEventListener("vistadmin:ready", function () {
    document.querySelectorAll("#wr-tabs .portal-tab").forEach(function (t) {
      t.addEventListener("click", function () {
        document.querySelectorAll("#wr-tabs .portal-tab").forEach(function (o) { o.classList.toggle("active", o === t); o.setAttribute("aria-selected", String(o === t)); });
        view = t.getAttribute("data-view");
        load();
      });
    });
    $("wr-filter").addEventListener("change", load);
    $("wr-d-form").addEventListener("submit", submitDialog);
    $("wr-d-cancel").addEventListener("click", function () { $("wr-dialog").close(); });
    if (canIssue) {
      $("wr-date").value = new Date().toISOString().slice(0, 10);
      $("wr-date").max = $("wr-date").value;
      document.querySelectorAll('input[name="wr-kind"]').forEach(function (r) { r.addEventListener("change", function () { clear(); $("wr-results").hidden = true; loadTypes(); search(); }); });
      $("wr-q").addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(search, 250); });
      $("wr-form").addEventListener("submit", issue);
      $("wr-clear").addEventListener("click", clear);
      $("wr-types").addEventListener("toggle", function () { if ($("wr-types").open) loadTypeList(); });
      $("wr-type-form").addEventListener("submit", async function (e) {
        e.preventDefault();
        const name = $("wr-type-name").value.trim();
        if (name.length < 3) { $("wr-type-name").focus(); return; }
        const r = await VA.fetchJson(API + "/violation-types", json("POST", { applies_to: $("wr-type-for").value, name: name }));
        if (!r.ok) { VA.toast(err(r, "Could not add."), "error"); return; }
        $("wr-type-name").value = "";
        loadTypeList(); loadTypes();
      });
      loadTypes();
    }
    load();
  });
})();
