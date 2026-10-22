(function () {
  "use strict";
  // Records for one student or employee: degree audit, clearance, certificates / ID card, documents on file.
  //   window.RecordsPanel.open({ type: "students" | "employees", id, name })
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/records";
  const STATUS = { PASSED: ["Passed", "active"], IN_PROGRESS: ["Studying now", "pending"], FAILED: ["Failed", "dropped"], NOT_TAKEN: ["Not taken", ""] };
  let dlg = null;
  let current = null;
  let kinds = null;

  function errText(r, fallback) { return (r.body && r.body.error && r.body.error.message) || fallback; }
  function day(iso) { return iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—"; }

  function build() {
    dlg = document.createElement("dialog");
    dlg.className = "modal modal-wide records-dialog";
    dlg.setAttribute("aria-labelledby", "rp-title");
    dlg.innerHTML = '<div class="modal-body"><h2 id="rp-title">Records</h2><p class="cc-meta" id="rp-sub"></p>' +
      '<div class="portal-tabs" role="tablist"><button type="button" class="portal-tab active" data-rp="clear" role="tab"><span>Clearance</span></button>' +
      '<button type="button" class="portal-tab" data-rp="audit" role="tab"><span>Degree audit</span></button>' +
      '<button type="button" class="portal-tab" data-rp="certs" role="tab"><span>Certificates &amp; ID card</span></button>' +
      '<button type="button" class="portal-tab" data-rp="docs" role="tab"><span>Documents</span></button></div>' +
      '<div data-rp-panel="clear"></div><div data-rp-panel="audit" hidden></div><div data-rp-panel="certs" hidden></div><div data-rp-panel="docs" hidden></div>' +
      '<p class="dash-alert error" id="rp-error" role="alert" hidden></p>' +
      '<button type="button" class="back-link modal-close-btn" id="rp-close">Close</button></div>';
    document.body.appendChild(dlg);
    dlg.querySelector("#rp-close").addEventListener("click", function () { dlg.close(); });
    dlg.querySelectorAll("[data-rp]").forEach(function (b) {
      b.addEventListener("click", function () { show(b.getAttribute("data-rp")); });
    });
  }

  function panel(name) { return dlg.querySelector('[data-rp-panel="' + name + '"]'); }
  function show(name) {
    dlg.querySelectorAll("[data-rp]").forEach(function (b) { const on = b.getAttribute("data-rp") === name; b.classList.toggle("active", on); b.setAttribute("aria-selected", on ? "true" : "false"); });
    dlg.querySelectorAll("[data-rp-panel]").forEach(function (p) { p.hidden = p.getAttribute("data-rp-panel") !== name; });
    ({ clear: loadClearance, audit: loadAudit, certs: loadCerts, docs: loadDocs })[name]();
  }
  function fail(name, r, retry) {
    const p = panel(name);
    p.innerHTML = '<p class="dash-alert error">' + esc(errText(r, "Could not load this.")) + ' <button type="button" class="link-btn">Try again</button></p>';
    p.querySelector("button").addEventListener("click", retry);
  }

  async function loadClearance() {
    const p = panel("clear");
    p.innerHTML = '<p class="cc-meta">Loading…</p>';
    const r = await VA.fetchJson(API + "/" + current.type + "/" + current.id + "/clearance");
    if (!r.ok) return fail("clear", r, loadClearance);
    const c = r.body;
    let html = c.clear ? '<p class="success-text">Nothing outstanding: cleared.</p>'
      : '<p class="dash-alert error">Not cleared yet:</p><ul>' + c.problems.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>";
    if (c.store_items && c.store_items.length) html += '<p class="cc-meta">Store items still out: ' + c.store_items.map(function (i) { return esc(i.quantity + " × " + i.item + " (" + i.reference + ")"); }).join(", ") + "</p>";
    if (c.to_settle && c.to_settle.length) html += '<h3 class="modal-subhead">When they leave</h3><ul>' + c.to_settle.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>";
    p.innerHTML = html;
  }

  async function loadAudit() {
    const p = panel("audit");
    if (current.type !== "students") { p.innerHTML = '<p class="cc-meta">Only students have a degree audit.</p>'; return; }
    p.innerHTML = '<p class="cc-meta">Loading…</p>';
    const r = await VA.fetchJson(API + "/students/" + current.id + "/degree-audit");
    if (!r.ok) return fail("audit", r, loadAudit);
    const a = r.body;
    let html = '<div class="kpi-grid"><div class="kpi"><div class="kpi-label">Credits passed</div><div class="kpi-value">' + a.credits_passed + " / " + a.credits_required + "</div></div>" +
      '<div class="kpi"><div class="kpi-label">CGPA</div><div class="kpi-value">' + (a.cgpa || 0).toFixed(2) + '</div><div class="kpi-sub">needs ' + a.min_cgpa.toFixed(2) + "</div></div>" +
      '<div class="kpi"><div class="kpi-label">Courses left</div><div class="kpi-value">' + a.missing.length + "</div></div></div>";
    html += a.clear ? '<p class="success-text">Clear to graduate.</p>' : '<p class="dash-alert error">' + a.problems.map(esc).join(" ") + "</p>";
    html += '<div class="table-scroll"><table class="tk-table no-skeleton"><thead><tr><th>Sem</th><th>Course</th><th class="num">Cr</th><th>Status</th></tr></thead><tbody>' +
      (a.courses.length ? a.courses.map(function (c) {
        const s = STATUS[c.status] || [c.status, ""];
        return "<tr><td>" + c.semester + "</td><td>" + esc(c.code + " " + c.title) + '</td><td class="num">' + c.credit_hours + '</td><td><span class="status-pill ' + s[1] + '">' + esc(s[0]) + "</span></td></tr>";
      }).join("") : '<tr><td colspan="4">The program has no curriculum to check against.</td></tr>') + "</tbody></table></div>";
    p.innerHTML = html;
  }

  async function issue(url, name) {
    let r = await VA.downloadFile(url, name, "certificate", { method: "POST" });
    if (r) { loadCerts(); return; }
    if ((VA.downloadFile.lastError || "").indexOf("give a reason") === -1 && (VA.downloadFile.lastError || "").indexOf("Give a reason") === -1) return;
    // not cleared? offer to issue with a reason
    const reason = await VA.ask({ title: "Issue it anyway?", intro: "If the problem shown cannot be cleared first, write why the certificate is being issued anyway. It is kept on record.", label: "Reason", min: 10 });
    if (!reason) return;
    const fd = new FormData(); fd.append("override_reason", reason);
    r = await VA.downloadFile(url, name, "certificate", { method: "POST", body: fd });
    if (r) loadCerts();
  }

  async function loadCerts() {
    const p = panel("certs");
    const stu = current.type === "students";
    p.innerHTML = '<div class="form-actions" style="flex-wrap:wrap;gap:8px">' +
      (stu ? '<button type="button" class="role-btn" data-c="BONAFIDE">Bonafide certificate</button><button type="button" class="role-btn" data-c="CHARACTER">Character certificate</button>' +
        '<button type="button" class="role-btn" data-c="COMPLETION">Completion certificate</button>' : '<button type="button" class="role-btn" data-c="EXPERIENCE">Experience certificate</button>') +
      '<button type="button" class="role-btn secondary-btn" data-c="CARD">ID card</button></div>' +
      '<p class="panel-hint">Each certificate and card gets a number and a QR code that anyone can check online. Issued ones are listed below and can be cancelled.</p><div id="rp-issued"><p class="cc-meta">Loading…</p></div>';
    p.querySelectorAll("[data-c]").forEach(function (b) {
      b.addEventListener("click", async function () {
        const k = b.getAttribute("data-c");
        if (k === "CARD") {
          const body = stu ? { student_ids: [current.id] } : { employee_ids: [current.id] };
          const ok = await VA.downloadFile(API + "/id-cards", "id-card.pdf", "ID card", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
          if (ok) loadCerts();
          return;
        }
        if (k === "EXPERIENCE") return issue(API + "/employees/" + current.id + "/certificates/experience", "experience-certificate.pdf");
        issue(API + "/students/" + current.id + "/certificates/" + k, k.toLowerCase() + "-certificate.pdf");
      });
    });
    const r = await VA.fetchJson(API + "/" + current.type + "/" + current.id + "/issued");
    const box = p.querySelector("#rp-issued");
    if (!r.ok) { box.innerHTML = '<p class="dash-alert error">' + esc(errText(r, "Could not load the list.")) + "</p>"; return; }
    box.innerHTML = r.body.length ? '<div class="table-scroll"><table class="tk-table no-skeleton"><thead><tr><th>Number</th><th>What</th><th>Issued</th><th>Check code</th><th></th></tr></thead><tbody>' +
      r.body.map(function (d) {
        return "<tr><td>" + esc(d.reference) + "</td><td>" + esc(d.kind_label) + (d.revoked ? ' <span class="status-pill dropped">cancelled</span>' : "") + "</td><td>" + esc(day(d.issued_at)) +
          '</td><td><a href="/verify/' + esc(d.code) + '" target="_blank" rel="noopener">' + esc(d.code) + "</a></td><td>" +
          (d.revoked ? "" : '<button type="button" class="role-btn small-btn secondary-btn" data-revoke="' + esc(d.id) + '">Cancel</button>') + "</td></tr>";
      }).join("") + "</tbody></table></div>" : '<p class="cc-meta">Nothing issued yet.</p>';
    box.querySelectorAll("[data-revoke]").forEach(function (b) {
      b.addEventListener("click", async function () {
        const reason = await VA.ask({ title: "Cancel this certificate?", intro: "The online check will show it as cancelled.", label: "Reason", min: 5, danger: true, ok: "Cancel it" });
        if (!reason) return;
        const x = await VA.fetchJson(API + "/issued/" + b.getAttribute("data-revoke") + "/revoke", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: reason }) });
        if (!x.ok) return VA.toast(errText(x, "Could not cancel it."), "error");
        loadCerts();
      });
    });
  }

  async function loadDocs() {
    const p = panel("docs");
    if (!kinds) {
      const k = await VA.fetchJson(API + "/kinds", { quiet: true });
      kinds = k.ok ? k.body.documents : { OTHER: "Other" };
    }
    p.innerHTML = '<form class="ann-toolbar" id="rp-up" novalidate><select class="select-input" id="rp-kind" aria-label="Kind of document">' +
      Object.keys(kinds).map(function (k) { return '<option value="' + k + '">' + esc(kinds[k]) + "</option>"; }).join("") + "</select>" +
      '<input class="text-input" id="rp-dtitle" maxlength="150" placeholder="Title (optional)" aria-label="Title"><input type="file" id="rp-file" class="text-input" aria-label="File" accept=".pdf,.jpg,.jpeg,.png,.docx">' +
      '<button type="submit" class="role-btn">Add</button></form><div id="rp-docs"><p class="cc-meta">Loading…</p></div>';
    p.querySelector("#rp-up").addEventListener("submit", async function (e) {
      e.preventDefault();
      const f = p.querySelector("#rp-file").files[0];
      if (!f) return VA.toast("Choose the file first.", "error");
      const fd = new FormData(); fd.append("file", f); fd.append("kind", p.querySelector("#rp-kind").value); fd.append("title", p.querySelector("#rp-dtitle").value);
      const r = await VA.fetchJson(API + "/" + current.type + "/" + current.id + "/documents", { method: "POST", body: fd });
      if (!r.ok) return VA.toast(errText(r, "Could not add it."), "error");
      VA.toast("Added."); loadDocs();
    });
    const r = await VA.fetchJson(API + "/" + current.type + "/" + current.id + "/documents?include_archived=true");
    const box = p.querySelector("#rp-docs");
    if (!r.ok) { box.innerHTML = '<p class="dash-alert error">' + esc(errText(r, "Could not load the documents.")) + "</p>"; return; }
    box.innerHTML = r.body.length ? '<div class="table-scroll"><table class="tk-table no-skeleton"><thead><tr><th>Document</th><th>Added</th><th>Checked</th><th></th></tr></thead><tbody>' +
      r.body.map(function (d) {
        return "<tr" + (d.archived ? ' class="muted-row"' : "") + "><td><strong>" + esc(d.title) + "</strong><small>" + esc(d.kind_label + " · " + d.file) + (d.archived ? " · archived: " + esc(d.archive_reason || "") : "") + "</small></td><td>" + esc(day(d.uploaded_at)) +
          "</td><td>" + (d.verified ? '<span class="status-pill active">checked</span>' : '<span class="status-pill pending">not checked</span>') + '</td><td class="row-actions">' +
          '<a class="role-btn small-btn secondary-btn" href="' + API + "/documents/" + esc(d.id) + '/file" data-download="' + esc(d.file) + '">Open</a>' +
          (d.archived ? "" : (d.verified ? "" : '<button type="button" class="role-btn small-btn" data-verify="' + esc(d.id) + '">Checked against original</button>') +
            '<button type="button" class="role-btn small-btn secondary-btn" data-archive="' + esc(d.id) + '">Archive</button>') + "</td></tr>";
      }).join("") + "</tbody></table></div>" : '<p class="cc-meta">No documents on file yet.</p>';
    box.querySelectorAll("[data-verify]").forEach(function (b) {
      b.addEventListener("click", async function () {
        const x = await VA.fetchJson(API + "/documents/" + b.getAttribute("data-verify") + "/verify", { method: "POST" });
        if (!x.ok) return VA.toast(errText(x, "Could not mark it."), "error");
        loadDocs();
      });
    });
    box.querySelectorAll("[data-archive]").forEach(function (b) {
      b.addEventListener("click", async function () {
        const reason = await VA.ask({ title: "Archive this document?", intro: "It stays on record but no longer shows as current.", label: "Reason", min: 4 });
        if (!reason) return;
        const x = await VA.fetchJson(API + "/documents/" + b.getAttribute("data-archive") + "/archive", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: reason }) });
        if (!x.ok) return VA.toast(errText(x, "Could not archive it."), "error");
        loadDocs();
      });
    });
  }

  window.RecordsPanel = {
    open: function (who) {
      if (!dlg) build();
      current = who;
      dlg.querySelector("#rp-title").textContent = "Records: " + (who.name || "");
      dlg.querySelector("#rp-sub").textContent = who.type === "students" ? "Clearance, degree audit, certificates, ID card and documents on file." : "Clearance for leaving, experience certificate, ID card and documents on file.";
      dlg.querySelector('[data-rp="audit"]').hidden = who.type !== "students";
      dlg.showModal();
      show("clear");
    },
  };
})();