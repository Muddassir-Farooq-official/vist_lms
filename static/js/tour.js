// A small guided tour: dims the page, highlights one part at a time and explains it, with Back / Next / Skip.
//   VistTour.start([{ el: "#id", title: "…", text: "…", before: function () {…} }, …], { onEnd: fn })
// A step whose element is missing or hidden is skipped. Esc ends the tour; ← → move. On a phone the card sits at the bottom.
(function () {
  "use strict";
  let steps = [], i = 0, opts = {}, ring = null, card = null, keyHandler = null;
  const esc = function (v) { return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) { return "&#" + c.charCodeAt(0) + ";"; }); };

  function visible(el) { return !!(el && el.getClientRects().length && getComputedStyle(el).visibility !== "hidden"); }

  function end() {
    if (ring) ring.remove();
    if (card) card.remove();
    ring = card = null;
    document.removeEventListener("keydown", keyHandler);
    window.removeEventListener("resize", place);
    window.removeEventListener("scroll", place, true);
    if (opts.onEnd) opts.onEnd();
  }

  function place() {
    if (!ring || !card) return;
    const s = steps[i], el = s && s.target;
    if (!el) return;
    const r = el.getBoundingClientRect(), pad = 6;
    ring.style.top = (r.top - pad) + "px";
    ring.style.left = (r.left - pad) + "px";
    ring.style.width = (r.width + pad * 2) + "px";
    ring.style.height = (r.height + pad * 2) + "px";
    if (window.innerWidth < 640) { card.style.top = ""; card.style.left = ""; card.classList.add("vt-bottom"); return; }
    card.classList.remove("vt-bottom");
    const cw = card.offsetWidth, ch = card.offsetHeight, gap = 14;
    let top = r.bottom + gap;
    if (top + ch > window.innerHeight - 8) top = Math.max(8, r.top - ch - gap);
    let left = Math.min(Math.max(8, r.left), window.innerWidth - cw - 8);
    card.style.top = top + "px";
    card.style.left = left + "px";
  }

  function show(n, dir) {
    if (n < 0) { n = 0; dir = 1; }
    if (n >= steps.length) { end(); return; }
    i = n;
    const s = steps[i];
    if (s.before) { try { s.before(); } catch (e) { /* keep going */ } }
    // give the page a moment to switch tabs or draw before measuring
    setTimeout(function () {
      const el = typeof s.el === "string" ? document.querySelector(s.el) : s.el;
      if (!visible(el)) { show(i + (dir || 1), dir || 1); return; }
      s.target = el;
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      card.innerHTML = '<div class="vt-count">' + (i + 1) + " / " + steps.length + '</div><h3 class="vt-title" id="vt-title">' + esc(s.title) + "</h3>" +
        '<p class="vt-text">' + esc(s.text) + "</p>" +
        '<div class="vt-actions"><button type="button" class="link-btn vt-skip">Skip tour</button><span class="vt-spacer"></span>' +
        (i > 0 ? '<button type="button" class="role-btn secondary-btn vt-back">Back</button>' : "") +
        '<button type="button" class="role-btn vt-next">' + (i === steps.length - 1 ? "Finish" : "Next") + "</button></div>";
      card.querySelector(".vt-skip").onclick = end;
      card.querySelector(".vt-next").onclick = function () { show(i + 1, 1); };
      const back = card.querySelector(".vt-back");
      if (back) back.onclick = function () { show(i - 1, -1); };
      setTimeout(place, 260);
      place();
      card.querySelector(".vt-next").focus({ preventScroll: true });
    }, 120);
  }

  function start(list, options) {
    if (card) end();
    steps = list || [];
    opts = options || {};
    if (!steps.length) return;
    ring = document.createElement("div");
    ring.className = "vt-ring";
    card = document.createElement("div");
    card.className = "vt-card";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-labelledby", "vt-title");
    document.body.appendChild(ring);
    document.body.appendChild(card);
    keyHandler = function (e) {
      if (e.key === "Escape") end();
      else if (e.key === "ArrowRight") show(i + 1, 1);
      else if (e.key === "ArrowLeft") show(i - 1, -1);
    };
    document.addEventListener("keydown", keyHandler);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    show(0, 1);
  }

  window.VistTour = { start: start, end: end };
})();
