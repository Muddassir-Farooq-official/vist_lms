/* Urdu for the student, parent and sign-in pages.
   The pages are written in English; when the language is Urdu (<html lang="ur">, from the vist_lang cookie) every piece of
   text on the page, including what scripts add later, is looked up in /static/i18n/ur.json and replaced. Numbers,
   amounts and percentages inside a sentence are carried over ("Rs. {n} overdue"). Names, course titles and anything not
   in the dictionary stay as they are. The language button saves the choice on the account (and the SMS follow it). */
(function () {
  "use strict";
  var COOKIE = "vist_lang";
  var me = document.currentScript;
  var lang = document.documentElement.getAttribute("lang") === "ur" ? "ur" : "en";

  function setCookie(v) { document.cookie = COOKIE + "=" + v + "; path=/; max-age=31536000; SameSite=Lax" + (location.protocol === "https:" ? "; Secure" : ""); }

  // ------------------------------------------------------------- the language button
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-lang-toggle]");
    if (!b) return;
    e.preventDefault();
    var next = lang === "ur" ? "en" : "ur";
    setCookie(next);
    var done = function () { location.reload(); };
    fetch("/api/v1/auth/me/language", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ language: next }) })
      .then(done, done);                                  // not signed in yet (sign-in page): the cookie is enough
  });

  // after sign-in the account's own choice wins (same person on a new phone)
  window.VistLang = { current: lang, sync: function (accountLang) {
    if ((accountLang === "ur" || accountLang === "en") && accountLang !== lang) { setCookie(accountLang); location.reload(); }
  } };

  if (lang !== "ur") return;

  // ------------------------------------------------------------- the translator
  var dict = null, patterns = null;
  var TOK = /Rs\. ?[\d,]+(?:\.\d+)?|\d+(?:\.\d+)?%|\d[\d,:.–-]*|\b(?:January|February|March|April|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\b/g;
  var MONTH = /^(?:January|February|March|April|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)$/;
  var WORDS = { January: "جنوری", February: "فروری", March: "مارچ", April: "اپریل", May: "مئی", June: "جون", July: "جولائی", August: "اگست",
    September: "ستمبر", October: "اکتوبر", November: "نومبر", December: "دسمبر", Jan: "جنوری", Feb: "فروری", Mar: "مارچ", Apr: "اپریل",
    Jun: "جون", Jul: "جولائی", Aug: "اگست", Sep: "ستمبر", Sept: "ستمبر", Oct: "اکتوبر", Nov: "نومبر", Dec: "دسمبر",
    Monday: "پیر", Tuesday: "منگل", Wednesday: "بدھ", Thursday: "جمعرات", Friday: "جمعہ", Saturday: "ہفتہ", Sunday: "اتوار",
    Mon: "پیر", Tue: "منگل", Wed: "بدھ", Thu: "جمعرات", Fri: "جمعہ", Sat: "ہفتہ", Sun: "اتوار", Due: "واجب", due: "واجب", Room: "کمرہ" };
  var DATEISH = /^[\s\d,:.·–\-()]*(?:(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Wed|Thu|Fri|Sat|Sun|Due|due|Room)\b[\s\d,:.·–\-()]*)+$/;

  function translate(raw) {
    if (!raw || !/[A-Za-z]/.test(raw)) return null;
    var lead = raw.match(/^\s*/)[0], trail = raw.match(/\s*$/)[0];
    var s = raw.trim().replace(/\s+/g, " ");
    if (dict[s] != null) return lead + dict[s] + trail;
    var toks = [];
    var key = s.replace(TOK, function (m) {
      if (WORDS[m] && /^[A-Z]/.test(m) && MONTH.test(m)) { toks.push(WORDS[m]); return "{m}"; }
      toks.push(m.indexOf("Rs.") === 0 ? m.replace(/^Rs\. ?/, "") : m.replace(/%$/, ""));
      return m.indexOf("Rs.") === 0 ? "Rs. {n}" : (/%$/.test(m) ? "{n}%" : "{n}");
    });
    if (toks.length && dict[key] != null) { var i = 0; return lead + dict[key].replace(/\{[nm]\}/g, function () { return toks[i++]; }) + trail; }
    if (DATEISH.test(s)) return lead + s.replace(/[A-Za-z]+/g, function (w) { return WORDS[w] || w; }) + trail;
    return null;
  }

  var SKIP = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, CODE: 1, PRE: 1, NOSCRIPT: 1 };
  function walk(root) {
    if (!dict) return;
    if (root.nodeType === 3) { fixText(root); return; }
    if (root.nodeType !== 1 || SKIP[root.tagName] || root.closest("[translate=no],[data-no-translate]")) return;
    fixAttrs(root);
    var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode: function (n) { return n.nodeType === 1 && (SKIP[n.tagName] || n.getAttribute("translate") === "no" || n.hasAttribute("data-no-translate")) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT; } });
    var n;
    while ((n = w.nextNode())) { if (n.nodeType === 3) fixText(n); else fixAttrs(n); }
  }
  var NUMERIC = /^[\s\d.,/%:–\-+()Rs]*\d[\s\d.,/%:–\-+()Rs]*$/;
  function fixText(n) {
    if (n.__ur === n.nodeValue) return;
    var t = translate(n.nodeValue);
    if (t != null) { n.nodeValue = t; }
    else if (NUMERIC.test(n.nodeValue) && n.parentElement && n.parentElement.childNodes.length === 1) {
      n.parentElement.setAttribute("dir", "ltr");          // "13 / 20" must not turn into "20 / 13"
      n.parentElement.style.unicodeBidi = "isolate";
    }
    n.__ur = n.nodeValue;
  }
  function fixAttrs(el) {
    ["placeholder", "aria-label", "title"].forEach(function (a) {
      var v = el.getAttribute && el.getAttribute(a);
      if (v) { var t = translate(v); if (t != null && t !== v) el.setAttribute(a, t); }
    });
    if (el.tagName === "INPUT" && (el.type === "submit" || el.type === "button") && el.value) { var t2 = translate(el.value); if (t2) el.value = t2; }
  }

  var observer = new MutationObserver(function (list) {
    list.forEach(function (m) {
      if (m.type === "characterData") fixText(m.target);
      else if (m.type === "attributes") fixAttrs(m.target);
      else m.addedNodes.forEach(walk);
    });
  });

  function reveal() { document.documentElement.classList.add("i18n-ready"); }
  setTimeout(reveal, 1500);                               // never keep the page hidden if the dictionary is slow
  fetch(me.getAttribute("data-dict"), { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
    dict = d;
    var go = function () {
      walk(document.body);
      if (document.title) { var tt = translate(document.title); if (tt) document.title = tt; }
      observer.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["placeholder", "aria-label", "title"] });
      reveal();
    };
    if (document.body) go(); else document.addEventListener("DOMContentLoaded", go);
  }).catch(reveal);
})();
