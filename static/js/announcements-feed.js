(function () {
  "use strict";

  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const PAGE = 20;

  const listEl = document.getElementById("ann-list");
  const moreBtn = document.getElementById("ann-more");
  const emptyEl = document.getElementById("ann-empty");
  const searchEl = document.getElementById("ann-search");
  const categoryEl = document.getElementById("ann-category");
  const unreadEl = document.getElementById("ann-unread");
  const modal = document.getElementById("ann-modal");

  let items = [];
  let offset = 0;
  let searchTimer = null;

  const AU = window.AnnouncementUI;
  const initials = AU.initials, when = AU.when, badges = AU.badges, hueOf = AU.hueOf;

  function card(a) {
    const el = document.createElement("article");
    el.className = "ann-card " + a.priority.toLowerCase() + (a.is_read ? "" : " unread");
    el.tabIndex = 0;
    el.setAttribute("role", "button");
    el.innerHTML =
      '<div class="ann-card-top"><div class="ann-badges">' + badges(a) + "</div>" + (a.is_read ? "" : '<span class="ann-new">New</span>') + "</div>" +
      "<h3>" + esc(a.title) + "</h3>" +
      '<p class="ann-snippet">' + esc(a.body.replace(/\s+/g, " ").slice(0, 220)) + (a.body.length > 220 ? "\u2026" : "") + "</p>" +
      '<div class="ann-card-foot"><span class="ann-who"><span class="sh-avatar" style="--h:' + hueOf(a.issuer_name) + '">' + esc(initials(a.issuer_name)) + "</span><span><strong>" + esc(a.issuer_name) +
      "</strong><small>" + esc(a.issuer_designation) + "</small></span></span><span class=\"ann-meta\">" +
      (a.attachments.length ? "\uD83D\uDCCE " + a.attachments.length + " \u00b7 " : "") + esc(when(a.published_at)) + "</span></div>";
    const open = function () { openDetail(a); };
    el.addEventListener("click", open);
    el.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    return el;
  }

  function render() {
    listEl.innerHTML = "";
    emptyEl.hidden = items.length > 0;
    items.forEach(function (a) { listEl.appendChild(card(a)); });
  }

  function params(extra) {
    const p = new URLSearchParams({ limit: String(PAGE), offset: String(extra) });
    if (categoryEl.value) p.set("category", categoryEl.value);
    if (unreadEl.checked) p.set("unread_only", "true");
    if (searchEl.value.trim()) p.set("q", searchEl.value.trim());
    return p.toString();
  }

  async function load(reset) {
    if (reset) {
      offset = 0;
      listEl.innerHTML = '<div class="skeleton-stack"><div class="skeleton-line"></div><div class="skeleton-line short"></div><div class="skeleton-line"></div></div>';
    }
    const r = await VA.fetchJson("/api/v1/announcements?" + params(offset));
    if (!r.ok) {
      listEl.innerHTML = '<p class="home-empty">' + esc((r.body && r.body.error && r.body.error.message) || "Could not load announcements.") + "</p>";
      return;
    }
    items = reset ? r.body : items.concat(r.body);
    offset += r.body.length;
    moreBtn.hidden = r.body.length < PAGE;
    emptyEl.textContent = (searchEl.value || categoryEl.value || unreadEl.checked) ? "No announcements match." : "There are no announcements here yet.";
    render();
  }

  async function openDetail(a) {
    AU.openDetail(a);
    if (!a.is_read) {
      a.is_read = true;
      render();
      const r = await VA.fetchJson("/api/v1/announcements/" + a.id + "/read", { method: "POST", quiet: true });
      if (r.ok) window.dispatchEvent(new CustomEvent("announcements:unread", { detail: r.body.unread_count }));
    }
  }

  document.getElementById("ann-modal-close").addEventListener("click", function () { modal.close(); });
  moreBtn.addEventListener("click", function () { load(false); });
  categoryEl.addEventListener("change", function () { load(true); });
  unreadEl.addEventListener("change", function () { load(true); });
  searchEl.addEventListener("input", function () { clearTimeout(searchTimer); searchTimer = setTimeout(function () { load(true); }, 300); });
  document.getElementById("ann-readall").addEventListener("click", async function () {
    const r = await VA.fetchJson("/api/v1/announcements/read-all", { method: "POST" });
    if (r.ok) {
      items.forEach(function (a) { a.is_read = true; });
      render();
      window.dispatchEvent(new CustomEvent("announcements:unread", { detail: 0 }));
      VA.toast("All announcements marked as read.");
    }
  });

  document.addEventListener("vistadmin:ready", async function () {
    await load(true);
    // arrived from a bell notification or the Home page: open that announcement
    const wanted = new URLSearchParams(window.location.search).get("open");
    if (!wanted) return;
    let target = items.find(function (a) { return a.id === wanted; });
    if (!target) {
      const r = await VA.fetchJson("/api/v1/announcements/" + wanted);
      if (r.ok) target = r.body;
      else VA.toast("That announcement is no longer available.", "error");
    }
    if (target) openDetail(target);
  });
})();