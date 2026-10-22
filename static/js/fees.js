(function () {
  "use strict";

  const VA = window.VistAdmin;
  let activeAssignmentId = null;
  let programsCache = [];

  // =========================================================================
  // Tabs — same pattern as static/js/courses.js
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

  const feeTypesSection = document.getElementById("fee-types-section");
  const structuresSection = document.getElementById("fee-structures-section");

  const feeTypesTbody = document.getElementById("fee-types-tbody");
  const feeTypesEmpty = document.getElementById("fee-types-empty");
  const createFeeTypeForm = document.getElementById("create-fee-type-form");
  const createFeeTypeSubmit = document.getElementById("create-fee-type-submit");
  const feeTypeError = document.getElementById("fee-type-error");
  const fsTypeSelect = document.getElementById("fs-type");

  const fsProgramsList = document.getElementById("fs-programs-list");
  const fsSemestersList = document.getElementById("fs-semesters-list");
  const createStructureForm = document.getElementById("create-structure-form");
  const createStructureSubmit = document.getElementById("create-structure-submit");
  const structureError = document.getElementById("structure-error");
  const structureSuccess = document.getElementById("structure-success");
  const structuresTbody = document.getElementById("structures-tbody");
  const structuresEmpty = document.getElementById("structures-empty");

  const assignmentsTbody = document.getElementById("assignments-tbody");
  const assignmentsEmpty = document.getElementById("assignments-empty");

  const assignmentModal = document.getElementById("assignment-modal");
  const assignmentModalTitle = document.getElementById("assignment-modal-title");
  const assignmentDetailList = document.getElementById("assignment-detail-list");
  const paymentAmount = document.getElementById("payment-amount");
  const paymentDescription = document.getElementById("payment-description");
  const paymentMethod = document.getElementById("payment-method");
  const paymentReference = document.getElementById("payment-reference");
  const paymentPaidOn = document.getElementById("payment-paid-on");
  const recordPaymentBtn = document.getElementById("record-payment-btn");
  const assignmentStatusSelect = document.getElementById("assignment-status-select");
  const saveAssignmentStatusBtn = document.getElementById("save-assignment-status-btn");
  const assignmentModalError = document.getElementById("assignment-modal-error");
  const assignmentModalSuccess = document.getElementById("assignment-modal-success");
  const ledgerTbody = document.getElementById("ledger-tbody");
  const closeAssignmentModalBtn = document.getElementById("close-assignment-modal");

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    if (body && Array.isArray(body.detail)) {
      return body.detail.map((e) => e.loc[e.loc.length - 1] + ": " + e.msg).join("; ");
    }
    return fallback;
  }

  function standingPillHtml(isGood) {
    return isGood
      ? '<span class="status-pill active">Good Standing</span>'
      : '<span class="status-pill dropped">Dues Pending</span>';
  }

  function money(n) {
    return window.VistAdmin && window.VistAdmin.formatMoney ? window.VistAdmin.formatMoney(n) : "Rs. " + Number(n || 0).toLocaleString("en-PK");
  }

  async function loadDashboard() {
    const result = await VA.fetchJson("/api/v1/fees/dashboard");
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load fee dashboard."));
      return;
    }
    VA.clearError();
    const d = result.body;
    document.getElementById("stat-collected").textContent = money(d.total_collected);
    document.getElementById("stat-charged").textContent = money(d.total_charged);
    document.getElementById("stat-outstanding").textContent = money(d.total_outstanding);
    document.getElementById("stat-overdue").textContent = money(d.total_overdue);
    document.getElementById("stat-overdue-count").textContent = d.overdue_assignment_count + " assignment(s)";
    document.getElementById("stat-structures").textContent = d.active_fee_structures;
  }

  async function loadFeeTypes() {
    const result = await VA.fetchJson("/api/v1/fees/types");
    if (!result.ok) return;
    const types = result.body || [];

    feeTypesTbody.innerHTML = "";
    if (types.length === 0) {
      feeTypesEmpty.hidden = false;
    } else {
      feeTypesEmpty.hidden = true;
      types.forEach(function (t) {
        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" + VA.escapeHtml(t.code) + "</td>" +
          "<td>" + VA.escapeHtml(t.name) + "</td>" +
          "<td>" + VA.escapeHtml(t.description || "\u2014") + "</td>" +
          "<td>" + (t.is_active ? '<span class="status-pill active">Active</span>' : '<span class="status-pill dropped">Inactive</span>') + "</td>";
        feeTypesTbody.appendChild(tr);
      });
    }

    if (fsTypeSelect) {
      fsTypeSelect.innerHTML = '<option value="">Select&hellip;</option>';
      types
        .filter(function (t) { return t.is_active; })
        .forEach(function (t) {
          const opt = document.createElement("option");
          opt.value = t.id;
          opt.textContent = t.name;
          fsTypeSelect.appendChild(opt);
        });
    }
  }

  if (createFeeTypeForm) {
    createFeeTypeForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      createFeeTypeSubmit.disabled = true;
      feeTypeError.hidden = true;

      const payload = {
        code: document.getElementById("ft-code").value.trim(),
        name: document.getElementById("ft-name").value.trim(),
        description: document.getElementById("ft-description").value.trim() || null,
      };

      const result = await VA.fetchJson("/api/v1/fees/types", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      createFeeTypeSubmit.disabled = false;

      if (!result.ok) {
        feeTypeError.textContent = extractErrorMessage(result.body, "Could not create fee type.");
        feeTypeError.hidden = false;
        return;
      }

      createFeeTypeForm.reset();
      loadFeeTypes();
    });
  }

  async function loadPrograms() {
    const result = await VA.fetchJson("/api/v1/programs?active_only=true");
    if (!result.ok || !result.body) return;
    programsCache = result.body;

    if (fsProgramsList) {
      fsProgramsList.innerHTML = "";
      programsCache.forEach(function (p) {
        const label = document.createElement("label");
        label.className = "chip";
        label.style.cursor = "pointer";
        label.innerHTML =
          '<input type="checkbox" class="fs-program-checkbox" value="' + p.id + '" style="margin-right:5px;" />' +
          VA.escapeHtml(p.code);
        fsProgramsList.appendChild(label);
      });
    }
  }

  const fsCourseSelect = document.getElementById("fs-course");

  async function loadFeeCourses() {
    if (!fsCourseSelect) return;
    const result = await VA.fetchJson("/api/v1/academics/courses?active_only=true");
    if (!result.ok || !result.body) return;

    fsCourseSelect.innerHTML = '<option value="">None &mdash; use Programs/Semesters below instead</option>';
    result.body.forEach(function (c) {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = c.code + " \u2014 " + c.title;
      fsCourseSelect.appendChild(opt);
    });
  }

  function updateCourseScopeToggle() {
    if (!fsCourseSelect) return;
    const courseChosen = !!fsCourseSelect.value;
    document.querySelectorAll(".fs-program-checkbox, .fs-semester-checkbox").forEach(function (cb) {
      cb.disabled = courseChosen;
      if (courseChosen) cb.checked = false;
    });
  }

  if (fsCourseSelect) {
    fsCourseSelect.addEventListener("change", updateCourseScopeToggle);
  }

  function renderSemesterCheckboxes() {
    if (!fsSemestersList) return;
    fsSemestersList.innerHTML = "";
    for (let n = 1; n <= 12; n++) {
      const label = document.createElement("label");
      label.className = "chip";
      label.style.cursor = "pointer";
      label.innerHTML =
        '<input type="checkbox" class="fs-semester-checkbox" value="' + n + '" style="margin-right:5px;" />' +
        "Sem " + n;
      fsSemestersList.appendChild(label);
    }
  }

  async function loadStructures() {
    const result = await VA.fetchJson("/api/v1/fees/structures");
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load fee structures."));
      return;
    }
    VA.clearError();
    const structures = result.body || [];

    structuresTbody.innerHTML = "";
    if (structures.length === 0) {
      structuresEmpty.hidden = false;
    } else {
      structuresEmpty.hidden = true;
      structures.forEach(function (s) {
        let scope;
        if (s.course_id) {
          scope = "Subject: " + (s.course_label || "Unknown course") + " (every enrollment, incl. retakes)";
        } else {
          const programScope = s.program_names.length > 0 ? s.program_names.join(", ") : "All programs";
          const semesterScope = s.semester_numbers && s.semester_numbers.length > 0 ? "Sem " + s.semester_numbers.join(", ") : "All semesters";
          scope = programScope + " \u00b7 " + semesterScope;
        }
        const statusPill =
          s.status === "ACTIVE"
            ? '<span class="status-pill active">Active</span>'
            : s.status === "DRAFT"
            ? '<span class="status-pill suspended">Draft</span>'
            : '<span class="status-pill dropped">Closed</span>';

        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" + VA.escapeHtml(s.name) + "</td>" +
          "<td>" + VA.escapeHtml(s.fee_type_name) + "</td>" +
          "<td>" + VA.escapeHtml(s.term_label) + "</td>" +
          '<td class="num">' + money(s.total_amount) + "</td>" +
          "<td>" + VA.escapeHtml(scope) + "</td>" +
          "<td>" + statusPill + "</td>" +
          '<td class="num">' +
          (s.status === "DRAFT" || s.status === "ACTIVE"
            ? '<button type="button" class="row-action activate-structure-btn" data-id="' + s.id + '">' +
              (s.status === "DRAFT" ? "Activate" : "Re-check") + "</button>"
            : "") +
          (s.status === "ACTIVE" ? ' &middot; <button type="button" class="row-action undo-activation-btn" data-id="' + s.id + '">Undo activation\u2026</button>' : "") +
          ' &middot; <button type="button" class="row-action clone-structure-btn" data-id="' + s.id +
          '" data-name="' + VA.escapeHtml(s.name) + '" data-amount="' + s.total_amount + '">Clone &amp; Adjust</button>' +
          "</td>";
        structuresTbody.appendChild(tr);
      });

      structuresTbody.querySelectorAll(".clone-structure-btn").forEach(function (btn) {
        btn.addEventListener("click", async function () {
          const sourceName = btn.getAttribute("data-name");
          const sourceAmount = parseFloat(btn.getAttribute("data-amount"));

          const openTerms = window.VistTerms ? window.VistTerms.open().map(function (t) { return t.label; }) : [];
          const newTerm = (await window.VistAdmin.promptText('Term for the clone of "' + sourceName + '"' + (openTerms.length ? " (open terms: " + openTerms.join(", ") + ")" : "") + ":", openTerms[0] || ""));
          if (!newTerm) return;

          const pctInput = (await window.VistAdmin.promptText("Percentage change (e.g. 10 for a 10% increase, -5 for a 5% decrease, 0 for no change):", "10"));
          if (pctInput === null) return;
          const pct = parseFloat(pctInput);
          if (isNaN(pct)) {
            VA.showError("Enter a valid number for the percentage change.");
            return;
          }

          const effectiveFrom = (await window.VistAdmin.promptText("First installment due date for the new term (YYYY-MM-DD):", ""));
          if (!effectiveFrom) return;

          const preview = (sourceAmount * (1 + pct / 100)).toFixed(2);
          if (!(await window.VistAdmin.confirm('This will create a new DRAFT structure "' + newTerm + '" at ' + preview + " (currently " + sourceAmount.toFixed(2) + "). Continue?"))) {
            return;
          }

          btn.disabled = true;
          const result = await VA.fetchJson("/api/v1/fees/structures/" + btn.getAttribute("data-id") + "/clone", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_term_label: newTerm, percentage_change: pct, effective_from: effectiveFrom }),
          });
          btn.disabled = false;

          if (!result.ok) {
            VA.showError(extractErrorMessage(result.body, "Could not clone structure."));
            return;
          }

          structureSuccess.textContent =
            'Created "' + result.body.name + '" as a draft at ' + money(result.body.total_amount) + ". Activate it when ready.";
          structureSuccess.hidden = false;
          loadStructures();
        });
      });

      structuresTbody.querySelectorAll(".undo-activation-btn").forEach(function (btn) {
        btn.addEventListener("click", async function () {
          const why = await VA.ask({ title: "Undo this activation?", danger: true, ok: "Undo activation",
            intro: "Every charge this fee made is removed and it goes back to a draft you can correct. This only works while nobody has paid anything on it." });
          if (!why) return;
          const r = await VA.fetchJson("/api/v1/fees/structures/" + btn.getAttribute("data-id") + "/undo-activation", {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: why }) });
          if (!r.ok) { VA.showError(extractErrorMessage(r.body, "Could not undo it.")); return; }
          structureSuccess.textContent = "Activation undone: " + r.body.removed_charges + " charge(s) removed. The fee is a draft again.";
          structureSuccess.hidden = false;
          loadStructures(); loadAssignments(); loadDashboard();
        });
      });

      structuresTbody.querySelectorAll(".activate-structure-btn").forEach(function (btn) {
        btn.addEventListener("click", async function () {
          if (btn.textContent === "Activate" && !(await VA.confirm("Activate this fee? Every matching student (or employee) is charged now and gets bank challans. If something is wrong you can undo it until someone pays.", "Activate"))) return;
          btn.disabled = true;
          const result = await VA.fetchJson("/api/v1/fees/structures/" + btn.getAttribute("data-id") + "/activate", {
            method: "POST",
          });
          btn.disabled = false;
          if (!result.ok) {
            VA.showError(extractErrorMessage(result.body, "Could not activate structure."));
            return;
          }
          structureSuccess.textContent = "Charged " + result.body.newly_assigned + " newly-matching payer(s).";
          structureSuccess.hidden = false;
          loadStructures();
          loadAssignments();
          loadDashboard();
        });
      });
    }
  }

  if (createStructureForm) {
    createStructureForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      createStructureSubmit.disabled = true;
      structureError.hidden = true;
      structureSuccess.hidden = true;

      const courseId = fsCourseSelect && fsCourseSelect.value ? fsCourseSelect.value : null;
      // Mutually exclusive with program/semester scope, per backend
      // validation — omit them entirely rather than send empty arrays
      // vs. checked-then-unchecked ambiguity when a course is chosen.
      const programIds = courseId
        ? []
        : Array.from(document.querySelectorAll(".fs-program-checkbox:checked")).map(function (cb) {
            return cb.value;
          });
      const semesterNumbers = courseId
        ? []
        : Array.from(document.querySelectorAll(".fs-semester-checkbox:checked")).map(function (cb) {
            return parseInt(cb.value, 10);
          });

      const payload = {
        fee_type_id: fsTypeSelect.value,
        name: document.getElementById("fs-name").value.trim(),
        term_label: document.getElementById("fs-term").value.trim(),
        total_amount: parseFloat(document.getElementById("fs-amount").value),
        installment_count: parseInt(document.getElementById("fs-installments").value, 10),
        installment_interval_months: parseInt(document.getElementById("fs-interval").value, 10),
        effective_from: document.getElementById("fs-effective").value,
        late_fee_per_day: document.getElementById("fs-late-fee").value
          ? parseFloat(document.getElementById("fs-late-fee").value)
          : null,
        applies_to_students: document.getElementById("fs-applies-students").checked,
        applies_to_employees: document.getElementById("fs-applies-employees").checked,
        program_ids: programIds,
        semester_numbers: semesterNumbers,
        course_id: courseId,
      };

      const result = await VA.fetchJson("/api/v1/fees/structures", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      createStructureSubmit.disabled = false;

      if (!result.ok) {
        structureError.textContent = extractErrorMessage(result.body, "Could not create fee structure.");
        structureError.hidden = false;
        return;
      }

      structureSuccess.textContent = 'Created "' + result.body.name + '" as a draft. Activate it to start charging.';
      structureSuccess.hidden = false;
      createStructureForm.reset();
      document.getElementById("fs-applies-students").checked = true;
      updateCourseScopeToggle();
      loadStructures();
    });
  }

  async function loadAssignments() {
    const result = await VA.fetchJson("/api/v1/fees/assignments");
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load fee assignments."));
      return;
    }
    VA.clearError();
    const assignments = result.body || [];

    assignmentsTbody.innerHTML = "";
    if (assignments.length === 0) {
      assignmentsEmpty.hidden = false;
      return;
    }
    assignmentsEmpty.hidden = true;

    assignments.forEach(function (a) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(a.payer_name) + " (" + VA.escapeHtml(a.payer_type) + ")</td>" +
        "<td>" + VA.escapeHtml(a.fee_structure_name) + "</td>" +
        '<td class="num">' + money(a.total_charged) + "</td>" +
        '<td class="num">' + money(a.total_paid) + "</td>" +
        '<td class="num">' + money(a.total_due_now) + "</td>" +
        "<td>" + standingPillHtml(a.is_in_good_standing) + "</td>" +
        '<td class="num"><button type="button" class="row-action manage-assignment-btn" data-id="' + a.id + '">Manage</button></td>';
      assignmentsTbody.appendChild(tr);
    });

    assignmentsTbody.querySelectorAll(".manage-assignment-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openAssignmentModal(btn.getAttribute("data-id"));
      });
    });
  }

  async function loadLedger(assignmentId) {
    const result = await VA.fetchJson("/api/v1/fees/assignments/" + assignmentId + "/statement");
    const instTbody = document.getElementById("installments-tbody");
    ledgerTbody.innerHTML = "";
    instTbody.innerHTML = "";
    if (!result.ok || !result.body) return;
    const PILL = { PAID: ["active", "Paid"], PARTIAL: ["pending", "Partly paid"], OVERDUE: ["dropped", "Overdue"], UPCOMING: ["", "Not yet due"] };

    result.body.installments.forEach(function (r) {
      const pill = PILL[r.status] || ["", r.status];
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(r.label) + "</td>" +
        "<td>" + VA.escapeHtml(r.due_date || "\u2014") + "</td>" +
        '<td class="num">' + money(r.charged) + "</td>" +
        '<td class="num">' + (r.concession ? money(r.concession) : "\u2014") + "</td>" +
        '<td class="num">' + money(r.paid) + "</td>" +
        '<td class="num"><strong>' + money(r.remaining) + "</strong></td>" +
        '<td><span class="status-pill ' + pill[0] + '">' + pill[1] + "</span>" +
          (r.late_fee > 0.005 ? '<br><small>late fee ' + money(r.late_fee) + (r.late_fee_posted ? " (" + money(r.late_fee_posted) + " posted)" : "") + "</small>" : "") + "</td>" +
        '<td class="row-actions"></td>';
      if (r.late_fee > r.late_fee_posted + 0.005) {
        const postFeeBtn = document.createElement("button");
        postFeeBtn.className = "role-btn small-btn";
        postFeeBtn.type = "button";
        postFeeBtn.textContent = "Post Late Fee";
        postFeeBtn.addEventListener("click", async function () {
          const lateFeeError = document.getElementById("late-fee-error");
          const lateFeeSuccess = document.getElementById("late-fee-success");
          lateFeeError.hidden = true;
          lateFeeSuccess.hidden = true;
          postFeeBtn.disabled = true;
          const res = await VA.fetchJson(
            "/api/v1/fees/assignments/" + assignmentId + "/installments/" + r.ledger_entry_id + "/post-late-fee",
            { method: "POST" }
          );
          postFeeBtn.disabled = false;
          if (!res.ok) {
            lateFeeError.textContent = extractErrorMessage(res.body, "Could not post late fee.");
            lateFeeError.hidden = false;
            return;
          }
          lateFeeSuccess.textContent = res.body.posted
            ? "Posted a " + money(res.body.amount) + " late fee \u2014 it is now a fine the payer must pay."
            : (res.body.message || "Nothing new to post for this installment.");
          lateFeeSuccess.hidden = false;
          loadLedger(assignmentId);
        });
        tr.querySelector(".row-actions").appendChild(postFeeBtn);
      }
      instTbody.appendChild(tr);
    });

    result.body.lines.forEach(function (l) {
      const adds = l.type === "DEBIT" || l.type === "REFUND";
      const when = l.paid_on || (l.created_at || "").slice(0, 10);
      const details = [];
      if (l.type === "DEBIT" && l.due_date) details.push("due " + l.due_date);
      if (l.applied_to && l.applied_to.length) {
        details.push("settled " + l.applied_to.map(function (a) { return a.installment + " (" + money(a.amount) + ")"; }).join(", "));
      } else if (l.type === "CREDIT" && l.what === "Payment") {
        details.push("extra: kept as credit");
      }
      if (l.payment_method) details.push(l.payment_method.replace(/_/g, " ").toLowerCase() + (l.payment_reference ? " #" + l.payment_reference : ""));
      if (l.description && l.type !== "DEBIT") details.push(l.description);
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(when) + "</td>" +
        "<td><strong>" + VA.escapeHtml(l.what) + "</strong></td>" +
        '<td class="num">' + (adds ? money(l.amount) : "") + "</td>" +
        '<td class="num ok">' + (!adds ? money(l.amount) : "") + "</td>" +
        '<td class="num">' + money(l.balance) + "</td>" +
        "<td>" + VA.escapeHtml(details.join(" \u00b7 ")) +
          (l.receipt_number ? ' \u00b7 <a href="/api/v1/fees/receipts/' + encodeURIComponent(l.id) + '/pdf" target="_blank" rel="noopener">Receipt ' + VA.escapeHtml(l.receipt_number) + "</a>" : "") +
          (l.reversed ? ' <span class="status-pill dropped">Reversed</span>' : "") + "</td>";
      if (l.type === "CREDIT" && !l.reversed) {
        const rev = document.createElement("button");
        rev.type = "button";
        rev.className = "role-btn small-btn secondary-btn";
        rev.textContent = "Reverse\u2026";
        rev.addEventListener("click", async function () {
          const reason = await VA.ask({ title: "Reverse this payment?", danger: true, ok: "Reverse payment",
            intro: money(l.amount) + (l.receipt_number ? ", receipt " + l.receipt_number : "") + ". Use this only for a payment recorded by mistake or a bounced cheque. The receipt will print as VOID and the amount will be owed again." });
          if (!reason) return;
          const res = await VA.fetchJson("/api/v1/fees/assignments/payments/" + l.id + "/reverse", {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: reason }) });
          if (!res.ok) { VA.toast(extractErrorMessage(res.body, "Could not reverse the payment."), "error"); return; }
          VA.toast("Payment reversed. The receipt now prints as VOID.");
          loadLedger(assignmentId); loadAssignments(); loadDashboard();
        });
        tr.lastElementChild.appendChild(document.createTextNode(" "));
        tr.lastElementChild.appendChild(rev);
      }
      ledgerTbody.appendChild(tr);
    });
  }

  async function openAssignmentModal(assignmentId) {
    activeAssignmentId = assignmentId;
    assignmentModalError.hidden = true;
    assignmentModalSuccess.hidden = true;
    paymentAmount.value = "";
    paymentDescription.value = "";
    paymentReference.value = "";
    paymentPaidOn.value = "";
    assignmentModal.showModal();

    const result = await VA.fetchJson("/api/v1/fees/assignments/" + assignmentId);
    if (!result.ok) {
      assignmentModalError.textContent = extractErrorMessage(result.body, "Could not load assignment.");
      assignmentModalError.hidden = false;
      return;
    }

    const a = result.body;
    assignmentModalTitle.textContent = a.payer_name + " \u2014 " + a.fee_structure_name;
    assignmentDetailList.innerHTML =
      "<dt>Charged</dt><dd>" + money(a.total_charged) + "</dd>" +
      "<dt>Paid</dt><dd>" + money(a.total_paid) + "</dd>" +
      "<dt>Overdue Principal</dt><dd>" + money(a.overdue_principal) + "</dd>" +
      "<dt>Accrued Fine</dt><dd>" + money(a.accrued_fine) + "</dd>" +
      "<dt>Total Due Now</dt><dd><strong>" + money(a.total_due_now) + "</strong></dd>" +
      "<dt>Standing</dt><dd>" + standingPillHtml(a.is_in_good_standing) + "</dd>";
    assignmentStatusSelect.value = a.status;

    await loadLedger(assignmentId);
  }

  recordPaymentBtn.addEventListener("click", async function () {
    if (!activeAssignmentId) return;
    const amount = parseFloat(paymentAmount.value);
    if (!amount || amount <= 0) {
      assignmentModalError.textContent = "Enter a positive amount.";
      assignmentModalError.hidden = false;
      return;
    }
    if (paymentMethod.value !== "CASH" && !paymentReference.value.trim()) {
      assignmentModalError.textContent = "Enter the deposit slip, transaction or cheque number (needed to match the bank statement).";
      assignmentModalError.hidden = false;
      return;
    }
    recordPaymentBtn.disabled = true;
    assignmentModalError.hidden = true;
    assignmentModalSuccess.hidden = true;

    const result = await VA.fetchJson("/api/v1/fees/assignments/" + activeAssignmentId + "/payments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        amount: amount, description: paymentDescription.value.trim() || null, method: paymentMethod.value,
        reference: paymentReference.value.trim() || null, paid_on: paymentPaidOn.value || null,
      }),
    });

    recordPaymentBtn.disabled = false;

    if (!result.ok) {
      assignmentModalError.textContent = extractErrorMessage(result.body, "Could not record payment.");
      assignmentModalError.hidden = false;
      return;
    }

    assignmentModalSuccess.textContent = "Payment recorded. The receipt is in the ledger below.";
    assignmentModalSuccess.hidden = false;
    paymentAmount.value = "";
    paymentDescription.value = "";
    paymentReference.value = "";
    paymentPaidOn.value = "";
    await openAssignmentModal(activeAssignmentId);
    loadAssignments();
    loadDashboard();
  });

  saveAssignmentStatusBtn.addEventListener("click", async function () {
    if (!activeAssignmentId) return;
    assignmentModalError.hidden = true;
    const status = assignmentStatusSelect.value;
    let reason = null;
    if (status !== "ACTIVE") {
      reason = await VA.ask({ title: status === "WAIVED" ? "Waive this fee?" : "Cancel this fee?", danger: true, ok: status === "WAIVED" ? "Waive it" : "Cancel it",
        intro: "Nothing more will be collected on it and its bank challans stop working. Money already paid stays recorded." });
      if (!reason) return;
    }
    saveAssignmentStatusBtn.disabled = true;

    const result = await VA.fetchJson("/api/v1/fees/assignments/" + activeAssignmentId + "/status", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: status, reason: reason }),
    });

    saveAssignmentStatusBtn.disabled = false;

    if (!result.ok) {
      assignmentModalError.textContent = extractErrorMessage(result.body, "Could not update status.");
      assignmentModalError.hidden = false;
      return;
    }

    assignmentModalSuccess.textContent = "Status updated.";
    assignmentModalSuccess.hidden = false;
    loadAssignments();
  });

  closeAssignmentModalBtn.addEventListener("click", function () {
    assignmentModal.close();
  });

  document.addEventListener("vistadmin:ready", function (event) {
    const roles = (event.detail && event.detail.me && event.detail.me.roles) || [];
    const isAdmin = roles.indexOf("ADMIN") !== -1;

    if (!isAdmin) {
      // Hide the tab BUTTONS themselves, not just the inner sections —
      // now that these live inside tab panels, hiding only the section
      // would leave an accounts officer able to click a "Fee Types" tab
      // that shows nothing. Land them on Assignments instead, which is
      // the tab they actually have access to.
      document.querySelectorAll('.portal-tab[data-tab="types"], .portal-tab[data-tab="structures"]').forEach(function (btn) {
        btn.hidden = true;
      });
      if (feeTypesSection) feeTypesSection.hidden = true;
      if (structuresSection) structuresSection.hidden = true;
      activateTab("assignments");
    } else {
      loadFeeTypes();
      loadPrograms();
      loadFeeCourses();
      renderSemesterCheckboxes();
      loadStructures();
    }

    loadDashboard();
    loadAssignments();
  });
})();