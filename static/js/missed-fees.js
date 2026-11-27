// After a student's suspension is lifted: class fees assigned while they were suspended, which they never got
// (API /api/v1/students/{id}/missed-fees). Offered once, with one click to assign them. Used by Students and Warnings.
(function () {
  "use strict";
  const VA = window.VistAdmin;
  window.VistMissedFees = async function (studentId, name) {
    const r = await VA.fetchJson("/api/v1/students/" + studentId + "/missed-fees", { quiet: true });
    if (!r.ok || !r.body.length) return;
    const lines = r.body.map(function (f) { return "• " + f.name + " (" + f.term + "): Rs. " + Number(f.total).toLocaleString(); });
    const ok = await VA.confirm((name || "This student") + " was suspended when these fees were assigned to their class, so they did not get them:\n\n" +
      lines.join("\n") + "\n\nAssign them now?");
    if (!ok) return;
    const a = await VA.fetchJson("/api/v1/students/" + studentId + "/missed-fees", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fee_structure_ids: r.body.map(function (f) { return f.fee_structure_id; }) }) });
    if (a.ok) VA.toast(a.body.assigned + " fee" + (a.body.assigned === 1 ? "" : "s") + " assigned.", "success");
    else VA.toast((a.body && a.body.error && a.body.error.message) || "Could not assign the fees.", "error");
  };
})();
