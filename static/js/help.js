(function () {
  "use strict";
  // Help & manual: filter the chapters' sections as you type.
  const q = document.getElementById("help-q");
  if (!q) return;
  const sections = [];
  document.querySelectorAll(".help-chapter").forEach(function (ch) {
    let current = null;
    Array.from(ch.children).forEach(function (el) {
      if (/^H[23]$/.test(el.tagName) || !current) { current = { chapter: ch, nodes: [] }; sections.push(current); }
      current.nodes.push(el);
    });
  });
  q.addEventListener("input", function () {
    const term = q.value.trim().toLowerCase();
    let shown = 0;
    sections.forEach(function (s) {
      const hit = !term || s.nodes.some(function (n) { return n.textContent.toLowerCase().indexOf(term) >= 0; });
      s.nodes.forEach(function (n) { n.hidden = !hit; });
      if (hit) shown++;
    });
    document.querySelectorAll(".help-chapter").forEach(function (ch) {
      ch.hidden = !Array.from(ch.children).some(function (n) { return !n.hidden; });
    });
    document.getElementById("help-none").hidden = shown > 0;
  });
})();
