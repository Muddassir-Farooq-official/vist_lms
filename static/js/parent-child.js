/* Parent pages: the child picker. Remembers the chosen child on this device and calls window.ParentChild.onChange(id). */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const KEY = "vist_parent_child";
  const $ = function (id) { return document.getElementById(id); };
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function initials(n) { const w = String(n || "").split(/\s+/).filter(Boolean); return ((w[0] || "?")[0] + (w.length > 1 ? w[w.length - 1][0] : "")).toUpperCase(); }
  const api = { kids: [], current: null, onChange: null, el: el,
    money: function (v) { return "Rs. " + Number(v || 0).toLocaleString("en-PK", { maximumFractionDigits: 0 }); },
    day: function (iso, opts) { return iso ? new Date(String(iso).slice(0, 10) + "T00:00:00").toLocaleDateString("en-GB", opts || { day: "numeric", month: "short", year: "numeric" }) : "—"; },
    time: function (t) { return t ? VA.formatTimeShort(t) : ""; },
    error: function (msg) { const e = $("pc-error"); e.textContent = msg; e.hidden = !msg; } };
  window.ParentChild = api;

  function pick(id) {
    api.current = id;
    try { localStorage.setItem(KEY, id); } catch (e) { /* private mode */ }
    Array.prototype.forEach.call($("pc-tabs").children, function (b) { const on = b.dataset.id === id; b.classList.toggle("active", on); b.setAttribute("aria-selected", String(on)); });
    api.error("");
    if (api.onChange) api.onChange(id);
  }

  document.addEventListener("vistadmin:ready", async function () {
    const r = await VA.fetchJson("/api/v1/parents/me/children");
    if (!r.ok) { api.error("Could not load your children. Please refresh the page."); return; }
    api.kids = r.body;
    if (!api.kids.length) { $("pc-none").hidden = false; return; }
    const tabs = $("pc-tabs");
    api.kids.forEach(function (k) {
      const b = el("button", "child-tab"); b.type = "button"; b.setAttribute("role", "tab"); b.dataset.id = k.id;
      b.appendChild(el("span", "child-tab-av", initials(k.name))); b.appendChild(el("span", null, k.name.split(" ")[0]));
      b.addEventListener("click", function () { if (api.current !== k.id) pick(k.id); });
      tabs.appendChild(b);
    });
    $("pc-bar").hidden = api.kids.length < 2;
    let saved = null;
    try { saved = localStorage.getItem(KEY); } catch (e) { /* ignore */ }
    pick(api.kids.some(function (k) { return k.id === saved; }) ? saved : api.kids[0].id);
  });
})();
