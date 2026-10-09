/* VIST LMS — shared admin page bootstrap.
 *
 * Every admin page includes this before its own page-specific script.
 * It handles the one thing every admin page needs identically: confirm
 * the session is a valid ADMIN session, reveal the shell, wire the
 * drawer + logout button, then fire a `vistadmin:ready` event so the
 * page's own script can load its own data. Centralizing this here means
 * a fix (like the [hidden] CSS bug) only ever needs to happen once.
 */
window.VistAdmin = (function () {
  "use strict";

  // =========================================================================
  // Network activity. Every page loads its data through fetchJson, so this is the
  // one place that makes "the system is working" visible everywhere:
  //   * a thin progress bar at the top of the screen while anything is loading
  //   * skeleton rows in every table on its first load
  //   * a spinner on the button you just pressed (which also blocks double-clicks)
  //   * a "still working" note when something is slow
  //   * plain messages when the network fails or the server does not answer
  // =========================================================================
  const root = document.documentElement;
  const SLOW_AFTER_MS = 8000;        // say something only if a single request has really been waiting this long
  const net = { pending: 0, inflight: new Map(), nextId: 1, idleTimer: null, refreshTimer: null, watcher: null, dismissed: false, lastClick: null, busyButtons: [] };

  function ensureNetUi() {
    if (document.getElementById("net-progress") || !document.body) return;
    const bar = document.createElement("div");
    bar.id = "net-progress";
    bar.setAttribute("aria-hidden", "true");
    bar.innerHTML = "<span></span>";
    document.body.appendChild(bar);
    const note = document.createElement("div");
    note.id = "net-slow";
    note.setAttribute("role", "status");
    note.hidden = true;
    note.innerHTML = '<span id="net-slow-text"></span><button type="button" id="net-slow-close" aria-label="Dismiss">\u00d7</button>';
    document.body.appendChild(note);
    document.getElementById("net-slow-close").addEventListener("click", function () { net.dismissed = true; note.hidden = true; });
  }

  // "/api/v1/academics/allocation-overview?term=x" -> "academics/allocation-overview"
  function shortName(url) {
    return String(url || "").split("?")[0].replace(/^.*\/api\/v1\//, "").replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, "\u2026");
  }

  // Runs once a second while anything is loading. The note shows ONLY while a request is genuinely still
  // waiting (and hides again the moment none is), so it can never linger over a page whose data has arrived.
  function watchSlow() {
    const note = document.getElementById("net-slow");
    if (!note) return;
    let oldest = null;
    net.inflight.forEach(function (r) { if (!oldest || r.t < oldest.t) oldest = r; });
    const age = oldest ? Date.now() - oldest.t : 0;
    if (!oldest || age < SLOW_AFTER_MS) { note.hidden = true; return; }
    if (!oldest.warned) {
      oldest.warned = true;
      console.warn("[slow request] " + shortName(oldest.url) + " has been waiting " + Math.round(age / 1000) + "s");
    }
    document.getElementById("net-slow-text").textContent = "Still working\u2026 " + shortName(oldest.url) + " is taking longer than usual (" + Math.round(age / 1000) + " s).";
    note.hidden = net.dismissed;
  }

  function netStart(url) {
    ensureNetUi();
    const id = net.nextId++;
    net.inflight.set(id, { url: url, t: Date.now(), warned: false });
    net.pending = net.inflight.size;
    clearTimeout(net.idleTimer);
    if (net.pending === 1) {
      clearTimeout(net.refreshTimer);
      clearInterval(net.watcher);
      root.classList.remove("net-done");
      root.classList.add("net-busy");
      // Only tables that stay busy for a moment are dimmed, so quick refreshes never flicker.
      net.refreshTimer = setTimeout(function () { if (net.pending > 0) root.classList.add("net-refreshing"); }, 350);
      net.watcher = setInterval(watchSlow, 1000);
    }
    // The button that was just pressed gets a spinner (after a beat, so instant replies never flash).
    const lc = net.lastClick;
    if (lc && Date.now() - lc.t < 800 && net.busyButtons.indexOf(lc.el) === -1) {
      net.lastClick = null;
      net.busyButtons.push(lc.el);
      setTimeout(function () { if (net.pending > 0) lc.el.classList.add("is-loading"); }, 200);
    }
    return id;
  }

  function netFinish() {
    if (net.inflight.size > 0) return;
    clearTimeout(net.refreshTimer);
    clearInterval(net.watcher);
    net.dismissed = false;
    root.classList.remove("net-busy", "net-refreshing");
    root.classList.add("net-done");
    setTimeout(function () { root.classList.remove("net-done"); }, 500);
    const note = document.getElementById("net-slow");
    if (note) note.hidden = true;
    net.busyButtons.forEach(function (el) { el.classList.remove("is-loading"); });
    net.busyButtons = [];
    clearSkeletons();
  }

  function netDone(id) {
    net.inflight.delete(id);
    net.pending = net.inflight.size;
    if (net.pending === 0) {
      // A short grace period: pages that load in steps start the next request immediately,
      // and the indicator should not blink off and on between the steps.
      clearTimeout(net.idleTimer);
      net.idleTimer = setTimeout(netFinish, 120);
    } else {
      watchSlow();      // the slow one may have just finished: update (hide) the note right away
    }
  }

  document.addEventListener("click", function (e) {
    const btn = e.target.closest && e.target.closest("button, input[type=submit]");
    if (btn && !btn.closest("[data-tab], .portal-tab, .tt-daytab, .seg, summary, .chip-x")) net.lastClick = { el: btn, t: Date.now() };
  }, true);
  document.addEventListener("submit", function (e) {
    const btn = e.target.querySelector && e.target.querySelector("button[type=submit], input[type=submit], button:not([type])");
    if (btn) net.lastClick = { el: btn, t: Date.now() };
  }, true);

  // ---- Skeleton rows: a table that is about to fill shows placeholder rows instead of a blank space ----
  function insertSkeletons(scope) {
    (scope || document).querySelectorAll("table.dash-table").forEach(function (table) {
      if (table.classList.contains("no-skeleton") || table.closest("[hidden], dialog:not([open])")) return;
      const tbody = table.tBodies[0];
      if (!tbody || tbody.children.length) return;
      const cols = table.querySelectorAll("thead th").length || 1;
      let rows = "";
      for (let i = 0; i < 4; i++) {
        rows += '<tr class="skeleton-row" data-skeleton="1"><td colspan="' + cols + '"><div class="skeleton-line" style="width:' + (62 + ((i * 17) % 34)) + '%"></div></td></tr>';
      }
      tbody.innerHTML = rows;
      table.setAttribute("aria-busy", "true");
      // The moment the page puts real rows in, the placeholders go.
      new MutationObserver(function (_m, obs) {
        const real = Array.prototype.some.call(tbody.children, function (c) { return !c.hasAttribute("data-skeleton"); });
        if (real) {
          tbody.querySelectorAll("[data-skeleton]").forEach(function (r) { r.remove(); });
          table.removeAttribute("aria-busy");
          obs.disconnect();
        }
      }).observe(tbody, { childList: true });
    });
  }

  function clearSkeletons() {
    document.querySelectorAll("tr[data-skeleton]").forEach(function (r) { r.remove(); });
    document.querySelectorAll("table[aria-busy]").forEach(function (t) { t.removeAttribute("aria-busy"); });
  }

  // Dialogs sit above the page, so they carry their own thin progress bar and skeletons.
  if (window.HTMLDialogElement && !HTMLDialogElement.prototype.__vistPatched) {
    const originalShowModal = HTMLDialogElement.prototype.showModal;
    HTMLDialogElement.prototype.showModal = function () {
      if (!this.querySelector(":scope > .dlg-progress")) {
        const bar = document.createElement("div");
        bar.className = "dlg-progress";
        bar.setAttribute("aria-hidden", "true");
        bar.innerHTML = "<span></span>";
        this.insertBefore(bar, this.firstChild);
      }
      const dialog = this;
      setTimeout(function () { if (net.pending > 0) insertSkeletons(dialog); }, 40);
      return originalShowModal.apply(this, arguments);
    };
    HTMLDialogElement.prototype.__vistPatched = true;
  }

  async function fetchJson(url, options) {
    const opts = Object.assign({ credentials: "include" }, options || {});
    const quiet = !!opts.quiet;       // background refreshes stay silent: no progress bar, no spinners
    delete opts.quiet;
    const isUpload = typeof FormData !== "undefined" && opts.body instanceof FormData;
    const controller = new AbortController();
    if (!opts.signal) opts.signal = controller.signal;
    // Never leave a screen waiting forever if the server stops answering.
    const timer = setTimeout(function () { controller.abort(); }, isUpload ? 300000 : 60000);
    const reqId = quiet ? 0 : netStart(url);
    try {
      const resp = await fetch(url, opts);
      let body = null;
      try {
        body = await resp.json();
      } catch (e) {
        body = null;
      }
      return { ok: resp.ok, status: resp.status, body: body, headers: resp.headers };
    } catch (err) {
      // Status 0 = we never got an answer. Pages already show error.message, so they now say something useful.
      const timedOut = !!(err && err.name === "AbortError");
      return {
        ok: false,
        status: 0,
        body: { error: {
          code: timedOut ? "TIMEOUT" : "NETWORK",
          message: timedOut
            ? "The server took too long to respond. Please try again."
            : "Can't reach the server. Check your internet connection and try again.",
        } },
      };
    } finally {
      clearTimeout(timer);
      if (!quiet) netDone(reqId);
    }
  }

  // =========================================================================
  // Messages and downloads
  // =========================================================================
  function toast(message, tone) {
    let box = document.getElementById("toast");
    if (!box) {
      box = document.createElement("div");
      box.id = "toast";
      box.setAttribute("role", "status");
      document.body.appendChild(box);
    }
    box.className = "toast " + (tone || "info");
    box.textContent = message;
    box.hidden = false;
    clearTimeout(box._timer);
    box._timer = setTimeout(function () { box.hidden = true; }, tone === "error" ? 9000 : 4000);
  }

  // Downloads through the page (not a bare link) so that if the server cannot make the file, the person
  // reads WHY -- instead of the browser saving the error message as a file that will not open.
  async function downloadFile(url, fallbackName, label) {
    const reqId = netStart(url);
    try {
      const resp = await fetch(url, { credentials: "include" });
      if (!resp.ok) {
        let message = null;
        try { const j = await resp.json(); message = j && j.error && j.error.message; } catch (e) { /* not JSON */ }
        toast(message || "The " + (label || "file") + " could not be downloaded (error " + resp.status + ").", "error");
        return false;
      }
      const blob = await resp.blob();
      const m = /filename="?([^";]+)"?/i.exec(resp.headers.get("content-disposition") || "");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (m && m[1]) || fallbackName || "download";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
      return true;
    } catch (err) {
      toast("Can't reach the server. Check your internet connection and try again.", "error");
      return false;
    } finally {
      netDone(reqId);
    }
  }

  // Any link marked  data-download="name.pdf" data-label="PDF"  downloads safely.
  document.addEventListener("click", function (e) {
    const a = e.target.closest && e.target.closest("a[data-download]");
    if (!a) return;
    e.preventDefault();
    downloadFile(a.href, a.getAttribute("data-download"), a.getAttribute("data-label"));
  });

  function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  function formatDateTime(isoString) {
    try {
      const d = new Date(isoString);
      if (isNaN(d.getTime())) return isoString;
      return d.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (e) {
      return isoString;
    }
  }

  const WEEKDAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const MONTH_ABBREVIATIONS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function formatDateLong(dateStr) {
    // "2026-08-21" -> "Monday, 21 Aug 2026" -- used everywhere a
    // session/schedule date is shown, across every portal. Built
    // manually rather than via toLocaleDateString: locale-based
    // formatting is inconsistent even within "day before month"
    // locales (e.g. en-GB renders September as "Sept", not "Sep"),
    // and this format needs to be exact and stable everywhere.
    if (!dateStr) return "";
    try {
      const d = new Date(dateStr.length <= 10 ? dateStr + "T00:00:00" : dateStr);
      if (isNaN(d.getTime())) return dateStr;
      return WEEKDAY_NAMES[d.getDay()] + ", " + d.getDate() + " " + MONTH_ABBREVIATIONS[d.getMonth()] + " " + d.getFullYear();
    } catch (e) {
      return dateStr;
    }
  }

  function formatTimeShort(timeStr) {
    // "09:00:00" -> "09:00" -- the API always returns HH:MM:SS.
    return timeStr ? timeStr.slice(0, 5) : "";
  }

  function showError(message) {
    const banner = document.getElementById("error-banner");
    if (!banner) return;
    banner.textContent = message;
    banner.hidden = false;
  }

  function clearError() {
    const banner = document.getElementById("error-banner");
    if (!banner) return;
    banner.hidden = true;
  }

  async function tryRefreshSession() {
    const result = await fetchJson("/api/v1/auth/refresh", { method: "POST" });
    return result.ok;
  }

  function allowedRoles() {
    // Each page shell declares which roles may enter via
    // <body data-allowed-roles="ADMIN,LIBRARIAN">. Defaults to ADMIN-only
    // if not specified, so every existing admin page keeps working
    // unchanged without needing this attribute added retroactively.
    const raw = document.body.getAttribute("data-allowed-roles");
    if (!raw) return ["ADMIN"];
    return raw.split(",").map(function (r) {
      return r.trim();
    });
  }

  function loginRedirectPath() {
    // There is one sign-in page for everyone. ?reauth=1 tells it not to
    // forward a "still signed in" visitor straight back to the page that
    // just rejected them.
    return "/?reauth=1";
  }

  // The sign-in page stores who just signed in. Using it lets every page
  // appear at once instead of waiting on a "who am I?" round trip; the
  // session is still re-verified quietly in the background, and every API
  // call is authorised by the server regardless.
  const ME_KEY = "vist_me";
  const ME_TTL_MS = 10 * 60 * 1000;

  function readCachedMe() {
    try {
      const raw = sessionStorage.getItem(ME_KEY);
      if (!raw) return null;
      const entry = JSON.parse(raw);
      if (!entry || !entry.me || Date.now() - entry.ts > ME_TTL_MS) return null;
      if (!Array.isArray(entry.me.permissions) || !Array.isArray(entry.me.roles)) return null;
      return entry.me;
    } catch (e) {
      return null;
    }
  }
  function cacheMe(me) {
    try { sessionStorage.setItem(ME_KEY, JSON.stringify({ ts: Date.now(), me: me })); } catch (e) { /* ignore */ }
  }
  function clearCachedMe() {
    try { sessionStorage.removeItem(ME_KEY); } catch (e) { /* ignore */ }
  }

  async function fetchMeWithRefresh() {
    let me = await fetchJson("/api/v1/auth/me");
    if (!me.ok && me.status === 401) {
      const refreshed = await tryRefreshSession();
      if (refreshed) me = await fetchJson("/api/v1/auth/me");
    }
    return me;
  }

  function hasAllowedRole(roles) {
    const allowed = allowedRoles();
    return (roles || []).some(function (r) { return allowed.indexOf(r) !== -1; });
  }

  // Staff holding several roles (e.g. Teacher + Exam Controller) get a "Switch portal" menu in the top bar.
  const PORTALS = [
    ["ADMIN", "Administration", "/dashboard/admin"], ["ACCOUNTS_OFFICER", "Accounts", "/dashboard/accounts/fees"],
    ["LIBRARIAN", "Library", "/dashboard/library"], ["PRINCIPAL", "Principal", "/dashboard/principal"], ["HOD", "Department (HOD)", "/dashboard/hod"], ["TEACHER", "Faculty", "/dashboard/faculty"],
    ["EXAM_CONTROLLER", "Exam Department", "/dashboard/exam"], ["HOSTEL_WARDEN", "Hostel", "/dashboard/warden"],
    ["SECURITY_GUARD", "Gate", "/dashboard/gate"], ["STUDENT", "Student", "/dashboard/student"],
  ];
  function addPortalSwitcher(me) {
    const holder = document.querySelector(".content-topbar-right");
    if (!holder || !me || document.getElementById("portal-switch")) return;
    const mine = PORTALS.filter(function (p) { return (me.roles || []).indexOf(p[0]) !== -1; });
    if (mine.length < 2) return;
    const here = window.location.pathname;
    const sel = document.createElement("select");
    sel.id = "portal-switch";
    sel.className = "select-input small-input";
    sel.setAttribute("aria-label", "Switch portal");
    sel.innerHTML = mine.map(function (p) {
      const base = p[2].split("/").slice(0, 3).join("/");
      return '<option value="' + p[2] + '"' + (here.indexOf(base) === 0 ? " selected" : "") + ">" + escapeHtml(p[1]) + "</option>";
    }).join("");
    sel.addEventListener("change", function () { window.location.href = sel.value; });
    holder.insertBefore(sel, holder.firstChild);
  }

  async function requireAdminSession() {
    const authGate = document.getElementById("auth-gate");

    // Fast path: we know who this is from the sign-in a moment ago.
    const cached = readCachedMe();
    if (cached && !cached.must_change_password && hasAllowedRole(cached.roles)) {
      fetchMeWithRefresh().then(function (res) {
        if (res.status === 0) return;                 // no network right now: not a reason to sign anyone out
        if (!res.ok || !res.body) {
          clearCachedMe();
          window.location.replace(loginRedirectPath());
        } else {
          cacheMe(res.body);
        }
      });
      return cached;
    }

    const me = await fetchMeWithRefresh();
    if (me.status === 0) {
      if (authGate) {
        authGate.innerHTML = "<span>" + escapeHtml(me.body.error.message) + '</span> <button type="button" class="role-btn small-btn" id="gate-retry">Try again</button>';
        const retry = document.getElementById("gate-retry");
        if (retry) retry.addEventListener("click", function () { window.location.reload(); });
      }
      return null;
    }
    if (!me.ok || !me.body) {
      clearCachedMe();
      window.location.replace(loginRedirectPath());
      return null;
    }
    cacheMe(me.body);

    if (!hasAllowedRole(me.body.roles)) {
      if (authGate) {
        authGate.textContent =
          'This account ("' + (me.body.username || "unknown") + '") does not have access to this area.';
      }
      return null;
    }

    return me.body;
  }

  // Wide tables turn into cards on phones: give every cell its column name
  // (CSS shows it), including rows added later by each page's own script.
  function enhanceTables() {
    document.querySelectorAll("table.dash-table").forEach(function (table) {
      if (table.dataset.enhanced) return;
      table.dataset.enhanced = "1";
      const heads = Array.prototype.map.call(table.querySelectorAll("thead th"), function (th) { return th.textContent.trim(); });
      if (heads.length >= 4 && !table.classList.contains("no-stack")) table.classList.add("stackable");
      const label = function (row) {
        Array.prototype.forEach.call(row.children, function (cell, i) {
          if (cell.tagName === "TD" && !cell.hasAttribute("colspan") && !cell.hasAttribute("data-label")) {
            cell.setAttribute("data-label", heads[i] || "");
          }
        });
      };
      const tbody = table.tBodies[0];
      if (!tbody) return;
      Array.prototype.forEach.call(tbody.rows, label);
      new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
          m.addedNodes.forEach(function (n) { if (n.nodeType === 1 && n.tagName === "TR") label(n); });
        });
      }).observe(tbody, { childList: true });
    });
  }

  // Pages that mark their tab bar with data-auto-tabs get tab switching for free:
  // buttons carry data-tab="x", panels carry data-tab-panel="x". #x in the URL opens that tab.
  function showTab(name) {
    const bar = document.querySelector("[data-auto-tabs]");
    if (!bar) return false;
    let found = false;
    bar.querySelectorAll("[data-tab]").forEach(function (b) {
      const on = b.getAttribute("data-tab") === name;
      found = found || on;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
      if (on && b.scrollIntoView) b.scrollIntoView({ inline: "center", block: "nearest" });
    });
    if (!found) return false;
    document.querySelectorAll("[data-tab-panel]").forEach(function (panel) {
      panel.hidden = panel.getAttribute("data-tab-panel") !== name;
    });
    return true;
  }

  function wireAutoTabs() {
    document.querySelectorAll("[data-auto-tabs]").forEach(function (bar) {
      bar.querySelectorAll("[data-tab]").forEach(function (b) {
        b.addEventListener("click", function () {
          const name = b.getAttribute("data-tab");
          showTab(name);
          try { history.replaceState(null, "", "#" + name); } catch (e) { /* ignore */ }
        });
      });
      const fromUrl = (location.hash || "").replace("#", "");
      if (fromUrl && bar.querySelector('[data-tab="' + fromUrl + '"]')) showTab(fromUrl);
    });
  }

  // Tab bars that overflow fade at the edge and keep the chosen tab in view.
  function enhanceTabs() {
    document.querySelectorAll(".portal-tabs").forEach(function (bar) {
      function update() {
        bar.classList.toggle("has-more", bar.scrollWidth - bar.clientWidth - bar.scrollLeft > 4);
        bar.classList.toggle("has-less", bar.scrollLeft > 4);
      }
      bar.addEventListener("scroll", update, { passive: true });
      window.addEventListener("resize", update);
      bar.addEventListener("click", function (e) {
        const tab = e.target.closest(".portal-tab");
        if (tab) tab.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
      });
      update();
    });
  }

  // A success or error message that appears inside a dialog must never be left
  // off-screen below a long form (on a phone that looks like "nothing happened").
  function watchDialogMessages() {
    const isMessage = function (el) {
      return el.nodeType === 1 && el.closest && el.closest("dialog") &&
        (el.classList.contains("success-text") || el.classList.contains("dash-alert") || el.classList.contains("alert"));
    };
    new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        const el = m.target;
        if (!isMessage(el) || el.hidden || !el.textContent.trim()) return;
        // Wait a frame so the new text has been laid out.
        requestAnimationFrame(function () { el.scrollIntoView({ block: "center", behavior: "smooth" }); });
      });
    }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["hidden"] });
  }

  // =========================================================================
  // Notification bell: unread count + a list of recent notifications on every portal.
  // =========================================================================
  const CATEGORY_LABEL = { FINANCE: "Accounts", RESULT: "Result", TASK: "Task", APPLICATION: "Application", ASSIGNMENT: "Assignment", ANNOUNCEMENT: "Announcement", LIBRARY: "Library", FINE: "Fines", COMPLAINT: "Complaints", FEE: "Fees", PAYROLL: "Payroll", GENERAL: "Update" };
  const BELL_SVG = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>';

  function timeAgo(iso) {
    const t = new Date(iso).getTime();
    if (isNaN(t)) return "";
    const sec = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (sec < 60) return "just now";
    if (sec < 3600) return Math.round(sec / 60) + " min ago";
    if (sec < 86400) return Math.round(sec / 3600) + " h ago";
    if (sec < 7 * 86400) return Math.round(sec / 86400) + " d ago";
    return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  }

  function wireNotifications() {
    const host = document.querySelector(".content-topbar-right");
    if (!host || document.getElementById("bell-btn")) return;
    const wrap = document.createElement("div");
    wrap.className = "bell-wrap";
    wrap.innerHTML =
      '<button type="button" id="bell-btn" class="bell-btn" aria-label="Notifications" aria-haspopup="true" aria-expanded="false">' + BELL_SVG +
      '<span id="bell-count" class="bell-count" hidden></span></button>' +
      '<div id="bell-panel" class="bell-panel" role="region" aria-label="Notifications" hidden>' +
      '<div class="bell-head"><strong>Notifications</strong><button type="button" id="bell-readall" class="bell-readall">Mark all read</button></div>' +
      '<div id="bell-list" class="bell-list" aria-live="polite"></div></div>';
    host.insertBefore(wrap, host.firstChild);

    const btn = document.getElementById("bell-btn");
    const badge = document.getElementById("bell-count");
    const panel = document.getElementById("bell-panel");
    const list = document.getElementById("bell-list");
    const readAll = document.getElementById("bell-readall");
    const PAGE = 20;
    let offset = 0;
    let unread = 0;

    function setUnread(n) {
      unread = Math.max(0, n);
      badge.hidden = unread === 0;
      badge.textContent = unread > 99 ? "99+" : String(unread);
      btn.setAttribute("aria-label", unread ? "Notifications, " + unread + " unread" : "Notifications");
      readAll.hidden = unread === 0;
    }

    async function refreshCount() {
      const r = await fetchJson("/api/v1/notifications/me/unread-count", { quiet: true });
      if (r.ok && r.body) setUnread(r.body.unread_count);
    }

    function itemEl(n) {
      const a = document.createElement(n.link ? "a" : "div");
      a.className = "bell-item" + (n.is_read ? "" : " unread");
      if (n.link) a.href = n.link;
      a.innerHTML =
        '<span class="bell-cat">' + escapeHtml(CATEGORY_LABEL[n.category] || "Update") + " \u00b7 " + escapeHtml(timeAgo(n.created_at)) + "</span>" +
        '<strong class="bell-title">' + escapeHtml(n.title) + "</strong>" +
        '<span class="bell-msg">' + escapeHtml(n.message) + "</span>";
      a.addEventListener("click", function () {
        if (!n.is_read) {
          n.is_read = true;
          a.classList.remove("unread");
          setUnread(unread - 1);
          fetchJson("/api/v1/notifications/" + n.id + "/read", { method: "POST", quiet: true, keepalive: true });
        }
      });
      return a;
    }

    async function load(more) {
      if (!more) { offset = 0; list.innerHTML = '<div class="skeleton-stack"><div class="skeleton-line"></div><div class="skeleton-line short"></div><div class="skeleton-line"></div></div>'; }
      const r = await fetchJson("/api/v1/notifications/me?limit=" + PAGE + "&offset=" + offset, { quiet: true });
      const old = list.querySelector(".bell-more");
      if (old) old.remove();
      if (!more) list.innerHTML = "";
      if (!r.ok || !r.body) {
        list.innerHTML = '<p class="bell-empty">' + escapeHtml((r.body && r.body.error && r.body.error.message) || "Could not load notifications.") + "</p>";
        return;
      }
      if (!more && !r.body.length) {
        list.innerHTML = '<p class="bell-empty">You\u2019re all caught up. Nothing new.</p>';
        return;
      }
      r.body.forEach(function (n) { list.appendChild(itemEl(n)); });
      offset += r.body.length;
      if (r.body.length === PAGE) {
        const m = document.createElement("button");
        m.type = "button";
        m.className = "bell-more";
        m.textContent = "Show older";
        m.addEventListener("click", function () { m.textContent = "Loading\u2026"; load(true); });
        list.appendChild(m);
      }
    }

    function open() {
      panel.hidden = false;
      btn.setAttribute("aria-expanded", "true");
      load(false);
    }
    function close() {
      panel.hidden = true;
      btn.setAttribute("aria-expanded", "false");
    }
    btn.addEventListener("click", function (e) { e.stopPropagation(); if (panel.hidden) open(); else close(); });
    document.addEventListener("click", function (e) { if (!panel.hidden && !wrap.contains(e.target)) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !panel.hidden) { close(); btn.focus(); } });
    readAll.addEventListener("click", async function () {
      const r = await fetchJson("/api/v1/notifications/me/read-all", { method: "POST", quiet: true });
      if (r.ok) {
        setUnread(0);
        list.querySelectorAll(".bell-item.unread").forEach(function (el) { el.classList.remove("unread"); });
      }
    });

    refreshCount();
    setInterval(function () { if (!document.hidden) refreshCount(); }, 60000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) refreshCount(); });
  }

  // =========================================================================
  // Who is signed in: photo (or initials), real name, roll number / role. Shown in the top bar.
  // =========================================================================
  const ROLE_LABEL = { ADMIN: "Administrator", TEACHER: "Teacher", LIBRARIAN: "Librarian", ACCOUNTS_OFFICER: "Accounts Officer", STUDENT: "Student" };
  const IDENT_KEY = "vist_ident";

  function readCachedIdentity() {
    try {
      const raw = JSON.parse(sessionStorage.getItem(IDENT_KEY) || "null");
      return raw && Date.now() - raw.ts < 30 * 60 * 1000 ? raw.ident : null;
    } catch (e) { return null; }
  }

  async function loadIdentity(me) {
    const roles = me.roles || [];
    let ident = null;
    if (roles.indexOf("STUDENT") !== -1) {
      const r = await fetchJson("/api/v1/students/me", { quiet: true });
      if (r.ok && r.body) {
        ident = { name: (r.body.first_name + " " + r.body.last_name).trim(), sub: r.body.roll_number || "Student",
                  photo: r.body.has_photo ? "/api/v1/students/me/photo" : null };
      }
    } else if (roles.length === 1 && (roles[0] === "ADMIN" || roles[0] === "PARENT")) {
      return null;       // the administrator and parents are not employee records: nothing to ask for, no noisy 404
    } else {
      const r = await fetchJson("/api/v1/employees/me", { quiet: true });
      if (r.ok && r.body) {
        ident = { name: (r.body.first_name + " " + r.body.last_name).trim(), sub: ROLE_LABEL[roles[0]] || r.body.employee_code || "Staff",
                  photo: r.body.has_photo ? "/api/v1/employees/me/photo" : null };
      }
    }
    if (ident) {
      try { sessionStorage.setItem(IDENT_KEY, JSON.stringify({ ts: Date.now(), ident: ident })); } catch (e) { /* ignore */ }
    }
    return ident;
  }

  function initialsOf(name) {
    const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "?";
    return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
  }

  function paintWhoami(me, ident) {
    const whoami = document.getElementById("whoami");
    if (!whoami) return;
    const name = (ident && ident.name) || me.username;
    const sub = (ident && ident.sub) || (me.roles && ROLE_LABEL[me.roles[0]]) || "";
    whoami.innerHTML =
      '<span class="whoami-avatar" aria-hidden="true">' +
      (ident && ident.photo ? '<img src="' + escapeHtml(ident.photo) + '" alt="" />' : escapeHtml(initialsOf(name))) + "</span>" +
      '<span class="whoami-text"><strong>' + escapeHtml(name) + "</strong><small>" + escapeHtml(sub + (me.must_change_password ? " \u00b7 password change required" : "")) + "</small></span>" +
      '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
    const img = whoami.querySelector("img");
    if (img) img.addEventListener("error", function () { img.replaceWith(document.createTextNode(initialsOf(name))); });
  }

  // The number of announcements I have not opened yet, shown beside "Announcements" in the menu.
  function wireAnnouncementBadge(me) {
    if ((me.roles || []).indexOf("ADMIN") !== -1 && (me.roles || []).length === 1) return;
    const paint = function (n) {
      document.querySelectorAll('a[href$="/announcements"]').forEach(function (link) {
        const old = link.querySelector(".nav-badge");
        if (old) old.remove();
        if (n > 0) {
          const b = document.createElement("span");
          b.className = "nav-badge";
          b.textContent = n > 99 ? "99+" : String(n);
          b.setAttribute("aria-label", n + " unread");
          link.appendChild(b);
        }
      });
    };
    window.addEventListener("announcements:unread", function (e) { paint(e.detail); });
    fetchJson("/api/v1/announcements/unread-count", { quiet: true }).then(function (r) { if (r.ok && r.body) paint(r.body.unread_count); });
  }

  // Applications: for the administration, how many are waiting; for everyone else, how many are waiting for MY answer.
  function wireApplicationBadge() {
    const paint = function (n) {
      document.querySelectorAll('a[href$="/applications"]').forEach(function (link) {
        const old = link.querySelector(".nav-badge");
        if (old) old.remove();
        if (n > 0) {
          const b = document.createElement("span");
          b.className = "nav-badge";
          b.textContent = n > 99 ? "99+" : String(n);
          b.setAttribute("aria-label", n + " need attention");
          link.appendChild(b);
        }
      });
    };
    window.addEventListener("applications:badge", function (e) { paint(e.detail); });
    fetchJson("/api/v1/applications/badge", { quiet: true }).then(function (r) { if (r.ok && r.body) paint(r.body.count); });
  }

  // Tasks: how many are waiting for MY answer (and, for the administration, how many flags need a decision).
  function wireTaskBadge(me) {
    const roles = me.roles || [];
    // Students and parents have no tasks: do not ask (it would only be refused).
    if (roles.every(function (r) { return r === "STUDENT" || r === "PARENT"; })) return;
    const paint = function (n) {
      document.querySelectorAll('a[href$="/tasks"]').forEach(function (link) {
        const old = link.querySelector(".nav-badge");
        if (old) old.remove();
        if (n > 0) {
          const b = document.createElement("span");
          b.className = "nav-badge";
          b.textContent = n > 99 ? "99+" : String(n);
          b.setAttribute("aria-label", n + " need attention");
          link.appendChild(b);
        }
      });
    };
    window.addEventListener("tasks:badge", function (e) { paint(e.detail); });
    fetchJson("/api/v1/tasks/summary", { quiet: true }).then(function (r) { if (r.ok && r.body) paint(r.body.badge); });
  }

  function wireDrawer() {
    const drawer = document.getElementById("drawer");
    const backdrop = document.getElementById("drawer-backdrop");
    const openBtn = document.getElementById("drawer-open");
    const closeBtn = document.getElementById("drawer-close");
    const moreBtn = document.getElementById("bottom-more");
    if (!drawer || !backdrop || !openBtn || !closeBtn) return;

    function openDrawer() {
      drawer.classList.add("drawer-open");
      backdrop.hidden = false;
    }
    function closeDrawer() {
      drawer.classList.remove("drawer-open");
      backdrop.hidden = true;
    }

    openBtn.addEventListener("click", openDrawer);
    if (moreBtn) moreBtn.addEventListener("click", openDrawer);
    // Choosing a page from the menu should never leave the menu covering it.
    drawer.addEventListener("click", function (e) { if (e.target.closest("a.drawer-link") && window.innerWidth < 900) closeDrawer(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeDrawer(); });
    closeBtn.addEventListener("click", closeDrawer);
    backdrop.addEventListener("click", closeDrawer);
  }

  function wireLogout() {
    const logoutBtn = document.getElementById("logout-btn");
    if (!logoutBtn) return;
    logoutBtn.addEventListener("click", async function () {
      logoutBtn.disabled = true;
      clearCachedMe();
      try { sessionStorage.removeItem(IDENT_KEY); } catch (e) { /* ignore */ }
      try {
        await fetch("/api/v1/auth/logout", { method: "POST", credentials: "include" });
      } catch (e) {
        // Cookie is short-lived regardless — proceed to the landing page
        // even if the network call itself failed.
      }
      window.location.replace("/");
    });
  }

  async function initShell() {
    const me = await requireAdminSession();
    if (!me) return null; // requireAdminSession already redirected or showed a message

    if (me.must_change_password) {
      // Real enforcement now exists server-side — every other endpoint
      // this shell's pages would call next (dashboard stats, lists,
      // anything) will 403 immediately for an account in this state.
      // Redirect here too, not just at login, so bookmarking or
      // reloading a dashboard URL directly can't leave someone looking
      // at a broken page full of failed requests.
      window.location.replace("/change-password");
      return null;
    }

    const authGate = document.getElementById("auth-gate");
    const shell = document.getElementById("app-shell");

    if (authGate) authGate.hidden = true;
    if (shell) shell.hidden = false;

    paintWhoami(me, readCachedIdentity());
    loadIdentity(me).then(function (ident) { if (ident) paintWhoami(me, ident); });

    wireDrawer();
    wireLogout();
    wireNotifications();
    wireAnnouncementBadge(me);
    wireApplicationBadge();
    wireTaskBadge(me);
    wireAutoTabs();
    enhanceTables();
    enhanceTabs();
    watchDialogMessages();

    // An admin visiting the Library / Accounts portals gets a way back.
    const backToAdmin = document.getElementById("back-to-admin");
    if (backToAdmin && (me.roles || []).indexOf("ADMIN") !== -1) backToAdmin.hidden = false;

    insertSkeletons(document.querySelector(".content-main") || document);
    addPortalSwitcher(me);
    document.dispatchEvent(new CustomEvent("vistadmin:ready", { detail: { me: me } }));
    return me;
  }

  function loadingRowHtml(colspan, label) {
    return (
      '<tr><td colspan="' + colspan + '" class="loading-cell">' +
      '<div class="progress-ring small"></div><span>' + escapeHtml(label || "Loading\u2026") + "</span>" +
      "</td></tr>"
    );
  }

  return {
    fetchJson: fetchJson,
    insertSkeletons: insertSkeletons,
    downloadFile: downloadFile,
    showTab: showTab,
    toast: toast,
    escapeHtml: escapeHtml,
    formatDateTime: formatDateTime,
    formatDateLong: formatDateLong,
    formatTimeShort: formatTimeShort,
    showError: showError,
    clearError: clearError,
    initShell: initShell,
    loadingRowHtml: loadingRowHtml,
  };
})();

document.addEventListener("DOMContentLoaded", function () {
  window.VistAdmin.initShell().catch(function () {
    const authGate = document.getElementById("auth-gate");
    if (authGate) authGate.textContent = "Something went wrong loading this page. Please refresh.";
  });
});