(function () {
  "use strict";

  // Academic terms: fills every <select class="term-select"> with the OPEN terms (and their dates),
  // and runs the Academic Terms panel on the Courses page. API: /api/v1/academics/academic-terms
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/academics/academic-terms";
  let terms = [];
  window.VistTerms = { open: function () { return terms.filter(function (t) { return t.status === "OPEN"; }); } };

  function fillSelects() {
    document.querySelectorAll("select.term-select").forEach(function (sel) {
      const keep = sel.value;
      sel.innerHTML = '<option value="">Select a term\u2026</option>' + window.VistTerms.open().map(function (t) {
        return '<option value="' + esc(t.label) + '">' + esc(t.label) + (t.start_date ? " (" + esc(t.start_date) + " \u2013 " + esc(t.end_date || "?") + ")" : "") + "</option>";
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

  function renderTable() {
    const tbody = document.getElementById("terms-tbody");
    if (!tbody) return;
    tbody.innerHTML = terms.map(function (t) {
      const next = t.status === "OPEN" ? "CLOSED" : "OPEN";
      return "<tr><td>" + esc(t.label) + "</td><td>" + esc(t.start_date || "\u2014") + "</td><td>" + esc(t.end_date || "\u2014") + "</td><td>" +
        esc(t.status === "OPEN" ? "Open" : "Closed") + '</td><td><button type="button" class="row-action" data-id="' + esc(t.id) + '" data-next="' + next + '">' +
        (next === "CLOSED" ? "Close" : "Reopen") + "</button></td></tr>";
    }).join("") || '<tr><td colspan="5">No terms yet. Add the current term above.</td></tr>';
    tbody.querySelectorAll("button[data-id]").forEach(function (b) {
      b.addEventListener("click", async function () {
        if (b.dataset.next === "CLOSED" && !window.confirm("Close this term? It can no longer be used for new offerings or fees.")) return;
        const r = await VA.fetchJson(API + "/" + b.dataset.id, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: b.dataset.next }) });
        if (!r.ok) return showError(r.body);
        load();
      });
    });
  }

  function showError(body) {
    const box = document.getElementById("terms-error");
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

  const addBtn = document.getElementById("term-add-btn");
  if (addBtn) {
    addBtn.addEventListener("click", async function () {
      document.getElementById("terms-error").hidden = true;
      const body = { label: document.getElementById("term-new-label").value.trim(), start_date: document.getElementById("term-new-start").value,
                     end_date: document.getElementById("term-new-end").value };
      if (!body.label || !body.start_date || !body.end_date) return showError({ error: { message: "Give the term name, start date and end date." } });
      const r = await VA.fetchJson(API, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) return showError(r.body);
      ["term-new-label", "term-new-start", "term-new-end"].forEach(function (id) { document.getElementById(id).value = ""; });
      load();
    });
  }

  document.addEventListener("vistadmin:ready", load);
})();