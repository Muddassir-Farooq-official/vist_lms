(function () {
  "use strict";
  // Question bank (teachers and the exam office): API /api/v1/qbank
  const VA = window.VistAdmin;
  const API = "/api/v1/qbank";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const KIND = { MCQ: "MCQ", SHORT: "Short", LONG: "Long" };
  const LEVEL = { EASY: "Easy", MEDIUM: "Medium", HARD: "Hard" };
  const BLOOMS = ["C1", "C2", "C3", "C4", "C5", "C6", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "A1", "A2", "A3", "A4", "A5"];
  let list = [], editing = null;
  $("qb-bloom").insertAdjacentHTML("beforeend", BLOOMS.map(function (b) { return '<option value="' + b + '">' + b + "</option>"; }).join(""));

  function options(values) {
    values = values || ["", "", "", ""];
    $("qb-opts").innerHTML = values.map(function (v, i) {
      return '<label>Option ' + "ABCDEF"[i] + '<input class="text-input qb-opt" maxlength="500" value="' + esc(v) + '"></label>';
    }).join("") + (values.length < 6 ? '<button type="button" class="link-btn" id="qb-more">+ Option</button>' : "");
    $("qb-answer").innerHTML = values.map(function (_v, i) { return '<option value="' + "ABCDEF"[i] + '">' + "ABCDEF"[i] + "</option>"; }).join("");
  }
  function kindChanged() {
    const mcq = $("qb-kind").value === "MCQ";
    $("qb-opts").hidden = !mcq; $("qb-answer-l").hidden = !mcq; $("qb-key-l").hidden = mcq;
  }
  $("qb-opts").addEventListener("click", function (e) {
    if (e.target.id !== "qb-more") return;
    const vals = Array.prototype.map.call($("qb-opts").querySelectorAll(".qb-opt"), function (i) { return i.value; });
    const ans = $("qb-answer").value;
    options(vals.concat([""])); $("qb-answer").value = ans;
  });
  $("qb-kind").addEventListener("change", kindChanged);

  async function courses() {
    const r = await VA.fetchJson(API + "/courses");
    const rows = r.ok ? r.body : [];
    $("qb-course").innerHTML = rows.map(function (c) { return '<option value="' + c.id + '">' + esc(c.code + " " + c.title) + " (" + c.questions + ")</option>"; }).join("") ||
      '<option value="">No course is assigned to you</option>';
    document.dispatchEvent(new CustomEvent("qbank:courses", { detail: rows }));
    load();
  }
  async function load() {
    if (!$("qb-course").value) { $("qb-list").innerHTML = ""; return; }
    const r = await VA.fetchJson(API + "/courses/" + $("qb-course").value + "/questions" + ($("qb-retired").checked ? "?include_retired=true" : ""));
    if (!r.ok) { $("qb-list").innerHTML = '<tr><td colspan="6">' + esc(err(r, "Could not load.")) + "</td></tr>"; return; }
    list = r.body.questions;
    const s = r.body.summary;
    $("qb-summary").textContent = Object.keys(s).length ? Object.keys(s).map(function (k) { return KIND[k] + ": " + s[k].EASY + " easy, " + s[k].MEDIUM + " medium, " + s[k].HARD + " hard"; }).join(" · ") : "No questions yet.";
    $("qb-list").innerHTML = list.map(function (q) {
      const opts = q.kind === "MCQ" ? "<small>" + (q.options || []).map(function (o, i) { return "(" + "ABCDEF"[i] + ") " + esc(o); }).join(" ") + " — answer " + esc(q.answer) + "</small>" : (q.answer ? "<small>Key: " + esc(q.answer) + "</small>" : "");
      return '<tr data-id="' + q.id + '"' + (q.is_active ? "" : ' class="sd-dim"') + "><td>" + esc(q.text) + opts + "</td><td>" + KIND[q.kind] + "</td><td>" + LEVEL[q.difficulty] +
        "<small>" + esc([q.bloom, q.clo].filter(Boolean).join(" · ")) + "</small></td><td>" + q.marks + "</td><td>" + q.times_used + "</td><td>" +
        '<button type="button" class="row-action qb-edit">Change</button> <button type="button" class="row-action qb-retire">' + (q.is_active ? "Retire" : "Bring back") + "</button></td></tr>";
    }).join("") || '<tr><td colspan="6" class="sd-dim">No questions yet.</td></tr>';
  }
  function reset() {
    editing = null; $("qb-form").reset(); $("qb-form-title").textContent = "Add a question"; $("qb-cancel").hidden = true; options(); kindChanged();
  }
  $("qb-cancel").addEventListener("click", reset);
  $("qb-course").addEventListener("change", load);
  $("qb-retired").addEventListener("change", load);
  $("qb-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const kind = $("qb-kind").value;
    const body = { kind: kind, text: $("qb-text").value, marks: Number($("qb-marks").value), difficulty: $("qb-level").value, bloom: $("qb-bloom").value || null,
      clo: $("qb-clo").value || null };
    if (kind === "MCQ") {
      body.options = Array.prototype.map.call($("qb-opts").querySelectorAll(".qb-opt"), function (i) { return i.value; }).filter(function (v) { return v.trim(); });
      body.answer = $("qb-answer").value;
    } else body.answer = $("qb-key").value || null;
    if (!editing) body.course_id = $("qb-course").value;
    const r = await VA.fetchJson(API + "/questions" + (editing ? "/" + editing : ""), { method: editing ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
    VA.toast("Saved."); reset(); load();
  });
  $("qb-list").addEventListener("click", async function (e) {
    const tr = e.target.closest("tr"), q = tr && list.find(function (x) { return x.id === tr.getAttribute("data-id"); });
    if (!q) return;
    if (e.target.closest(".qb-edit")) {
      editing = q.id;
      $("qb-form-title").textContent = "Change the question"; $("qb-cancel").hidden = false;
      $("qb-kind").value = q.kind; $("qb-text").value = q.text; $("qb-marks").value = q.marks; $("qb-level").value = q.difficulty; $("qb-bloom").value = q.bloom || ""; $("qb-clo").value = q.clo || "";
      if (q.kind === "MCQ") { options(q.options); $("qb-answer").value = q.answer; } else $("qb-key").value = q.answer || "";
      kindChanged(); $("qb-text").focus();
    } else if (e.target.closest(".qb-retire")) {
      const r = await VA.fetchJson(API + "/questions/" + q.id, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ is_active: !q.is_active }) });
      if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
      load();
    }
  });
  options(); kindChanged(); courses();
})();
