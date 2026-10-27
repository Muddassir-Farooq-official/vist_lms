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

  function netStart(url, method) {
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
    // The spinner belongs to THIS request: it stops the moment this request is answered, even if
    // something else on the page (a slow list, a background refresh) is still loading.
    const lc = net.lastClick;
    if (lc && Date.now() - lc.t < 800) {
      net.lastClick = null;
      net.inflight.get(id).btn = lc.el;
      if (net.busyButtons.indexOf(lc.el) === -1) net.busyButtons.push(lc.el);
      // Saving something: the button is locked AT ONCE (not after the spinner delay), so a
      // double click or a second tap cannot send it twice.
      if (method && method !== "GET" && !lc.el.disabled) {
        lc.el.disabled = true;
        lc.el.setAttribute("data-net-locked", "1");
        lc.el.setAttribute("aria-busy", "true");
      }
      setTimeout(function () { if (buttonBusy(lc.el)) lc.el.classList.add("is-loading"); }, 200);
    }
    return id;
  }

  function buttonBusy(el) {
    let busy = false;
    net.inflight.forEach(function (r) { if (r.btn === el) busy = true; });
    return busy;
  }

  function unlock(el) {
    if (el.getAttribute("data-net-locked")) {
      el.disabled = false;
      el.removeAttribute("data-net-locked");
      el.removeAttribute("aria-busy");
    }
  }

  function releaseButton(el) {
    if (!el || buttonBusy(el)) return;
    unlock(el);
    el.classList.remove("is-loading");
    net.busyButtons = net.busyButtons.filter(function (b) { return b !== el; });
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
    net.busyButtons.forEach(function (el) { el.classList.remove("is-loading"); unlock(el); });
    net.busyButtons = [];
    clearSkeletons();
  }

  function netDone(id) {
    const entry = net.inflight.get(id);
    net.inflight.delete(id);
    if (entry && entry.btn) releaseButton(entry.btn);
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
    (scope || document).querySelectorAll("table.dash-table, table.tk-table").forEach(function (table) {
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
    const method = String(opts.method || "GET").toUpperCase();
    const reqId = quiet ? 0 : netStart(url, method);
    try {
      let resp = await fetch(url, opts);
      // The sign-in pass lasts 15 minutes and is renewed in the background. If it ran out
      // while someone was typing, renew it and send the same request again, so nothing is lost.
      if (resp.status === 401 && String(url).indexOf("/api/v1/auth/") === -1) {
        if (await renewSession()) {
          resp = await fetch(url, opts);
        }
        if (resp.status === 401) sessionEnded();
      }
      let body = null;
      try {
        body = await resp.json();
      } catch (e) {
        body = null;
      }
      if (!resp.ok) {
        body = normaliseError(resp.status, body);
        if (method === "GET" && !quiet && resp.status >= 500) loadProblem(url, body);
      }
      return { ok: resp.ok, status: resp.status, body: body, headers: resp.headers };
    } catch (err) {
      if (method === "GET" && !quiet) loadProblem(url, null);
      // Status 0 = we never got an answer. Pages already show error.message, so they now say something useful.
      const timedOut = !!(err && err.name === "AbortError");
      return {
        ok: false,
        status: 0,
        body: { error: {
          code: timedOut ? "TIMEOUT" : "NETWORK",
          message: timedOut
            ? (method === "GET"
              ? "The server took too long to respond. Please try again."
              : "The server did not answer in time. It may still have saved this, so refresh the page and check before trying again.")
            : (method === "GET"
              ? "Can't reach the server. Check your internet connection and try again."
              : "Can't reach the server, so this was probably not saved. Check your internet connection, refresh the page and check before trying again."),
        } },
      };
    } finally {
      clearTimeout(timer);
      if (!quiet) netDone(reqId);
    }
  }

  // Every error, whatever produced it, comes back as {error: {code, message}}. FastAPI's own
  // field checks ({detail: [{loc, msg}]}) are turned into one plain sentence here.
  const FIELD_WORDS = { amount: "Amount", paid_on: "Payment date", reference: "Reference", reason: "Reason", note: "Note", cnic: "CNIC", contact_no: "Phone number" };
  function plainField(loc) {
    const name = String((loc || []).filter(function (x) { return x !== "body" && x !== "query" && x !== "path" && typeof x === "string"; }).pop() || "");
    return FIELD_WORDS[name] || (name ? name.replace(/_/g, " ").replace(/^./, function (c) { return c.toUpperCase(); }) : "A value");
  }
  function plainMsg(m) {
    return String(m || "is not valid").replace(/^Value error, /, "").replace(/^String should have at least (\d+) characters?/, "must be at least $1 characters")
      .replace(/^Field required$/, "is required").replace(/^Input should be greater than (.*)$/, "must be more than $1")
      .replace(/^Input should be a valid (.*)$/, "must be a valid $1");
  }
  function normaliseError(status, body) {
    if (body && body.error && body.error.message) return body;
    if (body && Array.isArray(body.detail)) {
      const parts = body.detail.slice(0, 3).map(function (d) { return plainField(d.loc) + " " + plainMsg(d.msg); });
      return { error: { code: "VALIDATION_ERROR", message: parts.join("; ") + ".", details: body.detail }, detail: body.detail };
    }
    if (body && typeof body.detail === "string") return { error: { code: "ERROR", message: body.detail }, detail: body.detail };
    const fallback = status === 403 ? "You do not have permission to do this." : status === 404 ? "That was not found (it may have been removed)." :
      status === 409 ? "This was just submitted, so it was not saved twice. Refresh to see it." : status >= 500 ? "Something went wrong on the server. Please try again in a moment." :
      "That did not work (error " + status + ").";
    return Object.assign({}, body || {}, { error: { code: "HTTP_" + status, message: fallback } });
  }

  // A page that could not load its information says so, at the top, with a way to try again,
  // instead of showing empty tables that look like "there is nothing".
  let problemTimer = null;
  const problems = new Set();
  function loadProblem(url, body) {
    if (!document.body) return;
    problems.add(shortName(url));
    clearTimeout(problemTimer);
    problemTimer = setTimeout(function () {
      let box = document.getElementById("load-problem");
      if (!box) {
        box = document.createElement("div");
        box.id = "load-problem";
        box.className = "dash-alert error load-problem";
        box.setAttribute("role", "alert");
        const host = document.querySelector(".content-body, main, .content") || document.body;
        host.insertBefore(box, host.firstChild);
      }
      const msg = (body && body.error && body.error.message && body.error.code !== "HTTP_500") ? body.error.message : "the server did not answer properly";
      box.innerHTML = "<strong>Some of this page could not be loaded</strong> (" + escapeHtml(Array.from(problems).slice(0, 3).join(", ")) + ": " + escapeHtml(msg) +
        "). What you see may be incomplete. <button type=\"button\" class=\"role-btn small-btn\">Try again</button>";
      box.querySelector("button").addEventListener("click", function () { window.location.reload(); });
      box.hidden = false;
    }, 250);
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
  async function downloadFile(url, fallbackName, label, init) {
    const reqId = netStart(url);
    try {
      const resp = await fetch(url, Object.assign({ credentials: "include" }, init || {}));
      if (!resp.ok) {
        let message = null;
        try { const j = await resp.json(); message = j && j.error && j.error.message; } catch (e) { /* not JSON */ }
        downloadFile.lastError = message || "";
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
      // The same on every computer: "10 Oct, 09:30", Pakistan time.
      return d.toLocaleString("en-GB", {
        timeZone: "Asia/Karachi",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
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
    if (!banner) { toast(message, "error"); return; }
    banner.setAttribute("role", "alert");
    banner.textContent = message;
    banner.hidden = false;
    // An error below the fold used to go unseen: bring it into view.
    const r = banner.getBoundingClientRect();
    if (r.top < 0 || r.bottom > window.innerHeight) banner.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function clearError() {
    const banner = document.getElementById("error-banner");
    if (!banner) return;
    banner.hidden = true;
  }

  async function tryRefreshSession() {
    return renewSession();
  }

  // One renewal at a time, shared by every request that found the pass expired.
  let renewing = null;
  function renewSession() {
    if (!renewing) {
      renewing = fetch("/api/v1/auth/refresh", { method: "POST", credentials: "include" })
        .then(function (r) { return r.ok; })
        .catch(function () { return false; })
        .finally(function () { setTimeout(function () { renewing = null; }, 2000); });
    }
    return renewing;
  }

  // The session really ended (signed out elsewhere, password reset, account closed).
  // Do NOT navigate away: whatever is typed on this page stays. Sign in in another
  // tab, come back, and press Save again.
  let endedShown = false;
  function sessionEnded() {
    if (endedShown || !document.body) return;
    endedShown = true;
    const d = document.createElement("dialog");
    d.className = "modal";
    d.setAttribute("aria-labelledby", "sess-end-title");
    d.innerHTML = '<div class="modal-body"><h2 id="sess-end-title">You are signed out</h2>' +
      '<p class="cc-meta">Your session ended, so the last action was not saved. What you typed on this page is still here.</p>' +
      "<ol class=\"cc-meta\"><li>Sign in again in a new tab.</li><li>Come back to this tab and press Save again.</li></ol>" +
      '<div class="form-actions"><a class="role-btn" target="_blank" rel="noopener" href="/?reauth=1&next=close">Sign in (new tab)</a>' +
      '<button type="button" class="role-btn secondary-btn" data-done>I have signed in</button></div>' +
      '<button type="button" class="back-link modal-close-btn" data-leave>Go to the sign-in page instead</button></div>';
    document.body.appendChild(d);
    d.querySelector("[data-done]").addEventListener("click", function () { endedShown = false; d.close(); d.remove(); });
    d.querySelector("[data-leave]").addEventListener("click", function () { window.location.replace(loginRedirectPath()); });
    d.addEventListener("cancel", function (e) { e.preventDefault(); });
    d.showModal();
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
    return "/?reauth=1&next=" + encodeURIComponent(window.location.pathname + window.location.search);
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
    ["ADMIN", "Administration", "/dashboard/admin"], ["ACCOUNTS_OFFICER", "Accounts", "/dashboard/accounts"],
    ["LIBRARIAN", "Library", "/dashboard/library"], ["PRINCIPAL", "Principal", "/dashboard/principal"], ["HOD", "Department (HOD)", "/dashboard/hod"], ["TEACHER", "Faculty", "/dashboard/faculty"],
    ["EXAM_CONTROLLER", "Exam Department", "/dashboard/exam/home"], ["HOSTEL_WARDEN", "Hostel", "/dashboard/warden/home"],
    ["SECURITY_GUARD", "Gate", "/dashboard/gate/home"], ["STAFF", "Staff", "/dashboard/staff"], ["STUDENT", "Student", "/dashboard/student"],
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
      // A table fills its panel; when it is wider than the screen it scrolls inside this wrapper, not the whole page.
      const holder = table.parentElement;
      if (holder && !holder.matches(".dt-scroll, .table-scroll, .table-wrap, .sd-table-wrap, .stat-table-wrap")) {
        const wrap = document.createElement("div");
        wrap.className = "dt-scroll";
        holder.insertBefore(wrap, table);
        wrap.appendChild(table);
      }
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
  const ROLE_LABEL = { ADMIN: "Administrator", TEACHER: "Teacher", LIBRARIAN: "Librarian", ACCOUNTS_OFFICER: "Accounts Officer", STUDENT: "Student",
    SECURITY_GUARD: "Security Guard", HOSTEL_WARDEN: "Hostel Warden", EXAM_CONTROLLER: "Exam Department", STAFF: "General Staff" };
  const IDENT_KEY = "vist_ident";

  function readCachedIdentity(me) {
    try {
      const raw = JSON.parse(sessionStorage.getItem(IDENT_KEY) || "null");
      // only this same person's name: on a shared computer the next person must never see the previous one's
      return raw && raw.uid === (me && me.id) && Date.now() - raw.ts < 30 * 60 * 1000 ? raw.ident : null;
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
      try { sessionStorage.setItem(IDENT_KEY, JSON.stringify({ ts: Date.now(), uid: me.id, ident: ident })); } catch (e) { /* ignore */ }
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
    // A parent signs in with the guardian's CNIC: show "Guardian" rather than a number as their name.
    const isParent = !ident && (me.roles || []).indexOf("PARENT") !== -1 && !/[A-Za-z]/.test(me.username || "");
    const name = (ident && ident.name) || (isParent ? "Guardian" : me.username);
    const sub = (ident && ident.sub) || (isParent ? me.username : (me.roles && ROLE_LABEL[me.roles[0]]) || "");
    whoami.innerHTML =
      '<span class="whoami-avatar" aria-hidden="true">' +
      (ident && ident.photo ? '<img src="' + escapeHtml(ident.photo) + '" alt="" />' : escapeHtml(initialsOf(name))) + "</span>" +
      '<span class="whoami-text"><strong>' + escapeHtml(name) + "</strong><small>" + escapeHtml(sub + (me.must_change_password ? " \u00b7 password change required" : "")) + "</small></span>" +
      '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
    const img = whoami.querySelector("img");
    if (img) img.addEventListener("error", function () { img.replaceWith(document.createTextNode(initialsOf(name))); });
  }

  function showViewAsBar(me) {
    if (document.getElementById("viewas-bar")) return;
    const bar = document.createElement("div");
    bar.id = "viewas-bar";
    bar.className = "viewas-bar";
    bar.setAttribute("role", "status");
    bar.innerHTML = '<span class="viewas-eye" aria-hidden="true"></span><span class="viewas-text">Viewing as <b id="viewas-name">' + escapeHtml(me.username) +
      "</b> · read-only · opened by " + escapeHtml(me.viewing_as_by) + '</span><button type="button" class="viewas-exit">Exit view</button>';
    document.body.prepend(bar);
    document.body.classList.add("is-viewing-as");
    loadIdentity(me).then(function (ident) { if (ident && ident.name) document.getElementById("viewas-name").textContent = ident.name; }).catch(function () {});
    bar.querySelector(".viewas-exit").addEventListener("click", async function () {
      this.disabled = true;
      const r = await fetchJson("/api/v1/view-as/stop", { method: "POST" });
      try { sessionStorage.removeItem(IDENT_KEY); } catch (e) { /* ignore */ }
      location.href = (r.ok && r.body && r.body.redirect) || "/dashboard/admin";
    });
  }

  // The number of announcements I have not opened yet, shown beside "Announcements" in the menu.
  function wireAnnouncementBadge(me) {
    if ((me.roles || []).indexOf("ADMIN") !== -1 && (me.roles || []).length === 1) return;
    const paint = function (n) {
      // Only the menu links (side menu, phone bar), not "View all" links inside pages.
      document.querySelectorAll('.drawer a[href$="/announcements"], .bottom-nav a[href$="/announcements"], .bottom-sheet a[href$="/announcements"]').forEach(function (link) {
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

    let opener = null;
    function openDrawer(e) {
      opener = (e && e.currentTarget) || openBtn;
      drawer.classList.add("drawer-open");
      backdrop.hidden = false;
      openBtn.setAttribute("aria-expanded", "true");
      const first = drawer.querySelector("a.drawer-link, button");
      if (first) first.focus();
    }
    function closeDrawer() {
      if (!drawer.classList.contains("drawer-open")) return;
      drawer.classList.remove("drawer-open");
      backdrop.hidden = true;
      openBtn.setAttribute("aria-expanded", "false");
      if (opener && window.innerWidth < 900) opener.focus();
    }
    // Keep the keyboard inside the open menu on small screens.
    drawer.addEventListener("keydown", function (e) {
      if (e.key !== "Tab" || !drawer.classList.contains("drawer-open") || window.innerWidth >= 900) return;
      const items = Array.prototype.filter.call(drawer.querySelectorAll("a[href], button:not([disabled])"), function (x) { return x.offsetParent !== null; });
      if (!items.length) return;
      const firstEl = items[0], lastEl = items[items.length - 1];
      if (e.shiftKey && document.activeElement === firstEl) { e.preventDefault(); lastEl.focus(); }
      else if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); firstEl.focus(); }
    });

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

    paintWhoami(me, readCachedIdentity(me));
    loadIdentity(me).then(function (ident) { if (ident) paintWhoami(me, ident); });

    wireDrawer();
    wireLogout();
    wireNotifications();
    wireAnnouncementBadge(me);
    wireApplicationBadge();
    wireTaskBadge(me);
    wireAutoTabs();
    enhanceTables();
    // Tables drawn later by a page's script (inside cards and dialogs) get the same treatment.
    let tablesQueued = false;
    new MutationObserver(function () {
      if (tablesQueued) return;
      tablesQueued = true;
      requestAnimationFrame(function () { tablesQueued = false; enhanceTables(); });
    }).observe(document.body, { childList: true, subtree: true });
    enhanceTabs();
    watchDialogMessages();

    // An admin visiting the Library / Accounts portals gets a way back.
    const backToAdmin = document.getElementById("back-to-admin");
    if (backToAdmin && (me.roles || []).indexOf("ADMIN") !== -1) backToAdmin.hidden = false;

    // An administrator looking at this person's screens ("view as"): say so on every page, read-only, with a way out.
    if (me.viewing_as_by) showViewAsBar(me);

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

  // =========================================================================
  // ask(): an in-page dialog instead of the browser's prompt()/confirm().
  //   ask({title, intro, label, ok, danger, input: "text"|"number"|"none", value, min, required})
  //   -> Promise of the typed text (or true when input is "none"), or null if cancelled.
  // =========================================================================
  function ask(o) {
    o = o || {};
    return new Promise(function (resolve) {
      const d = document.createElement("dialog");
      d.className = "modal ask-dialog";
      d.setAttribute("aria-labelledby", "ask-title");
      const kind = o.input || "text";
      const field = kind === "none" ? "" :
        '<div class="form-field"><label for="ask-input">' + escapeHtml(o.label || "Reason (kept on record)") + "</label>" +
        (kind === "textarea" ? '<textarea id="ask-input" class="text-input" rows="3" maxlength="300"></textarea>'
          : '<input id="ask-input" class="text-input" type="' + (kind === "number" ? "number" : kind === "date" ? "date" : "text") + '"' + (kind === "number" ? ' step="0.01" inputmode="decimal"' : kind === "date" ? "" : ' maxlength="200"') + ">") + "</div>";
      d.innerHTML = '<div class="modal-body"><h2 id="ask-title">' + escapeHtml(o.title || "Are you sure?") + "</h2>" +
        (o.intro ? '<p class="cc-meta">' + escapeHtml(o.intro) + "</p>" : "") + field +
        '<p class="dash-alert error" role="alert" hidden></p>' +
        '<div class="form-actions"><button type="button" class="role-btn' + (o.danger ? " danger-btn" : "") + '" data-ask="yes">' + escapeHtml(o.ok || "OK") + "</button></div>" +
        '<button type="button" class="back-link modal-close-btn" data-ask="no">Cancel</button></div>';
      document.body.appendChild(d);
      const input = d.querySelector("#ask-input");
      const err = d.querySelector(".dash-alert");
      if (input && o.value != null) input.value = o.value;
      let done = false;
      function finish(v) { if (done) return; done = true; if (d.open) d.close(); d.remove(); resolve(v); }
      d.querySelector('[data-ask="no"]').addEventListener("click", function () { finish(null); });
      d.addEventListener("cancel", function (e) { e.preventDefault(); finish(null); });
      function accept() {
        if (!input) return finish(true);
        const v = input.value.trim();
        const min = o.min == null ? (o.required === false ? 0 : 4) : o.min;
        if (kind === "number" ? (v === "" || isNaN(parseFloat(v))) : kind === "date" ? (o.required !== false && !v) : v.length < min) {
          err.textContent = kind === "number" ? "Enter an amount." : kind === "date" ? "Choose a date." : (min > 1 ? "Write a few words (at least " + min + " letters)." : "This is needed.");
          err.hidden = false; input.focus(); return;
        }
        finish(v);
      }
      d.querySelector('[data-ask="yes"]').addEventListener("click", accept);
      if (input) input.addEventListener("keydown", function (e) { if (e.key === "Enter" && input.tagName !== "TEXTAREA") { e.preventDefault(); accept(); } });
      d.showModal();
      (input || d.querySelector('[data-ask="yes"]')).focus();
    });
  }

  // Drop-in replacements for window.confirm / window.prompt (await them).
  function confirmDialog(message, okLabel) {
    return ask({ title: "Please confirm", intro: String(message || ""), ok: okLabel || "Yes, continue", input: "none" }).then(function (v) { return !!v; });
  }
  function promptText(message, value) {
    return ask({ title: "Please enter", label: String(message || ""), value: value == null ? "" : value, input: "text", required: false, min: 0 })
      .then(function (v) { return v === true ? "" : v; });
  }

  // Money shown the same way everywhere: Rs. 12,500 (paisa only when there are any).
  function formatMoney(v) {
    const n = Number(v || 0);
    const whole = Math.abs(n - Math.round(n)) < 0.005;
    return (n < 0 ? "\u2212" : "") + "Rs. " + Math.abs(n).toLocaleString("en-PK", { minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: 2 });
  }

  // Inputs that only have grey hint text get a real (spoken) label from it.
  function labelInputs(scope) {
    (scope || document).querySelectorAll("input:not([type=hidden]):not([type=checkbox]):not([type=radio]), select, textarea").forEach(function (el) {
      if (el.getAttribute("aria-label") || el.getAttribute("aria-labelledby") || el.closest("label")) return;
      if (el.id && document.querySelector('label[for="' + (window.CSS && CSS.escape ? CSS.escape(el.id) : el.id) + '"]')) return;
      let text = el.getAttribute("placeholder") || el.getAttribute("title") || "";
      if (!text && el.tagName === "SELECT" && el.options.length) text = (el.options[0].textContent || "").replace(/^[\s\u2014-]+|[\s\u2014-]+$/g, "");
      if (!text && el.type === "file") text = "Choose a file";
      if (!text && el.id) text = el.id.replace(/[-_]+/g, " ");
      if (text) el.setAttribute("aria-label", text.trim());
    });
  }
  document.addEventListener("DOMContentLoaded", function () { labelInputs(document); });
  document.addEventListener("vistadmin:ready", function () { setTimeout(function () { labelInputs(document); }, 0); });

  return {
    ask: ask,
    confirm: confirmDialog,
    promptText: promptText,
    formatMoney: formatMoney,
    labelInputs: labelInputs,
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