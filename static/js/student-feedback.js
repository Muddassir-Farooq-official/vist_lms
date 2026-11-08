/* Student -> Course feedback (GET /api/v1/feedback/me, POST /api/v1/feedback/me/{offering}). */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function day(d) { return d ? new Date(d + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" }) : ""; }
  let data = null;

  function form(item, wrap) {
    const f = el("form", "fb-form");
    f.noValidate = true;
    data.questions.forEach(function (q, qi) {
      const box = el("div", "fb-q");
      box.appendChild(el("p", null, (qi + 1) + ". " + q.text));
      const fs = el("fieldset", "fb-scale");
      fs.setAttribute("aria-label", q.text);
      data.scale.forEach(function (label, i) {
        const id = "q-" + item.offering_id + "-" + q.id + "-" + (i + 1);
        const inp = el("input"); inp.type = "radio"; inp.name = q.id; inp.value = String(i + 1); inp.id = id;
        const lab = el("label"); lab.htmlFor = id;
        lab.appendChild(el("b", null, String(i + 1))); lab.appendChild(el("span", null, label));
        fs.appendChild(inp); fs.appendChild(lab);
      });
      box.appendChild(fs);
      f.appendChild(box);
    });
    const cbox = el("div", "fb-q");
    cbox.appendChild(el("p", null, "Anything else? (optional)"));
    const ta = el("textarea", "text-input fb-comment"); ta.maxLength = 1000; ta.placeholder = "What went well, and what could be better?";
    ta.setAttribute("aria-label", "Comment");
    cbox.appendChild(ta);
    f.appendChild(cbox);
    const bar = el("div", "fb-submit");
    const btn = el("button", "fb-btn", "Send feedback"); btn.type = "submit";
    const left = el("span", "fb-left", "");
    bar.appendChild(btn); bar.appendChild(left);
    f.appendChild(bar);
    function count() {
      const n = data.questions.filter(function (q) { return !f.querySelector('input[name="' + q.id + '"]:checked'); }).length;
      left.textContent = n ? n + (n === 1 ? " question" : " questions") + " left" : "All answered";
      return n;
    }
    f.addEventListener("change", count); count();
    f.addEventListener("submit", async function (e) {
      e.preventDefault();
      if (count()) { const first = data.questions.find(function (q) { return !f.querySelector('input[name="' + q.id + '"]:checked'); }); f.querySelector('input[name="' + first.id + '"]').focus(); return; }
      const scores = {};
      data.questions.forEach(function (q) { scores[q.id] = parseInt(f.querySelector('input[name="' + q.id + '"]:checked').value, 10); });
      btn.disabled = true; btn.textContent = "Sending…";
      const r = await VA.fetchJson("/api/v1/feedback/me/" + item.offering_id, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scores: scores, comment: ta.value }) });
      if (!r.ok) {
        btn.disabled = false; btn.textContent = "Send feedback";
        $("fb-error").textContent = (r.body && r.body.error && r.body.error.message) || "Could not send. Please try again.";
        $("fb-error").hidden = false;
        return;
      }
      $("fb-error").hidden = true;
      load();
    });
    wrap.appendChild(f);
    const firstInput = f.querySelector("input"); if (firstInput) firstInput.focus({ preventScroll: true });
  }

  function draw() {
    const open = $("fb-open");
    open.textContent = "";
    $("fb-open-none").hidden = data.open.length > 0;
    data.open.forEach(function (item, i) {
      const wrap = el("div", "fb-course");
      const head = el("button"); head.type = "button"; head.setAttribute("aria-expanded", "false");
      const t = el("span");
      t.appendChild(el("strong", null, item.course_code + " · " + item.course_title));
      t.appendChild(el("small", null, (item.teacher ? item.teacher + " · " : "") + "open until " + day(item.closes_on)));
      head.appendChild(t);
      head.appendChild(el("span", "fb-go", "Answer ›"));
      head.addEventListener("click", function () {
        const isOpen = wrap.classList.toggle("open");
        head.setAttribute("aria-expanded", String(isOpen));
        const f = wrap.querySelector("form");
        if (isOpen && !f) form(item, wrap); else if (f) f.hidden = !isOpen;
      });
      wrap.appendChild(head);
      open.appendChild(wrap);
      if (i === 0 && data.open.length === 1) head.click();
    });
    const later = $("fb-later"), done = $("fb-done");
    later.textContent = ""; done.textContent = "";
    data.upcoming.forEach(function (i) { const li = el("li"); li.appendChild(el("span", null, i.course_code + " · " + i.course_title)); li.appendChild(el("small", null, "opens " + day(i.opens_on))); later.appendChild(li); });
    data.done.forEach(function (i) { const li = el("li"); li.appendChild(el("span", null, i.course_code + " · " + i.course_title)); li.appendChild(el("small", null, "✓ sent")); done.appendChild(li); });
    $("fb-later-panel").hidden = !data.upcoming.length;
    $("fb-done-panel").hidden = !data.done.length;
  }

  async function load() {
    const r = await VA.fetchJson("/api/v1/feedback/me");
    if (!r.ok) { $("fb-open").textContent = ""; $("fb-error").textContent = (r.body && r.body.error && r.body.error.message) || "Could not load."; $("fb-error").hidden = false; return; }
    data = r.body; draw();
  }
  document.addEventListener("vistadmin:ready", load);
})();
