/* Tasks -> Task map: who gives tasks to whom (/api/v1/tasks/map). People are circles, arrows go from the person who
   gave the task to the person who received it, thicker = more tasks, coloured by the most urgent state among them. */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  let W = 1000, H = 620;                       // a portrait drawing on phones (set in draw())
  const NS = "http://www.w3.org/2000/svg";
  const ORDER = ["late", "open", "waiting", "done", "rejected", "cancelled"];
  const LABEL = { late: "Late", open: "In progress", waiting: "Waiting for confirmation", done: "Done", rejected: "Rejected", cancelled: "Called off" };
  const STATUS = { PENDING: "Not accepted yet", ACCEPTED: "In progress", AWAITING_CONFIRMATION: "Waiting for confirmation", COMPLETED: "Done",
    REJECTED: "Rejected", CANCELLED: "Called off" };
  let data = null, loaded = false, selected = null;

  const $ = function (id) { return document.getElementById(id); };
  function el(tag, attrs, parent) {
    const n = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (parent) parent.appendChild(n);
    return n;
  }
  function h(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function initials(name) { return String(name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map(function (w) { return w[0]; }).join("").toUpperCase(); }
  function main(c) {                                   // the group that colours a link: the most urgent one present
    if (c.late) return "late";
    if (c.open) return "open";
    if (c.waiting) return "waiting";
    if (c.done) return "done";
    return c.rejected ? "rejected" : "cancelled";
  }
  function day(d) { return d ? new Date(d).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : ""; }

  // ------------------------------------------------------------------ layout: a small force simulation
  function layout(nodes, edges) {
    const n = nodes.length, pos = {};
    const me = nodes.find(function (x) { return x.is_me; });
    nodes.forEach(function (node, i) {
      const a = (2 * Math.PI * i) / Math.max(n, 1);
      const r = Math.min(W, H) * (n > 12 ? 0.38 : 0.3);
      pos[node.user_id] = { x: W / 2 + r * Math.cos(a), y: H / 2 + r * Math.sin(a), vx: 0, vy: 0 };
    });
    if (me) pos[me.user_id] = { x: W / 2, y: H / 2, vx: 0, vy: 0, fixed: true };
    if (n <= 2) return pos;
    const k = Math.sqrt((W * H) / n) * 0.62;
    for (let it = 0; it < 320; it++) {
      const t = 1 - it / 320;
      nodes.forEach(function (a) {
        const p = pos[a.user_id];
        nodes.forEach(function (b) {
          if (a === b) return;
          const q = pos[b.user_id];
          let dx = p.x - q.x, dy = p.y - q.y, d = Math.sqrt(dx * dx + dy * dy) || 0.01;
          const f = (k * k) / d / d;
          p.vx += dx * f * 0.6; p.vy += dy * f * 0.6;
        });
      });
      edges.forEach(function (e) {
        const p = pos[e.from], q = pos[e.to];
        if (!p || !q) return;
        const dx = q.x - p.x, dy = q.y - p.y, d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const f = (d * d) / k / d * 0.06 * (1 + Math.log(1 + e.counts.total));
        p.vx += dx * f; p.vy += dy * f; q.vx -= dx * f; q.vy -= dy * f;
      });
      nodes.forEach(function (a) {
        const p = pos[a.user_id];
        if (p.fixed) { p.vx = p.vy = 0; return; }
        p.vx += (W / 2 - p.x) * 0.01; p.vy += (H / 2 - p.y) * 0.012;
        const v = Math.sqrt(p.vx * p.vx + p.vy * p.vy) || 1, cap = 24 * t + 1;
        p.x += (p.vx / v) * Math.min(v, cap); p.y += (p.vy / v) * Math.min(v, cap);
        p.x = Math.max(60, Math.min(W - 60, p.x)); p.y = Math.max(50, Math.min(H - 60, p.y));
        p.vx *= 0.5; p.vy *= 0.5;
      });
    }
    return pos;
  }

  // ------------------------------------------------------------------ drawing
  function draw() {
    const box = $("tm-canvas");
    const narrow = (box.clientWidth || window.innerWidth) < 640;
    W = narrow ? 420 : 1000; H = narrow ? 560 : 620;
    box.innerHTML = "";
    $("tm-empty").hidden = data.edges.length > 0;
    if (!data.edges.length) { $("tm-side").innerHTML = ""; return; }
    const radius = {};
    data.nodes.forEach(function (n) { radius[n.user_id] = Math.min(30, 14 + Math.sqrt(n.given.total + n.received.total) * 2.6); });
    const pos = layout(data.nodes, data.edges);
    const svg = el("svg", { viewBox: "0 0 " + W + " " + H, class: "tm-svg", preserveAspectRatio: "xMidYMid meet" }, box);
    const defs = el("defs", {}, svg);
    ORDER.forEach(function (g) {
      const m = el("marker", { id: "tm-arrow-" + g, viewBox: "0 0 10 10", refX: 8, refY: 5, markerWidth: 13, markerHeight: 13, markerUnits: "userSpaceOnUse",
        orient: "auto-start-reverse" }, defs);       // the same size however thick the line is
      el("path", { d: "M0,0 L10,5 L0,10 z", class: "tm-arrow g-" + g }, m);
    });
    const max = Math.max.apply(null, data.edges.map(function (e) { return e.counts.total; }));
    const gEdges = el("g", {}, svg), gNodes = el("g", {}, svg);
    data.edges.forEach(function (e) {
      const a = pos[e.from], b = pos[e.to];
      if (!a || !b) return;
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
      const ux = dx / d, uy = dy / d;
      const sx = a.x + ux * radius[e.from], sy = a.y + uy * radius[e.from];
      const tx = b.x - ux * (radius[e.to] + 4), ty = b.y - uy * (radius[e.to] + 4);
      const bend = 0.16 * d;                                       // A->B and B->A curve apart
      const cx = (sx + tx) / 2 - uy * bend, cy = (sy + ty) / 2 + ux * bend;
      const g = main(e.counts);
      const width = 1.6 + 6 * Math.sqrt(e.counts.total / max);
      const grp = el("g", { class: "tm-edge", tabindex: 0, role: "button" }, gEdges);
      el("path", { d: "M" + sx + "," + sy + " Q" + cx + "," + cy + " " + tx + "," + ty, class: "tm-hit" }, grp);
      el("path", { d: "M" + sx + "," + sy + " Q" + cx + "," + cy + " " + tx + "," + ty, class: "tm-line g-" + g, "stroke-width": width.toFixed(1),
        "marker-end": "url(#tm-arrow-" + g + ")" }, grp);
      const mx = 0.25 * sx + 0.5 * cx + 0.25 * tx, my = 0.25 * sy + 0.5 * cy + 0.25 * ty;
      el("circle", { cx: mx, cy: my, r: 10, class: "tm-badge g-" + g }, grp);
      const tt = el("text", { x: mx, y: my + 4, class: "tm-badge-t" }, grp); tt.textContent = e.counts.total;
      const title = el("title", {}, grp);
      title.textContent = nameOf(e.from) + " → " + nameOf(e.to) + ": " + e.counts.total + " task(s)";
      const pick = function () { select({ edge: e }, grp); };
      grp.addEventListener("click", pick);
      grp.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } });
    });
    data.nodes.forEach(function (n) {
      const p = pos[n.user_id];
      const late = n.received.late;
      const grp = el("g", { class: "tm-node" + (n.is_me ? " me" : ""), transform: "translate(" + p.x.toFixed(1) + "," + p.y.toFixed(1) + ")", tabindex: 0, role: "button" }, gNodes);
      el("circle", { r: radius[n.user_id], class: "tm-node-c" + (late ? " has-late" : "") }, grp);
      const t = el("text", { y: 4, class: "tm-node-i" }, grp); t.textContent = initials(n.name);
      const nm = el("text", { y: radius[n.user_id] + 15, class: "tm-node-n" }, grp);
      nm.textContent = n.name.length > 18 ? n.name.slice(0, 17) + "…" : n.name;
      if (late) {
        el("circle", { cx: radius[n.user_id] * 0.72, cy: -radius[n.user_id] * 0.72, r: 8, class: "tm-late-dot" }, grp);
        const lt = el("text", { x: radius[n.user_id] * 0.72, y: -radius[n.user_id] * 0.72 + 3.5, class: "tm-late-t" }, grp); lt.textContent = late;
      }
      const title = el("title", {}, grp);
      title.textContent = n.name + " — gave " + n.given.total + ", received " + n.received.total + (late ? ", " + late + " late" : "");
      const pick = function () { select({ node: n }, grp); };
      grp.addEventListener("click", pick);
      grp.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } });
    });
  }

  function nameOf(uid) { const n = data.nodes.find(function (x) { return x.user_id === uid; }); return n ? n.name : "?"; }

  function counts(c) {
    const row = h("div", "tm-counts");
    ORDER.forEach(function (g) {
      if (!c[g]) return;
      const chip = h("span", "tm-chip g-" + g);
      chip.appendChild(h("b", null, String(c[g])));
      chip.appendChild(document.createTextNode(" " + LABEL[g]));
      row.appendChild(chip);
    });
    if (!row.childNodes.length) row.appendChild(h("span", "tm-hint", "None"));
    return row;
  }

  function select(what, grp) {
    document.querySelectorAll(".tm-node.sel, .tm-edge.sel").forEach(function (x) { x.classList.remove("sel"); });
    if (grp) grp.classList.add("sel");
    selected = what;
    const side = $("tm-side");
    side.innerHTML = "";
    if (what.node) {
      const n = what.node;
      side.appendChild(h("h3", null, n.name));
      side.appendChild(h("p", "tm-hint", n.detail || ""));
      side.appendChild(h("h4", null, "Gave " + n.given.total));
      side.appendChild(counts(n.given));
      side.appendChild(h("h4", null, "Received " + n.received.total));
      side.appendChild(counts(n.received));
      const links = data.edges.filter(function (e) { return e.from === n.user_id || e.to === n.user_id; })
        .sort(function (a, b) { return b.counts.total - a.counts.total; });
      const ul = h("ul", "tm-links");
      links.forEach(function (e) {
        const li = h("li");
        const b = h("button", "tm-link", (e.from === n.user_id ? "→ " + nameOf(e.to) : "← " + nameOf(e.from)) + "  (" + e.counts.total + ")");
        b.type = "button";
        b.classList.add("g-" + main(e.counts));
        b.addEventListener("click", function () { select({ edge: e }); });
        li.appendChild(b); ul.appendChild(li);
      });
      side.appendChild(ul);
      return;
    }
    const e = what.edge;
    side.appendChild(h("h3", null, nameOf(e.from) + " → " + nameOf(e.to)));
    side.appendChild(h("p", "tm-hint", e.counts.total + " task(s) given"));
    side.appendChild(counts(e.counts));
    const ul = h("ul", "tm-tasks");
    e.tasks.forEach(function (t) {
      const li = h("li", "g-" + t.group);
      li.appendChild(h("strong", null, t.title));
      li.appendChild(h("small", null, t.ref + " · " + (STATUS[t.status] || t.status) + " · due " + day(t.deadline) + (t.group === "late" ? " · LATE" : "")));
      ul.appendChild(li);
    });
    side.appendChild(ul);
    if (e.counts.total > e.tasks.length) side.appendChild(h("p", "tm-hint", "Showing the latest " + e.tasks.length + "."));
  }

  function totals() {
    const t = data.totals, box = $("tm-totals");
    box.innerHTML = "";
    [["People", data.nodes.length], ["Tasks", t.total], ["Late", t.late], ["In progress", t.open + t.waiting], ["Done", t.done]].forEach(function (k) {
      const d = h("div", "tm-total" + (k[0] === "Late" && k[1] ? " bad" : ""));
      d.appendChild(h("span", null, k[0])); d.appendChild(h("b", null, String(k[1])));
      box.appendChild(d);
    });
  }

  async function load() {
    const mine = $("tm-mine") ? $("tm-mine").checked : false;
    $("tm-canvas").innerHTML = '<div class="skeleton-line"></div>';
    const r = await VA.fetchJson("/api/v1/tasks/map?days=" + encodeURIComponent($("tm-days").value) + (mine ? "&mine=true" : ""));
    if (!r.ok) { $("tm-canvas").textContent = (r.body && r.body.error && r.body.error.message) || "Could not load the map."; return; }
    data = r.body;
    loaded = true;
    totals();
    draw();
    const me = data.nodes.find(function (n) { return n.is_me; });
    if (me) select({ node: me }); else $("tm-side").innerHTML = '<p class="tm-hint">Click a person or an arrow to see the tasks.</p>';
  }

  document.addEventListener("vistadmin:ready", function () {
    if (!$("tm")) return;
    const tab = document.querySelector('[data-tab="map"]');
    if (tab) tab.addEventListener("click", function () { if (!loaded) load(); });
    $("tm-days").addEventListener("change", load);
    if ($("tm-mine")) $("tm-mine").addEventListener("change", load);
    if ((location.hash || "") === "#map") load();
  });
})();
