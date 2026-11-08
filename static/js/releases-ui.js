/* Release notes: the "What's new" list on the status page (/health), and the card shown once in the staff portals
   after each update. Text is always inserted as text, never as HTML. */
(function () {
  "use strict";
  var KIND_LABEL = { new: "New", improved: "Improved", fixed: "Fixed", security: "Safety" };
  var SEEN_KEY = "vist_seen_version";

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function renderRelease(r, isCurrent, onlyFor) {
    var li = el("li", "rl" + (isCurrent ? " is-current" : ""));
    var meta = el("div", "rl-meta");
    meta.appendChild(el("span", "rl-ver", "v" + r.version));
    if (r.date_text) meta.appendChild(el("span", null, r.date_text));
    if (isCurrent) meta.appendChild(el("span", "rl-now", "Running now"));
    li.appendChild(meta);
    li.appendChild(el("h3", null, r.title || ""));
    if (r.summary) li.appendChild(el("p", "rl-sum", r.summary));
    var ul = el("ul", "rl-items");
    (r.changes || []).forEach(function (c) {
      if (onlyFor && c.for !== onlyFor && c.for !== "Everyone") return;
      var item = el("li");
      item.appendChild(el("span", "rl-kind k-" + c.kind, KIND_LABEL[c.kind] || c.kind));
      var t = el("span", null, c.text);
      if (c.for) t.appendChild(el("span", "rl-for", "For: " + c.for));
      item.appendChild(t);
      ul.appendChild(item);
    });
    if (ul.children.length) li.appendChild(ul);
    else if (onlyFor) return null;
    return li;
  }

  function load(limit) {
    return fetch("/api/v1/system/releases" + (limit ? "?limit=" + limit : ""), { credentials: "include", headers: { Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw r.status; return r.json(); });
  }

  // ---------------------------------------------------------------- status page
  function statusPage(list) {
    var loading = document.getElementById("wn-loading");
    var signin = document.getElementById("wn-signin");
    var more = document.getElementById("wn-more");
    var filter = document.getElementById("wn-filter");
    var shownAll = false, onlyFor = "", data = null;
    var FIRST = 3;

    function draw() {
      list.textContent = "";
      var n = 0;
      data.releases.forEach(function (r, i) {
        if (!shownAll && n >= FIRST) return;
        var li = renderRelease(r, i === 0, onlyFor);
        if (li) { list.appendChild(li); n += 1; }
      });
      more.hidden = shownAll || data.releases.length <= FIRST;
      if (!list.children.length) list.appendChild(el("li", "hs-muted", "No changes for this group yet."));
    }

    load().then(function (d) {
      data = d;
      loading.hidden = true;
      var groups = [];
      d.releases.forEach(function (r) { (r.changes || []).forEach(function (c) { if (c.for && c.for !== "Everyone" && groups.indexOf(c.for) < 0) groups.push(c.for); }); });
      groups.sort().forEach(function (g) { var b = el("button", null, g); b.type = "button"; b.dataset.for = g; filter.appendChild(b); });
      filter.hidden = groups.length < 2;
      filter.addEventListener("click", function (e) {
        var b = e.target.closest("button"); if (!b) return;
        onlyFor = b.dataset.for || "";
        Array.prototype.forEach.call(filter.children, function (x) { x.classList.toggle("active", x === b); });
        draw();
      });
      more.addEventListener("click", function () { shownAll = true; draw(); });
      draw();
      try { localStorage.setItem(SEEN_KEY, d.version); } catch (e) { /* private mode */ }
    }).catch(function () {
      loading.hidden = true;
      signin.hidden = false;
    });
  }

  // ---------------------------------------------------------------- card in the portals, once per version
  function popupOnce(current) {
    var seen = null;
    try { seen = localStorage.getItem(SEEN_KEY); } catch (e) { return; }
    if (seen === current) return;
    load(1).then(function (d) {
      if (!d.releases.length || d.version !== current) return;
      var dlg = el("dialog", "wn-modal");
      dlg.setAttribute("aria-labelledby", "wn-modal-title");
      var body = el("div", "wn-body");
      body.appendChild(el("p", "wn-eyebrow", "What's new"));
      var ol = el("ol", "hs-releases");
      var li = renderRelease(d.releases[0], true, "");
      li.querySelector("h3").id = "wn-modal-title";
      ol.appendChild(li);
      body.appendChild(ol);
      var actions = el("div", "wn-actions");
      var all = el("a", null, "All updates"); all.href = "/health#whats-new";
      var ok = el("button", null, "Got it"); ok.type = "button";
      actions.appendChild(all); actions.appendChild(ok);
      dlg.appendChild(body); dlg.appendChild(actions);
      document.body.appendChild(dlg);
      function done() { try { localStorage.setItem(SEEN_KEY, current); } catch (e) { /* ignore */ } }
      ok.addEventListener("click", function () { done(); dlg.close(); });
      all.addEventListener("click", done);
      dlg.addEventListener("cancel", done);
      if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
    }).catch(function () { /* not signed in yet: try again next page */ });
  }

  function start() {
    var list = document.getElementById("wn-list");
    if (list) { statusPage(list); return; }
    var link = document.getElementById("app-version");
    if (!link) return;
    // wait until the portal has checked the session and shown itself
    var tries = 0;
    var t = setInterval(function () {
      var shell = document.getElementById("app-shell");
      if ((shell && !shell.hidden) || ++tries > 40) { clearInterval(t); if (shell && !shell.hidden) popupOnce(link.dataset.version); }
    }, 250);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
