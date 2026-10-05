(function () {
  "use strict";

  const VA = window.VistAdmin;

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  // ---- Journal search ----
  const journalForm = document.getElementById("journal-search-form");
  const journalQueryInput = document.getElementById("journal-search-q");
  const journalSubmit = document.getElementById("journal-search-submit");
  const journalError = document.getElementById("journal-search-error");
  const journalSourceErrors = document.getElementById("journal-source-errors");
  const journalResults = document.getElementById("journal-results");
  const journalResultsEmpty = document.getElementById("journal-results-empty");

  function journalSourceLabel(source) {
    if (source === "pubmed") return "PubMed";
    if (source === "pmc") return "PMC";
    if (source === "doaj") return "DOAJ";
    return source;
  }

  journalForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    const query = journalQueryInput.value.trim();
    if (query.length < 2) return;

    const sources = Array.from(document.querySelectorAll(".journal-source:checked")).map(function (cb) { return cb.value; });
    if (sources.length === 0) {
      journalError.textContent = "Pick at least one source to search.";
      journalError.hidden = false;
      return;
    }

    journalSubmit.disabled = true;
    journalSubmit.textContent = "Searching...";
    journalError.hidden = true;
    journalSourceErrors.innerHTML = "";
    journalResultsEmpty.hidden = true;

    const params = new URLSearchParams();
    params.set("q", query);
    sources.forEach(function (s) { params.append("sources", s); });

    const result = await VA.fetchJson("/api/v1/library/journals/search?" + params.toString());

    journalSubmit.disabled = false;
    journalSubmit.textContent = "Search";

    if (!result.ok) {
      journalError.textContent = extractErrorMessage(result.body, "Search failed.");
      journalError.hidden = false;
      return;
    }

    const data = result.body;

    Object.keys(data.source_errors || {}).forEach(function (source) {
      const banner = document.createElement("div");
      banner.className = "source-error-banner";
      banner.textContent = journalSourceLabel(source) + " is unavailable right now: " + data.source_errors[source];
      journalSourceErrors.appendChild(banner);
    });

    journalResults.innerHTML = "";
    if (data.results.length === 0) {
      journalResultsEmpty.textContent = "No results found. Try a different search term.";
      journalResultsEmpty.hidden = false;
      return;
    }

    data.results.forEach(function (article) {
      const card = document.createElement("div");
      card.className = "journal-card";

      const metaParts = [];
      if (article.journal) metaParts.push(article.journal);
      if (article.year) metaParts.push(article.year);

      card.innerHTML =
        '<div class="journal-card-top">' +
          '<div class="journal-card-title">' + VA.escapeHtml(article.title) + "</div>" +
          '<span class="journal-source-badge ' + VA.escapeHtml(article.source) + '">' + journalSourceLabel(article.source) + "</span>" +
        "</div>" +
        '<div class="journal-card-meta">' + VA.escapeHtml(article.authors) +
          (metaParts.length ? " &middot; " + VA.escapeHtml(metaParts.join(", ")) : "") + "</div>" +
        (article.snippet ? '<div class="journal-card-snippet">' + VA.escapeHtml(article.snippet) + "</div>" : "") +
        '<div class="journal-card-actions"><a class="role-btn" href="' + VA.escapeHtml(article.url) + '" target="_blank" rel="noopener noreferrer">View \u2192</a></div>';

      journalResults.appendChild(card);
    });
  });

  // ---- E-Resources ----
  const eresourceGrid = document.getElementById("eresource-grid");

  async function loadEresources() {
    const result = await VA.fetchJson("/api/v1/library/journals/e-resources");
    if (!result.ok) return;

    eresourceGrid.innerHTML = "";
    result.body.forEach(function (r) {
      const card = document.createElement("div");
      card.className = "eresource-card";
      card.innerHTML =
        '<span class="eresource-category-badge">' + VA.escapeHtml(r.category) + "</span>" +
        '<div class="eresource-card-name">' + VA.escapeHtml(r.name) + "</div>" +
        '<div class="eresource-card-desc">' + VA.escapeHtml(r.description) +
          (r.searchable_in_app ? " Searchable directly above." : "") + "</div>" +
        '<div class="eresource-card-actions"><a class="role-btn" href="' + VA.escapeHtml(r.url) + '" target="_blank" rel="noopener noreferrer">Open \u2192</a></div>';
      eresourceGrid.appendChild(card);
    });
  }

  document.addEventListener("vistadmin:ready", function () {
    loadEresources();
  });
})();