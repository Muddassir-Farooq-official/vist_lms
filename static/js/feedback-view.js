/* Draws one course's anonymous feedback summary (teacher page and the oversight dialog). */
(function () {
  "use strict";
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  const LABELS = ["Strongly disagree", "Disagree", "Not sure", "Agree", "Strongly agree"];

  function summary(s) {
    const box = el("div");
    if (!s || !s.enough) return box;
    const o = el("div", "fs-overall");
    o.appendChild(el("b", null, s.overall != null ? s.overall.toFixed(1) : "—"));
    o.appendChild(el("span", null, "out of 5 overall · " + s.responses + " students answered"));
    box.appendChild(o);
    s.questions.forEach(function (q) {
      const row = el("div", "fs-q");
      row.appendChild(el("span", null, q.text));
      const n = q.counts.reduce(function (a, b) { return a + b; }, 0) || 1;
      const bar = el("div", "fs-bar");
      bar.title = q.counts.map(function (c, i) { return LABELS[i] + ": " + c; }).join(" · ");
      q.counts.forEach(function (c, i) { const seg = el("span", "s" + (i + 1)); seg.style.width = (100 * c / n) + "%"; bar.appendChild(seg); });
      row.appendChild(bar);
      row.appendChild(el("span", "fs-avg" + (q.average != null && q.average < 3 ? " low" : ""), q.average != null ? q.average.toFixed(1) : "—"));
      box.appendChild(row);
    });
    const lg = el("div", "fs-legend");
    LABELS.forEach(function (l, i) { const s2 = el("span"); s2.appendChild(el("i", "s" + (i + 1))); s2.appendChild(document.createTextNode(l)); lg.appendChild(s2); });
    box.appendChild(lg);
    if (s.comments && s.comments.length) {
      box.appendChild(el("h3", "dash-sub-h", "Comments (" + s.comments.length + ")"));
      const ul = el("ul", "fs-comments");
      s.comments.forEach(function (c) { ul.appendChild(el("li", null, c)); });
      box.appendChild(ul);
    }
    return box;
  }

  function card(r, hiddenReason) {
    const c = el("section", "panel fs-card");
    const h = el("div", "fs-head");
    h.appendChild(el("h2", null, r.course_code + " · " + r.course_title + (r.section_label && r.section_label !== "A" ? " (" + r.section_label + ")" : "")));
    h.appendChild(el("small", null, r.term_label + (r.teacher ? " · " + r.teacher : "") + " · " + r.responses + " of " + r.enrolled + " answered"));
    c.appendChild(h);
    if (hiddenReason) c.appendChild(el("p", "fs-hidden", hiddenReason));
    else c.appendChild(summary(r.summary));
    return c;
  }

  function day(d) { return d ? new Date(d + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" }) : ""; }
  window.FeedbackView = { summary: summary, card: card, el: el, day: day };
})();
