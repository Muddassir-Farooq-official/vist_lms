(function () {
  "use strict";
  // My substitute classes (teacher): API /api/v1/substitutes/mine; attendance saved with the usual marking endpoint.
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (v) { return VA.escapeHtml(v == null ? "" : String(v)); };
  const day = function (v) { return new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }); };
  let current = null;
  async function load() {
    const r = await VA.fetchJson("/api/v1/substitutes/mine");
    $("ms-body").innerHTML = (r.ok ? r.body : []).map(function (x) {
      return '<tr><td><strong>' + esc(day(x.date)) + "</strong><small>" + x.start + "–" + x.end + "</small></td><td>" + esc(x.course) + "<small>Section " + esc(x.section) + "</small></td><td>" +
        esc(x.room) + "</td><td>" + (x.marked ? "marked" : "not yet") + '</td><td><button type="button" class="row-action ms-open" data-id="' + x.class_session_id + '" data-name="' + esc(x.course) + '">Mark…</button></td></tr>';
    }).join("") || '<tr><td colspan="5" class="sd-dim">No substitute class for you.</td></tr>';
  }
  $("ms-body").addEventListener("click", async function (e) {
    const b = e.target.closest(".ms-open");
    if (!b) return;
    current = b.getAttribute("data-id");
    const r = await VA.fetchJson("/api/v1/substitutes/mine/" + current + "/students");
    if (!r.ok) { VA.toast("Could not load the students.", "error"); return; }
    $("ms-title").textContent = b.getAttribute("data-name");
    $("ms-students").innerHTML = r.body.students.map(function (s) {
      return '<label><input type="checkbox" value="' + s.id + '"' + (s.status === "ABSENT" ? "" : " checked") + "> " + esc(s.name) + " <small>" + esc(s.roll_number || "") + "</small></label>";
    }).join("");
    $("ms-mark").hidden = false;
  });
  $("ms-save").addEventListener("click", async function () {
    const present = Array.prototype.map.call($("ms-students").querySelectorAll("input:checked"), function (c) { return c.value; });
    const r = await VA.fetchJson("/api/v1/attendance/sessions/" + current + "/mark", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ present_student_ids: present }) });
    if (!r.ok) { VA.toast((r.body && r.body.error && r.body.error.message) || "Not saved.", "error"); return; }
    VA.toast("Attendance saved.");
    $("ms-mark").hidden = true;
    load();
  });
  load();
})();
