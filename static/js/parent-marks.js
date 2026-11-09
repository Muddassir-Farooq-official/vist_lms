/* Parent -> Marks (GET /api/v1/parents/me/children/{id}/marks). */
(function () {
  "use strict";
  const P = window.ParentChild, VA = window.VistAdmin, el = P.el;
  const $ = function (id) { return document.getElementById(id); };
  function num(v) { return Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }); }

  P.onChange = async function (id) {
    const r = await VA.fetchJson("/api/v1/parents/me/children/" + encodeURIComponent(id) + "/marks");
    if (!r.ok) { P.error("Could not load the marks. Please refresh the page."); return; }
    $("pm-view").hidden = false;
    const list = $("pm-list"); list.textContent = "";
    r.body.forEach(function (c) {
      const card = el("section", "panel pp-panel pp-subject");
      const head = el("div", "pp-subject-head");
      const t = el("div"); t.appendChild(el("strong", null, c.course_code)); t.appendChild(el("span", null, " " + c.course_title));
      head.appendChild(t);
      if (c.percentage != null) {
        const tone = c.percentage < 50 ? "bad" : c.percentage < 65 ? "warn" : "good";
        head.appendChild(el("b", "pp-pct " + tone, Math.round(c.percentage) + "%"));
      }
      card.appendChild(head);
      if (c.items.length) {
        const bar = el("div", "pp-bar"); const fill = el("span"); fill.style.width = Math.min(100, c.percentage || 0) + "%"; bar.appendChild(fill); card.appendChild(bar);
        const ul = el("ul", "pp-marks");
        c.items.forEach(function (i) { const li = el("li"); li.appendChild(el("span", null, i.name)); li.appendChild(el("b", null, num(i.obtained) + " / " + num(i.max))); ul.appendChild(li); });
        card.appendChild(ul);
        card.appendChild(el("p", "pp-note", "So far: " + num(c.obtained) + " of " + num(c.max) + " marks"));
      } else {
        card.appendChild(el("p", "pp-note", "No marks entered yet."));
      }
      list.appendChild(card);
    });
    $("pm-none").hidden = r.body.length > 0;
  };
})();
