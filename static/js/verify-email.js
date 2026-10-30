(function () {
  "use strict";
  // /verify-email: prove an email address with a 6-digit code. Required before using the portal when the
  // administrator turns it on; opened from the menu ("?from=menu") it changes the address instead.

  const $ = function (id) { return document.getElementById(id); };
  const voluntary = new URLSearchParams(window.location.search).get("from") === "menu";
  const intro = $("ve-intro"), emailForm = $("ve-email-form"), codeForm = $("ve-code-form"), err = $("ve-error");
  let state = null, timer = null;

  async function fetchJson(url, options) {
    let resp;
    try { resp = await fetch(url, Object.assign({ credentials: "same-origin" }, options || {})); }
    catch (e) { return { ok: false, status: 0, body: null }; }
    let body = null;
    try { body = await resp.json(); } catch (e) { body = null; }
    return { ok: resp.ok, status: resp.status, body: body };
  }
  function post(url, data) {
    return fetchJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  }
  function message(r, fallback) {
    if (r.status === 0) return "Can't reach the server. Check your internet connection and try again.";
    return (r.body && r.body.error && r.body.error.message) || fallback;
  }
  function show(el, text) { el.textContent = text; el.hidden = false; }
  function hide(el) { el.textContent = ""; el.hidden = true; }
  function busy(btn, labelEl, on, idle, working) {
    btn.disabled = on; btn.setAttribute("aria-busy", on ? "true" : "false"); labelEl.textContent = on ? working : idle;
  }

  function stepEmail() {
    codeForm.hidden = true; emailForm.hidden = false;
    $("ve-email").focus();
  }
  function stepCode(to) {
    emailForm.hidden = true; codeForm.hidden = false;
    $("ve-sent-to").textContent = "We sent a 6-digit code to " + to + ". It works for 10 minutes.";
    $("ve-code").value = ""; $("ve-code").focus();
  }
  function countdown(seconds) {
    const b = $("ve-resend");
    clearInterval(timer);
    let left = seconds;
    b.disabled = true; b.textContent = "Send a new code (" + left + "s)";
    timer = setInterval(function () {
      left -= 1;
      if (left <= 0) { clearInterval(timer); b.disabled = false; b.textContent = "Send a new code"; return; }
      b.textContent = "Send a new code (" + left + "s)";
    }, 1000);
  }

  async function start() {
    const me = await fetchJson("/api/v1/auth/me");
    if (!me.ok || !me.body) { window.location.replace("/?reauth=1"); return; }
    if (me.body.must_change_password) { window.location.replace("/change-password"); return; }
    const r = await fetchJson("/api/v1/auth/email");
    if (!r.ok) { show(err, message(r, "Could not load your account. Refresh the page.")); return; }
    state = r.body;
    if (!state.required && !voluntary) { window.location.replace("/"); return; }
    $("ve-back").hidden = !!state.required;
    if (state.required) {
      intro.textContent = "Welcome, " + me.body.username + ". Before you continue, confirm your email address. Notices from the institute " +
        "(fees, results, tasks, approvals…) will also come to this address.";
    } else if (state.verified) {
      document.querySelector("h1").textContent = "Change your email";
      intro.textContent = "Your verified email is " + state.email + ". To use another address, enter it below and confirm it with a code.";
    } else {
      intro.textContent = "Confirm your email address with a 6-digit code. Notices from the institute will also come to this address.";
    }
    if (!state.can_send) {
      show(err, "The institute has not set up email yet, so no code can be sent. Please contact the office.");
      return;
    }
    $("ve-email").value = (!state.verified && state.suggested) || "";
    if (state.code_sent_to) { stepCode(state.code_sent_to); countdown(30); } else { stepEmail(); }
  }

  async function send(address) {
    hide(err); hide($("ve-email-error"));
    if (!/^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/.test(address)) { show($("ve-email-error"), "Enter a valid email address, like name@gmail.com."); return false; }
    busy($("ve-send"), $("ve-send-label"), true, "Email me a code", "Sending…");
    const r = await post("/api/v1/auth/email/send-code", { email: address });
    busy($("ve-send"), $("ve-send-label"), false, "Email me a code", "Sending…");
    if (!r.ok) { show(err, message(r, "Could not send the code. Try again.")); return false; }
    state.code_sent_to = r.body.sent_to;
    stepCode(r.body.sent_to);
    countdown(60);
    return true;
  }

  emailForm.addEventListener("submit", function (e) { e.preventDefault(); send($("ve-email").value.trim()); });
  $("ve-email").addEventListener("input", function () { hide($("ve-email-error")); hide(err); });
  $("ve-code").addEventListener("input", function () {
    const v = $("ve-code").value.replace(/\D/g, "").slice(0, 6);
    if (v !== $("ve-code").value) $("ve-code").value = v;
    hide($("ve-code-error")); hide(err);
    if (v.length === 6) codeForm.requestSubmit ? codeForm.requestSubmit() : codeForm.dispatchEvent(new Event("submit"));
  });
  codeForm.addEventListener("submit", async function (e) {
    e.preventDefault();
    const code = $("ve-code").value.trim();
    if (code.length !== 6) { show($("ve-code-error"), "Enter the 6 digits from the email."); return; }
    if ($("ve-verify").disabled) return;
    busy($("ve-verify"), $("ve-verify-label"), true, "Verify and continue", "Checking…");
    const r = await post("/api/v1/auth/email/verify", { code: code });
    if (!r.ok) {
      busy($("ve-verify"), $("ve-verify-label"), false, "Verify and continue", "Checking…");
      show(err, message(r, "Could not verify the code."));
      $("ve-code").select();
      return;
    }
    try { sessionStorage.removeItem("vist_me"); } catch (x) { /* ignore */ }
    codeForm.hidden = true;
    show($("ve-done"), "Your email " + r.body.email + " is verified. Opening the portal…");
    setTimeout(function () { window.location.replace(r.body.redirect_to || "/"); }, 900);
  });
  $("ve-resend").addEventListener("click", function () { if (state && state.code_sent_to) send(state.code_sent_to); });
  $("ve-change").addEventListener("click", function () { hide(err); $("ve-email").value = ""; stepEmail(); });
  $("ve-logout").addEventListener("click", async function () {
    try { sessionStorage.removeItem("vist_me"); } catch (x) { /* ignore */ }
    await fetchJson("/api/v1/auth/logout", { method: "POST" });
    window.location.replace("/");
  });

  start();
})();