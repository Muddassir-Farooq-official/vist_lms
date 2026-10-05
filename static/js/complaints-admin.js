(function () {
  "use strict";

  const VA = window.VistAdmin;

  const analyticsChart = document.getElementById("analytics-chart");
  const analyticsEmpty = document.getElementById("analytics-empty");

  const filterStatus = document.getElementById("filter-status");
  const filterTargetType = document.getElementById("filter-target-type");
  const filterReason = document.getElementById("filter-reason");
  const filterAnon = document.getElementById("filter-anon");
  const tbody = document.getElementById("complaints-tbody");
  const emptyState = document.getElementById("complaints-empty");

  const detailModal = document.getElementById("complaint-detail-modal");
  const detailFields = document.getElementById("complaint-detail-fields");
  const statusForm = document.getElementById("complaint-status-form");
  const statusSelect = document.getElementById("complaint-status-select");
  const statusNote = document.getElementById("complaint-status-note");
  const statusSubmit = document.getElementById("complaint-status-submit");
  const detailError = document.getElementById("complaint-detail-error");
  const closeDetailModalBtn = document.getElementById("close-complaint-detail-modal");

  let activeComplaintId = null;

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  function statusPillHtml(status) {
    const label = String(status || "").replace(/_/g, " ").toLowerCase().replace(/^./, function (m) { return m.toUpperCase(); });
    return '<span class="status-pill ' + (status || "").toLowerCase() + '">' + VA.escapeHtml(label) + "</span>";
  }

  async function loadReasonFilter() {
    const result = await VA.fetchJson("/api/v1/complaints/reasons");
    if (!result.ok) return;
    result.body.forEach(function (r) {
      const opt = document.createElement("option");
      opt.value = r.value;
      opt.textContent = r.label;
      filterReason.appendChild(opt);
    });
  }

  async function loadAnalytics() {
    const result = await VA.fetchJson("/api/v1/complaints/analytics");
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load analytics."));
      return;
    }

    analyticsChart.innerHTML = "";
    if (result.body.length === 0) {
      analyticsEmpty.hidden = false;
      return;
    }
    analyticsEmpty.hidden = true;

    const maxCount = Math.max.apply(null, result.body.map(function (e) { return e.total_count; }));

    result.body.forEach(function (entry) {
      const row = document.createElement("div");
      row.className = "bar-chart-row" + (entry.highlighted ? " highlighted" : "");
      const widthPct = Math.max(4, Math.round((entry.total_count / maxCount) * 100));

      row.innerHTML =
        '<div class="bar-chart-label">' + VA.escapeHtml(entry.target_name) +
          '<span class="bar-chart-role">' + VA.escapeHtml(entry.target_role_label) +
            (entry.highlighted ? " \u2014 pattern flagged" : "") + "</span></div>" +
        '<div class="bar-chart-track"><div class="bar-chart-fill" style="width:' + widthPct + '%"></div></div>' +
        '<div class="bar-chart-count">' + entry.total_count + "</div>";

      analyticsChart.appendChild(row);
    });
  }


  // ---- Overview tab: KPIs and charts -------------------------------------------------
  const STATUS_COLOR = function (P) {
    return { SUBMITTED: P.yellow, UNDER_REVIEW: P.blueSoft, RESOLVED: P.green, DISMISSED: P.grey, WITHDRAWN: P.greyLight };
  };
  function setText(id, value) { const e = document.getElementById(id); if (e) e.textContent = value; }
  function duration(hours) {
    if (hours === null || hours === undefined) return "\u2014";
    if (hours < 1) return Math.max(1, Math.round(hours * 60)) + " min";
    if (hours < 48) return (Math.round(hours * 10) / 10) + " h";
    return (Math.round(hours / 24 * 10) / 10) + " days";
  }

  async function loadOverview() {
    const result = await VA.fetchJson("/api/v1/complaints/analytics/overview");
    if (!result.ok) {
      ["chart-status", "chart-reason", "chart-weekly", "chart-filedby", "chart-against"].forEach(function (id) {
        document.getElementById(id).innerHTML = '<p class="chart-empty">' + VA.escapeHtml(extractErrorMessage(result.body, "Could not load the charts.")) + "</p>";
      });
      return;
    }
    const d = result.body, k = d.kpis, P = Charts.palette(), colors = STATUS_COLOR(P);
    setText("kpi-open", k.open);
    setText("kpi-open-sub", k.oldest_open_days === null ? "nothing waiting" : "oldest waiting " + k.oldest_open_days + " day" + (k.oldest_open_days === 1 ? "" : "s"));
    setText("kpi-review", k.under_review);
    setText("kpi-closed", k.resolved + k.dismissed + k.withdrawn);
    setText("kpi-closed-sub", k.resolved + " resolved \u00b7 " + k.dismissed + " dismissed \u00b7 " + k.withdrawn + " withdrawn");
    setText("kpi-avg", duration(k.avg_resolution_hours));
    setText("kpi-30", k.filed_last_30_days);
    const diff = k.filed_last_30_days - k.filed_previous_30_days;
    setText("kpi-30-sub", diff === 0 ? "same as the 30 days before" : (diff > 0 ? "\u25B2 " + diff + " more" : "\u25BC " + Math.abs(diff) + " fewer") + " than the 30 days before");
    setText("kpi-anon", k.anonymous_pct + "%");
    setText("kpi-anon-sub", k.anonymous + " of " + k.total + " complaints");

    Charts.donut(document.getElementById("chart-status"),
      d.by_status.map(function (s) { return { label: s.label, count: s.count, color: colors[s.key] }; }),
      { title: "Complaints by status", centerLabel: "complaints", empty: "No complaints have been filed yet." });
    Charts.hbars(document.getElementById("chart-reason"),
      d.by_reason.map(function (r) { return { label: r.label, count: r.count }; }), { empty: "No complaints have been filed yet." });
    Charts.lineChart(document.getElementById("chart-weekly"),
      d.by_week.map(function (w) {
        return { label: new Date(w.week_start + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" }), filed: w.filed, closed: w.closed };
      }),
      [{ key: "filed", label: "Filed", color: P.blue }, { key: "closed", label: "Closed", color: P.green }],
      { title: "Complaints filed and closed per week", empty: "No complaints in the last 12 weeks." });
    Charts.donut(document.getElementById("chart-filedby"),
      d.filed_by.map(function (x, i) { return { label: x.label, count: x.count, color: [P.blue, P.yellow][i % 2] }; }),
      { title: "Who files complaints", centerLabel: "complaints", empty: "No complaints yet." });
    Charts.donut(document.getElementById("chart-against"),
      d.filed_against.map(function (x, i) { return { label: x.label, count: x.count, color: [P.blue, P.yellow][i % 2] }; }),
      { title: "Who complaints are about", centerLabel: "complaints", empty: "No complaints yet." });
  }

  async function loadComplaints() {
    const params = new URLSearchParams();
    if (filterStatus.value) params.set("status", filterStatus.value);
    if (filterTargetType.value) params.set("target_type", filterTargetType.value);
    if (filterReason.value) params.set("reason", filterReason.value);

    const result = await VA.fetchJson("/api/v1/complaints?" + params.toString());
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load complaints."));
      return;
    }
    VA.clearError();

    tbody.innerHTML = "";
    const shown = result.body.filter(function (c) {
      return !filterAnon.value || (filterAnon.value === "anon") === c.is_anonymous;
    });
    emptyState.hidden = shown.length > 0;

    shown.forEach(function (c) {
      const tr = document.createElement("tr");
      tr.className = "clickable-row";
      const reasonLabel = c.reason === "OTHER" && c.custom_reason ? c.custom_reason : c.reason;

      tr.innerHTML =
        "<td>" + VA.escapeHtml(c.target_name) + " (" + VA.escapeHtml(c.target_role_label) + ")</td>" +
        "<td>" + (c.is_anonymous ? '<span class="anon-badge">\uD83D\uDD12 Anonymous</span>' : VA.escapeHtml(c.complainant_name)) + "</td>" +
        "<td>" + VA.escapeHtml(reasonLabel) + "</td>" +
        "<td>" + statusPillHtml(c.status) + "</td>" +
        "<td>" + VA.formatDateTime(c.created_at) + "</td>" +
        '<td><span class="row-action">View &rarr;</span></td>';

      tr.addEventListener("click", function () {
        openDetailModal(c);
      });

      tbody.appendChild(tr);
    });
  }

  function openDetailModal(c) {
    activeComplaintId = c.id;
    detailError.hidden = true;
    const reasonLabel = c.reason === "OTHER" && c.custom_reason ? c.custom_reason + " (Other)" : c.reason;

    detailFields.innerHTML =
      "<dt>Against</dt><dd>" + VA.escapeHtml(c.target_name) + " \u2014 " + VA.escapeHtml(c.target_role_label) +
        (c.target_identifier ? " (" + VA.escapeHtml(c.target_identifier) + ")" : "") + "</dd>" +
      "<dt>Filed By</dt><dd id=\"filer-line\">" + (c.is_anonymous
        ? '<span class="anon-badge">\uD83D\uDD12 Anonymous</span> <button type="button" class="role-btn small-btn secondary-btn" id="reveal-btn">Reveal name</button>' +
          '<small class="reveal-hint">Revealing who filed an anonymous complaint is recorded in the audit log.</small>'
        : VA.escapeHtml(c.complainant_name) + (c.complainant_identifier ? " (" + VA.escapeHtml(c.complainant_identifier) + ")" : "")) + "</dd>" +
      "<dt>Reason</dt><dd>" + VA.escapeHtml(reasonLabel) + "</dd>" +
      "<dt>Description</dt><dd>" + VA.escapeHtml(c.description) + "</dd>" +
      "<dt>Status</dt><dd>" + statusPillHtml(c.status) + "</dd>" +
      "<dt>Filed</dt><dd>" + VA.formatDateTime(c.created_at) + "</dd>" +
      (c.resolution_note ? "<dt>Resolution Note</dt><dd>" + VA.escapeHtml(c.resolution_note) + "</dd>" : "") +
      (c.resolved_by_name ? "<dt>Resolved By</dt><dd>" + VA.escapeHtml(c.resolved_by_name) + "</dd>" : "");

    const isTerminal = c.status === "RESOLVED" || c.status === "DISMISSED" || c.status === "WITHDRAWN";
    statusForm.hidden = isTerminal;
    statusNote.value = "";

    // Anonymous: the name stays hidden until the admin deliberately asks (and that is logged).
    const revealBtn = document.getElementById("reveal-btn");
    if (revealBtn) {
      revealBtn.addEventListener("click", async function () {
        revealBtn.disabled = true;
        const r = await VA.fetchJson("/api/v1/complaints/" + c.id + "/reveal-identity", { method: "POST" });
        if (!r.ok) {
          revealBtn.disabled = false;
          detailError.textContent = extractErrorMessage(r.body, "Could not reveal the name.");
          detailError.hidden = false;
          return;
        }
        document.getElementById("filer-line").innerHTML = "<strong>" + VA.escapeHtml(r.body.complainant_name) + "</strong>" +
          (r.body.complainant_identifier ? " (" + VA.escapeHtml(r.body.complainant_identifier) + ")" : "") +
          ' <span class="anon-badge">\uD83D\uDD12 filed anonymously</span>';
      });
    }
    detailModal.showModal();
  }

  closeDetailModalBtn.addEventListener("click", function () {
    detailModal.close();
    activeComplaintId = null;
  });

  statusForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!activeComplaintId) return;
    statusSubmit.disabled = true;
    detailError.hidden = true;

    const result = await VA.fetchJson("/api/v1/complaints/" + activeComplaintId + "/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: statusSelect.value, note: statusNote.value.trim() || null }),
    });

    statusSubmit.disabled = false;

    if (!result.ok) {
      detailError.textContent = extractErrorMessage(result.body, "Could not update status.");
      detailError.hidden = false;
      return;
    }

    detailModal.close();
    activeComplaintId = null;
    loadComplaints();
    loadAnalytics();
  });

  filterStatus.addEventListener("change", loadComplaints);
  filterTargetType.addEventListener("change", loadComplaints);
  filterReason.addEventListener("change", loadComplaints);
  filterAnon.addEventListener("change", loadComplaints);

  document.addEventListener("vistadmin:ready", function () {
    loadReasonFilter();
    loadOverview();
    loadAnalytics();
    loadComplaints();
  });
})();