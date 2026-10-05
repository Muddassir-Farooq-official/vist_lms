(function () {
  "use strict";

  const VA = window.VistAdmin;

  // =========================================================================
  // Tabs
  // =========================================================================
  const tabButtons = document.querySelectorAll(".portal-tab");
  const tabPanels = document.querySelectorAll(".portal-tab-panel");

  function activateTab(tabName) {
    tabButtons.forEach(function (btn) {
      btn.classList.toggle("active", btn.dataset.tab === tabName);
    });
    tabPanels.forEach(function (panel) {
      panel.hidden = panel.dataset.tabPanel !== tabName;
    });
  }

  tabButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      activateTab(btn.dataset.tab);
    });
  });

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  function statusPillHtml(status) {
    return '<span class="status-pill ' + (status || "").toLowerCase() + '">' + VA.escapeHtml(status) + "</span>";
  }

  // =========================================================================
  // My Library — active/history loans, my requests
  // =========================================================================
  const activeTbody = document.getElementById("active-loans-tbody");
  const activeEmpty = document.getElementById("active-loans-empty");
  const historyTbody = document.getElementById("history-loans-tbody");
  const historyEmpty = document.getElementById("history-loans-empty");
  const requestsTbody = document.getElementById("requests-tbody");
  const requestsEmpty = document.getElementById("requests-empty");

  async function loadLoans() {
    const result = await VA.fetchJson("/api/v1/library/me/loans");
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load your loans."));
      return;
    }
    VA.clearError();

    const d = result.body;

    activeTbody.innerHTML = "";
    if (d.active.length === 0) {
      activeEmpty.hidden = false;
    } else {
      activeEmpty.hidden = true;
      d.active.forEach(function (loan) {
        const tr = document.createElement("tr");
        const titleLabel = loan.book_title + (loan.is_journal ? " (Journal)" : "");
        const statusLabel = loan.is_overdue ? "OVERDUE" : loan.status;
        tr.innerHTML =
          "<td>" + VA.escapeHtml(titleLabel) + "</td>" +
          "<td>" + VA.escapeHtml(loan.accession_code) + "</td>" +
          "<td>" + VA.escapeHtml(loan.issued_at) + "</td>" +
          "<td>" + VA.escapeHtml(loan.due_date) + "</td>" +
          "<td>" + statusPillHtml(statusLabel) + "</td>" +
          '<td class="row-actions"></td>';

        if (!loan.is_overdue && loan.can_renew) {
          const renewBtn = document.createElement("button");
          renewBtn.className = "role-btn small-btn";
          renewBtn.type = "button";
          renewBtn.textContent = "Renew";
          renewBtn.addEventListener("click", async function () {
            renewBtn.disabled = true;
            const res = await VA.fetchJson("/api/v1/library/me/loans/" + loan.issue_id + "/renew", { method: "POST" });
            if (res.ok) {
              loadLoans();
            } else {
              renewBtn.disabled = false;
              VA.showError(extractErrorMessage(res.body, "Could not renew."));
            }
          });
          tr.querySelector(".row-actions").appendChild(renewBtn);
        }

        activeTbody.appendChild(tr);
      });
    }

    historyTbody.innerHTML = "";
    if (d.history.length === 0) {
      historyEmpty.hidden = false;
    } else {
      historyEmpty.hidden = true;
      d.history.forEach(function (loan) {
        const titleLabel = loan.book_title + (loan.is_journal ? " (Journal)" : "");
        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" + VA.escapeHtml(titleLabel) + "</td>" +
          "<td>" + VA.escapeHtml(loan.issued_at) + "</td>" +
          "<td>" + VA.escapeHtml(loan.returned_at || "\u2014") + "</td>" +
          "<td>" + statusPillHtml(loan.status) + "</td>";
        historyTbody.appendChild(tr);
      });
    }
  }

  async function loadRequests() {
    const result = await VA.fetchJson("/api/v1/library/me/requests");
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load your requests."));
      return;
    }

    const items = result.body;
    requestsTbody.innerHTML = "";
    if (items.length === 0) {
      requestsEmpty.hidden = false;
      return;
    }
    requestsEmpty.hidden = true;

    items.forEach(function (r) {
      const tr = document.createElement("tr");
      const titleLabel = r.book_title + (r.is_journal ? " (Journal)" : "");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(titleLabel) + "</td>" +
        "<td>" + VA.escapeHtml(r.note || "\u2014") + "</td>" +
        "<td>" + statusPillHtml(r.status) + "</td>" +
        '<td class="row-actions"></td>';

      if (r.status === "PENDING") {
        const cancelBtn = document.createElement("button");
        cancelBtn.className = "danger-btn small-btn";
        cancelBtn.type = "button";
        cancelBtn.textContent = "Cancel";
        cancelBtn.addEventListener("click", async function () {
          cancelBtn.disabled = true;
          const res = await VA.fetchJson("/api/v1/library/me/requests/" + r.id + "/cancel", { method: "POST" });
          if (res.ok) {
            loadRequests();
          } else {
            cancelBtn.disabled = false;
          }
        });
        tr.querySelector(".row-actions").appendChild(cancelBtn);
      }

      requestsTbody.appendChild(tr);
    });
  }

  async function submitBorrowRequest(bookId, note) {
    return VA.fetchJson("/api/v1/library/me/requests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ book_id: bookId, note: note || null }),
    });
  }

  // =========================================================================
  // Catalogue — e-commerce style browse / search / filter / request
  // =========================================================================
  const catSearchInput = document.getElementById("cat-search");
  const catAvailableOnly = document.getElementById("cat-available-only");
  const bookGrid = document.getElementById("book-grid");
  const bookGridEmpty = document.getElementById("book-grid-empty");
  const catPrevBtn = document.getElementById("cat-prev-page");
  const catNextBtn = document.getElementById("cat-next-page");
  const catPageInfo = document.getElementById("cat-page-info");

  const borrowRequestModal = document.getElementById("borrow-request-modal");
  const borrowRequestBookTitle = document.getElementById("borrow-request-book-title");
  const borrowRequestForm = document.getElementById("borrow-request-form");
  const borrowRequestNote = document.getElementById("borrow-request-note");
  const borrowRequestSubmit = document.getElementById("borrow-request-submit");
  const borrowRequestError = document.getElementById("borrow-request-error");
  const closeBorrowRequestModalBtn = document.getElementById("close-borrow-request-modal");

  let pendingBorrowBookId = null;
  let pendingBorrowButton = null;

  const CAT_PAGE_SIZE = 12;
  let catCurrentPage = 1;
  let catTotalPages = 1;
  let catSearchDebounce = null;

  function bookCoverHtml(book) {
    if (book.cover_image_url) {
      return '<img src="' + VA.escapeHtml(book.cover_image_url) + '" alt="" loading="lazy" />';
    }
    const initial = (book.title || "?").trim().charAt(0).toUpperCase() || "?";
    return VA.escapeHtml(initial);
  }

  async function loadCatalogue() {
    const params = new URLSearchParams();
    params.set("page", String(catCurrentPage));
    params.set("page_size", String(CAT_PAGE_SIZE));
    params.set("is_journal", "false"); // Catalogue tab is books only — journals live entirely in the Journals tab now.
    if (catSearchInput.value.trim()) params.set("q", catSearchInput.value.trim());

    const result = await VA.fetchJson("/api/v1/library/books?" + params.toString());
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load the catalogue."));
      return;
    }
    VA.clearError();

    let items = result.body.items;
    if (catAvailableOnly.checked) {
      items = items.filter(function (b) { return b.available_copies > 0; });
    }

    catTotalPages = Math.max(1, Math.ceil(result.body.total / result.body.page_size));
    catPageInfo.textContent = "Page " + catCurrentPage + " of " + catTotalPages;
    catPrevBtn.disabled = catCurrentPage <= 1;
    catNextBtn.disabled = catCurrentPage >= catTotalPages;

    bookGrid.innerHTML = "";
    if (items.length === 0) {
      bookGridEmpty.hidden = false;
      return;
    }
    bookGridEmpty.hidden = true;

    items.forEach(function (book) {
      const available = book.available_copies > 0;
      const card = document.createElement("div");
      card.className = "book-card";

      const badgeHtml = available
        ? '<span class="book-availability-badge in-stock">' + book.available_copies + " available</span>"
        : '<span class="book-availability-badge out-of-stock">Currently unavailable</span>';

      card.innerHTML =
        '<div class="book-card-cover">' + bookCoverHtml(book) + "</div>" +
        '<div class="book-card-body">' +
          '<div class="book-card-title">' + VA.escapeHtml(book.title) + "</div>" +
          '<div class="book-card-author">' + VA.escapeHtml(book.author) + "</div>" +
          '<div class="book-card-badges">' + badgeHtml + "</div>" +
          '<div class="book-card-actions"><button class="role-btn" type="button">Request to Borrow</button></div>' +
        "</div>";

      card.querySelector(".book-card-cover").style.cursor = "pointer";
      card.querySelector(".book-card-title").style.cursor = "pointer";
      card.querySelector(".book-card-cover").addEventListener("click", function () {
        openBookDetailModal(book);
      });
      card.querySelector(".book-card-title").addEventListener("click", function () {
        openBookDetailModal(book);
      });

      const btn = card.querySelector("button");
      btn.addEventListener("click", function () {
        openBorrowRequestModal(book.id, book.title, btn);
      });

      bookGrid.appendChild(card);
    });
  }

  function openBorrowRequestModal(bookId, title, triggerButton) {
    pendingBorrowBookId = bookId;
    pendingBorrowButton = triggerButton || null;
    borrowRequestBookTitle.textContent = title;
    borrowRequestNote.value = "";
    borrowRequestError.hidden = true;
    borrowRequestSubmit.disabled = false;
    borrowRequestSubmit.textContent = "Submit Request";
    borrowRequestModal.showModal();
  }

  // ---- Book detail modal ----
  const bookDetailModal = document.getElementById("book-detail-modal");
  const bookDetailTitle = document.getElementById("book-detail-title");
  const bookDetailCoverWrap = document.getElementById("book-detail-cover-wrap");
  const bookDetailFields = document.getElementById("book-detail-fields");
  const bookDetailAvailability = document.getElementById("book-detail-availability");
  const bookDetailRequestBtn = document.getElementById("book-detail-request-btn");
  const closeBookDetailModalBtn = document.getElementById("close-book-detail-modal");

  function openBookDetailModal(book) {
    bookDetailTitle.textContent = book.title;
    bookDetailCoverWrap.innerHTML = book.cover_image_url
      ? '<img src="' + VA.escapeHtml(book.cover_image_url) + '" alt="" />'
      : '<div class="book-card-cover">' + bookCoverHtml(book) + "</div>";

    const fields = [
      ["Author", book.author],
      ["Category", book.category],
      ["Publisher", book.publisher],
      ["Publication Year", book.publication_year],
      ["ISBN", book.isbn],
      ["Shelf Location", book.shelf_location],
      ["Description", book.description],
    ];
    bookDetailFields.innerHTML = fields
      .filter(function (f) { return f[1]; })
      .map(function (f) { return "<dt>" + f[0] + "</dt><dd>" + VA.escapeHtml(f[1]) + "</dd>"; })
      .join("");

    const available = book.available_copies > 0;
    bookDetailAvailability.textContent = available
      ? book.available_copies + " cop" + (book.available_copies === 1 ? "y" : "ies") + " available right now."
      : "Currently unavailable — you can still request it and the librarian will let you know when a copy is free.";

    bookDetailRequestBtn.onclick = function () {
      bookDetailModal.close();
      openBorrowRequestModal(book.id, book.title, null);
    };

    bookDetailModal.showModal();
  }

  closeBookDetailModalBtn.addEventListener("click", function () {
    bookDetailModal.close();
  });

  closeBorrowRequestModalBtn.addEventListener("click", function () {
    borrowRequestModal.close();
    pendingBorrowBookId = null;
    pendingBorrowButton = null;
  });

  borrowRequestForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!pendingBorrowBookId) return;

    borrowRequestSubmit.disabled = true;
    borrowRequestSubmit.textContent = "Requesting...";
    borrowRequestError.hidden = true;

    const res = await submitBorrowRequest(pendingBorrowBookId, borrowRequestNote.value.trim());

    if (res.ok) {
      if (pendingBorrowButton) {
        pendingBorrowButton.textContent = "Requested \u2713";
        pendingBorrowButton.disabled = true;
      }
      borrowRequestModal.close();
      pendingBorrowBookId = null;
      pendingBorrowButton = null;
      loadRequests();
    } else {
      borrowRequestSubmit.disabled = false;
      borrowRequestSubmit.textContent = "Submit Request";
      borrowRequestError.textContent = extractErrorMessage(res.body, "Could not submit request.");
      borrowRequestError.hidden = false;
    }
  });

  catSearchInput.addEventListener("input", function () {
    clearTimeout(catSearchDebounce);
    catSearchDebounce = setTimeout(function () {
      catCurrentPage = 1;
      loadCatalogue();
    }, 350);
  });
  catAvailableOnly.addEventListener("change", loadCatalogue);
  catPrevBtn.addEventListener("click", function () {
    if (catCurrentPage > 1) { catCurrentPage--; loadCatalogue(); }
  });
  catNextBtn.addEventListener("click", function () {
    if (catCurrentPage < catTotalPages) { catCurrentPage++; loadCatalogue(); }
  });

  // =========================================================================
  // Journals — live search, PubMed / PMC / DOAJ, no local catalogue at all
  // =========================================================================
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

  // =========================================================================
  // E-Resources — curated list, loaded once
  // =========================================================================
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
          (r.searchable_in_app ? " Searchable directly in the Journals tab." : "") + "</div>" +
        '<div class="eresource-card-actions"><a class="role-btn" href="' + VA.escapeHtml(r.url) + '" target="_blank" rel="noopener noreferrer">Open \u2192</a></div>';
      eresourceGrid.appendChild(card);
    });
  }

  // =========================================================================
  // Init
  // =========================================================================
  document.addEventListener("vistadmin:ready", function () {
    loadLoans();
    loadRequests();
    loadCatalogue();
    loadEresources();
  });
})();