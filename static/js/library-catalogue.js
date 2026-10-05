(function () {
  "use strict";

  const VA = window.VistAdmin;
  const PAGE_SIZE = 10;
  const COPY_STATUSES = ["AVAILABLE", "ISSUED", "LOST", "DAMAGED", "RETIRED"];

  let currentPage = 1;
  let currentTotalPages = 1;
  let searchDebounceTimer = null;
  let activeBookId = null;

  const searchInput = document.getElementById("search-input");
  const tbody = document.getElementById("books-tbody");
  const emptyState = document.getElementById("books-empty");
  const prevBtn = document.getElementById("prev-page");
  const nextBtn = document.getElementById("next-page");
  const pageInfo = document.getElementById("page-info");

  const bookModal = document.getElementById("book-modal");
  const bookModalTitle = document.getElementById("book-modal-title");
  const editBookForm = document.getElementById("edit-book-form");
  const editBookSubmit = document.getElementById("edit-book-submit");
  const bookModalError = document.getElementById("book-modal-error");
  const bookModalSuccess = document.getElementById("book-modal-success");
  const closeBookModalBtn = document.getElementById("close-book-modal");
  const copiesCount = document.getElementById("copies-count");
  const copiesTbody = document.getElementById("copies-tbody");
  const addCopiesCount = document.getElementById("add-copies-count");
  const addCopiesBtn = document.getElementById("add-copies-btn");

  const openAddBookBtn = document.getElementById("open-add-book");
  const addBookModal = document.getElementById("add-book-modal");
  const addBookForm = document.getElementById("add-book-form");
  const addBookSubmit = document.getElementById("add-book-submit");
  const addBookError = document.getElementById("add-book-error");
  const addBookSuccess = document.getElementById("add-book-success");
  const closeAddBookModalBtn = document.getElementById("close-add-book-modal");

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    if (body && Array.isArray(body.detail)) {
      return body.detail.map((e) => e.loc[e.loc.length - 1] + ": " + e.msg).join("; ");
    }
    return fallback;
  }

  function statusPillHtml(status) {
    return '<span class="status-pill ' + (status || "").toLowerCase() + '">' + VA.escapeHtml(status) + "</span>";
  }

  async function loadBooks() {
    const params = new URLSearchParams();
    params.set("page", String(currentPage));
    params.set("page_size", String(PAGE_SIZE));
    params.set("is_journal", "false"); // this page is the general book catalogue only — journals live in the student/faculty portal's live-search tab, no local admin catalogue for them.
    if (searchInput.value.trim()) params.set("q", searchInput.value.trim());

    const result = await VA.fetchJson("/api/v1/library/books?" + params.toString());
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load books."));
      return;
    }
    VA.clearError();

    const data = result.body;
    currentTotalPages = Math.max(1, Math.ceil(data.total / data.page_size));
    tbody.innerHTML = "";

    if (data.items.length === 0) {
      emptyState.hidden = false;
    } else {
      emptyState.hidden = true;
      data.items.forEach(function (book) {
        const tr = document.createElement("tr");
        tr.className = "clickable-row";
        tr.innerHTML =
          "<td>" + VA.escapeHtml(book.short_code) + "</td>" +
          "<td>" + VA.escapeHtml(book.title) + "</td>" +
          "<td>" + VA.escapeHtml(book.author) + "</td>" +
          '<td class="num">' + VA.escapeHtml(book.total_copies) + "</td>" +
          '<td class="num">' + VA.escapeHtml(book.available_copies) + "</td>" +
          '<td class="num"><span class="row-action">Manage &rarr;</span></td>';
        tr.addEventListener("click", function () {
          openBookModal(book.id);
        });
        tbody.appendChild(tr);
      });
    }

    pageInfo.textContent = "Page " + currentPage + " of " + currentTotalPages + " (" + data.total + " total)";
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= currentTotalPages;
  }

  async function loadCopies(bookId) {
    const result = await VA.fetchJson("/api/v1/library/books/" + bookId + "/copies");
    copiesTbody.innerHTML = "";
    if (!result.ok || !result.body) return;

    copiesCount.textContent = String(result.body.length);

    result.body.forEach(function (copy) {
      const tr = document.createElement("tr");
      const optionsHtml = COPY_STATUSES.map(function (s) {
        return '<option value="' + s + '"' + (s === copy.status ? " selected" : "") + ">" + s + "</option>";
      }).join("");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(copy.accession_code) + "</td>" +
        "<td>" + statusPillHtml(copy.status) + "</td>" +
        "<td>" + VA.escapeHtml(copy.condition_notes || "\u2014") + "</td>" +
        '<td class="num">' +
        '<select class="select-input copy-status-select" data-copy-id="' + copy.id + '">' + optionsHtml + "</select> " +
        '<button type="button" class="row-action copy-save-btn" data-copy-id="' + copy.id + '">Save</button>' +
        "</td>";
      copiesTbody.appendChild(tr);
    });

    copiesTbody.querySelectorAll(".copy-save-btn").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        const copyId = btn.getAttribute("data-copy-id");
        const select = copiesTbody.querySelector('.copy-status-select[data-copy-id="' + copyId + '"]');
        btn.disabled = true;
        bookModalError.hidden = true;

        const result = await VA.fetchJson("/api/v1/library/books/copies/" + copyId, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: select.value }),
        });

        btn.disabled = false;

        if (!result.ok) {
          bookModalError.textContent = extractErrorMessage(result.body, "Could not update copy status.");
          bookModalError.hidden = false;
          return;
        }

        loadCopies(bookId);
        loadBooks();
      });
    });
  }

  async function openBookModal(bookId) {
    activeBookId = bookId;
    bookModalError.hidden = true;
    bookModalSuccess.hidden = true;
    bookModal.showModal();

    const result = await VA.fetchJson("/api/v1/library/books/" + bookId);
    if (!result.ok) {
      bookModalError.textContent = extractErrorMessage(result.body, "Could not load book.");
      bookModalError.hidden = false;
      return;
    }

    const b = result.body;
    bookModalTitle.textContent = b.title + " (" + b.short_code + ")";
    document.getElementById("eb-title").value = b.title;
    document.getElementById("eb-author").value = b.author;
    document.getElementById("eb-isbn").value = b.isbn || "";
    document.getElementById("eb-category").value = b.category || "";
    document.getElementById("eb-publisher").value = b.publisher || "";
    document.getElementById("eb-year").value = b.publication_year || "";
    document.getElementById("eb-shelf").value = b.shelf_location || "";
    document.getElementById("eb-cover").value = b.cover_image_url || "";
    document.getElementById("eb-description").value = b.description || "";

    await loadCopies(bookId);
  }

  closeBookModalBtn.addEventListener("click", function () {
    bookModal.close();
  });

  editBookForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!activeBookId) return;
    editBookSubmit.disabled = true;
    bookModalError.hidden = true;
    bookModalSuccess.hidden = true;

    const val = (id) => document.getElementById(id).value.trim();
    const payload = {
      title: val("eb-title"),
      author: val("eb-author"),
      isbn: val("eb-isbn") || null,
      category: val("eb-category") || null,
      publisher: val("eb-publisher") || null,
      publication_year: val("eb-year") ? parseInt(val("eb-year"), 10) : null,
      shelf_location: val("eb-shelf") || null,
      cover_image_url: val("eb-cover") || null,
      description: val("eb-description") || null,
    };

    const result = await VA.fetchJson("/api/v1/library/books/" + activeBookId, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    editBookSubmit.disabled = false;

    if (!result.ok) {
      bookModalError.textContent = extractErrorMessage(result.body, "Could not update book.");
      bookModalError.hidden = false;
      return;
    }

    bookModalSuccess.textContent = "Saved.";
    bookModalSuccess.hidden = false;
    loadBooks();
  });

  addCopiesBtn.addEventListener("click", async function () {
    if (!activeBookId) return;
    const count = parseInt(addCopiesCount.value, 10);
    if (!count || count <= 0) return;
    addCopiesBtn.disabled = true;
    bookModalError.hidden = true;

    const result = await VA.fetchJson("/api/v1/library/books/" + activeBookId + "/copies", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ count: count }),
    });

    addCopiesBtn.disabled = false;

    if (!result.ok) {
      bookModalError.textContent = extractErrorMessage(result.body, "Could not add copies.");
      bookModalError.hidden = false;
      return;
    }

    await loadCopies(activeBookId);
    loadBooks();
  });

  openAddBookBtn.addEventListener("click", function () {
    addBookError.hidden = true;
    addBookSuccess.hidden = true;
    addBookModal.showModal();
  });

  closeAddBookModalBtn.addEventListener("click", function () {
    addBookModal.close();
  });

  addBookForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    addBookSubmit.disabled = true;
    addBookError.hidden = true;
    addBookSuccess.hidden = true;

    const val = (id) => document.getElementById(id).value.trim();
    const payload = {
      title: val("ab-title"),
      author: val("ab-author"),
      isbn: val("ab-isbn") || null,
      category: val("ab-category") || null,
      publisher: val("ab-publisher") || null,
      publication_year: val("ab-year") ? parseInt(val("ab-year"), 10) : null,
      shelf_location: val("ab-shelf") || null,
      cover_image_url: val("ab-cover") || null,
      description: val("ab-description") || null,
      initial_copies: val("ab-copies") ? parseInt(val("ab-copies"), 10) : 1,
      is_journal: false,
    };

    const result = await VA.fetchJson("/api/v1/library/books", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    addBookSubmit.disabled = false;

    if (!result.ok) {
      addBookError.textContent = extractErrorMessage(result.body, "Could not add book.");
      addBookError.hidden = false;
      return;
    }

    addBookSuccess.textContent =
      'Added "' + result.body.title + '" (' + result.body.short_code + ") with " + result.body.total_copies + " cop(y/ies).";
    addBookSuccess.hidden = false;
    addBookForm.reset();
    loadBooks();
  });

  prevBtn.addEventListener("click", function () {
    if (currentPage > 1) {
      currentPage -= 1;
      loadBooks();
    }
  });

  nextBtn.addEventListener("click", function () {
    if (currentPage < currentTotalPages) {
      currentPage += 1;
      loadBooks();
    }
  });

  searchInput.addEventListener("input", function () {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(function () {
      currentPage = 1;
      loadBooks();
    }, 350);
  });

  document.addEventListener("vistadmin:ready", function () {
    loadBooks();
  });
})();