/* Admin / principal / HOD -> Course feedback (GET /api/v1/feedback/overview, /overview/{id}, /questions). */
(function () {
  "use strict";
  const VA = window.VistAdmin, FV = window.FeedbackView, el = FV.el;
  const $ = function (id) { return document.getElementById(id); };
  let data = null;

  function kpis() {
    const k = $("fb-kpis"); k.textContent = "";
    const open = data.courses.filter(function (c) { return c.is_open; }).length;
    [["Courses", data.courses.length], ["Students asked", data.enrolled], ["Answers", data.responses], ["Response rate", data.rate == null ? "—" : data.rate + "%"], ["Open now", open]]
      .forEach(function (p) { const d = el("div", "fb-kpi"); d.appendChild(el("small", null, p[0])); d.appendChild(el("b", null, String(p[1]))); k.appendChild(d); });
  }

  function rows() {
    const q = ($("fb-q").value || "").toLowerCase();
    const tb = $("fb-rows"); tb.textContent = "";
    const list = data.courses.filter(function (c) { return !q || ((c.teacher || "") + " " + c.course_code + " " + c.course_title).toLowerCase().indexOf(q) >= 0; });
    if (!list.length) { const tr = el("tr"); const td = el("td", null, "No courses with an end date in this term."); td.colSpan = 5; tr.appendChild(td); tb.appendChild(tr); }
    list.forEach(function (c) {
      const tr = el("tr");
      tr.appendChild(el("td", null, c.teacher || "No teacher"));
      const tdc = el("td"); tdc.appendChild(document.createTextNode(c.course_code + (c.section_label && c.section_label !== "A" ? " · " + c.section_label : "")));
      tdc.appendChild(el("small", null, c.course_title)); tr.appendChild(tdc);
      const tdr = el("td"); const rate = el("div", "fb-rate" + (c.rate != null && c.rate < 50 ? " low" : ""));
      const bar = el("div", "fs-bar"); const seg = el("span"); seg.style.width = (c.rate || 0) + "%"; bar.appendChild(seg);
      rate.appendChild(bar); rate.appendChild(el("span", null, c.responses + "/" + c.enrolled)); tdr.appendChild(rate);
      tdr.appendChild(el("small", null, c.is_open ? "open until " + FV.day(c.closes_on) : (c.term_over ? "closed" : "opens " + FV.day(c.opens_on)))); tr.appendChild(tdr);
      tr.appendChild(el("td", null, c.term_over ? (c.enough ? c.overall.toFixed(1) + " / 5" : "too few answers") : "after term ends"));
      const tda = el("td");
      if (c.term_over && c.enough) { const b = el("button", "fb-link", "Details"); b.type = "button"; b.addEventListener("click", function () { detail(c.offering_id); }); tda.appendChild(b); }
      tr.appendChild(tda);
      tb.appendChild(tr);
    });
    $("fb-note").textContent = "Scores show once the term has ended and at least " + data.min_responses + " students answered a course, so no one can be identified.";
  }

  async function detail(id) {
    const r = await VA.fetchJson("/api/v1/feedback/overview/" + id);
    if (!r.ok) return;
    const body = $("fb-dialog-body"); body.textContent = "";
    body.appendChild(FV.card(r.body, r.body.hidden_reason));
    $("fb-dialog").showModal();
  }

  async function load(term) {
    const r = await VA.fetchJson("/api/v1/feedback/overview" + (term ? "?term_label=" + encodeURIComponent(term) : ""));
    if (!r.ok) { $("fb-rows").innerHTML = ""; return; }
    data = r.body;
    const sel = $("fb-term"); sel.textContent = "";
    data.terms.forEach(function (t) { const o = el("option", null, t); o.value = t; o.selected = t === data.term; sel.appendChild(o); });
    if (!data.terms.length) { const o = el("option", null, "No terms yet"); sel.appendChild(o); }
    kpis(); rows();
  }

  async function questions() {
    const box = $("fq-text"); if (!box) return;
    const r = await VA.fetchJson("/api/v1/feedback/questions");
    if (r.ok) box.value = r.body.questions.join("\n");
    $("fq-save").addEventListener("click", async function () {
      const msg = $("fq-msg");
      const list = box.value.split("\n").map(function (s) { return s.trim(); }).filter(Boolean);
      const s = await VA.fetchJson("/api/v1/feedback/questions", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ questions: list }) });
      msg.textContent = s.ok ? "Saved. " + s.body.questions.length + " questions." : ((s.body && s.body.error && s.body.error.message) || "Could not save.");
      if (s.ok) box.value = s.body.questions.join("\n");
    });
  }

  document.addEventListener("vistadmin:ready", function () {
    load();
    questions();
    $("fb-term").addEventListener("change", function () { load(this.value); });
    $("fb-q").addEventListener("input", rows);
    $("fb-dialog-close").addEventListener("click", function () { $("fb-dialog").close(); });
  });
})();
