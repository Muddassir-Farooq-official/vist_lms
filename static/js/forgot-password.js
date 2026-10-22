(function () {
  "use strict";
  // Forgotten password: ask for a code by SMS, then set a new password with it.
  const $ = function (id) { return document.getElementById(id); };
  let username = "";

  async function post(url, body) {
    try {
      const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "include", body: JSON.stringify(body) });
      let data = null;
      try { data = await r.json(); } catch (e) { data = null; }
      return { ok: r.ok, status: r.status, body: data };
    } catch (e) {
      return { ok: false, status: 0, body: { error: { message: "No connection. Check the internet and try again." } } };
    }
  }
  function busy(btn, on) { btn.disabled = on; btn.setAttribute("aria-busy", on ? "true" : "false"); }
  function msg(r, fallback) {
    const e = r.body && r.body.error;
    if (e && e.details && Array.isArray(e.details.errors) && e.details.errors.length) return e.details.errors[0].message || e.message;
    return (e && e.message) || fallback;
  }

  async function ask() {
    const err = $("ask-error"); err.hidden = true;
    username = $("fp-username").value.trim();
    if (!username) { err.textContent = "Enter your username."; err.hidden = false; $("fp-username").focus(); return; }
    busy($("ask-btn"), true);
    const r = await post("/api/v1/auth/forgot-password", { username: username });
    busy($("ask-btn"), false);
    if (!r.ok) { err.textContent = msg(r, "Could not send a code right now."); err.hidden = false; return; }
    $("ask-form").hidden = true;
    $("reset-form").hidden = false;
    $("reset-intro").textContent = r.body.message;
    $("fp-code").focus();
  }
  $("ask-form").addEventListener("submit", function (e) { e.preventDefault(); ask(); });
  $("fp-again-code").addEventListener("click", function () { $("reset-form").hidden = true; $("ask-form").hidden = false; $("fp-username").value = username; });

  $("reset-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const err = $("reset-error"); err.hidden = true;
    const code = $("fp-code").value.trim(), pw = $("fp-new").value, again = $("fp-again").value;
    if (!/^\d{6}$/.test(code)) { err.textContent = "Enter the 6-digit code from the SMS."; err.hidden = false; return; }
    if (pw.length < 10) { err.textContent = "The new password needs at least 10 characters."; err.hidden = false; return; }
    if (pw !== again) { err.textContent = "The two passwords are not the same."; err.hidden = false; return; }
    busy($("reset-btn"), true);
    const r = await post("/api/v1/auth/reset-password", { username: username, code: code, new_password: pw });
    busy($("reset-btn"), false);
    if (!r.ok) { err.textContent = msg(r, "Could not change the password."); err.hidden = false; return; }
    $("reset-form").hidden = true;
    $("done").hidden = false;
  });
})();