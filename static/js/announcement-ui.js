/* Rendering shared by what readers see and what the administrator previews. */
window.AnnouncementUI = (function () {
  "use strict";
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;

  function hueOf(name) { let h = 0; for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360; return h; }

  function initials(name) {
    const p = String(name || "").replace(/^(dr|prof|mr|mrs|ms|engr)\.?\s+/i, "").trim().split(/\s+/).filter(Boolean);
    return p.length ? (p[0][0] + (p.length > 1 ? p[p.length - 1][0] : "")).toUpperCase() : "?";
  }
  function when(iso) {
    if (!iso) return "";
    const d = new Date(iso), now = new Date();
    const days = Math.floor((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
    const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    if (days === 0) return "Today, " + time;
    if (days === 1) return "Yesterday, " + time;
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" });
  }
  function size(bytes) { return bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(bytes / 1024)) + " KB"; }
  function fileKind(name) { const m = /\.([a-z0-9]+)$/i.exec(name || ""); return m ? m[1].toUpperCase() : "FILE"; }

  // Plain text in, safe HTML out: paragraphs and clickable web links, nothing else.
  function format(text) {
    return String(text || "").split(/\n{2,}/).map(function (para) {
      const html = esc(para).replace(/\n/g, "<br>").replace(/(https?:\/\/[^\s<]+)/g, function (u) { return '<a href="' + u + '" target="_blank" rel="noopener noreferrer">' + u + "</a>"; });
      return "<p>" + html + "</p>";
    }).join("");
  }

  function audienceText(a) {
    const who = [];
    if (a.audience_students) who.push(a.program_ids && a.program_ids.length ? "students of selected programs" : "students");
    if (a.audience_teachers) who.push("teachers");
    if (a.audience_staff) who.push("other staff");
    return "Sent to: " + who.join(", ") + ".";
  }

  function badges(a) {
    return (a.pinned ? '<span class="ann-badge pinned">\uD83D\uDCCC Pinned</span>' : "") +
      (a.priority !== "NORMAL" ? '<span class="ann-badge ' + a.priority.toLowerCase() + '">' + (a.priority === "URGENT" ? "Urgent" : "Important") + "</span>" : "") +
      '<span class="ann-badge cat">' + esc(a.category_label) + "</span>";
  }

  function files(a) {
    if (!a.attachments.length) return "";
    const images = a.attachments.filter(function (f) { return f.is_image; });
    const others = a.attachments.filter(function (f) { return !f.is_image; });
    const url = function (f) { return "/api/v1/announcements/attachments/" + f.id + "/file"; };
    return '<h3 class="ann-files-title">Attachments (' + a.attachments.length + ")</h3>" +
      (images.length ? '<div class="ann-images">' + images.map(function (f) {
        return '<a href="' + url(f) + '" target="_blank" rel="noopener"><img loading="lazy" src="' + url(f) + '" alt="' + esc(f.original_name) + '" /></a>';
      }).join("") + "</div>" : "") +
      '<ul class="ann-files">' + a.attachments.map(function (f) {
        const open = f.inline && !f.is_image;
        return '<li><span class="ann-ext">' + esc(fileKind(f.original_name)) + '</span><span class="ann-fname"><strong>' + esc(f.original_name) + "</strong><small>" + size(f.size_bytes) + "</small></span>" +
          (open ? '<a class="role-btn small-btn" href="' + url(f) + '" target="_blank" rel="noopener">Open</a>' : "") +
          '<a class="role-btn small-btn secondary-btn" href="' + url(f) + '" data-download="' + esc(f.original_name) + '" data-label="file">Download</a></li>';
      }).join("") + "</ul>";
  }


  // Fills the detail dialog's parts (the markup lives in templates/_announcements_feed.html).
  function fillDetail(a) {
    document.getElementById("ann-modal-badges").innerHTML = badges(a);
    document.getElementById("ann-modal-title").textContent = a.title;
    document.getElementById("ann-modal-issuer").innerHTML =
      '<span class="sh-avatar" style="--h:' + hueOf(a.issuer_name) + '">' + esc(initials(a.issuer_name)) + "</span><span><small>Issued by</small><strong>" + esc(a.issuer_name) +
      "</strong><em>" + esc(a.issuer_designation) + "</em></span><span class=\"ann-date\">" + esc(when(a.published_at)) + "</span>";
    document.getElementById("ann-modal-body").innerHTML = format(a.body);
    document.getElementById("ann-modal-files").innerHTML = files(a);
    document.getElementById("ann-modal-audience").textContent = audienceText(a);
  }

  function openDetail(a) {
    fillDetail(a);
    const modal = document.getElementById("ann-modal");
    modal.showModal();
    const title = document.getElementById("ann-modal-title");
    title.tabIndex = -1;
    title.focus({ preventScroll: true });
  }

  return { openDetail: openDetail, initials: initials, when: when, size: size, format: format, badges: badges, hueOf: hueOf, files: files, audienceText: audienceText, fillDetail: fillDetail };
})();