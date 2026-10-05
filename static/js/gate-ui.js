/* Rendering shared by the guard's log and the administration's gate screens. */
window.GateUI = (function () {
  "use strict";
  const VA = window.VistAdmin, SU = window.StudentUI, esc = VA.escapeHtml;
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  const hhmm = function (t) { return t ? String(t).slice(0, 5) : ""; };
  const clock = function (iso) { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); };
  const human = function (m) { const h = Math.floor(m / 60), r = m % 60; return h && r ? h + " h " + r + " min" : h ? h + " h" : r + " min"; };
  const money = function (v) { return "Rs. " + Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }); };
  const toLocalInput = function (iso) { const d = new Date(iso), p = function (n) { return String(n).padStart(2, "0"); }; return p(d.getHours()) + ":" + p(d.getMinutes()); };

  // A row of seven day boxes. Returns { get(), set(list) }.
  function dayBoxes(root, selected) {
    root.innerHTML = DAYS.map(function (d, i) { return '<label class="daybox"><input type="checkbox" value="' + i + '"' + (selected.indexOf(i) !== -1 ? " checked" : "") + " /><span>" + d + "</span></label>"; }).join("");
    return {
      get: function () { return Array.prototype.map.call(root.querySelectorAll("input:checked"), function (c) { return parseInt(c.value, 10); }); },
      set: function (list) { root.querySelectorAll("input").forEach(function (c) { c.checked = list.indexOf(parseInt(c.value, 10)) !== -1; }); },
    };
  }

  // One entry in the gate log. opts: { onUndo(l), onEdit(l), onDelete(l) }
  function logCard(l, opts) {
    opts = opts || {};
    const el = document.createElement("article");
    const late = l.late_minutes > 0;
    el.className = "gate-card log" + (late ? " late" : "");
    const chips = [];
    if (late) chips.push('<span class="gstate late">Late ' + esc(human(l.late_minutes)) + "</span>");
    if (l.early_minutes > 0) chips.push('<span class="gstate early">Left ' + esc(human(l.early_minutes)) + " early</span>");
    if (!l.check_out_at) chips.push('<span class="gstate inside">Inside</span>');
    if (!l.is_working_day) chips.push('<span class="gstate off">Day off</span>');
    el.innerHTML =
      '<div class="gate-who">' + SU.avatar(l.name) + "<span><strong>" + esc(l.name) + "</strong><small>" + esc([l.identifier, l.group].filter(Boolean).join(" \u00b7 ")) + "</small></span>" +
      '<span class="gate-kind">' + (l.kind === "STUDENT" ? "Student" : "Staff") + "</span></div>" +
      (chips.length ? '<div class="gate-chips">' + chips.join("") + "</div>" : "") +
      '<p class="gate-times">In <b>' + esc(l.check_in_at ? clock(l.check_in_at) : "\u2014") + "</b> \u2192 Out <b>" + esc(l.check_out_at ? clock(l.check_out_at) : "\u2014") + "</b>" +
      (l.expected_in ? ' <small>expected ' + esc(hhmm(l.expected_in)) + (l.expected_out ? "\u2013" + esc(hhmm(l.expected_out)) : "") + "</small>" : "") + "</p>" +
      (l.fine ? '<p class="gate-fine">Fine ' + esc(money(l.fine.amount)) + " \u00b7 " + esc({ PENDING_APPROVAL: "waiting for the administration", PENDING: "approved", PAID: "paid", REJECTED: "not charged", WAIVED: "waived" }[l.fine.status] || l.fine.status.toLowerCase()) + "</p>" : "") +
      '<p class="gate-by">' + (l.in_marked_by ? "In marked by " + esc(l.in_marked_by) : "") + (l.out_marked_by ? " \u00b7 out by " + esc(l.out_marked_by) : "") + (l.note ? " \u00b7 \u201c" + esc(l.note) + "\u201d" : "") + "</p>" +
      '<div class="gate-actions"></div>';
    const bar = el.querySelector(".gate-actions");
    const add = function (label, cls, fn) { const b = document.createElement("button"); b.type = "button"; b.className = "gbtn " + cls; b.textContent = label; b.addEventListener("click", function () { fn(l, b); }); bar.appendChild(b); };
    if (opts.onUndo && l.can_undo) add("Undo", "undo", opts.onUndo);
    if (opts.onEdit) add("Correct times", "out", opts.onEdit);
    if (opts.onDelete) add("Remove", "danger", opts.onDelete);
    if (!bar.children.length) bar.remove();
    return el;
  }

  return { hhmm: hhmm, clock: clock, human: human, money: money, toLocalInput: toLocalInput, dayBoxes: dayBoxes, logCard: logCard, DAYS: DAYS };
})();