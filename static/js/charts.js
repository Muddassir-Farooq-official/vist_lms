/* Small dependency-free charts (SVG / CSS) in the app's own colours. Every chart has a text equivalent. */
window.Charts = (function () {
  "use strict";
  const esc = function (v) { return window.VistAdmin.escapeHtml(v == null ? "" : String(v)); };
  const css = function (name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  };
  const palette = function () {
    return {
      blue: css("--blue-600", "#2454d6"), blueSoft: css("--blue-500", "#3b6df0"), yellow: css("--yellow-400", "#f5b400"),
      green: css("--success", "#15803d"), amber: css("--amber-500", "#d97706"), red: css("--danger", "#dc2626"),
      grey: "#94a3b8", greyLight: "#cbd5e1", grid: css("--border", "#e3e8f2"), text: css("--text-secondary", "#5b6478"),
    };
  };

  function empty(el, message) {
    el.innerHTML = '<p class="chart-empty">' + esc(message || "Nothing to show yet.") + "</p>";
  }

  // Ring with the total in the middle and a legend (label, count, share).
  function donut(el, items, opts) {
    opts = opts || {};
    const total = items.reduce(function (n, i) { return n + i.count; }, 0);
    if (!total) return empty(el, opts.empty);
    const R = 52, C = 2 * Math.PI * R;
    let offset = 0;
    let rings = "";
    items.filter(function (i) { return i.count > 0; }).forEach(function (i) {
      const len = (i.count / total) * C;
      rings += '<circle cx="70" cy="70" r="' + R + '" fill="none" stroke="' + i.color + '" stroke-width="18" stroke-dasharray="' + len.toFixed(2) + " " + (C - len).toFixed(2) +
        '" stroke-dashoffset="' + (-offset).toFixed(2) + '" transform="rotate(-90 70 70)"><title>' + esc(i.label) + ": " + i.count + "</title></circle>";
      offset += len;
    });
    const summary = items.map(function (i) { return i.label + " " + i.count; }).join(", ");
    el.innerHTML =
      '<div class="donut-wrap"><svg class="donut" viewBox="0 0 140 140" role="img" aria-label="' + esc((opts.title || "Breakdown") + ": " + summary) + '">' +
      '<circle cx="70" cy="70" r="' + R + '" fill="none" stroke="' + palette().grid + '" stroke-width="18"/>' + rings +
      '<text x="70" y="68" text-anchor="middle" class="donut-total">' + total + '</text><text x="70" y="86" text-anchor="middle" class="donut-sub">' + esc(opts.centerLabel || "total") + "</text></svg>" +
      '<ul class="chart-legend">' + items.map(function (i) {
        return '<li><span class="swatch" style="background:' + i.color + '"></span><span class="lg-label">' + esc(i.label) + '</span><strong>' + i.count +
          '</strong><span class="lg-pct">' + Math.round((i.count / total) * 100) + "%</span></li>";
      }).join("") + "</ul></div>";
  }

  // Horizontal bars, longest first.
  function hbars(el, items, opts) {
    opts = opts || {};
    const max = Math.max.apply(null, items.map(function (i) { return i.count; }).concat([0]));
    if (!max) return empty(el, opts.empty);
    el.innerHTML = items.map(function (i) {
      const pct = Math.max(3, Math.round((i.count / max) * 100));
      return '<div class="hbar"><span class="hbar-label">' + esc(i.label) + '</span><span class="hbar-track"><span class="hbar-fill" style="width:' + pct +
        "%;background:" + (i.color || opts.color || palette().blue) + '"></span></span><strong class="hbar-val">' + i.count + "</strong></div>";
    }).join("");
  }

  // Line/area chart with up to two series over labelled points.
  function lineChart(el, points, series, opts) {
    opts = opts || {};
    const maxVal = Math.max.apply(null, points.map(function (p) { return Math.max.apply(null, series.map(function (s) { return p[s.key] || 0; })); }).concat([0]));
    if (!maxVal) return empty(el, opts.empty);
    const W = 640, H = 240, L = 34, R = 12, T = 12, B = 34;
    const top = Math.max(4, Math.ceil(maxVal / 2) * 2);
    const x = function (i) { return L + (points.length === 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (points.length - 1)); };
    const y = function (v) { return T + (H - T - B) * (1 - v / top); };
    const pal = palette();
    let grid = "";
    [0, 0.5, 1].forEach(function (f) {
      const v = Math.round(top * f);
      grid += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + y(v) + '" y2="' + y(v) + '" stroke="' + pal.grid + '"/><text x="' + (L - 8) + '" y="' + (y(v) + 4) + '" text-anchor="end" class="axis">' + v + "</text>";
    });
    const step = points.length > 8 ? 2 : 1;
    const labels = points.map(function (p, i) { return i % step === 0 ? '<text x="' + x(i) + '" y="' + (H - 10) + '" text-anchor="middle" class="axis">' + esc(p.label) + "</text>" : ""; }).join("");
    const drawn = series.map(function (s, n) {
      const pts = points.map(function (p, i) { return x(i).toFixed(1) + "," + y(p[s.key] || 0).toFixed(1); });
      const area = n === 0 ? '<polygon points="' + x(0) + "," + y(0) + " " + pts.join(" ") + " " + x(points.length - 1) + "," + y(0) + '" fill="' + s.color + '" opacity="0.12"/>' : "";
      const dots = points.map(function (p, i) {
        return '<circle cx="' + x(i).toFixed(1) + '" cy="' + y(p[s.key] || 0).toFixed(1) + '" r="3.5" fill="' + s.color + '"><title>' + esc(p.label + " \u2014 " + s.label + ": " + (p[s.key] || 0)) + "</title></circle>";
      }).join("");
      return area + '<polyline points="' + pts.join(" ") + '" fill="none" stroke="' + s.color + '" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>' + dots;
    }).join("");
    const text = series.map(function (s) { return s.label + ": " + points.map(function (p) { return p[s.key] || 0; }).join(", "); }).join("; ");
    el.innerHTML =
      '<svg class="linechart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc((opts.title || "Trend") + ". " + text) + '">' + grid + labels + drawn + "</svg>" +
      '<ul class="chart-legend inline">' + series.map(function (s) { return '<li><span class="swatch" style="background:' + s.color + '"></span><span class="lg-label">' + esc(s.label) + "</span></li>"; }).join("") + "</ul>";
  }

  return { donut: donut, hbars: hbars, lineChart: lineChart, palette: palette, empty: empty };
})();