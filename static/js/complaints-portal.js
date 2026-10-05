(function () {
  "use strict";

  const VA = window.VistAdmin;
  let searchDebounce = null;

  const targetTypeSelect = document.getElementById("complaint-target-type");
  const targetSearchInput = document.getElementById("complaint-target-search");
  const targetResults = document.getElementById("complaint-target-results");
  const targetIdInput = document.getElementById("complaint-target-id");
  const reasonSelect = document.getElementById("complaint-reason");
  const customReasonWrap = document.getElementById("complaint-custom-reason-wrap");
  const customReasonInput = document.getElementById("complaint-custom-reason");
  const descriptionInput = document.getElementById("complaint-description");
  const form = document.getElementById("complaint-form");
  const submitBtn = document.getElementById("complaint-submit");
  const errorBox = document.getElementById("complaint-error");
  const successBox = document.getElementById("complaint-success");

  const myList = document.getElementById("my-complaints-list");
  const anonymousInput = document.getElementById("complaint-anonymous");
  const myEmpty = document.getElementById("my-complaints-empty");

  const actionModal = document.getElementById("complaint-action-modal");
  const actionTitle = document.getElementById("complaint-action-title");
  const actionSummary = document.getElementById("complaint-action-summary");
  const actionForm = document.getElementById("complaint-action-form");
  const actionNote = document.getElementById("complaint-action-note");
  const actionSubmit = document.getElementById("complaint-action-submit");
  const actionError = document.getElementById("complaint-action-error");
  const closeActionModalBtn = document.getElementById("close-complaint-action-modal");

  let pendingAction = null; // { complaintId, kind: "withdraw" | "self-resolve" }

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  function statusPillHtml(status) {
    const label = String(status || "").replace(/_/g, " ").toLowerCase().replace(/^./, function (m) { return m.toUpperCase(); });
    return '<span class="status-pill ' + (status || "").toLowerCase() + '">' + VA.escapeHtml(label) + "</span>";
  }

  async function loadReasons() {
    const result = await VA.fetchJson("/api/v1/complaints/reasons");
    if (!result.ok) return;
    result.body.forEach(function (r) {
      const opt = document.createElement("option");
      opt.value = r.value;
      opt.textContent = r.label;
      reasonSelect.appendChild(opt);
    });
  }

  targetTypeSelect.addEventListener("change", function () {
    targetSearchInput.value = "";
    targetIdInput.value = "";
    targetResults.hidden = true;
    targetSearchInput.disabled = !targetTypeSelect.value;
  });

  reasonSelect.addEventListener("change", function () {
    customReasonWrap.hidden = reasonSelect.value !== "OTHER";
    if (reasonSelect.value !== "OTHER") customReasonInput.value = "";
  });

  targetSearchInput.addEventListener("input", function () {
    targetIdInput.value = "";
    clearTimeout(searchDebounce);
    const q = targetSearchInput.value.trim();
    if (q.length < 2 || !targetTypeSelect.value) {
      targetResults.hidden = true;
      return;
    }
    searchDebounce = setTimeout(async function () {
      const [partyType, roleCode] = targetTypeSelect.value.split(":");
      const params = new URLSearchParams({ target_type: partyType, q: q });
      if (roleCode) params.set("role_code", roleCode);

      const result = await VA.fetchJson("/api/v1/complaints/search-targets?" + params.toString());
      if (!result.ok) return;

      targetResults.innerHTML = "";
      if (result.body.length === 0) {
        targetResults.innerHTML = '<div class="search-result-item search-result-empty">No matches.</div>';
        targetResults.hidden = false;
        return;
      }
      result.body.forEach(function (person) {
        const item = document.createElement("div");
        item.className = "search-result-item";
        item.textContent = person.name + " (" + person.role_label + (person.identifier ? " \u00b7 " + person.identifier : "") + ")";
        item.addEventListener("click", function () {
          targetIdInput.value = person.id;
          targetSearchInput.value = item.textContent;
          targetResults.hidden = true;
        });
        targetResults.appendChild(item);
      });
      targetResults.hidden = false;
    }, 300);
  });

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    errorBox.hidden = true;
    successBox.hidden = true;

    if (!targetIdInput.value) {
      errorBox.textContent = "Search and select who this complaint is about from the dropdown first.";
      errorBox.hidden = false;
      return;
    }

    submitBtn.disabled = true;

    const [partyType] = targetTypeSelect.value.split(":");
    const result = await VA.fetchJson("/api/v1/complaints", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_type: partyType,
        target_id: targetIdInput.value,
        reason: reasonSelect.value,
        custom_reason: customReasonInput.value.trim() || null,
        description: descriptionInput.value.trim(),
        is_anonymous: anonymousInput.checked,
      }),
    });

    submitBtn.disabled = false;

    if (!result.ok) {
      errorBox.textContent = extractErrorMessage(result.body, "Could not submit complaint.");
      errorBox.hidden = false;
      return;
    }

    successBox.textContent = anonymousInput.checked
      ? "Complaint filed anonymously. Only the administrator can look up who filed it. You can track it under My complaints."
      : "Complaint filed. You can track it under My complaints.";
    successBox.hidden = false;
    form.reset();
    targetIdInput.value = "";
    targetSearchInput.disabled = true;
    customReasonWrap.hidden = true;
    loadMyComplaints();
    loadOverview();
  });

  // ------------------------------------------------------------------------
  // One complaint in full, with a dated timeline
  // ------------------------------------------------------------------------
  const detailModal = document.getElementById("complaint-detail-modal");
  function fmt(iso) { return iso ? VA.formatDateTime(iso) : null; }

  function openDetail(c) {
    const reasonLabel = c.reason === "OTHER" && c.custom_reason ? c.custom_reason : c.reason.replace(/_/g, " ").toLowerCase().replace(/^./, function (m) { return m.toUpperCase(); });
    const isOpen = c.status === "SUBMITTED" || c.status === "UNDER_REVIEW";
    const reviewed = c.status === "UNDER_REVIEW" || c.status === "RESOLVED" || c.status === "DISMISSED";
    const closedLabel = { RESOLVED: "Resolved", DISMISSED: "Dismissed", WITHDRAWN: "Withdrawn" }[c.status];
    document.getElementById("cd-badges").innerHTML = (c.is_anonymous ? '<span class="anon-badge">\uD83D\uDD12 Anonymous</span>' : "") + statusPillHtml(c.status);
    document.getElementById("cd-title").textContent = "Complaint about " + c.target_name;
    document.getElementById("cd-meta").textContent = c.target_role_label + " \u00b7 " + reasonLabel;
    document.getElementById("cd-timeline").innerHTML =
      '<li class="done"><strong>Filed</strong><span>' + VA.escapeHtml(fmt(c.created_at)) + "</span></li>" +
      '<li class="' + (reviewed ? "done" : "") + (c.status === "UNDER_REVIEW" ? " current" : "") + '"><strong>Under review</strong><span>' +
        VA.escapeHtml(c.under_review_at ? fmt(c.under_review_at) : (reviewed ? "Reviewed" : "Waiting for the administrator")) + "</span></li>" +
      '<li class="' + (closedLabel ? "done " + c.status.toLowerCase() : "") + '"><strong>' + (closedLabel || "Decision") + "</strong><span>" +
        VA.escapeHtml(closedLabel ? (fmt(c.resolved_at) || "") : "Not decided yet") + "</span></li>";
    document.getElementById("cd-text").textContent = c.description;
    document.getElementById("cd-note").innerHTML = c.resolution_note
      ? '<div class="cc-note"><strong>Note from the administrator</strong><span>' + VA.escapeHtml(c.resolution_note) + "</span></div>" : "";
    document.getElementById("cd-privacy").textContent = c.is_anonymous
      ? "You filed this anonymously. Your name is hidden from everyone, including the person it is about. Only the administrator can look up who filed it, and each look-up is recorded."
      : "Only you and the administrator can see this complaint.";
    const actions = document.getElementById("cd-actions");
    actions.innerHTML = "";
    if (isOpen) {
      const resolveBtn = document.createElement("button");
      resolveBtn.className = "role-btn small-btn"; resolveBtn.type = "button"; resolveBtn.textContent = "Mark resolved";
      resolveBtn.addEventListener("click", function () { detailModal.close(); openActionModal(c.id, "self-resolve", c.target_name); });
      const withdrawBtn = document.createElement("button");
      withdrawBtn.className = "danger-btn small-btn"; withdrawBtn.type = "button"; withdrawBtn.textContent = "Withdraw";
      withdrawBtn.addEventListener("click", function () { detailModal.close(); openActionModal(c.id, "withdraw", c.target_name); });
      actions.appendChild(resolveBtn); actions.appendChild(withdrawBtn);
    }
    detailModal.showModal();
  }
  document.getElementById("cd-close").addEventListener("click", function () { detailModal.close(); });

  // ------------------------------------------------------------------------
  // Overview: your own numbers, then the institute-wide (anonymous) picture
  // ------------------------------------------------------------------------
  function setText(id, value) { const e = document.getElementById(id); if (e) e.textContent = value; }
  function duration(hours) {
    if (hours === null || hours === undefined) return "\u2014";
    if (hours < 1) return Math.max(1, Math.round(hours * 60)) + " min";
    if (hours < 48) return (Math.round(hours * 10) / 10) + " h";
    return (Math.round(hours / 24 * 10) / 10) + " days";
  }
  async function loadOverview() {
    const [mine, stats] = await Promise.all([VA.fetchJson("/api/v1/complaints/me"), VA.fetchJson("/api/v1/complaints/stats")]);
    if (mine.ok) {
      const m = mine.body;
      setText("co-mine-total", m.length);
      setText("co-mine-open", m.filter(function (c) { return c.status === "SUBMITTED" || c.status === "UNDER_REVIEW"; }).length);
      setText("co-mine-resolved", m.filter(function (c) { return c.status === "RESOLVED"; }).length);
      setText("co-mine-anon", m.filter(function (c) { return c.is_anonymous; }).length);
    }
    const ids = ["co-chart-status", "co-chart-reason", "co-chart-weekly"];
    if (!stats.ok) {
      ids.forEach(function (id) { document.getElementById(id).innerHTML = '<p class="chart-empty">Could not load the figures right now.</p>'; });
      return;
    }
    const d = stats.body, k = d.kpis, P = Charts.palette();
    const color = { SUBMITTED: P.yellow, UNDER_REVIEW: P.blueSoft, RESOLVED: P.green, DISMISSED: P.grey, WITHDRAWN: P.greyLight };
    setText("co-all-total", k.total);
    const diff = k.filed_last_30_days - k.filed_previous_30_days;
    setText("co-all-total-sub", k.filed_last_30_days + " in the last 30 days" + (diff ? (diff > 0 ? " (\u25B2 " + diff + ")" : " (\u25BC " + Math.abs(diff) + ")") : ""));
    setText("co-all-open", k.open);
    setText("co-all-open-sub", k.under_review + " being reviewed");
    setText("co-all-rate", d.resolution_rate === null ? "\u2014" : d.resolution_rate + "%");
    setText("co-all-avg", duration(k.avg_resolution_hours));
    Charts.donut(document.getElementById("co-chart-status"), d.by_status.map(function (s) { return { label: s.label, count: s.count, color: color[s.key] }; }),
      { title: "Complaints by status", centerLabel: "complaints", empty: "No complaints have been filed yet." });
    Charts.hbars(document.getElementById("co-chart-reason"), d.by_reason.map(function (r) { return { label: r.label, count: r.count }; }), { empty: "No complaints have been filed yet." });
    Charts.lineChart(document.getElementById("co-chart-weekly"), d.by_week.map(function (w) {
      return { label: new Date(w.week_start + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" }), filed: w.filed, closed: w.closed };
    }), [{ key: "filed", label: "Filed", color: P.blue }, { key: "closed", label: "Closed", color: P.green }],
    { title: "Complaints filed and closed per week", empty: "No complaints in the last 12 weeks." });
  }
  const fileLink = document.getElementById("co-file-link");
  if (fileLink) fileLink.addEventListener("click", function (e) { e.preventDefault(); VA.showTab("new"); });

  // A small progress line: Submitted -> Under review -> Outcome
  function steps(status) {
    const reviewed = status === "UNDER_REVIEW" || status === "RESOLVED" || status === "DISMISSED";
    const outcome = { RESOLVED: "Resolved", DISMISSED: "Dismissed", WITHDRAWN: "Withdrawn" }[status];
    return '<ol class="cc-steps"><li class="done">Submitted</li>' +
      '<li class="' + (reviewed ? "done" : "") + (status === "UNDER_REVIEW" ? " current" : "") + '">Under review</li>' +
      '<li class="' + (outcome ? "done " + status.toLowerCase() : "") + '">' + (outcome || "Decision") + "</li></ol>";
  }

  async function loadMyComplaints() {
    const result = await VA.fetchJson("/api/v1/complaints/me");
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load your complaints."));
      return;
    }
    VA.clearError();
    myList.innerHTML = "";
    myEmpty.hidden = result.body.length > 0;
    result.body.forEach(function (c) {
      const reasonLabel = c.reason === "OTHER" && c.custom_reason ? c.custom_reason : c.reason.replace(/_/g, " ").toLowerCase().replace(/^./, function (m) { return m.toUpperCase(); });
      const isOpen = c.status === "SUBMITTED" || c.status === "UNDER_REVIEW";
      const card = document.createElement("article");
      card.className = "complaint-card " + (c.status || "").toLowerCase();
      card.innerHTML =
        '<div class="cc-head"><div><strong>' + VA.escapeHtml(c.target_name) + '</strong><span class="cc-role">' + VA.escapeHtml(c.target_role_label) + "</span></div>" +
        '<div class="cc-badges">' + (c.is_anonymous ? '<span class="anon-badge" title="Your name is hidden from everyone except the administrator">\uD83D\uDD12 Anonymous</span>' : "") + statusPillHtml(c.status) + "</div></div>" +
        '<p class="cc-meta">' + VA.escapeHtml(reasonLabel) + " \u00b7 Filed " + VA.escapeHtml(VA.formatDateTime(c.created_at)) + "</p>" +
        steps(c.status) +
        '<p class="cc-desc">' + VA.escapeHtml(c.description) + "</p>" +
        (c.resolution_note ? '<div class="cc-note"><strong>Note from the administrator</strong><span>' + VA.escapeHtml(c.resolution_note) + "</span></div>" : "") +
        '<div class="cc-actions"></div>';
      const actions = card.querySelector(".cc-actions");
      const viewBtn = document.createElement("button");
      viewBtn.className = "role-btn small-btn secondary-btn"; viewBtn.type = "button"; viewBtn.textContent = "View details";
      viewBtn.addEventListener("click", function () { openDetail(c); });
      actions.appendChild(viewBtn);
      if (isOpen) {
        const resolveBtn = document.createElement("button");
        resolveBtn.className = "role-btn small-btn";
        resolveBtn.type = "button";
        resolveBtn.textContent = "Mark resolved";
        resolveBtn.addEventListener("click", function () { openActionModal(c.id, "self-resolve", c.target_name); });
        const withdrawBtn = document.createElement("button");
        withdrawBtn.className = "danger-btn small-btn";
        withdrawBtn.type = "button";
        withdrawBtn.textContent = "Withdraw";
        withdrawBtn.addEventListener("click", function () { openActionModal(c.id, "withdraw", c.target_name); });
        actions.appendChild(resolveBtn);
        actions.appendChild(withdrawBtn);
      }
      card.classList.add("clickable");
      card.addEventListener("click", function (e) { if (!e.target.closest("button")) openDetail(c); });
      myList.appendChild(card);
    });
  }

  function openActionModal(complaintId, kind, targetName) {
    pendingAction = { complaintId: complaintId, kind: kind };
    actionTitle.textContent = kind === "withdraw" ? "Withdraw Complaint" : "Mark Complaint Resolved";
    actionSummary.textContent = "Complaint against " + targetName;
    actionNote.value = "";
    actionError.hidden = true;
    actionSubmit.textContent = kind === "withdraw" ? "Confirm Withdraw" : "Confirm Resolved";
    actionModal.showModal();
  }

  closeActionModalBtn.addEventListener("click", function () {
    actionModal.close();
    pendingAction = null;
  });

  actionForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!pendingAction) return;
    actionSubmit.disabled = true;
    actionError.hidden = true;

    const result = await VA.fetchJson("/api/v1/complaints/" + pendingAction.complaintId + "/" + pendingAction.kind, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: actionNote.value.trim() || null }),
    });

    actionSubmit.disabled = false;

    if (!result.ok) {
      actionError.textContent = extractErrorMessage(result.body, "Could not save.");
      actionError.hidden = false;
      return;
    }

    actionModal.close();
    pendingAction = null;
    loadMyComplaints();
    loadOverview();
  });

  document.addEventListener("vistadmin:ready", function () {
    loadReasons();
    loadMyComplaints();
    loadOverview();
  });
})();