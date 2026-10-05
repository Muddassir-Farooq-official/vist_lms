(function () {
  "use strict";

  const VA = window.VistAdmin;

  const bookSearch = document.getElementById("issue-book-search");
  const bookResults = document.getElementById("issue-book-results");
  const copySelect = document.getElementById("issue-copy-select");
  const borrowerSearch = document.getElementById("issue-borrower-search");
  const borrowerResults = document.getElementById("issue-borrower-results");
  const selectedBorrowerEl = document.getElementById("selected-borrower");
  const dueDateInput = document.getElementById("issue-due-date");
  const issueSubmitBtn = document.getElementById("issue-submit-btn");
  const issueError = document.getElementById("issue-error");
  const issueSuccess = document.getElementById("issue-success");

  const activeTbody = document.getElementById("active-issues-tbody");
  const activeEmpty = document.getElementById("active-issues-empty");

  const returnModal = document.getElementById("return-modal");
  const returnBookHint = document.getElementById("return-book-hint");
  const returnCondition = document.getElementById("return-condition");
  const returnIsLost = document.getElementById("return-is-lost");
  const returnFine = document.getElementById("return-fine");
  const returnSubmitBtn = document.getElementById("return-submit-btn");
  const returnError = document.getElementById("return-error");
  const closeReturnModalBtn = document.getElementById("close-return-modal");

  let selectedBorrower = null; // { borrower_type, borrower_id, display_name }
  let activeReturnIssueId = null;
  let bookSearchDebounce = null;
  let borrowerSearchDebounce = null;

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    if (body && Array.isArray(body.detail)) {
      return body.detail.map((e) => e.loc[e.loc.length - 1] + ": " + e.msg).join("; ");
    }
    return fallback;
  }

  function defaultDueDate() {
    const d = new Date();
    d.setDate(d.getDate() + 14);
    return d.toISOString().slice(0, 10);
  }
  dueDateInput.value = defaultDueDate();

  function updateIssueButtonState() {
    issueSubmitBtn.disabled = !(copySelect.value && selectedBorrower && selectedBorrower.can_borrow && dueDateInput.value);
  }

  // ---------------------------------------------------------------------
  // Step 1: search for a book, then load its AVAILABLE copies
  // ---------------------------------------------------------------------
  bookSearch.addEventListener("input", function () {
    clearTimeout(bookSearchDebounce);
    bookSearchDebounce = setTimeout(async function () {
      const q = bookSearch.value.trim();
      bookResults.innerHTML = "";
      if (q.length < 2) return;

      const result = await VA.fetchJson("/api/v1/library/books?q=" + encodeURIComponent(q) + "&page_size=8");
      if (!result.ok || !result.body) return;

      result.body.items.forEach(function (book) {
        const chip = document.createElement("span");
        chip.className = "chip";
        chip.style.cursor = "pointer";
        chip.textContent = book.title + " (" + book.available_copies + " available)";
        chip.addEventListener("click", function () {
          loadCopiesForIssue(book.id, book.title);
        });
        bookResults.appendChild(chip);
      });
    }, 300);
  });

  async function loadCopiesForIssue(bookId, bookTitle) {
    const result = await VA.fetchJson("/api/v1/library/books/" + bookId + "/copies");
    copySelect.innerHTML = "";
    if (!result.ok || !result.body) return;

    const available = result.body.filter(function (c) {
      return c.status === "AVAILABLE";
    });

    if (available.length === 0) {
      copySelect.innerHTML = '<option value="">No available copies of "' + bookTitle + '"</option>';
      copySelect.disabled = true;
    } else {
      copySelect.disabled = false;
      copySelect.innerHTML = '<option value="">Select a copy&hellip;</option>';
      available.forEach(function (c) {
        const opt = document.createElement("option");
        opt.value = c.id;
        opt.textContent = c.accession_code + " \u2014 " + bookTitle;
        copySelect.appendChild(opt);
      });
    }
    updateIssueButtonState();
  }

  copySelect.addEventListener("change", updateIssueButtonState);
  dueDateInput.addEventListener("change", updateIssueButtonState);

  // ---------------------------------------------------------------------
  // Step 2: search for a borrower
  // ---------------------------------------------------------------------
  borrowerSearch.addEventListener("input", function () {
    clearTimeout(borrowerSearchDebounce);
    borrowerSearchDebounce = setTimeout(async function () {
      const q = borrowerSearch.value.trim();
      borrowerResults.innerHTML = "";
      if (q.length < 2) return;

      const result = await VA.fetchJson("/api/v1/library/borrowers/search?q=" + encodeURIComponent(q));
      if (!result.ok || !result.body) return;

      result.body.forEach(function (b) {
        const chip = document.createElement("span");
        chip.className = "chip";
        chip.style.cursor = "pointer";
        chip.textContent = b.display_name + " (" + b.borrower_type + " \u00b7 " + b.identifier + ")" +
          (b.can_borrow ? " \u00b7 " + b.active_loans + "/" + b.loan_limit + " books" : " \u2014 cannot borrow");
        if (!b.can_borrow) chip.classList.add("chip-blocked");
        chip.title = b.blocked_reason || "";
        chip.addEventListener("click", function () {
          selectedBorrower = b;
          if (b.can_borrow) {
            selectedBorrowerEl.textContent = "Selected: " + b.display_name + " (" + b.borrower_type + ") \u00b7 has " + b.active_loans + " of " + b.loan_limit + " books";
            selectedBorrowerEl.classList.remove("borrower-blocked");
          } else {
            // Say WHY before the librarian even tries to issue.
            selectedBorrowerEl.textContent = b.display_name + " cannot borrow: " + b.blocked_reason;
            selectedBorrowerEl.classList.add("borrower-blocked");
          }
          selectedBorrowerEl.hidden = false;
          updateIssueButtonState();
        });
        borrowerResults.appendChild(chip);
      });
    }, 300);
  });

  // ---------------------------------------------------------------------
  // Submit issue
  // ---------------------------------------------------------------------
  issueSubmitBtn.addEventListener("click", async function () {
    issueError.hidden = true;
    issueSuccess.hidden = true;
    issueSubmitBtn.disabled = true;

    const result = await VA.fetchJson("/api/v1/library/issues", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        book_copy_id: copySelect.value,
        borrower_type: selectedBorrower.borrower_type,
        borrower_id: selectedBorrower.borrower_id,
        due_date: dueDateInput.value,
      }),
    });

    if (!result.ok) {
      issueError.textContent = extractErrorMessage(result.body, "Could not issue book.");
      issueError.hidden = false;
      issueSubmitBtn.disabled = false;
      return;
    }

    issueSuccess.textContent = 'Issued "' + result.body.book_title + '" to ' + result.body.borrower_name + ".";
    issueSuccess.hidden = false;

    bookSearch.value = "";
    bookResults.innerHTML = "";
    copySelect.innerHTML = '<option value="">Search and select a book first&hellip;</option>';
    copySelect.disabled = true;
    borrowerSearch.value = "";
    borrowerResults.innerHTML = "";
    selectedBorrower = null;
    selectedBorrowerEl.hidden = true;
    dueDateInput.value = defaultDueDate();
    updateIssueButtonState();

    loadActiveIssues();
  });

  // ---------------------------------------------------------------------
  // Active issues + return flow
  // ---------------------------------------------------------------------
  async function loadActiveIssues() {
    const result = await VA.fetchJson("/api/v1/library/issues/active");
    activeTbody.innerHTML = "";
    if (!result.ok || !result.body) return;

    if (result.body.length === 0) {
      activeEmpty.hidden = false;
      return;
    }
    activeEmpty.hidden = true;

    const today = new Date().toISOString().slice(0, 10);

    result.body.forEach(function (issue) {
      const tr = document.createElement("tr");
      const overdue = issue.due_date < today;
      tr.innerHTML =
        "<td>" + VA.escapeHtml(issue.book_title) + "</td>" +
        "<td>" + VA.escapeHtml(issue.accession_code) + "</td>" +
        "<td>" + VA.escapeHtml(issue.borrower_name) + "</td>" +
        "<td>" + (overdue ? '<span class="status-pill dropped">' : "<span>") + VA.escapeHtml(issue.due_date) + "</span></td>" +
        '<td class="num"><button type="button" class="row-action return-btn" data-issue-id="' +
        issue.id + '" data-book-title="' + VA.escapeHtml(issue.book_title) + '">Return</button></td>';
      activeTbody.appendChild(tr);
    });

    activeTbody.querySelectorAll(".return-btn").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        activeReturnIssueId = btn.getAttribute("data-issue-id");
        returnBookHint.textContent = "Returning: " + btn.getAttribute("data-book-title");
        returnCondition.value = "";
        returnIsLost.checked = false;
        returnFine.value = "";
        returnError.hidden = true;
        returnModal.showModal();
        // Work out the standard late fee so the librarian sees it before confirming.
        const preview = await VA.fetchJson("/api/v1/library/issues/" + activeReturnIssueId + "/return-preview");
        if (!preview.ok || activeReturnIssueId !== btn.getAttribute("data-issue-id")) return;
        const p = preview.body;
        if (p.days_overdue > 0) {
          returnBookHint.innerHTML = "Returning: <strong>" + VA.escapeHtml(p.book_title) + "</strong> from " + VA.escapeHtml(p.borrower_name) +
            ". It is <strong>" + p.days_overdue + " day" + (p.days_overdue > 1 ? "s" : "") + " late</strong> (due " + VA.escapeHtml(p.due_date) +
            "). Standard late fee: <strong>Rs. " + Number(p.suggested_fine).toLocaleString() + "</strong> (Rs. " + p.late_fee_per_day + " per day). " +
            "Keep the amount below to charge it, change it, or enter 0 to waive it.";
          returnFine.value = p.suggested_fine || "";
        } else {
          returnBookHint.innerHTML = "Returning: <strong>" + VA.escapeHtml(p.book_title) + "</strong> from " + VA.escapeHtml(p.borrower_name) + ". Returned on time \u2014 no fine.";
        }
      });
    });
  }

  returnSubmitBtn.addEventListener("click", async function () {
    if (!activeReturnIssueId) return;
    returnSubmitBtn.disabled = true;
    returnError.hidden = true;

    const result = await VA.fetchJson("/api/v1/library/issues/" + activeReturnIssueId + "/return", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        condition_notes: returnCondition.value.trim() || null,
        is_lost: returnIsLost.checked,
        fine_amount: returnFine.value ? parseFloat(returnFine.value) : null,
      }),
    });

    returnSubmitBtn.disabled = false;

    if (!result.ok) {
      returnError.textContent = extractErrorMessage(result.body, "Could not process return.");
      returnError.hidden = false;
      return;
    }

    returnModal.close();
    loadActiveIssues();
  });

  closeReturnModalBtn.addEventListener("click", function () {
    returnModal.close();
  });

  document.addEventListener("vistadmin:ready", function () {
    loadActiveIssues();
  });
})();