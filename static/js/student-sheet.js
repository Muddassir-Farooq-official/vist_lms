(function () {
  "use strict";

  // One student's record in a dialog (templates/_student_sheet.html), and the row used by every student directory table.
  // Read-only for the principal / HOD / exam lookup; on the admin Students page the photo is editable and students.js fills
  // the "Edit details" and "Status & access" tabs from the "studentsheet:loaded" event.
  const VA = window.VistAdmin, esc = VA.escapeHtml;
  const $ = function (id) { return document.getElementById(id); };
  const STATUS = { ACTIVE: "Active", SUSPENDED: "Suspended", GRADUATED: "Graduated", DROPPED: "Left" };
  const PALETTE = ["#2563eb", "#7c3aed", "#db2777", "#dc2626", "#d97706", "#059669", "#0891b2", "#4f46e5"];
  const ICON_EYE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';

  function initials(name) {
    const w = String(name || "").trim().split(/\s+/).filter(Boolean);
    return ((w[0] || "?")[0] + (w.length > 1 ? w[w.length - 1][0] : "")).toUpperCase();
  }
  function colour(name) {
    let h = 0; const s = String(name || "");
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }
  function pill(status) { return '<span class="status-pill ' + esc(String(status || "").toLowerCase()) + '">' + esc(STATUS[status] || status || "") + "</span>"; }
  function avatarHtml(s, size) {
    const name = s.first_name + " " + s.last_name;
    return '<span class="sd-avatar' + (size ? " " + size : "") + '" data-tone="' + PALETTE.indexOf(colour(name)) + '">' +
      (s.has_photo ? '<img src="/api/v1/students/' + esc(s.id) + '/photo" alt="" loading="lazy" />' : esc(initials(name))) + "</span>";
  }
  function money(v) { return VA.formatMoney(v || 0); }
  function fmtDate(d) { return d ? new Date(d + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : ""; }

  // ---- the directory row (admin Students and the lookup pages)
  function rowHtml(s) {
    const name = s.first_name + " " + s.last_name;
    const place = [s.current_semester ? "Semester " + s.current_semester : "", s.section_label ? "Section " + s.section_label : ""].filter(Boolean).join(" · ");
    return '<tr data-open="' + esc(s.id) + '" tabindex="0" aria-label="Open ' + esc(name) + '">' +
      '<td class="sd-who"><div>' + avatarHtml(s) + '<span class="sd-name"><strong>' + esc(name) + "</strong><small>" + esc(s.roll_number || "No roll number yet") +
        (s.gender ? " · " + esc(s.gender) : "") + "</small></span></div></td>" +
      '<td class="sd-prog"><div><strong title="' + esc(s.program_name || "") + '">' + esc(s.program_code || "—") + "</strong><small>" + esc(place || "—") + "</small></div></td>" +
      '<td class="sd-contact">' + (s.contact_no ? '<a href="tel:' + esc(s.contact_no) + '">' + esc(s.contact_no) + "</a>" : '<span class="sd-dim">—</span>') + "</td>" +
      '<td class="sd-session">' + esc(s.admission_session || "—") + "</td>" +
      '<td class="sd-status">' + pill(s.status) + "</td>" +
      (window.PROFILE_COMPLETION_COLUMN ? '<td class="sd-pc" data-pc="' + esc(s.id) + '"><span class="sd-dim">…</span></td>' : "") +
      '<td class="sd-act"><button type="button" class="sd-view" data-open="' + esc(s.id) + '">' + ICON_EYE + "<span>View</span></button></td></tr>";
  }

  // ---- the dialog
  const sheet = $("student-sheet");
  if (!sheet) { window.StudentSheet = { rowHtml: rowHtml, pill: pill }; return; }
  const editable = sheet.getAttribute("data-editable") === "true";
  let current = null;

  function tab(name) {
    sheet.querySelectorAll("[data-ss-tab]").forEach(function (b) {
      const on = b.getAttribute("data-ss-tab") === name;
      b.classList.toggle("active", on); b.setAttribute("aria-selected", on ? "true" : "false");
    });
    sheet.querySelectorAll("[data-ss-panel]").forEach(function (p) { p.hidden = p.getAttribute("data-ss-panel") !== name; });
    const body = sheet.querySelector(".ss-body"); if (body) body.scrollTop = 0;
  }
  sheet.querySelector(".ss-tabs").addEventListener("click", function (e) {
    const b = e.target.closest("[data-ss-tab]"); if (b) tab(b.getAttribute("data-ss-tab"));
  });

  function dl(rows) {
    return rows.filter(function (r) { return r[1] !== null && r[1] !== undefined && r[1] !== ""; })
      .map(function (r) { return "<div><dt>" + esc(r[0]) + "</dt><dd>" + (r[2] ? r[1] : esc(r[1])) + "</dd></div>"; }).join("") || '<p class="ss-empty">Nothing on record.</p>';
  }

  function showPhoto(p) {
    const img = $("ss-photo"), ini = $("ss-initials"), name = p.first_name + " " + p.last_name;
    $("ss-avatar").style.setProperty("--av", colour(name));
    ini.textContent = initials(name);
    if (p.has_photo) {
      img.src = "/api/v1/students/" + p.id + "/photo?t=" + Date.now(); img.hidden = false; ini.hidden = true;
      const ext = ((p.photo_original_name || "").split(".").pop() || "jpg").toLowerCase();
      $("ss-photo-dl").href = "/api/v1/students/" + p.id + "/photo";
      $("ss-photo-dl").setAttribute("data-download", (p.roll_number || "student") + "-photo." + ext);
    } else { img.hidden = true; img.removeAttribute("src"); ini.hidden = false; }
    $("ss-photo-dl").hidden = !p.has_photo;
    if ($("ss-photo-rm")) $("ss-photo-rm").hidden = !p.has_photo;
  }
  function photoMsg(text, bad) {
    const m = $("ss-photo-msg"); m.textContent = text || ""; m.hidden = !text; m.classList.toggle("bad", !!bad);
  }

  function render(p) {
    const name = p.first_name + " " + p.last_name;
    $("ss-name").textContent = name;
    $("ss-sub").textContent = [p.roll_number || "No roll number yet", p.program_name, p.current_semester ? "Semester " + p.current_semester : "",
      p.section_label ? "Section " + p.section_label : ""].filter(Boolean).join(" · ");
    $("ss-chips").innerHTML = pill(p.status) + [p.gender, p.admission_session, p.admission_category, p.blood_group ? "Blood " + p.blood_group : ""]
      .filter(Boolean).map(function (c) { return '<span class="ss-chip">' + esc(c) + "</span>"; }).join("");
    showPhoto(p);
    $("ss-transcript").href = "/api/v1/results/students/" + p.id + "/transcript.pdf";
    $("ss-transcript").setAttribute("data-download", (p.roll_number || "student") + "-transcript.pdf");

    const f = p.fees || {}, fi = p.fines || {}, lib = p.library || {}, c = p.complaints || {};
    $("ss-kpis").innerHTML = [
      ["Fees outstanding", money(f.total_outstanding), f.total_outstanding > 0, money(f.total_paid) + " paid of " + money(f.total_due)],
      ["Fines pending", money(fi.total_pending), fi.total_pending > 0, money(fi.total_paid) + " paid"],
      ["Library books", String(lib.active_loans || 0), lib.overdue_loans > 0, lib.overdue_loans ? lib.overdue_loans + " overdue" : "None overdue"],
      ["Complaints", String(c.filed_against_them || 0), c.filed_against_them > 0, "against · " + (c.filed_by_them || 0) + " filed by them"],
    ].map(function (k) {
      return '<div class="ss-kpi' + (k[2] ? " bad" : "") + '"><span>' + esc(k[0]) + "</span><strong>" + esc(k[1]) + "</strong><small>" + esc(k[3]) + "</small></div>";
    }).join("");
    $("ss-admission").innerHTML = dl([["Roll number", p.roll_number], ["Program", p.program_name], ["Semester", p.current_semester], ["Section / shift", p.section_label],
      ["Session", p.admission_session], ["Category", p.admission_category], ["Enrolled on", fmtDate(p.enrollment_date)],
      ["Admission fee", p.admission_fee_paid === null || p.admission_fee_paid === undefined ? "" : (p.admission_fee_paid ? "Paid" : "Not paid")]]);
    $("ss-personal").innerHTML = dl([["CNIC / B-form", p.cnic], ["Date of birth", fmtDate(p.date_of_birth)], ["Gender", p.gender], ["Marital status", p.marital_status],
      ["Religion", p.religion], ["Nationality", p.nationality], ["Blood group", p.blood_group]]);
    $("ss-contact").innerHTML = dl([
      ["Mobile", p.contact_no ? '<a href="tel:' + esc(p.contact_no) + '">' + esc(p.contact_no) + "</a>" : "", true],
      ["Email", p.email ? '<a href="mailto:' + esc(p.email) + '">' + esc(p.email) + "</a>" : "", true],
      ["Address", p.present_address], ["City", [p.city, p.district, p.province].filter(Boolean).join(", ")]]);

    function card(title, rows) {
      return '<article class="ss-card"><span class="sd-avatar sm" aria-hidden="true">' + esc(initials(title)) + "</span><div><strong>" + esc(title) + "</strong>" +
        rows.filter(function (r) { return r; }).map(function (r) { return "<small>" + r + "</small>"; }).join("") + "</div></article>";
    }
    $("ss-guardians").innerHTML = (p.guardians || []).map(function (g) {
      return card(g.name, [esc(g.relation || "Guardian"), "CNIC " + esc(g.cnic), g.contact_no ? '<a href="tel:' + esc(g.contact_no) + '">' + esc(g.contact_no) + "</a>" : ""]);
    }).join("") || '<p class="ss-empty">No guardian on record.</p>';
    $("ss-emergency").innerHTML = (p.emergency_contacts || []).map(function (e) {
      return card(e.name || "Emergency contact", [esc(e.relation || ""), '<a href="tel:' + esc(e.contact_no) + '">' + esc(e.contact_no) + "</a>"]);
    }).join("") || '<p class="ss-empty">No emergency contact on record.</p>';

    const terms = [], byTerm = {};
    (p.enrollments || []).forEach(function (e) {
      if (!byTerm[e.term_label]) { byTerm[e.term_label] = []; terms.push(e.term_label); }
      byTerm[e.term_label].push(e);
    });
    terms.reverse();
    $("ss-course-count").textContent = (p.enrollments || []).length ? String(p.enrollments.length) : "";
    $("ss-terms-empty").hidden = terms.length > 0;
    $("ss-terms").innerHTML = terms.map(function (t) {
      const list = byTerm[t], cr = list.reduce(function (n, e) { return n + (e.credit_hours || 0); }, 0);
      return '<section class="ss-term"><header><strong>' + esc(t) + "</strong><small>" + list.length + " course" + (list.length === 1 ? "" : "s") + " · " + cr + " credit hours</small></header><ul>" +
        list.map(function (e) {
          return '<li><span class="ss-code">' + esc(e.course_code) + '</span><span class="ss-course">' + esc(e.course_title) + (e.is_retake ? ' <em class="ss-tag">Retake</em>' : "") +
            "<small>" + e.credit_hours + " cr</small></span><span class=\"ss-pills\">" + '<span class="status-pill ' + esc(String(e.status).toLowerCase()) + '">' + esc(e.status) + "</span>" +
            (e.outcome ? '<span class="status-pill ' + esc(String(e.outcome).toLowerCase()) + '">' + esc(e.outcome) + "</span>" : "") + "</span></li>";
        }).join("") + "</ul></section>";
    }).join("");
    $("ss-quals").innerHTML = (p.qualifications || []).map(function (q) {
      return card(q.degree_title, [esc(q.institution_name), [q.year_completed, q.marks_or_grade].filter(Boolean).map(esc).join(" · ")]);
    }).join("") || '<p class="ss-empty">None on record.</p>';
  }

  async function open(id, startTab) {
    current = null;
    $("modal-error").hidden = true; photoMsg("");
    $("ss-name").textContent = "Loading…"; $("ss-sub").textContent = ""; $("ss-chips").innerHTML = "";
    ["ss-kpis", "ss-admission", "ss-personal", "ss-contact", "ss-guardians", "ss-emergency", "ss-terms", "ss-quals"].forEach(function (k) { $(k).innerHTML = ""; });
    tab(startTab || "overview");
    if (!sheet.open) sheet.showModal();
    const r = await VA.fetchJson("/api/v1/students/" + id + "/full-profile");
    if (!r.ok) {
      $("ss-name").textContent = "Could not open this record";
      $("modal-error").textContent = (r.body && r.body.error && r.body.error.message) || "Please try again."; $("modal-error").hidden = false;
      return;
    }
    current = r.body;
    render(current);
    sheet.dispatchEvent(new CustomEvent("studentsheet:loaded", { detail: current }));
  }

  $("ss-close").addEventListener("click", function () { sheet.close(); });
  sheet.addEventListener("click", function (e) { if (e.target === sheet) sheet.close(); });   // click on the dimmed backdrop

  if (editable) {
    const input = $("ss-photo-input");
    $("ss-avatar").addEventListener("click", function () { if (current) input.click(); });
    input.addEventListener("change", async function () {
      if (!current || !input.files.length) return;
      const file = input.files[0];
      if (file.size > 5 * 1024 * 1024) { photoMsg("That image is larger than 5 MB. Choose a smaller one.", true); input.value = ""; return; }
      const fd = new FormData(); fd.append("file", file);
      $("ss-avatar").classList.add("is-busy"); photoMsg("Uploading the photo…");
      const r = await VA.fetchJson("/api/v1/students/" + current.id + "/photo", { method: "POST", body: fd });
      $("ss-avatar").classList.remove("is-busy"); input.value = "";
      if (!r.ok) { photoMsg((r.body && r.body.error && r.body.error.message) || "Could not upload the photo.", true); return; }
      current.has_photo = true; current.photo_original_name = file.name; showPhoto(current); photoMsg("");
      VA.toast("Photo saved.");
      sheet.dispatchEvent(new CustomEvent("studentsheet:changed", { detail: current }));
    });
    $("ss-photo-rm").addEventListener("click", async function () {
      if (!current || !(await VA.confirm("Remove " + current.first_name + "'s photo?", "Remove"))) return;
      const r = await VA.fetchJson("/api/v1/students/" + current.id + "/photo", { method: "DELETE" });
      if (!r.ok && r.status !== 204) { photoMsg((r.body && r.body.error && r.body.error.message) || "Could not remove the photo.", true); return; }
      current.has_photo = false; showPhoto(current); VA.toast("Photo removed.");
      sheet.dispatchEvent(new CustomEvent("studentsheet:changed", { detail: current }));
    });
  }

  window.StudentSheet = { open: open, tab: tab, rowHtml: rowHtml, pill: pill, current: function () { return current; }, element: sheet };
})();