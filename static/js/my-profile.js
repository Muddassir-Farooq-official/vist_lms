(function () {
  "use strict";

  const VA = window.VistAdmin;
  // The student portal shows the student record; every staff portal (faculty, offices, general staff) the employee record.
  const isFaculty = window.location.pathname.indexOf("/dashboard/student/") !== 0;   // i.e. any staff portal
  const ENTITY = isFaculty ? "employees" : "students";

  let myId = null; // resolved from /me on first load, needed to build the photo/certificate URLs

  function extractErrorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  // ---------------------------------------------------------------------
  // Personal details
  // ---------------------------------------------------------------------
  const detailsList = document.getElementById("my-profile-details");

  function fieldRow(label, value) {
    if (value === null || value === undefined || value === "") return "";
    return "<dt>" + label + "</dt><dd>" + VA.escapeHtml(value) + "</dd>";
  }

  async function loadMyDetails() {
    const result = await VA.fetchJson("/api/v1/" + ENTITY + "/me");
    if (!result.ok) {
      VA.showError(extractErrorMessage(result.body, "Could not load your profile."));
      return;
    }
    VA.clearError();

    const p = result.body;
    myId = p.id;
    myFullName = p.first_name + " " + p.last_name;
    // Header card: photo or initials, name, roll number, where they are in their studies.
    const phAvatar = document.getElementById("ph-avatar");
    if (phAvatar) {
      const SU = window.StudentUI;
      document.getElementById("ph-name").textContent = myFullName;
      if (isFaculty) {
        document.getElementById("ph-sub").textContent = (p.employee_code || "") + (p.department ? " \u00b7 " + p.department : "");
        document.getElementById("ph-pills").innerHTML = SU.pill(p.status || "ACTIVE") +
          (p.employment_type ? '<span class="pill">' + VA.escapeHtml(String(p.employment_type).replace(/_/g, " ").toLowerCase().replace(/^./, function (m) { return m.toUpperCase(); })) + "</span>" : "");
      } else {
        document.getElementById("ph-sub").textContent = "Roll number " + (p.roll_number || "\u2014") + (p.admission_session ? " \u00b7 " + p.admission_session + " intake" : "");
        document.getElementById("ph-pills").innerHTML = SU.pill(p.status || "ACTIVE") +
          (p.current_semester ? '<span class="pill">Semester ' + VA.escapeHtml(p.current_semester) + "</span>" : "") +
          (p.section_label ? '<span class="pill">Section ' + VA.escapeHtml(p.section_label) + "</span>" : "");
      }
    }
    const rows = isFaculty
      ? [
          fieldRow("Employee Code / Username", p.employee_code),
          fieldRow("CNIC", p.cnic),
          fieldRow("Contact No.", p.contact_no),
          fieldRow("Date of Birth", p.date_of_birth),
          fieldRow("Gender", p.gender),
          fieldRow("Hire Date", p.hire_date),
          fieldRow("Department", p.department),
          fieldRow("Status", p.status),
          fieldRow("Religion", p.religion),
          fieldRow("Address", p.address_line),
          fieldRow("Province", p.province),
          fieldRow("District", p.district),
          fieldRow("City", p.city),
        ]
      : [
          fieldRow("Roll Number / Username", p.roll_number),
          fieldRow("CNIC", p.cnic),
          fieldRow("Contact No.", p.contact_no),
          fieldRow("Date of Birth", p.date_of_birth),
          fieldRow("Gender", p.gender),
          fieldRow("Marital Status", p.marital_status),
          fieldRow("Religion", p.religion),
          fieldRow("Nationality", p.nationality),
          fieldRow("Current Semester", p.current_semester),
          fieldRow("Enrollment Date", p.enrollment_date),
          fieldRow("Status", p.status),
          fieldRow("Address", p.present_address),
          fieldRow("Province", p.province),
          fieldRow("District", p.district),
          fieldRow("City", p.city),
        ];

    detailsList.innerHTML = rows.join("");
    updatePhotoDisplay(p.has_photo);
    updateCertificateDisplay(p.has_certificate);
  }

  // ---------------------------------------------------------------------
  // Profile photo — self-service equivalent of the admin panel's photo
  // block (static/js/students.js / employees.js), same pattern.
  // ---------------------------------------------------------------------
  // Clicking the photo (or the small camera) chooses a new one and sends it at once. Students and staff: it waits for the
  // office's approval (202); the current photo stays until then.
  const photoInput = document.getElementById("my-photo-input");
  const photoDownloadLink = document.getElementById("my-photo-download-link");
  const photoRemoveBtn = document.getElementById("my-photo-remove-btn");
  const photoError = document.getElementById("my-photo-error");
  const avatarBtn = document.getElementById("ph-avatar");
  const PALETTE = ["#2563eb", "#7c3aed", "#db2777", "#dc2626", "#d97706", "#059669", "#0891b2", "#4f46e5"];

  function myPhotoUrl() {
    return "/api/v1/" + ENTITY + "/me/photo";
  }

  let myFullName = "";

  function updatePhotoDisplay(hasPhoto) {
    photoError.hidden = true;
    const img = document.getElementById("ph-photo"), ini = document.getElementById("ph-initials");
    let h = 0; for (let i = 0; i < myFullName.length; i++) h = (h * 31 + myFullName.charCodeAt(i)) >>> 0;
    avatarBtn.style.setProperty("--av", PALETTE[h % PALETTE.length]);
    ini.textContent = window.StudentUI ? window.StudentUI.initials(myFullName) : (myFullName[0] || "?");
    if (hasPhoto) { img.src = myPhotoUrl() + "?t=" + Date.now(); img.hidden = false; ini.hidden = true; photoDownloadLink.href = myPhotoUrl(); }
    else { img.hidden = true; img.removeAttribute("src"); ini.hidden = false; }
    photoDownloadLink.hidden = !hasPhoto;
    photoRemoveBtn.hidden = !hasPhoto;
  }

  avatarBtn.addEventListener("click", function () { photoInput.click(); });
  photoInput.addEventListener("change", async function () {
    photoError.hidden = true;
    if (!photoInput.files.length) return;
    const file = photoInput.files[0];
    if (file.size > 5 * 1024 * 1024) { photoError.textContent = "That image is larger than 5 MB. Choose a smaller one."; photoError.hidden = false; photoInput.value = ""; return; }
    avatarBtn.classList.add("is-busy");
    const formData = new FormData();
    formData.append("file", file);
    const result = await VA.fetchJson(myPhotoUrl(), { method: "POST", body: formData });
    avatarBtn.classList.remove("is-busy");
    photoInput.value = "";
    if (!result.ok) {
      photoError.textContent = extractErrorMessage(result.body, "Could not upload photo.");
      photoError.hidden = false;
      return;
    }
    if (result.status === 202 || (result.body && result.body.pending_approval)) {
      const sent = document.getElementById("my-photo-sent");
      if (sent) sent.hidden = false;
      document.dispatchEvent(new CustomEvent("profile-changes:refresh"));
      return;
    }
    updatePhotoDisplay(true);
  });

  photoRemoveBtn.addEventListener("click", async function () {
    if (!(await VA.confirm("Remove your photo?", "Remove"))) return;
    photoError.hidden = true;
    photoRemoveBtn.disabled = true;
    const result = await VA.fetchJson(myPhotoUrl(), { method: "DELETE" });
    photoRemoveBtn.disabled = false;
    if (!result.ok && result.status !== 204) {
      photoError.textContent = extractErrorMessage(result.body, "Could not remove photo.");
      photoError.hidden = false;
      return;
    }
    updatePhotoDisplay(false);
  });

  // ---------------------------------------------------------------------
  // Certificate PDF — same pattern as photo, different endpoint/type.
  // ---------------------------------------------------------------------
  const certInput = document.getElementById("my-certificate-input");
  const certUploadBtn = document.getElementById("my-certificate-upload-btn");
  const certDownloadLink = document.getElementById("my-certificate-download-link");
  const certRemoveBtn = document.getElementById("my-certificate-remove-btn");
  const certError = document.getElementById("my-certificate-error");

  function myCertificateUrl() {
    return "/api/v1/" + ENTITY + "/me/certificate";
  }

  function updateCertificateDisplay(hasCertificate) {
    certError.hidden = true;
    if (hasCertificate) {
      certDownloadLink.href = myCertificateUrl();
      certDownloadLink.setAttribute("download", "my-certificates.pdf");
      certDownloadLink.hidden = false;
      certRemoveBtn.hidden = false;
    } else {
      certDownloadLink.hidden = true;
      certRemoveBtn.hidden = true;
    }
  }

  certUploadBtn.addEventListener("click", async function () {
    certError.hidden = true;
    if (!certInput.files.length) {
      certError.textContent = "Choose a PDF file first.";
      certError.hidden = false;
      return;
    }
    certUploadBtn.disabled = true;
    const formData = new FormData();
    formData.append("file", certInput.files[0]);
    const result = await VA.fetchJson(myCertificateUrl(), { method: "POST", body: formData });
    certUploadBtn.disabled = false;

    if (!result.ok) {
      certError.textContent = extractErrorMessage(result.body, "Could not upload certificate PDF.");
      certError.hidden = false;
      return;
    }
    certInput.value = "";
    updateCertificateDisplay(true);
  });

  certRemoveBtn.addEventListener("click", async function () {
    certError.hidden = true;
    certRemoveBtn.disabled = true;
    const result = await VA.fetchJson(myCertificateUrl(), { method: "DELETE" });
    certRemoveBtn.disabled = false;

    if (!result.ok && result.status !== 204) {
      certError.textContent = extractErrorMessage(result.body, "Could not remove certificate.");
      certError.hidden = false;
      return;
    }
    updateCertificateDisplay(false);
  });

  // ---------------------------------------------------------------------
  // Qualifications — only rendered/available for students today (the
  // form is present on both pages via the shared template, but faculty
  // qualifications are just as valid — the backend already supports
  // EMPLOYEE party_type identically, so this works unchanged for both).
  // ---------------------------------------------------------------------
  const qualTbody = document.getElementById("my-qualifications-tbody");
  const qualEmpty = document.getElementById("my-qualifications-empty");
  const qualForm = document.getElementById("add-qualification-form");
  const qualSubmit = document.getElementById("add-qualification-submit");
  const qualError = document.getElementById("qualification-error");

  function partyTypeParam() {
    return isFaculty ? "EMPLOYEE" : "STUDENT";
  }

  async function loadQualifications() {
    if (!myId) return;
    const params = new URLSearchParams({ party_type: partyTypeParam(), party_id: myId });
    const result = await VA.fetchJson("/api/v1/profiles/qualifications?" + params.toString());
    if (!result.ok) return;

    qualTbody.innerHTML = "";
    if (result.body.length === 0) {
      qualEmpty.hidden = false;
      return;
    }
    qualEmpty.hidden = true;

    result.body.forEach(function (q) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + VA.escapeHtml(q.degree_title) + "</td>" +
        "<td>" + VA.escapeHtml(q.institution_name) + "</td>" +
        "<td>" + VA.escapeHtml(q.year_completed || "\u2014") + "</td>" +
        "<td>" + VA.escapeHtml(q.marks_or_grade || "\u2014") + "</td>" +
        '<td><button type="button" class="danger-btn small-btn qual-delete-btn" data-id="' + q.id + '">Remove</button></td>';
      qualTbody.appendChild(tr);
    });

    qualTbody.querySelectorAll(".qual-delete-btn").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        btn.disabled = true;
        const result = await VA.fetchJson("/api/v1/profiles/qualifications/" + btn.getAttribute("data-id"), { method: "DELETE" });
        if (!result.ok && result.status !== 204) {
          qualError.textContent = extractErrorMessage(result.body, "Could not remove qualification.");
          qualError.hidden = false;
          btn.disabled = false;
          return;
        }
        loadQualifications();
      });
    });
  }

  qualForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (!myId) return;
    qualSubmit.disabled = true;
    qualError.hidden = true;

    const yearValue = document.getElementById("q-year").value.trim();
    const params = new URLSearchParams({ party_type: partyTypeParam(), party_id: myId });
    const result = await VA.fetchJson("/api/v1/profiles/qualifications?" + params.toString(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        degree_title: document.getElementById("q-degree").value.trim(),
        institution_name: document.getElementById("q-institution").value.trim(),
        year_completed: yearValue ? parseInt(yearValue, 10) : null,
        marks_or_grade: document.getElementById("q-marks").value.trim() || null,
      }),
    });

    qualSubmit.disabled = false;

    if (!result.ok) {
      qualError.textContent = extractErrorMessage(result.body, "Could not add qualification.");
      qualError.hidden = false;
      return;
    }

    qualForm.reset();
    loadQualifications();
  });

  document.addEventListener("vistadmin:ready", async function () {
    await loadMyDetails();
    await loadQualifications();
  });
})();