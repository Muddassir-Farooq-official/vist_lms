(function () {
  "use strict";
  // Profile completion: how many of the details and documents the office needs are on file.
  //   ProfileCompleteness.fill(tbody, "students" | "employees")  fills every <td data-pc="id"> in a list
  //   ProfileCompleteness.card(el, result)                        a bar + "still missing" list
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;

  function tone(p) { return p >= 100 ? "ok" : p >= 60 ? "mid" : "low"; }
  function bar(p) { return '<span class="pc-meter ' + tone(p) + '"><i style="width:' + p + '%"></i></span>'; }

  async function fill(tbody, type) {
    const cells = Array.prototype.slice.call(tbody.querySelectorAll("td[data-pc]"));
    if (!cells.length) return;
    const r = await VA.fetchJson("/api/v1/records/completeness", { method: "POST", quiet: true, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: type, ids: cells.map(function (c) { return c.getAttribute("data-pc"); }) }) });
    cells.forEach(function (c) {
      const x = r.ok ? r.body[c.getAttribute("data-pc")] : null;
      if (!x) { c.innerHTML = '<span class="sd-dim">—</span>'; return; }
      const tip = x.missing.length ? "Missing: " + x.missing.join(", ") : "Everything is on file";
      c.innerHTML = '<div class="pc-cell" title="' + esc(tip) + '">' + bar(x.percent) + "<b>" + x.percent + "%</b></div>";
    });
  }

  function card(el, x, own, hint) {
    if (!el || !x) return;
    el.hidden = false;
    el.innerHTML = '<div class="pc-card ' + tone(x.percent) + '"><div class="pc-card-top"><strong>' + (own ? "Your profile is " : "Profile ") + x.percent + "% complete</strong>" +
      "<span>" + x.done + " of " + x.total + " on file</span></div>" + bar(x.percent) +
      (x.missing.length ? '<p class="pc-missing">Still missing: ' + x.missing.map(esc).join(", ") + (own ? ". " + (hint || "Add them under Update my details and Documents.") : ".") + "</p>"
        : '<p class="pc-missing">Everything the office needs is on file.</p>') + "</div>";
  }

  // ---- "Incomplete profiles": everyone active below a %, with one button to remind them all (notification + email)
  let dlg = null, dlgType = null;
  function build() {
    dlg = document.createElement("dialog");
    dlg.className = "modal modal-wide";
    dlg.setAttribute("aria-labelledby", "ic-title");
    dlg.innerHTML = '<div class="modal-body"><h2 id="ic-title">Incomplete profiles</h2>' +
      '<div class="ann-toolbar"><label class="cc-meta">Show <select class="select-input" id="ic-below"><option value="100">everyone not at 100%</option>' +
      '<option value="80">below 80%</option><option value="50">below 50%</option></select></label>' +
      '<button type="button" class="role-btn" id="ic-remind">Send reminders</button></div>' +
      '<p class="cc-meta" id="ic-sum"></p><div class="table-scroll" style="max-height:55vh"><table class="tk-table no-skeleton"><thead><tr><th>Name</th><th>Profile</th><th>Still missing</th></tr></thead>' +
      '<tbody id="ic-body"></tbody></table></div><button type="button" class="back-link modal-close-btn" id="ic-close">Close</button></div>';
    document.body.appendChild(dlg);
    dlg.querySelector("#ic-close").addEventListener("click", function () { dlg.close(); });
    dlg.querySelector("#ic-below").addEventListener("change", list);
    dlg.querySelector("#ic-remind").addEventListener("click", async function () {
      const below = dlg.querySelector("#ic-below").value;
      if (!(await VA.confirm("Send each person on this list a notification (and email) saying exactly what is missing? Anyone reminded in the last 3 days is skipped.", "Send reminders"))) return;
      const r = await VA.fetchJson("/api/v1/records/completeness/remind", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: dlgType, below: parseInt(below, 10) }) });
      if (!r.ok) return VA.toast("Could not send the reminders.", "error");
      const x = r.body;
      VA.toast(x.sent + " reminder(s) sent" + (x.skipped_recent ? ", " + x.skipped_recent + " already reminded recently" : "") + (x.no_login ? ", " + x.no_login + " have no login" : "") + ".");
    });
  }
  async function list() {
    const tb = dlg.querySelector("#ic-body");
    tb.innerHTML = '<tr><td colspan="3">Loading…</td></tr>';
    const r = await VA.fetchJson("/api/v1/records/completeness/incomplete?type=" + dlgType + "&below=" + dlg.querySelector("#ic-below").value);
    if (!r.ok) { tb.innerHTML = '<tr><td colspan="3">Could not load the list.</td></tr>'; return; }
    dlg.querySelector("#ic-sum").textContent = r.body.length + " active " + (dlgType === "students" ? "student" : "staff member") + (r.body.length === 1 ? "" : "s") + " on this list.";
    dlg.querySelector("#ic-remind").disabled = !r.body.length;
    tb.innerHTML = r.body.length ? r.body.map(function (x) {
      return "<tr><td><strong>" + esc(x.name) + "</strong><br><small>" + esc(x.code || "") + (x.has_login ? "" : " · no login") + '</small></td><td><div class="pc-cell">' + bar(x.percent) + "<b>" + x.percent +
        "%</b></div></td><td><small>" + esc(x.missing.join(", ")) + "</small></td></tr>";
    }).join("") : '<tr><td colspan="3">Everyone on this list is complete.</td></tr>';
  }
  function openIncomplete(type) {
    if (!dlg) build();
    dlgType = type;
    dlg.querySelector("#ic-title").textContent = "Incomplete profiles: " + (type === "students" ? "students" : "staff");
    dlg.showModal(); list();
  }
  document.addEventListener("click", function (e) {
    const b = e.target.closest && e.target.closest("[data-incomplete]");
    if (b) { const m = b.closest("details"); if (m) m.open = false; openIncomplete(b.getAttribute("data-incomplete")); }
  });

  window.ProfileCompleteness = { fill: fill, card: card, openIncomplete: openIncomplete };
})();