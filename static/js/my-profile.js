(function () {
  "use strict";

  const VA = window.VistAdmin;
  const isFaculty = window.location.pathname.indexOf("/faculty/") !== -1;
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
      phAvatar.innerHTML = p.has_photo ? '<img src="/api/v1/' + ENTITY + '/me/photo" alt="" />' : VA.escapeHtml(SU.initials(myFullName));
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
  const photoFrame = document.getElementById("my-photo-frame");
  const photoInput = document.getElementById("my-photo-input");
  const photoUploadBtn = document.getElementById("my-photo-upload-btn");
  const photoDownloadLink = document.getElementById("my-photo-download-link");
  const photoRemoveBtn = document.getElementById("my-photo-remove-btn");
  const photoError = document.getElementById("my-photo-error");

  function myPhotoUrl() {
    return "/api/v1/" + ENTITY + "/me/photo";
  }

  function initialsAvatarHtml(fullName) {
    const parts = (fullName || "").trim().split(/\s+/).filter(Boolean);
    const initials = ((parts[0] ? parts[0][0] : "") + (parts[1] ? parts[1][0] : "")).toUpperCase() || "?";
    const palette = ["#2563eb", "#7c3aed", "#db2777", "#dc2626", "#d97706", "#059669", "#0891b2", "#4f46e5"];
    let hash = 0;
    for (let i = 0; i < (fullName || "").length; i++) hash = (hash * 31 + fullName.charCodeAt(i)) >>> 0;
    const color = palette[hash % palette.length];
    return '<div class="profile-photo-initials" style="background:' + color + '">' + VA.escapeHtml(initials) + "</div>";
  }

  let myFullName = "";

  function updatePhotoDisplay(hasPhoto) {
    photoError.hidden = true;
    if (hasPhoto) {
      photoFrame.innerHTML = '<img src="' + myPhotoUrl() + "?t=" + Date.now() + '" alt="" />';
      photoDownloadLink.href = myPhotoUrl();
      photoDownloadLink.setAttribute("download", "my-photo.jpg");
      photoDownloadLink.hidden = false;
      photoRemoveBtn.hidden = false;
    } else {
      photoFrame.innerHTML = initialsAvatarHtml(myFullName);
      photoDownloadLink.hidden = true;
      photoRemoveBtn.hidden = true;
    }
  }

  photoUploadBtn.addEventListener("click", async function () {
    photoError.hidden = true;
    if (!photoInput.files.length) {
      photoError.textContent = "Choose an image file first.";
      photoError.hidden = false;
      return;
    }
    photoUploadBtn.disabled = true;
    const formData = new FormData();
    formData.append("file", photoInput.files[0]);
    const result = await VA.fetchJson(myPhotoUrl(), { method: "POST", body: formData });
    photoUploadBtn.disabled = false;

    if (!result.ok) {
      photoError.textContent = extractErrorMessage(result.body, "Could not upload photo.");
      photoError.hidden = false;
      return;
    }
    photoInput.value = "";
    updatePhotoDisplay(true);
  });

  photoRemoveBtn.addEventListener("click", async function () {
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