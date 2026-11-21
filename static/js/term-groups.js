// Term lists grouped by academic session (app/academics/sessions.py), on every admin page:
//   * a term dropdown gets one group per session ("Fall 2026 (current)", then coming, then "Older · Spring 2026");
//   * a term tick-list (.pt-terms) gets one block per session with a button that ticks the whole session; older
//     sessions are folded away.
// Nothing changes until sessions exist (Courses -> Academic terms -> Sessions). Each list keeps its own values, so the
// page's own code works exactly as before.
(function () {
  "use strict";

  let data = null, loading = null, timer = null;
  const esc = function (v) { return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) { return "&#" + c.charCodeAt(0) + ";"; }); };

  function load() {
    if (data || loading) return loading || Promise.resolve(data);
    if (!window.VistAdmin) return Promise.resolve(null);
    loading = window.VistAdmin.fetchJson("/api/v1/academics/sessions", { quiet: true }).then(function (r) {
      data = r.ok ? r.body : { sessions: [], ungrouped: [] };
      loading = null;
      return data;
    });
    return loading;
  }
  function reset() { data = null; }

  function sessionOf() {
    const map = {};
    (data.sessions || []).forEach(function (s) { s.terms.forEach(function (t) { map[t] = s; }); });
    return map;
  }
  function title(s) {
    return s.when === "current" ? s.name + " (current)" : s.when === "past" ? "Older · " + s.name : s.name;
  }

  function groupSelect(sel) {
    const opts = Array.prototype.slice.call(sel.options);
    const sig = opts.map(function (o) { return o.value; }).join("\u0001");
    if (sel.dataset.tgSig === sig) return;
    const of = sessionOf();
    const terms = opts.filter(function (o) { return o.value && of[o.value]; });
    if (terms.length < 1 || opts.length < 2) { sel.dataset.tgSig = sig; return; }
    const keep = sel.value;
    const special = function (o) { return !o.value || o.value === "All batches" || o.value.indexOf("session:") === 0 || o.dataset.keep != null; };
    const head = opts.filter(special);
    const other = opts.filter(function (o) { return !special(o) && !of[o.value]; });
    sel.innerHTML = "";
    head.forEach(function (o) { sel.appendChild(o); });
    (data.sessions || []).forEach(function (s) {
      const mine = terms.filter(function (o) { return of[o.value] === s; });
      if (!mine.length) return;
      const g = document.createElement("optgroup");
      g.label = title(s);
      mine.forEach(function (o) { g.appendChild(o); });
      sel.appendChild(g);
    });
    if (other.length) {
      const g = document.createElement("optgroup");
      g.label = "Other terms";
      other.forEach(function (o) { g.appendChild(o); });
      sel.appendChild(g);
    }
    sel.value = keep;
    sel.dataset.tgSig = Array.prototype.map.call(sel.options, function (o) { return o.value; }).join("\u0001");
  }

  function groupBoxes(box) {
    const labels = Array.prototype.slice.call(box.querySelectorAll(":scope > label, :scope .tg-items > label"))
      .filter(function (l) { const i = l.querySelector("input[type=checkbox][value]"); return !!i; });
    const sig = labels.map(function (l) { return l.querySelector("input").value; }).join("\u0001");
    if (!labels.length || box.dataset.tgSig === sig) return;
    const of = sessionOf();
    if (!labels.some(function (l) { return of[l.querySelector("input").value]; })) { box.dataset.tgSig = sig; return; }
    const blocks = [];
    (data.sessions || []).forEach(function (s) {
      const mine = labels.filter(function (l) { return of[l.querySelector("input").value] === s; });
      if (mine.length) blocks.push({ s: s, items: mine });
    });
    const other = labels.filter(function (l) { return !of[l.querySelector("input").value]; });
    if (other.length) blocks.push({ s: null, items: other });
    box.innerHTML = "";
    box.classList.add("tg-boxes");
    blocks.forEach(function (b) {
      const wrap = document.createElement(b.s && b.s.when === "past" ? "details" : "div");
      wrap.className = "tg-session";
      const head = document.createElement(b.s && b.s.when === "past" ? "summary" : "div");
      head.className = "tg-head";
      head.innerHTML = "<strong>" + esc(b.s ? title(b.s) : "Other terms") + "</strong>" +
        ' <button type="button" class="link-btn tg-all">Tick all ' + b.items.length + "</button>";
      wrap.appendChild(head);
      const items = document.createElement("div");
      items.className = "tg-items";
      b.items.forEach(function (l) { items.appendChild(l); });
      wrap.appendChild(items);
      head.querySelector(".tg-all").addEventListener("click", function (e) {
        e.preventDefault();
        const boxes = items.querySelectorAll("input[type=checkbox]");
        const all = Array.prototype.every.call(boxes, function (c) { return c.checked; });
        boxes.forEach(function (c) { c.checked = !all; c.dispatchEvent(new Event("change", { bubbles: true })); });
      });
      box.appendChild(wrap);
    });
    box.dataset.tgSig = sig;
  }

  function scan() {
    if (!data || !(data.sessions || []).length) return;
    document.querySelectorAll("select").forEach(function (sel) {
      if (sel.dataset.noTermGroups != null || sel.multiple) return;
      groupSelect(sel);
    });
    document.querySelectorAll(".pt-terms").forEach(groupBoxes);
  }

  function soon() { clearTimeout(timer); timer = setTimeout(function () { load().then(scan); }, 120); }

  document.addEventListener("DOMContentLoaded", function () {
    load().then(scan);
    new MutationObserver(function (list) {
      for (let i = 0; i < list.length; i++) {
        const t = list[i].target;
        if (t.tagName === "SELECT" || (t.classList && t.classList.contains("pt-terms")) || (t.querySelector && t.querySelector("select, .pt-terms"))) { soon(); return; }
      }
    }).observe(document.body, { childList: true, subtree: true });
  });

  window.TermGroups = { load: load, reset: function () { reset(); soon(); }, groupSelect: groupSelect, groupBoxes: groupBoxes };
})();
