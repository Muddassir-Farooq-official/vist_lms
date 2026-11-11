/* Homework diary + a month of daily attendance, for the student's and the parent's pages (School mode). */
(function () {
  "use strict";
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function day(iso) { return iso ? new Date(String(iso).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) : ""; }
  const WORD = { PRESENT: "Present", LATE: "Late", ABSENT: "Absent", LEAVE: "Leave" };

  function homework(box, d) {
    box.textContent = "";
    if (!d.section) { box.appendChild(el("p", "empty-state", "Not placed in a class section yet.")); return; }
    if (!d.homework.length) { box.appendChild(el("p", "empty-state", "No homework in the last two weeks.")); return; }
    let last = null, ul = null;
    d.homework.forEach(function (h) {
      const k = String(h.set_on).slice(0, 10);
      if (k !== last) { box.appendChild(el("h3", "hw-day", day(k))); ul = el("ul", "hw-list"); box.appendChild(ul); last = k; }
      const li = el("li", "hw-item");
      const top = el("div", "hw-top");
      top.appendChild(el("span", "hw-subject", h.subject));
      if (h.due_on) top.appendChild(el("span", "hw-meta", "due " + day(h.due_on)));
      li.appendChild(top);
      li.appendChild(el("p", null, h.title + (h.details ? "\n" + h.details : "")));
      if (h.file_url) {
        const a = el("a", "sd-view", "📎 " + (h.file_name || "Worksheet")); a.href = h.file_url; a.target = "_blank"; a.rel = "noopener";
        const act = el("div", "hw-actions"); act.appendChild(a); li.appendChild(act);
      }
      ul.appendChild(li);
    });
  }

  function month(box, d, onMonth) {
    box.textContent = "";
    const bar = el("div", "dy-bar");
    const input = document.createElement("input"); input.type = "month"; input.className = "text-input"; input.value = d.month; input.setAttribute("aria-label", "Month");
    input.addEventListener("change", function () { if (input.value) onMonth(input.value); });
    bar.appendChild(input);
    const c = d.counts;
    bar.appendChild(el("span", "hw-meta", (d.percentage == null ? "No roll call yet" : d.percentage + "% present") +
      " · " + c.PRESENT + " present, " + c.LATE + " late, " + c.ABSENT + " absent, " + c.LEAVE + " leave"));
    box.appendChild(bar);
    const grid = el("div", "dy-grid");
    ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].forEach(function (w) { grid.appendChild(el("div", "dy-cell dy-head", w)); });
    const first = new Date(d.month + "-01T00:00:00");
    const days = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
    const by = {};
    d.days.forEach(function (x) { by[String(x.day).slice(8, 10) * 1] = x; });
    for (let i = 0; i < (first.getDay() + 6) % 7; i++) grid.appendChild(el("div", "dy-cell dy-blank"));
    for (let n = 1; n <= days; n++) {
      const x = by[n];
      const cell = el("div", "dy-cell" + (x ? " dy-" + x.status : ""));
      cell.appendChild(el("b", null, String(n)));
      if (x) { cell.appendChild(document.createTextNode(WORD[x.status])); if (x.note) cell.title = x.note; }
      grid.appendChild(cell);
    }
    box.appendChild(grid);
  }

  window.VistDiary = { homework: homework, month: month };
})();
