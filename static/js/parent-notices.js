/* Parent -> Notices (GET /api/v1/parents/me/children/{id}/announcements). */
(function () {
  "use strict";
  const P = window.ParentChild, VA = window.VistAdmin, el = P.el;
  const $ = function (id) { return document.getElementById(id); };

  P.onChange = async function (id) {
    const r = await VA.fetchJson("/api/v1/parents/me/children/" + encodeURIComponent(id) + "/announcements");
    if (!r.ok) { P.error("Could not load the notices. Please refresh the page."); return; }
    $("pn-view").hidden = false;
    const ul = $("pn-list"); ul.textContent = "";
    r.body.forEach(function (a) {
      const li = el("li", "panel pp-notice" + (a.pinned ? " pinned" : "") + (a.priority === "URGENT" || a.priority === "IMPORTANT" ? " urgent" : ""));
      const meta = el("div", "pp-notice-meta");
      if (a.pinned) meta.appendChild(el("span", "pp-tag", "Pinned"));
      meta.appendChild(el("span", null, P.day(a.published_at) + " · " + a.issuer_name + (a.issuer_designation ? ", " + a.issuer_designation : "")));
      li.appendChild(meta);
      li.appendChild(el("h2", null, a.title));
      const body = el("p", "pp-notice-body", a.body);
      li.appendChild(body);
      ul.appendChild(li);
    });
    $("pn-none").hidden = r.body.length > 0;
  };
})();
