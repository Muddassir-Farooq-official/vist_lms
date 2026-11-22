(function () {
  "use strict";
  // My alumni page: API /api/v1/alumni/me, /directory, /me/surveys
  const VA = window.VistAdmin;
  const API = "/api/v1/alumni";
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const err = function (r, fb) { return (r.body && r.body.error && r.body.error.message) || fb; };
  const send = function (url, method, body) { return VA.fetchJson(url, { method: method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); };
  const form = $("al-form");
  let timer = null;
  async function me() {
    const r = await VA.fetchJson(API + "/me");
    if (!r.ok) return;
    const d = r.body;
    $("al-sub").textContent = d.name + " · " + (d.program_name || d.program || "") + (d.graduated ? " · graduated " + d.graduated : "");
    Object.keys(d.profile).forEach(function (k) {
      const el = form.elements[k];
      if (!el) return;
      if (el.type === "checkbox") el.checked = !!d.profile[k]; else el.value = d.profile[k] || "";
    });
    $("al-docs").innerHTML = d.documents.map(function (x) {
      return "<tr><td>" + (x.kind === "DEGREE" ? "Degree" : "Transcript") + "</td><td>" + esc(x.status_name) + "</td><td>" + esc(x.serial_no || "—") + "</td></tr>";
    }).join("") || '<tr><td colspan="3" class="sd-dim">Nothing yet. The office starts your degree after graduation.</td></tr>';
  }
  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    const body = {};
    Array.prototype.forEach.call(form.elements, function (el) { if (el.name) body[el.name] = el.type === "checkbox" ? el.checked : (el.value.trim() || null); });
    const r = await send(API + "/me", "PUT", body);
    if (!r.ok) return VA.toast(err(r, "Not saved."), "error");
    VA.toast("Saved."); me(); directory();
  });
  $("al-ask").addEventListener("click", async function () {
    const copies = await VA.ask({ title: "Printed transcript", label: "How many copies (1-10)", input: "number", min: 1 });
    if (!copies) return;
    const r = await send(API + "/me/transcript", "POST", { copies: Number(copies) });
    if (!r.ok) return VA.toast(err(r, "Not sent."), "error");
    VA.toast("Asked. You are told when it is ready."); me();
  });
  async function surveys() {
    const r = await VA.fetchJson(API + "/me/surveys");
    const rows = r.ok ? r.body : [];
    $("al-surveys").innerHTML = rows.map(function (s) {
      if (s.answered) return "<p><b>" + esc(s.title) + "</b> — answered. Thank you!</p>";
      return '<form class="al-survey" data-id="' + s.id + '"><h3>' + esc(s.title) + "</h3>" + (s.intro ? "<p>" + esc(s.intro) + "</p>" : "") + s.questions.map(function (q) {
        let input;
        if (q.kind === "RATING") input = [1, 2, 3, 4, 5].map(function (n) { return '<label><input type="radio" name="' + q.id + '" value="' + n + '"> ' + n + "</label>"; }).join(" ");
        else if (q.kind === "CHOICE") input = '<select name="' + q.id + '" class="text-input"><option value="">—</option>' + q.choices.map(function (c) { return "<option>" + esc(c) + "</option>"; }).join("") + "</select>";
        else input = '<textarea name="' + q.id + '" class="text-input" rows="2" maxlength="2000"></textarea>';
        return '<fieldset class="cl-days"><legend>' + esc(q.text) + "</legend>" + input + "</fieldset>";
      }).join("") + '<div class="decision-bar"><button type="submit" class="role-btn">Send answers</button></div></form>';
    }).join("") || '<p class="sd-dim">No survey is open.</p>';
  }
  $("al-surveys").addEventListener("submit", async function (e) {
    const f = e.target.closest(".al-survey");
    if (!f) return;
    e.preventDefault();
    const answers = {};
    Array.prototype.forEach.call(f.elements, function (el) {
      if (!el.name) return;
      if (el.type === "radio") { if (el.checked) answers[el.name] = Number(el.value); } else if (el.value.trim()) answers[el.name] = el.value.trim();
    });
    const r = await send(API + "/me/surveys/" + f.getAttribute("data-id"), "POST", { answers: answers });
    if (!r.ok) return VA.toast(err(r, "Not sent."), "error");
    VA.toast("Thank you!"); surveys();
  });
  async function directory() {
    const q = $("al-q").value.trim();
    const r = await VA.fetchJson(API + "/directory" + (q ? "?q=" + encodeURIComponent(q) : ""));
    $("al-dir").innerHTML = (r.ok ? r.body : []).map(function (a) {
      const contact = [a.email, a.phone].filter(Boolean).map(esc).join(" · ") + (a.linkedin ? ' · <a href="' + esc(/^https?:\/\//i.test(a.linkedin) ? a.linkedin : "https://" + a.linkedin) + '" rel="noopener noreferrer" target="_blank">LinkedIn</a>' : "");
      return "<tr><td>" + esc(a.name) + "</td><td>" + esc(a.program || "") + (a.year ? " · " + a.year : "") + "</td><td>" + esc([a.job_title, a.employer].filter(Boolean).join(", ") || a.further_study || "—") +
        "</td><td>" + esc([a.city, a.country].filter(Boolean).join(", ") || "—") + "</td><td>" + (contact || "—") + "</td></tr>";
    }).join("") || '<tr><td colspan="5" class="sd-dim">Nobody is listed yet.</td></tr>';
  }
  $("al-q").addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(directory, 300); });
  me(); surveys(); directory();
})();
