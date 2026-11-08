/* Release notes: the "What's new" list on the status page (/health), and the card shown once in the staff portals
   after each update. Text is always inserted as text, never as HTML. */
(function () {
  "use strict";
  var KIND_LABEL = { new: "New", improved: "Improved", fixed: "Fixed", security: "Safety" };
  var SEEN_KEY = "vist_seen_version";

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function renderRelease(r, isCurrent, onlyFor) {
    var li = el("li", "rl" + (isCurrent ? " is-current" : ""));
    var meta = el("div", "rl-meta");
    meta.appendChild(el("span", "rl-ver", "v" + r.version));
    if (r.date_text) meta.appendChild(el("span", null, r.date_text));
    if (isCurrent) meta.appendChild(el("span", "rl-now", "Running now"));
    li.appendChild(meta);
    li.appendChild(el("h3", null, r.title || ""));
    if (r.summary) li.appendChild(el("p", "rl-sum", r.summary));
    var ul = el("ul", "rl-items");
    (r.changes || []).forEach(function (c) {
      if (onlyFor && c.for !== onlyFor && c.for !== "Everyone") return;
      var item = el("li");
      item.appendChild(el("span", "rl-kind k-" + c.kind, KIND_LABEL[c.kind] || c.kind));
      var t = el("span", null, c.text);
      if (c.for) t.appendChild(el("span", "rl-for", "For: " + c.for));
      item.appendChild(t);
      ul.appendChild(item);
    });
    if (ul.children.length) li.appendChild(ul);
    else if (onlyFor) return null;
    return li;
  }

  function load(limit) {
    return fetch("/api/v1/system/releases" + (limit ? "?limit=" + limit : ""), { credentials: "include", headers: { Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw r.status; return r.json(); });
  }

  // ---------------------------------------------------------------- status page
  function statusPage(list) {
    var loading = document.getElementById("wn-loading");
    var signin = document.getElementById("wn-signin");
    var more = document.getElementById("wn-more");
    var filter = document.getElementById("wn-filter");
    var shownAll = false, onlyFor = "", data = null;
    var FIRST = 3;

    function draw() {
      list.textContent = "";
      var n = 0;
      data.releases.forEach(function (r, i) {
        if (!shownAll && n >= FIRST) return;
        var li = renderRelease(r, i === 0, onlyFor);
        if (li) { list.appendChild(li); n += 1; }
      });
      more.hidden = shownAll || data.releases.length <= FIRST;
      if (!list.children.length) list.appendChild(el("li", "hs-muted", "No changes for this group yet."));
    }

    load().then(function (d) {
      data = d;
      loading.hidden = true;
      var groups = [];
      d.releases.forEach(function (r) { (r.changes || []).forEach(function (c) { if (c.for && c.for !== "Everyone" && groups.indexOf(c.for) < 0) groups.push(c.for); }); });
      groups.sort().forEach(function (g) { var b = el("button", null, g); b.type = "button"; b.dataset.for = g; filter.appendChild(b); });
      filter.hidden = groups.length < 2;
      filter.addEventListener("click", function (e) {
        var b = e.target.closest("button"); if (!b) return;
        onlyFor = b.dataset.for || "";
        Array.prototype.forEach.call(filter.children, function (x) { x.classList.toggle("active", x === b); });
        draw();
      });
      more.addEventListener("click", function () { shownAll = true; draw(); });
      draw();
      try { localStorage.setItem(SEEN_KEY, d.version); } catch (e) { /* private mode */ }
    }).catch(function () {
      loading.hidden = true;
      signin.hidden = false;
    });
  }

  // ---------------------------------------------------------------- "A new version is here", once per version
  // A short burst of confetti (about 2.5 seconds). Nothing for people who asked their device for less motion.
  function confetti(host) {
    try { if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return; } catch (e) { return; }
    const c = document.createElement("canvas");
    c.className = "wn-confetti";
    c.setAttribute("aria-hidden", "true");
    (host || document.body).appendChild(c);
    const ctx = c.getContext("2d");
    if (!ctx) { c.remove(); return; }
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    function size() { c.width = innerWidth * dpr; c.height = innerHeight * dpr; }
    size();
    const colors = ["#2454d6", "#f5b400", "#16a34a", "#ea8c1f", "#7c3aed", "#dc2626", "#0ea5e9"];
    const parts = [];
    const n = innerWidth < 600 ? 90 : 160;
    for (let i = 0; i < n; i++) {
      const fromLeft = i % 2 === 0;
      parts.push({
        x: (fromLeft ? 0.15 : 0.85) * innerWidth, y: innerHeight * 0.55,
        vx: (fromLeft ? 1 : -1) * (3 + Math.random() * 7), vy: -(9 + Math.random() * 9),
        w: 6 + Math.random() * 6, h: 3 + Math.random() * 5, r: Math.random() * 6.28, vr: (Math.random() - 0.5) * 0.3,
        color: colors[i % colors.length],
      });
    }
    const start = performance.now();
    function frame(t) {
      const age = t - start;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      parts.forEach(function (p) {
        p.vy += 0.35; p.vx *= 0.99; p.x += p.vx; p.y += p.vy; p.r += p.vr;
        ctx.save();
        ctx.globalAlpha = Math.max(0, 1 - Math.max(0, age - 1800) / 700);
        ctx.translate(p.x, p.y); ctx.rotate(p.r);
        ctx.fillStyle = p.color; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
        ctx.restore();
      });
      if (age < 2500) requestAnimationFrame(frame); else c.remove();
    }
    requestAnimationFrame(frame);
  }

  function popupOnce(current) {
    let seen = null;
    try { seen = localStorage.getItem(SEEN_KEY); } catch (e) { return; }
    if (seen === current) return;
    function done() { try { localStorage.setItem(SEEN_KEY, current); } catch (e) { /* ignore */ } }
    fetch("/api/v1/system/version", { credentials: "include", headers: { Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw r.status; return r.json(); })
      .then(function (v) {
        if (v.version !== current) return;
        const dlg = el("dialog", "wn-modal wn-celebrate");
        dlg.setAttribute("aria-labelledby", "wn-modal-title");
        const body = el("div", "wn-body");
        body.appendChild(el("p", "wn-emoji", "\uD83C\uDF89"));
        const h = el("h2", null, "A new version is here!"); h.id = "wn-modal-title";
        body.appendChild(h);
        body.appendChild(el("p", "wn-ver", "Version " + v.version + (v.released_on_text ? " \u00b7 " + v.released_on_text : "")));
        const actions = el("div", "wn-actions");
        const all = el("a", null, "See what's new"); all.href = "/health#whats-new";
        const ok = el("button", null, "OK"); ok.type = "button";
        actions.appendChild(all); actions.appendChild(ok);
        dlg.appendChild(body); dlg.appendChild(actions);
        document.body.appendChild(dlg);
        ok.addEventListener("click", function () { done(); dlg.close(); });
        all.addEventListener("click", done);
        dlg.addEventListener("cancel", done);
        dlg.addEventListener("close", function () { setTimeout(function () { dlg.remove(); }, 50); });
        if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
        confetti(dlg.open ? dlg : document.body);
      }).catch(function () { /* try again on the next page */ });
  }

  function start() {
    var list = document.getElementById("wn-list");
    if (list) { statusPage(list); return; }
    var link = document.getElementById("app-version");
    if (!link) return;
    // wait until the portal has checked the session and shown itself
    var tries = 0;
    var t = setInterval(function () {
      var shell = document.getElementById("app-shell");
      if ((shell && !shell.hidden) || ++tries > 40) { clearInterval(t); if (shell && !shell.hidden) popupOnce(link.dataset.version); }
    }, 250);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
