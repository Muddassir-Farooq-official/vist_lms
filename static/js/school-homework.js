/* Teacher -> Homework diary (School mode): GET /api/v1/school/homework/mine, POST /homework, DELETE /homework/{id}. */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function day(iso) { return iso ? new Date(String(iso).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) : ""; }

  async function load() {
    const r = await VA.fetchJson("/api/v1/school/homework/mine");
    if (!r.ok) { $("hw-none").textContent = "Could not load your classes."; $("hw-none").hidden = false; return; }
    const sel = $("hw-class");
    if (!sel.options.length) {
      r.body.classes.forEach(function (c) { const o = el("option", null, c.class + " — " + c.section + " · " + c.subject); o.value = c.offering_id; sel.appendChild(o); });
      $("hw-form").hidden = !r.body.classes.length;
      $("hw-none").hidden = !!r.body.classes.length;
    }
    const ul = $("hw-list"); ul.textContent = "";
    if (!r.body.homework.length) { ul.appendChild(el("li", "empty-state", "No homework set in the last two weeks.")); return; }
    r.body.homework.forEach(function (h) {
      const li = el("li", "hw-item");
      const top = el("div", "hw-top");
      top.appendChild(el("span", "hw-subject", h.class + " — " + h.section + " · " + h.subject));
      top.appendChild(el("span", "hw-meta", "set " + day(h.set_on) + (h.due_on ? " · due " + day(h.due_on) : "")));
      li.appendChild(top);
      li.appendChild(el("p", null, h.title + (h.details ? "\n" + h.details : "")));
      const act = el("div", "hw-actions");
      if (h.file_url) { const a = el("a", "sd-view", "📎 " + (h.file_name || "File")); a.href = h.file_url; a.target = "_blank"; a.rel = "noopener"; act.appendChild(a); }
      if (h.can_remove) {
        const x = el("button", "sd-view tt-del", "Remove"); x.type = "button";
        x.addEventListener("click", async function () {
          if (!(await VA.confirm("Remove this homework? Students and parents will no longer see it.", "Remove"))) return;
          const d = await VA.fetchJson("/api/v1/school/homework/" + h.id, { method: "DELETE" });
          if (!d.ok) { VA.toast((d.body && d.body.error && d.body.error.message) || "Could not remove.", "error"); return; }
          load();
        });
        act.appendChild(x);
      }
      li.appendChild(act);
      ul.appendChild(li);
    });
  }

  async function send(e) {
    e.preventDefault();
    const err = $("hw-error"); err.hidden = true;
    const title = $("hw-title").value.trim();
    if (title.length < 3) { err.textContent = "Write the homework."; err.hidden = false; $("hw-title").focus(); return; }
    const fd = new FormData();
    fd.append("offering_id", $("hw-class").value);
    fd.append("title", title);
    if ($("hw-details").value.trim()) fd.append("details", $("hw-details").value.trim());
    if ($("hw-due").value) fd.append("due_on", $("hw-due").value);
    if ($("hw-file").files[0]) fd.append("file", $("hw-file").files[0]);
    $("hw-send").disabled = true;
    const r = await VA.fetchJson("/api/v1/school/homework", { method: "POST", body: fd });
    $("hw-send").disabled = false;
    if (!r.ok) { err.textContent = (r.body && r.body.error && r.body.error.message) || "Could not save. Please try again."; err.hidden = false; return; }
    VA.toast("Homework set. Students and parents can see it now.", "success");
    const keep = $("hw-class").value;
    $("hw-form").reset(); $("hw-class").value = keep;
    load();
  }

  document.addEventListener("vistadmin:ready", function () { $("hw-form").addEventListener("submit", send); load(); });
})();
