(function () {
  "use strict";
  // Question papers (the exam office): API /api/v1/qbank/papers
  const VA = window.VistAdmin;
  const API = "/api/v1/qbank/papers";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const send = function (url, method, body) { return VA.fetchJson(url, { method: method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); };
  const LEVEL = { EASY: "Easy", MEDIUM: "Medium", HARD: "Hard" };
  let current = null;

  function row() {
    $("qp-rows").insertAdjacentHTML("beforeend", '<tr><td><select class="text-input qp-k"><option value="MCQ">Multiple choice</option><option value="SHORT">Short</option><option value="LONG">Long</option></select></td>' +
      '<td><input type="number" min="1" max="100" value="5" class="text-input qp-n"></td><td><select class="text-input qp-d"><option value="">Any</option><option value="EASY">Easy</option><option value="MEDIUM">Medium</option><option value="HARD">Hard</option></select></td>' +
      '<td><input class="text-input qp-b" maxlength="4" placeholder="any"></td><td><input class="text-input qp-c" maxlength="12" placeholder="any"></td><td><input type="number" min="0.5" max="100" step="0.5" class="text-input qp-m" placeholder="as in bank"></td>' +
      '<td><button type="button" class="link-btn qp-rm">remove</button></td></tr>');
  }
  $("qp-addrow").addEventListener("click", row);
  $("qp-rows").addEventListener("click", function (e) { if (e.target.closest(".qp-rm")) e.target.closest("tr").remove(); });
  document.addEventListener("qbank:courses", function (e) {
    $("qp-course").innerHTML = e.detail.map(function (c) { return '<option value="' + c.id + '">' + esc(c.code + " " + c.title) + " (" + c.questions + ")</option>"; }).join("");
  });

  async function load() {
    const r = await VA.fetchJson(API);
    $("qp-list").innerHTML = (r.ok ? r.body : []).map(function (p) {
      return '<tr data-id="' + p.id + '"><td><strong>' + esc(p.title) + "</strong></td><td>" + esc(p.course) + "</td><td>" + p.total_marks + "</td><td>" + (p.status === "FINAL" ? "Final" : "Draft") +
        '</td><td><button type="button" class="row-action qp-open">Open</button></td></tr>';
    }).join("") || '<tr><td colspan="5" class="sd-dim">No papers yet.</td></tr>';
  }
  function show(p) {
    current = p;
    const draft = p.status === "DRAFT";
    $("qp-d-title").textContent = p.title + " — " + p.course + (draft ? " (draft)" : " (final)");
    $("qp-d-meta").textContent = "Total " + p.total_marks + " marks. By CLO: " + Object.keys(p.marks_by_clo).map(function (k) { return k + " " + p.marks_by_clo[k]; }).join(", ") +
      ". By Bloom level: " + Object.keys(p.marks_by_bloom).map(function (k) { return k + " " + p.marks_by_bloom[k]; }).join(", ") + ".";
    $("qp-items").innerHTML = p.items.map(function (x, i) {
      return '<tr data-id="' + x.item_id + '"><td>' + (i + 1) + "</td><td>" + esc(x.text) + "<small>" + esc(x.kind + (x.clo ? " · " + x.clo : "") + (x.bloom ? " · " + x.bloom : "")) + "</small></td><td>" + LEVEL[x.difficulty] + "</td><td>" + x.marks +
        "</td><td>" + (draft ? '<button type="button" class="row-action qp-up" aria-label="Up">↑</button> <button type="button" class="row-action qp-down" aria-label="Down">↓</button> <button type="button" class="row-action qp-swap">Swap</button> <button type="button" class="row-action qp-out">Remove</button>' : "") + "</td></tr>";
    }).join("");
    $("qp-pdf").href = API + "/" + p.id + "/pdf"; $("qp-key").href = API + "/" + p.id + "/pdf?key=true";
    $("qp-final").hidden = $("qp-del").hidden = !draft;
    $("qp-detail").hidden = false;
  }
  async function open(id) { const r = await VA.fetchJson(API + "/" + id); if (r.ok) show(r.body); }
  $("qp-list").addEventListener("click", function (e) { if (e.target.closest(".qp-open")) open(e.target.closest("tr").getAttribute("data-id")); });
  $("qp-items").addEventListener("click", async function (e) {
    const tr = e.target.closest("tr"), id = tr && tr.getAttribute("data-id");
    let r = null;
    if (e.target.closest(".qp-swap")) r = await send(API + "/" + current.id + "/items/" + id + "/swap", "POST");
    else if (e.target.closest(".qp-out")) r = await send(API + "/" + current.id + "/items/" + id, "DELETE");
    else if (e.target.closest(".qp-up")) r = await send(API + "/" + current.id + "/items/" + id + "/move", "POST", { step: -1 });
    else if (e.target.closest(".qp-down")) r = await send(API + "/" + current.id + "/items/" + id + "/move", "POST", { step: 1 });
    if (!r) return;
    if (!r.ok) return VA.toast(err(r, "Not changed."), "error");
    show(r.body); load();
  });
  $("qp-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const blueprint = Array.prototype.map.call($("qp-rows").querySelectorAll("tr"), function (tr) {
      const v = function (c) { return tr.querySelector(c).value.trim(); };
      return { kind: v(".qp-k"), count: Number(v(".qp-n")), difficulty: v(".qp-d") || null, bloom: v(".qp-b").toUpperCase() || null, clo: v(".qp-c").toUpperCase() || null, marks: v(".qp-m") ? Number(v(".qp-m")) : null };
    });
    const r = await send(API, "POST", { course_id: $("qp-course").value, title: $("qp-title").value, blueprint: blueprint,
      duration_minutes: $("qp-time").value ? Number($("qp-time").value) : null, instructions: $("qp-instr").value || null });
    if (!r.ok) return VA.toast(err(r, "Could not build it."), "error");
    VA.toast("Paper built."); show(r.body); load();
  });
  $("qp-final").addEventListener("click", async function () {
    if (!(await VA.confirm("Make this paper final? It can no longer be changed."))) return;
    const r = await send(API + "/" + current.id + "/final", "POST");
    if (!r.ok) return VA.toast(err(r, "Not done."), "error");
    show(r.body); load();
  });
  $("qp-del").addEventListener("click", async function () {
    if (!(await VA.confirm("Delete this draft paper?"))) return;
    const r = await send(API + "/" + current.id, "DELETE");
    if (!r.ok) return VA.toast(err(r, "Not deleted."), "error");
    $("qp-detail").hidden = true; load();
  });
  row(); load();
})();
