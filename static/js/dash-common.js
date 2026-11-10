/* Shared pieces for the portal dashboards (accounts, gate, exam, warden, faculty, library, student, parent).
   Read-only helpers: fetch quietly, fill a block, never break the rest of the page when one block fails. */
window.Dash = (function () {
  "use strict";
  const VA = window.VistAdmin;
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const $ = function (id) { return document.getElementById(id); };
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const KIND = { HOLIDAY: "Holiday", EXAM: "Exam", EVENT: "Event", TERM: "Term", DEADLINE: "Deadline" };

  function get(url) { return VA.fetchJson(url, { quiet: true }).then(function (r) { return r.ok ? r.body : null; }).catch(function () { return null; }); }
  async function getAll(map) {
    const keys = Object.keys(map);
    const values = await Promise.all(keys.map(function (k) { return get(map[k]); }));
    const out = {};
    keys.forEach(function (k, i) { out[k] = values[i]; });
    return out;
  }
  function set(id, v) { const el = $(id); if (el) el.textContent = v; }
  function num(v) { return Number(v || 0).toLocaleString("en-PK"); }
  function money(v) { return VA.formatMoney(v); }
  function iso(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
  function addDays(n) { return new Date(Date.now() + n * 864e5); }
  function bad(id, on) { const el = $(id); if (el) el.classList.toggle("bad", !!on); }
  function hide(id) { const el = $(id); if (el) { const p = el.closest(".dash-panel, .today-block"); (p || el).hidden = true; } }
  function run(fn) { try { fn(); } catch (err) { if (window.console) console.error(err); } }
  function compact(v) {
    const n = Math.abs(v);
    if (n >= 1e7) return (v / 1e6).toFixed(0) + "M";
    if (n >= 1e6) return (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (n >= 1e3) return (v / 1e3).toFixed(0) + "k";
    return String(Math.round(v));
  }
  function niceTop(v) { const p = Math.pow(10, Math.floor(Math.log10(v))); const n = v / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }
  function partOfDay() { const h = new Date().getHours(); return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"; }
  function longDate(d) { return (d || new Date()).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" }); }

  // The person's first name for the greeting: the shell's cached identity, else their own record, else the username.
  async function firstName(me) {
    if (!me) return "";
    try {
      const c = JSON.parse(sessionStorage.getItem("vist_ident") || "null");
      if (c && c.uid === me.id && c.ident && c.ident.name) return c.ident.name.split(/\s+/)[0];
    } catch (e) { /* storage may be blocked */ }
    const roles = me.roles || [];
    const url = roles.indexOf("STUDENT") !== -1 ? "/api/v1/students/me" : (roles.length === 1 && (roles[0] === "ADMIN" || roles[0] === "PARENT") ? null : "/api/v1/employees/me");
    const r = url ? await get(url) : null;
    // A username that is only digits (a parent's CNIC, a roll number) is not a name to greet by.
    return (r && r.first_name) || (/[A-Za-z]/.test(me.username || "") ? me.username : "");
  }
  function greet(greetId, dateId, me) {
    set(greetId, partOfDay());
    if (dateId) set(dateId, longDate());
    firstName(me).then(function (n) { if (n) set(greetId, partOfDay() + ", " + n); });
  }
  function chip(id, text, warn) {
    const el = $(id); if (!el) return;
    el.hidden = !text; el.textContent = text || "";
    el.className = "dash-chip" + (warn ? " warn" : "");
  }

  // Income vs expenses, one pair of bars per month. figs = [incomeId, expensesId, netId] (optional).
  function moneyChart(el, rows, figs) {
    if (!rows) { hide(el.id); return; }
    const inc = rows.reduce(function (n, r) { return n + (r.income || 0); }, 0);
    const exp = rows.reduce(function (n, r) { return n + (r.expenses || 0); }, 0);
    if (figs) { set(figs[0], money(inc)); set(figs[1], money(exp)); set(figs[2], money(inc - exp)); bad(figs[2], inc - exp < 0); }
    const max = Math.max.apply(null, rows.map(function (r) { return Math.max(r.income || 0, r.expenses || 0); }).concat([0]));
    if (!max) { el.innerHTML = '<p class="chart-empty">No money has been recorded in the last 12 months.</p>'; return; }
    const W = 640, H = 220, L = 44, R = 8, T = 10, B = 28, top = niceTop(max);
    const slot = (W - L - R) / rows.length, bw = Math.min(14, slot / 3);
    const y = function (v) { return T + (H - T - B) * (1 - v / top); };
    const P = window.Charts ? window.Charts.palette() : { blue: "#2454d6", amber: "#d97706", grid: "#e3e8f2" };
    let svg = "";
    [0, 0.5, 1].forEach(function (f) {
      const v = top * f;
      svg += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + y(v) + '" y2="' + y(v) + '" stroke="' + P.grid + '"/><text x="' + (L - 6) + '" y="' + (y(v) + 4) + '" text-anchor="end" class="axis">' + compact(v) + "</text>";
    });
    rows.forEach(function (r, i) {
      const cx = L + slot * i + slot / 2, m = Number((r.month || "").slice(5, 7));
      const tip = (MONTHS[m - 1] || r.month) + " " + (r.month || "").slice(0, 4);
      [["income", P.blue, -bw - 1], ["expenses", P.amber, 1]].forEach(function (s) {
        const v = r[s[0]] || 0, h = (H - T - B) * (v / top);
        svg += '<rect x="' + (cx + s[2]).toFixed(1) + '" y="' + (y(0) - h).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(0, h).toFixed(1) + '" rx="3" fill="' + s[1] +
          '"><title>' + esc(tip + " — " + s[0] + ": " + money(v)) + "</title></rect>";
      });
      svg += '<text x="' + cx.toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle" class="axis">' + esc(MONTHS[m - 1] || "") + "</text>";
    });
    el.innerHTML = '<svg viewBox="0 0 ' + W + " " + H + '" class="bar-chart" role="img" aria-label="Income and expenses for each of the last 12 months">' + svg + "</svg>" +
      '<ul class="chart-legend inline"><li><span class="swatch" style="background:' + P.blue + '"></span>Income</li><li><span class="swatch" style="background:' + P.amber + '"></span>Expenses</li></ul>';
  }

  // items: [{count, text, href, tone: "red"|"amber"|"blue"}] -- only the ones with something to do.
  function attention(listId, clearId, items) {
    const list = $(listId), clear = $(clearId);
    // A number shows when it is above 0; any other label ("!", "73%") always shows.
    const real = items.filter(function (i) { return i && (typeof i.count === "number" ? i.count > 0 : !!i.count && i.count !== "0"); });
    clear.hidden = real.length > 0; list.hidden = real.length === 0;
    list.innerHTML = real.map(function (i) {
      return '<li><a href="' + esc(i.href) + '" class="attn-item tone-' + (i.tone || "amber") + '"><span class="attn-count">' + esc(i.count) + '</span><span class="attn-text">' +
        esc(i.text) + '</span><span class="attn-go" aria-hidden="true">›</span></a></li>';
    }).join("");
    return real;
  }

  // [[label, value, "bad"?], ...]
  function stats(id, items) {
    const el = $(id); if (!el) return;
    el.innerHTML = items.map(function (i) { return '<li class="' + (i[2] || "") + '"><b>' + esc(i[1]) + "</b><span>" + esc(i[0]) + "</span></li>"; }).join("");
  }
  function facts(id, rows) {
    const el = $(id); if (!el) return;
    el.innerHTML = rows.map(function (r) { return '<li class="' + (r[2] || "") + '"><span>' + esc(r[0]) + "</span><b>" + esc(r[1]) + "</b></li>"; }).join("");
  }
  // [{title, sub, value, href?}]
  function people(id, rows) {
    const el = $(id); if (!el) return;
    el.innerHTML = rows.map(function (r) {
      const inner = '<span class="pl-avatar" aria-hidden="true">' + esc(initials(r.title)) + '</span><span class="pl-body"><strong>' + esc(r.title) + "</strong><small>" + esc(r.sub || "") +
        '</small></span><b class="pl-value' + (r.bad ? " bad" : "") + '">' + esc(r.value == null ? "" : r.value) + "</b>";
      return "<li>" + (r.href ? '<a href="' + esc(r.href) + '">' + inner + "</a>" : '<div>' + inner + "</div>") + "</li>";
    }).join("");
  }
  function initials(name) {
    const w = String(name || "").trim().split(/\s+/).filter(Boolean);
    return ((w[0] || "?")[0] + (w.length > 1 ? w[w.length - 1][0] : "")).toUpperCase();
  }

  function calendar(listId, emptyId, rows, n) {
    const list = $(listId);
    if (!rows) { hide(listId); return; }
    $(emptyId).hidden = rows.length > 0;
    list.innerHTML = rows.slice(0, n || 5).map(function (e) {
      const d = new Date(e.start_date + "T00:00:00");
      const range = e.end_date && e.end_date !== e.start_date ? "until " + new Date(e.end_date + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : d.toLocaleDateString("en-GB", { weekday: "long" });
      return '<li><span class="ev-date"><b>' + d.getDate() + "</b>" + MONTHS[d.getMonth()] + '</span><span class="ev-body"><strong>' + esc(e.title) + "</strong><small>" +
        '<span class="ev-kind kind-' + esc((e.kind || "").toLowerCase()) + '">' + esc(KIND[e.kind] || e.kind) + "</span> " + esc(range) + "</small></span></li>";
    }).join("");
  }
  function announcements(listId, emptyId, rows, n, href) {
    const list = $(listId);
    if (!rows) { hide(listId); return; }
    $(emptyId).hidden = rows.length > 0;
    // the newest first (the feed puts pinned ones on top), then only as many as the dashboard shows
    rows = rows.slice().sort(function (a, b) { return String(b.published_at || "").localeCompare(String(a.published_at || "")); });
    list.innerHTML = rows.slice(0, n || 1).map(function (a) {
      const body = "<strong>" + esc(a.title) + "</strong><small>" + esc(a.issuer_name || "") + (a.published_at ? " · " + esc(VA.formatDateTime(a.published_at)) : "") +
        "</small><span>" + esc((a.body || "").slice(0, 140)) + "</span>";
      return "<li>" + (href ? '<a href="' + esc(href) + '">' + body + "</a>" : '<div class="note">' + body + "</div>") + "</li>";
    }).join("");
  }
  function upcoming() { return "/api/v1/calendar?start=" + iso(new Date()) + "&end=" + iso(addDays(14)); }

  return { get: get, getAll: getAll, set: set, num: num, money: money, iso: iso, addDays: addDays, bad: bad, hide: hide, run: run, compact: compact,
    greet: greet, chip: chip, longDate: longDate, moneyChart: moneyChart, attention: attention, stats: stats, facts: facts, people: people,
    calendar: calendar, announcements: announcements, upcoming: upcoming, esc: esc, MONTHS: MONTHS };
})();
