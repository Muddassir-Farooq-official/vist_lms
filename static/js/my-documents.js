(function () {
  "use strict";
  // "My documents": a student or staff member uploads scans for the office to check, and sees each one's status.
  // The form carries data-blocked="CONTRACT,EXPERIENCE": kinds the office issues itself.
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  if (!$("md-form")) return;
  const err = function (r, f) { return (r.body && r.body.error && r.body.error.message) || f; };
  const blocked = ($("md-form").getAttribute("data-blocked") || "").split(",");

  async function docs() {
    const tb = $("md-table").tBodies[0];
    const r = await VA.fetchJson("/api/v1/records/me/documents");
    if (!r.ok) { tb.innerHTML = '<tr><td colspan="4">' + esc(err(r, "Could not load your documents.")) + "</td></tr>"; return; }
    $("md-empty").hidden = r.body.length > 0;
    tb.innerHTML = r.body.map(function (d) {
      return "<tr><td><strong>" + esc(d.title) + "</strong><br><small>" + esc(d.kind_label + " · " + d.file) + "</small></td><td>" + esc(new Date(d.uploaded_at).toLocaleDateString("en-GB")) +
        "</td><td>" + (d.verified ? '<span class="status-pill active">Checked</span>' : '<span class="status-pill pending">Waiting for the office</span>') +
        '</td><td><a class="role-btn small-btn secondary-btn" href="/api/v1/records/documents/' + esc(d.id) + '/file" data-download="' + esc(d.file) + '">Open</a></td></tr>';
    }).join("");
    document.dispatchEvent(new CustomEvent("my-documents:changed"));
  }

  $("md-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const er = $("md-error"); er.hidden = true;
    const f = $("md-file").files[0];
    if (!f) { er.textContent = "Choose the file to upload."; er.hidden = false; return; }
    const fd = new FormData(); fd.append("file", f); fd.append("kind", $("md-kind").value); fd.append("title", $("md-title").value.trim());
    const btn = $("md-form").querySelector("button[type=submit]"); btn.disabled = true;
    const r = await VA.fetchJson("/api/v1/records/me/documents", { method: "POST", body: fd });
    btn.disabled = false;
    if (!r.ok) { er.textContent = err(r, "Could not upload it."); er.hidden = false; return; }
    $("md-file").value = ""; $("md-title").value = "";
    VA.toast("Uploaded. The office will check it."); docs();
  });

  document.addEventListener("vistadmin:ready", async function () {
    const k = await VA.fetchJson("/api/v1/records/kinds", { quiet: true });
    const kinds = k.ok ? k.body.documents : { OTHER: "Other" };
    $("md-kind").innerHTML = Object.keys(kinds).filter(function (x) { return blocked.indexOf(x) === -1; })
      .map(function (x) { return '<option value="' + x + '">' + esc(kinds[x]) + "</option>"; }).join("");
    docs();
  });
})();