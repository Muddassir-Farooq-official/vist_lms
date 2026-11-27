// Guides on every page of every portal (loaded by admin-common.js once the session is known):
//  * a Tour button in the top bar, and a tour that starts by itself the first time someone opens a page. The tour comes
//    from the manual (the page's menu item, API /onboarding/page-help), the page's own tabs and buttons, and the words in
//    tours.js. Pages with their own tour (the Timetable wizard) keep it.
//  * "What to do now": the person's profile still to complete and today's important things (API /onboarding/me), as a
//    banner until done. After a 7-day grace period an incomplete profile opens first after sign-in (menu still works).
//  * The administrator's "Set up your LMS" checklist on the dashboard (API /onboarding/setup).
(function () {
  "use strict";
  if (window.VistGuide) return;
  const VA = window.VistAdmin;
  const path = location.pathname.replace(/\/+$/, "") || "/";
  const portal = path.split("/")[2] || "";
  const params = new URLSearchParams(location.search);
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  };
  let me = null;

  function load(src) {
    return new Promise(function (ok) {
      const s = document.createElement("script");
      s.src = src; s.onload = ok; s.onerror = ok;
      document.head.appendChild(s);
    });
  }
  function visible(el) { return !!(el && el.getClientRects().length && getComputedStyle(el).visibility !== "hidden"); }
  function clean(t) { return String(t || "").replace(/\s+/g, " ").trim(); }
  function titleText() {
    const t = document.querySelector(".content-title");
    if (!t) return "";
    const c = t.cloneNode(true);
    c.querySelectorAll("button, .sr-only, [aria-hidden='true']").forEach(function (x) { x.remove(); });
    return clean(c.textContent);
  }
  function navLabel() {
    const a = document.querySelector("#drawer .drawer-link.active, #drawer [aria-current='page']");
    return a ? clean(a.textContent) : titleText();
  }
  function entry() {
    const T = window.VIST_TOURS || {};
    if (T[path]) return T[path];
    if (/\/profile$/.test(path)) return T["/dashboard/faculty/profile"] || {};
    return {};
  }

  // ------------------------------------------------------------------ the tour of this page
  async function steps() {
    const e = entry();
    const out = [];
    const title = titleText() || navLabel();
    let help = null;
    const label = navLabel();
    if (label) {
      const r = await VA.fetchJson("/api/v1/onboarding/page-help?portal=" + encodeURIComponent(portal) + "&label=" + encodeURIComponent(label), { quiet: true });
      help = r.ok && r.body && r.body.text ? r.body : null;
    }
    const sub = clean((document.querySelector(".content-subtitle") || {}).textContent);
    let intro = (help && help.text) || sub || "What this page is for.";
    intro = intro.charAt(0).toUpperCase() + intro.slice(1);
    out.push({ el: ".content-title-group", title: title || "This page", text: intro, list: help ? help.more : [] });

    // the page's tabs, each opened in turn
    const tabs = Array.prototype.filter.call(document.querySelectorAll("[data-auto-tabs] [data-tab], .mc-tabs [data-tab], .portal-tabs [data-tab], [role='tablist'] [data-tab]"), visible).filter(function (t, i, all) { return all.indexOf(t) === i; }).slice(0, 12);
    tabs.forEach(function (t) {
      const key = t.getAttribute("data-tab");
      const panel = document.querySelector('[data-tab-panel="' + key + '"]');
      const hint = panel && panel.querySelector(".panel-hint, .content-subtitle, p");
      out.push({ el: t, title: clean(t.textContent), text: (e.tabs && e.tabs[key]) || (hint ? clean(hint.textContent).slice(0, 260) : "Open this tab."),
                 before: function () { t.click(); } });
    });
    if (tabs.length) out.push({ el: tabs[0], title: "Back to the start", text: "You can open any tab at any time. The page remembers the one you were on.", before: function () { tabs[0].click(); } });

    // the main buttons that have words in tours.js
    Object.keys(e.buttons || {}).forEach(function (words) {
      const b = Array.prototype.find.call(document.querySelectorAll(".content-main button, .content-main a.role-btn"), function (x) {
        return visible(x) && clean(x.textContent).toLowerCase().indexOf(words.toLowerCase()) !== -1;
      });
      if (b) out.push({ el: b, title: clean(b.textContent), text: e.buttons[words] });
    });
    (e.steps || []).forEach(function (s) { out.push(s); });
    out.push({ el: "#vg-tour", title: "Help is always here", text: "Press Tour on any page to see this again. The Help & manual page in the menu has the full guide for your portal." });
    return out;
  }

  async function tour() {
    const own = document.getElementById("tw-tour-btn");        // a page with its own tour (the Timetable wizard)
    if (own) { own.click(); return; }
    if (!window.VistTour) await load("/static/js/tour.js");
    window.VistTour.start(await steps(), { onEnd: function () { store.set(seenKey(), "1"); } });
  }
  function seenKey() { return "vist.tour.seen:" + (me && me.id) + ":" + path; }

  function tourButton() {
    const bar = document.querySelector(".content-topbar-right");
    if (!bar || document.getElementById("vg-tour")) return;
    const b = document.createElement("button");
    b.type = "button"; b.id = "vg-tour"; b.className = "vg-tour-btn";
    b.title = "A guided tour of this page"; b.setAttribute("aria-label", "Take a guided tour of this page");
    b.innerHTML = '<span aria-hidden="true">?</span><span class="vg-tour-word">Tour</span>';
    b.addEventListener("click", tour);
    bar.insertBefore(b, bar.firstChild);
  }

  function autoTour() {
    if (document.getElementById("tw-tour-btn")) return;            // that page starts its own
    if (path === "/dashboard/admin/welcome") return;                 // the first-run wizard explains itself
    if (!params.get("tour") && store.get(seenKey())) return;
    setTimeout(tour, 1500);        // after the page has drawn and any message that opens with it (the tour waits for it to close)
  }

  // ------------------------------------------------------------------ what to do now
  function itemLink(x) {
    if (x.link === "#push") return '<button type="button" class="link-btn og-push">' + esc(x.title) + "</button>";
    return '<a href="' + esc(x.link) + '">' + esc(x.title) + "</a>";
  }
  async function todo() {
    const r = await VA.fetchJson("/api/v1/onboarding/me", { quiet: true });
    if (!r.ok) return;
    const d = r.body, p = d.profile, onProfile = p && path === new URL(p.link, location.origin).pathname || /\/profile$/.test(path);
    // after the grace period, an incomplete profile opens first after sign-in (once a session; the menu still works)
    if (d.must_finish && !onProfile) {
      let done = null;
      try { done = sessionStorage.getItem("vist.og.sent"); } catch (e) { /* ignore */ }
      if (!done) {
        try { sessionStorage.setItem("vist.og.sent", "1"); } catch (e) { /* ignore */ }
        location.href = p.link + "?complete=1";
        return;
      }
    }
    const main = document.querySelector(".content-main");
    if (!main) return;
    const missing = p ? p.missing : [];
    if (onProfile && p) {
      // on the profile page: the checklist itself
      const box = document.createElement("section");
      box.className = "sh-card og-profile" + (d.must_finish ? " og-urgent" : "");
      box.id = "og-banner";
      box.innerHTML = "<h2 class=\"panel-title\">Complete your profile: " + p.done + " of " + p.total + "</h2>" +
        (d.must_finish ? '<p class="dash-alert error">Your profile is still incomplete, so this page opens first after you sign in. Finish these, then everything works as usual.</p>' :
          missing.length && d.grace_days_left != null ? '<p class="cc-meta">' + d.grace_days_left + " day" + (d.grace_days_left === 1 ? "" : "s") + " left before this page opens first after you sign in.</p>" : "") +
        '<ul class="og-checks">' + p.items.map(function (x) {
          return '<li class="' + (x.done ? "ok" : "todo") + '"><strong>' + esc(x.label) + "</strong>" + (x.waiting ? " <small>sent, waiting for the office</small>" : x.done ? "" : " <small>" + esc(x.how) + "</small>") + "</li>";
        }).join("") + "</ul>";
      if (missing.length || d.must_finish) main.insertBefore(box, main.firstChild);
    } else if ((missing.length || d.todo.length) && !sessionHidden()) {
      const bar = document.createElement("div");
      bar.className = "og-banner" + (d.must_finish ? " og-urgent" : "");
      bar.id = "og-banner";
      bar.setAttribute("role", "status");
      const lines = [];
      if (missing.length) lines.push('<a href="' + esc(p.link) + '">Complete your profile: ' + missing.length + " thing" + (missing.length === 1 ? "" : "s") + " left</a>" +
        (d.grace_days_left != null && !d.must_finish ? " <small>(" + d.grace_days_left + " day" + (d.grace_days_left === 1 ? "" : "s") + " left)</small>" : ""));
      d.todo.forEach(function (x) { lines.push(itemLink(x)); });
      bar.innerHTML = '<strong class="og-title">To do</strong><ul>' + lines.map(function (l) { return "<li>" + l + "</li>"; }).join("") + "</ul>" +
        '<button type="button" class="link-btn og-later" title="Hide until you sign in again">Later</button>';
      main.insertBefore(bar, main.firstChild);
      bar.querySelector(".og-later").addEventListener("click", function () { bar.remove(); try { sessionStorage.setItem("vist.og.later", "1"); } catch (e) { /* ignore */ } });
    }
    document.addEventListener("click", function (e) {
      if (e.target.closest(".og-push")) { const pl = document.getElementById("push-link"); if (pl) pl.click(); }
    });
  }
  function sessionHidden() { try { return sessionStorage.getItem("vist.og.later") === "1"; } catch (e) { return false; } }

  // ------------------------------------------------------------------ the administrator's setup checklist
  // A brand-new institute (nothing in Settings -> Institute, no programs or students): its administrator goes to the
  // short first-run wizard once per sign-in, unless they chose "Skip for now".
  async function firstRun() {
    if (path !== "/dashboard/admin" || me.viewing_as_by) return false;
    try { if (sessionStorage.getItem("vist.firstrun.skip")) return false; } catch (e) { /* private mode */ }
    const r = await VA.fetchJson("/api/v1/onboarding/first-run", { quiet: true });
    if (!r.ok || !r.body.needed) return false;
    location.replace("/dashboard/admin/welcome");
    return true;
  }

  async function setup() {
    if (path !== "/dashboard/admin" || (me.roles || []).indexOf("ADMIN") === -1) return;
    const until = Number(store.get("vist.og.setup.hidden") || 0);
    const r = await VA.fetchJson("/api/v1/onboarding/setup", { quiet: true });
    if (!r.ok || r.body.done === r.body.total || until > Date.now()) return;
    const d = r.body, main = document.querySelector(".content-main");
    const box = document.createElement("section");
    box.className = "sh-card og-setup";
    box.id = "og-setup";
    const next = d.steps.find(function (s) { return !s.done; });
    box.innerHTML = '<div class="og-setup-head"><div><h2 class="panel-title">Set up your LMS: ' + d.done + " of " + d.total + " done</h2>" +
      '<p class="cc-meta">Do these in order. Each one ticks itself when it is done.' + (next ? " Next: <strong>" + esc(next.title) + "</strong>." : "") + "</p></div>" +
      '<button type="button" class="link-btn og-hide">Hide for 7 days</button></div>' +
      '<div class="og-progress"><i style="width:' + Math.round(100 * d.done / d.total) + '%"></i></div>' +
      '<ol class="og-steps">' + d.steps.map(function (s) {
        const how = s.link + (s.link.indexOf("?") === -1 ? "?" : "&") + "tour=1";
        return '<li class="' + (s.done ? "ok" : "todo") + (next && s.key === next.key ? " next" : "") + '"><div><strong>' + esc(s.title) + "</strong><small>" + esc(s.text) + "</small></div>" +
          (s.done ? '<span class="og-done">Done</span>' : '<span class="og-acts"><a class="role-btn small-btn" href="' + esc(s.link) + '">Do it</a> <a class="link-btn" href="' + esc(how) + '">Show me how</a></span>') + "</li>";
      }).join("") + "</ol>";
    main.insertBefore(box, main.firstChild);
    box.querySelector(".og-hide").addEventListener("click", function () { store.set("vist.og.setup.hidden", String(Date.now() + 7 * 86400000)); box.remove(); });
  }

  async function start(m) {
    me = m || {};
    if (!document.querySelector(".content-main")) return;
    if (!window.VIST_TOURS) await load("/static/js/tours.js");
    tourButton();
    if ((me.roles || []).indexOf("ADMIN") !== -1) {
      if (await firstRun()) return;
      await setup();
    }
    if (!me.viewing_as_by) await todo();
    autoTour();
  }
  window.VistGuide = { tour: tour, start: start };
})();
