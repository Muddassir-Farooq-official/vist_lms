/* A list of report cards with a link to each PDF (students and parents, School mode). */
(function () {
  "use strict";
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  window.VistCards = function (box, cards) {
    box.textContent = "";
    if (!cards.length) { box.appendChild(el("p", "empty-state", "No report card has been published yet.")); return; }
    const ul = el("ul", "rcd-list");
    cards.forEach(function (c) {
      const li = el("li", "rcd-item");
      const n = el("div", "rcd-name");
      n.appendChild(el("strong", null, c.name + " examination"));
      n.appendChild(el("small", null, c.class + " · session " + c.session));
      li.appendChild(n);
      li.appendChild(el("span", "rcd-pct", c.percentage == null ? "—" : c.percentage.toFixed(1) + "%"));
      li.appendChild(el("span", "rcd-grade", c.grade));
      li.appendChild(el("span", "status-pill " + (c.passed ? "active" : c.passed === false ? "dropped" : ""), c.passed ? "Pass" : c.passed === false ? "Fail" : "—"));
      const a = el("a", "role-btn small-btn", "Open report card"); a.href = c.pdf; a.target = "_blank"; a.rel = "noopener";
      li.appendChild(a);
      ul.appendChild(li);
    });
    box.appendChild(ul);
  };
})();
