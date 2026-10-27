(function () {
  "use strict";

  // Academic terms: fills every <select class="term-select"> with the OPEN terms (and their dates),
  // and runs the Academic terms tab on the Courses page (add, edit dates / name / status, close and reopen).
  // API: /api/v1/academics/academic-terms
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/academics/academic-terms";
  const $ = function (id) { return document.getElementById(id); };
  const USED = { offerings: ["course offering", "course offerings"], fee_structures: ["fee structure", "fee structures"],
                 datesheets: ["datesheet", "datesheets"], admission_rounds: ["admission round", "admission rounds"] };
  let terms = [], editing = null;
  window.VistTerms = { open: function () { return terms.filter(function (t) { return t.status === "OPEN"; }); } };

  function fillSelects() {
    document.querySelectorAll("select.term-select").forEach(function (sel) {
      const keep = sel.value;
      sel.innerHTML = '<option value="">Select a term…</option>' + window.VistTerms.open().map(function (t) {
        return '<option value="' + esc(t.label) + '">' + esc(t.label) + (t.start_date ? " (" + esc(t.start_date) + " – " + esc(t.end_date || "?") + ")" : "") + "</option>";
      }).join("");
      if (keep) sel.value = keep;
      if (!sel.dataset.wired) {
        sel.dataset.wired = "1";
        sel.addEventListener("change", function () {     // a chosen term fills in its own dates
          const t = terms.find(function (x) { return x.label === sel.value; });
          if (!t) return;
          [["start", t.start_date], ["end", t.end_date]].forEach(function (pair) {
            const input = sel.dataset[pair[0]] && document.getElementById(sel.dataset[pair[0]]);
            if (input && pair[1]) input.value = pair[1];
          });
        });
      }
    });
  }

  function fmt(d) { return d ? new Date(d + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—"; }
  function weeks(a, b) { return a && b ? Math.round((new Date(b) - new Date(a)) / (7 * 864e5)) : null; }
  function usedCount(t) { return Object.values(t.used_by || {}).reduce(function (n, v) { return n + v; }, 0); }
  function current(t) {
    const today = new Date().toISOString().slice(0, 10);
    return t.status === "OPEN" && t.start_date && t.end_date && t.start_date <= today && today <= t.end_date;
  }

  function renderTable() {
    const tbody = $("terms-tbody");
    if (!tbody) return;
    tbody.innerHTML = terms.map(function (t) {
      const w = weeks(t.start_date, t.end_date);
      const used = Object.keys(USED).filter(function (k) { return (t.used_by || {})[k]; }).map(function (k) {
        const n = t.used_by[k]; return '<span class="crs-chip"><b>' + n + "</b> " + esc(USED[k][n === 1 ? 0 : 1]) + "</span>";
      }).join("") || '<span class="sd-dim">Not used yet</span>';
      return '<tr><td><span class="crs-title"><strong>' + esc(t.label) + (current(t) ? ' <em class="crs-now">Current</em>' : "") + "</strong></span></td>" +
        '<td><span class="crs-title"><span>' + esc(fmt(t.start_date)) + " – " + esc(fmt(t.end_date)) + "</span>" + (w ? "<small>" + w + " weeks</small>" : "") + "</span></td>" +
        '<td><div class="crs-chips">' + used + "</div></td>" +
        "<td>" + (t.status === "OPEN" ? '<span class="status-pill active">Open</span>' : '<span class="status-pill dropped">Closed</span>') + "</td>" +
        '<td class="crs-actions"><button type="button" class="sd-view" data-edit="' + esc(t.id) + '">Edit</button>' +
        '<button type="button" class="role-btn small-btn secondary-btn" data-id="' + esc(t.id) + '" data-next="' + (t.status === "OPEN" ? "CLOSED" : "OPEN") + '">' +
        (t.status === "OPEN" ? "Close" : "Reopen") + "</button></td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">No terms yet. Add the current term above.</td></tr>';
  }

  function showError(body, box) {
    box = box || $("terms-error");
    if (!box) return;
    box.textContent = (body && body.error && body.error.message) || "That did not work.";
    box.hidden = false;
  }

  async function load() {
    const r = await VA.fetchJson(API);
    if (!r.ok) return;
    terms = r.body;
    fillSelects();
    renderTable();
  }

  const tbody = $("terms-tbody");
  if (tbody) {
    tbody.addEventListener("click", async function (e) {
      const toggle = e.target.closest("button[data-next]");
      if (toggle) {
        const t = terms.find(function (x) { return x.id === toggle.dataset.id; });
        if (toggle.dataset.next === "CLOSED" && !(await VA.confirm('Close "' + t.label + '"? It can no longer be used for new offerings or fees. You can reopen it later.', "Close term"))) return;
        const r = await VA.fetchJson(API + "/" + toggle.dataset.id, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: toggle.dataset.next }) });
        if (!r.ok) return showError(r.body);
        VA.toast(toggle.dataset.next === "CLOSED" ? "Term closed." : "Term reopened.");
        return load();
      }
      const edit = e.target.closest("button[data-edit]");
      if (!edit) return;
      editing = terms.find(function (x) { return x.id === edit.dataset.edit; });
      const n = usedCount(editing);
      $("te-label").value = editing.label;
      $("te-label").disabled = false;
      $("te-start").value = editing.start_date || "";
      $("te-end").value = editing.end_date || "";
      $("te-status").value = editing.status;
      $("term-edit-hint").textContent = n > 0
        ? "A new name is written on all " + n + " record" + (n === 1 ? "" : "s") + " that use this term, in one step. Challans, receipts and certificates already printed keep the old name on paper."
        : "Nothing uses this term yet.";
      $("term-edit-error").hidden = true;
      $("term-edit").showModal();
    });
  }

  const editForm = $("term-edit-form");
  if (editForm) {
    $("term-edit-close").addEventListener("click", function () { $("term-edit").close(); });
    editForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      if (!editing) return;
      const body = { start_date: $("te-start").value || null, end_date: $("te-end").value || null, status: $("te-status").value };
      const label = $("te-label").value.trim();
      if (label && label !== editing.label) {
        const n = usedCount(editing);
        if (n && !(await VA.confirm('Rename "' + editing.label + '" to "' + label + '" on all ' + n + " records that use it?", "Rename"))) return;
        body.label = label;
      }
      if (!body.start_date || !body.end_date) return showError({ error: { message: "Give both the start and the end date." } }, $("term-edit-error"));
      $("term-edit-save").disabled = true;
      const r = await VA.fetchJson(API + "/" + editing.id, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      $("term-edit-save").disabled = false;
      if (!r.ok) return showError(r.body, $("term-edit-error"));
      $("term-edit").close();
      VA.toast(body.label ? "Term renamed everywhere it is used." : "Term saved.");
      load();
      document.dispatchEvent(new CustomEvent("terms:changed"));
    });
  }

  const addForm = $("term-add-form");
  if (addForm) {
    addForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      $("terms-error").hidden = true;
      const body = { label: $("term-new-label").value.trim(), start_date: $("term-new-start").value, end_date: $("term-new-end").value };
      if (!body.label || !body.start_date || !body.end_date) return showError({ error: { message: "Give the term name, start date and end date." } });
      const r = await VA.fetchJson(API, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) return showError(r.body);
      addForm.reset();
      VA.toast("Term added.");
      load();
      document.dispatchEvent(new CustomEvent("terms:changed"));
    });
  }

  document.addEventListener("vistadmin:ready", load);
})();