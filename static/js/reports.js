(function () {
  "use strict";

  // Reports centre: the list of reports comes from /api/v1/report-center/catalog (only the ones this person may run).
  // Each report declares its filters; the result is one table (columns, rows, totals, notes) shown here, printed,
  // or downloaded as CSV from the same endpoint, so the three always agree.
  const VA = window.VistAdmin;
  const esc = VA.escapeHtml;
  const API = "/api/v1/report-center";
  const $ = function (id) { return document.getElementById(id); };
  const msg = function (body, fallback) { return body && body.error && body.error.message ? body.error.message : fallback; };
  let catalog = null, current = null, lastParams = null;

  const pad = function (n) { return String(n).padStart(2, "0"); };
  const iso = function (d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); };
  function defaultValue(f) {
    const today = new Date();
    if (f.default === "today") return iso(today);
    if (f.default === "this-month") return today.getFullYear() + "-" + pad(today.getMonth() + 1);
    if (f.default === "this-year") return String(today.getFullYear());
    if (typeof f.default === "string" && /^-\d+$/.test(f.default)) { const d = new Date(); d.setDate(d.getDate() + parseInt(f.default, 10)); return iso(d); }
    return f.default == null ? "" : String(f.default);
  }

  function fmt(v, type) {
    if (v === null || v === undefined || v === "") return "";
    if (type === "money") return VA.formatMoney(v);
    if (type === "percent") return Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 }) + "%";
    if (type === "number") return Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
    if (type === "date") return new Date(String(v).slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
    return String(v);
  }

  // ---------------------------------------------------------------- the list of reports
  function renderNav(filter) {
    const q = (filter || "").trim().toLowerCase();
    const nav = $("rp-nav"); nav.innerHTML = "";
    catalog.groups.forEach(function (g) {
      const reps = g.reports.filter(function (r) { return !q || (r.title + " " + r.description + " " + g.name).toLowerCase().indexOf(q) !== -1; });
      if (!reps.length) return;
      const h = document.createElement("h3"); h.className = "rp-group"; h.textContent = g.name; nav.appendChild(h);
      reps.forEach(function (r) {
        const b = document.createElement("button"); b.type = "button"; b.className = "rp-item" + (current && current.key === r.key ? " active" : "");
        b.innerHTML = "<strong>" + esc(r.title) + "</strong><small>" + esc(r.description) + "</small>";
        b.addEventListener("click", function () { choose(r); });
        nav.appendChild(b);
      });
    });
    if (!nav.children.length) nav.innerHTML = '<p class="cc-meta">No report matches.</p>';
  }
  $("rp-search").addEventListener("input", function () { renderNav($("rp-search").value); });

  // ---------------------------------------------------------------- filters
  function field(f) {
    const wrap = document.createElement("div"); wrap.className = "form-field";
    const id = "rpf-" + f.key;
    const label = '<label for="' + id + '">' + esc(f.label) + (current.needs.indexOf(f.key) !== -1 ? " *" : "") + "</label>";
    const opts = function (list, blank) {
      return (blank ? '<option value="">' + esc(blank) + "</option>" : "") + (list || []).map(function (o) { return '<option value="' + esc(o.value) + '">' + esc(o.label) + "</option>"; }).join("");
    };
    const lists = catalog.options || {};
    if (f.type === "check") {
      wrap.innerHTML = '<label class="check-row"><input type="checkbox" id="' + id + '" data-key="' + f.key + '" /> <span>' + esc(f.label) + "</span></label>";
      return wrap;
    }
    let input;
    if (f.type === "select") input = '<select id="' + id + '" class="select-input">' + opts(f.options) + "</select>";
    else if (["program", "term", "stock_item", "payroll_run", "expense_category", "vendor", "cashier", "fy", "asset_category", "asset_location", "asset_check"].indexOf(f.type) !== -1) {
      const blank = f.type === "payroll_run" || f.type === "stock_item" || f.type === "asset_check" ? "Choose…" : f.type === "fy" ? null : "All";
      input = '<select id="' + id + '" class="select-input">' + opts(lists[f.type], blank) + "</select>";
    } else if (f.type === "student") {
      input = '<input id="' + id + '-q" class="text-input" type="search" placeholder="Name or roll number…" autocomplete="off" /><input id="' + id + '" type="hidden" /><div id="' + id + '-list" class="picker-list"></div>';
    } else {
      const t = f.type === "date" ? "date" : f.type === "month" ? "month" : f.type === "number" ? "number" : "text";
      input = '<input id="' + id + '" class="text-input" type="' + t + '" />';
    }
    wrap.innerHTML = label + input;
    return wrap;
  }

  function wireStudentPicker(f) {
    const q = $("rpf-" + f.key + "-q"), hidden = $("rpf-" + f.key), list = $("rpf-" + f.key + "-list");
    let timer = null;
    q.addEventListener("input", function () {
      hidden.value = ""; clearTimeout(timer);
      timer = setTimeout(async function () {
        list.innerHTML = "";
        if (q.value.trim().length < 2) return;
        const r = await VA.fetchJson("/api/v1/students?page_size=10&q=" + encodeURIComponent(q.value.trim()), { quiet: true });
        if (!r.ok) return;
        (r.body.items || []).forEach(function (s) {
          const b = document.createElement("button"); b.type = "button"; b.className = "picker-item";
          b.innerHTML = "<strong>" + esc(s.first_name + " " + s.last_name) + "</strong><small>" + esc(s.roll_number || "") + "</small>";
          b.addEventListener("click", function () { hidden.value = s.id; q.value = s.first_name + " " + s.last_name + (s.roll_number ? " (" + s.roll_number + ")" : ""); list.innerHTML = ""; run(); });
          list.appendChild(b);
        });
      }, 300);
    });
  }

  function choose(r, initial) {
    current = r;
    renderNav($("rp-search").value);
    $("rp-empty").hidden = true; $("rp-card").hidden = false; $("rp-result").hidden = true; $("rp-error").hidden = true;
    $("rp-title").textContent = r.title; $("rp-desc").textContent = r.description;
    const form = $("rp-filters"); form.innerHTML = "";
    r.filters.forEach(function (f) { form.appendChild(field(f)); });
    r.filters.forEach(function (f) {
      const el = $("rpf-" + f.key);
      const v = initial && initial[f.key] != null ? initial[f.key] : defaultValue(f);
      if (f.type === "check") el.checked = v === "1" || v === "true";
      else if (f.type === "student") wireStudentPicker(f);
      else if (v !== "" && el) el.value = v;
    });
    try { history.replaceState(null, "", "#" + r.key); } catch (e) { /* ignore */ }
    if (!r.needs.length) run();
  }

  function params() {
    const p = new URLSearchParams();
    current.filters.forEach(function (f) {
      const el = $("rpf-" + f.key);
      if (!el) return;
      let v = f.type === "check" ? (el.checked ? "1" : "") : el.value.trim();
      if (f.type === "month" && v) v = v + "-01";
      if (v) p.set(f.key, v);
    });
    return p;
  }

  // ---------------------------------------------------------------- running
  async function run() {
    if (!current) return;
    $("rp-error").hidden = true;
    const p = params(); lastParams = p;
    const missing = current.filters.filter(function (f) { return current.needs.indexOf(f.key) !== -1 && !p.get(f.key); });
    if (missing.length) { $("rp-error").textContent = "Choose: " + missing.map(function (f) { return f.label; }).join(", ") + "."; $("rp-error").hidden = false; return; }
    $("rp-csv").href = API + "/export/" + encodeURIComponent(current.key) + "?" + p.toString();
    $("rp-csv").setAttribute("data-download", current.key.replace(".", "-") + ".csv");
    $("rp-run").disabled = true;
    const r = await VA.fetchJson(API + "/run/" + encodeURIComponent(current.key) + "?" + p.toString());
    $("rp-run").disabled = false;
    if (!r.ok) { $("rp-error").textContent = msg(r.body, "Could not make this report."); $("rp-error").hidden = false; $("rp-result").hidden = true; return; }
    render(r.body);
  }
  $("rp-run").addEventListener("click", run);
  $("rp-filters").addEventListener("submit", function (e) { e.preventDefault(); run(); });
  $("rp-filters").addEventListener("change", function (e) { if (e.target && e.target.tagName === "SELECT") run(); });
  $("rp-print").addEventListener("click", function () { if ($("rp-result").hidden) run().then(function () { window.print(); }); else window.print(); });

  function render(d) {
    $("rp-result").hidden = false;
    $("rp-r-title").textContent = d.title;
    const filterText = current.filters.map(function (f) {
      const v = lastParams.get(f.key); if (!v) return "";
      const el = $("rpf-" + f.key);
      const shown = el && el.tagName === "SELECT" ? el.options[el.selectedIndex].text : f.type === "student" ? $("rpf-" + f.key + "-q").value : f.type === "check" ? "yes" : v;
      return f.label + ": " + shown;
    }).filter(Boolean).join(" · ");
    $("rp-r-sub").textContent = [d.subtitle, filterText, "Made on " + fmt(d.generated_on, "date")].filter(Boolean).join(" — ");
    const t = $("rp-table");
    const cols = d.columns;
    const numeric = function (c) { return ["money", "number", "percent"].indexOf(c.type) !== -1; };
    t.tHead.innerHTML = "<tr>" + cols.map(function (c) { return "<th" + (numeric(c) ? ' class="num"' : "") + ">" + esc(c.label) + "</th>"; }).join("") + "</tr>";
    t.tBodies[0].innerHTML = d.rows.map(function (row) {
      return "<tr" + (row._bold ? ' class="rp-sum"' : "") + ">" + cols.map(function (c) {
        const v = row[c.key];
        const neg = numeric(c) && Number(v) < 0;
        return "<td" + (numeric(c) ? ' class="num' + (neg ? " bad" : "") + '"' : "") + ">" + esc(fmt(v, c.type)) + "</td>";
      }).join("") + "</tr>";
    }).join("");
    const tot = d.totals || {};
    const hasTotals = Object.keys(tot).length > 0 && d.rows.length > 0;
    t.tFoot.innerHTML = hasTotals ? "<tr>" + cols.map(function (c, i) {
      const v = tot[c.key];
      return "<th" + (numeric(c) ? ' class="num"' : "") + ">" + (i === 0 && v == null ? "Total" : esc(fmt(v, c.type))) + "</th>";
    }).join("") + "</tr>" : "";
    $("rp-none").hidden = d.rows.length > 0;
    $("rp-notes").innerHTML = (d.notes || []).map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("");
  }

  document.addEventListener("vistadmin:ready", async function () {
    const portal = (location.pathname.split("/")[2] || "admin");
    $("rp-finance-link").href = "/dashboard/" + (portal === "accounts" ? "accounts" : "admin") + "/finance-reports";
    if (portal !== "admin" && portal !== "accounts") $("rp-finance-link").parentNode.hidden = true;
    const r = await VA.fetchJson(API + "/catalog");
    if (!r.ok) { $("rp-nav").innerHTML = '<p class="cc-meta">' + esc(msg(r.body, "Could not load the reports.")) + "</p>"; return; }
    catalog = r.body;
    renderNav("");
    const want = (location.hash || "").replace("#", "");
    const all = [].concat.apply([], catalog.groups.map(function (g) { return g.reports; }));
    const found = all.find(function (x) { return x.key === want; });
    if (found) choose(found);
  });
})();
