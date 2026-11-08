(function () {
  "use strict";

  const form = document.getElementById("login-form");
  const usernameInput = document.getElementById("username");
  const passwordInput = document.getElementById("password");
  const submitBtn = document.getElementById("submit-btn");
  const submitLabel = document.getElementById("submit-label");
  const formError = document.getElementById("login-error");
  const usernameError = document.getElementById("username-error");
  const passwordError = document.getElementById("password-error");
  const toggleBtn = document.getElementById("pw-toggle");

  // ---- small helpers -------------------------------------------------------
  function show(el, message) {
    el.textContent = message;
    el.hidden = false;
  }
  function hide(el) {
    el.hidden = true;
    el.textContent = "";
  }
  function setInvalid(input, errorEl, message) {
    input.setAttribute("aria-invalid", "true");
    show(errorEl, message);
  }
  function clearInvalid(input, errorEl) {
    input.removeAttribute("aria-invalid");
    hide(errorEl);
  }
  function setBusy(busy) {
    submitBtn.setAttribute("aria-busy", busy ? "true" : "false");
    submitBtn.disabled = busy;
    submitLabel.textContent = busy ? "Signing in\u2026" : "Sign in";
  }
  function clearAll() {
    hide(formError);
    clearInvalid(usernameInput, usernameError);
    clearInvalid(passwordInput, passwordError);
  }

  // Errors disappear as soon as the person starts fixing them.
  usernameInput.addEventListener("input", function () { hide(formError); clearInvalid(usernameInput, usernameError); });
  passwordInput.addEventListener("input", function () { hide(formError); clearInvalid(passwordInput, passwordError); });

  toggleBtn.addEventListener("click", function () {
    const reveal = passwordInput.type === "password";
    passwordInput.type = reveal ? "text" : "password";
    toggleBtn.textContent = reveal ? "Hide" : "Show";
    toggleBtn.setAttribute("aria-pressed", reveal ? "true" : "false");
    passwordInput.focus();
  });

  // Plain-language message for every way sign-in can fail.
  function messageFor(status, body) {
    const serverMessage = body && body.error && body.error.message;
    if (status === 401 || status === 403) return serverMessage || "Incorrect username or password.";
    if (status === 429) return serverMessage || "Too many attempts. Please wait a minute and try again.";
    if (status >= 500) return "Something went wrong on our side. Please try again in a moment.";
    return serverMessage || "Sign-in failed. Please try again.";
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (submitBtn.disabled) return;
    clearAll();

    const username = usernameInput.value.trim();
    const password = passwordInput.value;

    // Check the obvious things here, instantly, without asking the server.
    if (!username) {
      setInvalid(usernameInput, usernameError, "Enter your username.");
      usernameInput.focus();
      return;
    }
    if (!password) {
      setInvalid(passwordInput, passwordError, "Enter your password.");
      passwordInput.focus();
      return;
    }

    setBusy(true);
    let resp;
    let body = null;
    try {
      resp = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ username: username, password: password }),
      });
      body = await resp.json().catch(function () { return null; });
    } catch (err) {
      setBusy(false);
      show(formError, "Can't reach the server. Check your internet connection and try again.");
      return;
    }

    if (!resp.ok) {
      setBusy(false);
      show(formError, messageFor(resp.status, body));
      passwordInput.select();
      passwordInput.focus();
      return;
    }

    // Administrators: the right password, now the code from the authenticator app (or set it up the first time).
    if (body.two_factor) {
      setBusy(false);
      document.querySelectorAll(".tfa-days").forEach(function (el) { el.textContent = String(body.remember_days || 30); });
      startSecondStep(body.two_factor, body.challenge);
      return;
    }
    goIn(body);
  });

  // Signed in: no banner, no waiting. The answer already says where this person belongs, so go there right away.
  function goIn(body) {
    if (!body.redirect_to) {
      setBusy(false);
      show(formError, "Your account is active, but there is no portal for your role yet. Please contact the administration office.");
      return;
    }
    try {
      // Lets the next page show itself immediately instead of first asking
      // "who am I?" again (it re-checks quietly in the background).
      sessionStorage.setItem("vist_me", JSON.stringify({
        ts: Date.now(),
        me: {
          id: body.user_id, username: body.username, roles: body.roles || [],
          permissions: body.permissions || [], must_change_password: !!body.must_change_password,
          email_verification_required: !!body.email_verification_required,
        },
      }));
    } catch (e) { /* private mode etc. — harmless */ }
    window.location.replace(body.redirect_to);
  }

  // ---- two-step sign-in -----------------------------------------------------
  const codeForm = document.getElementById("tfa-code-form"), setupForm = document.getElementById("tfa-setup-form");
  let challenge = null;
  async function post(url, data) {
    try {
      const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body: JSON.stringify(data) });
      return { ok: r.ok, status: r.status, body: await r.json().catch(function () { return null; }) };
    } catch (e) { return { ok: false, status: 0, body: null }; }
  }
  function errorText(res, fallback) {
    if (res.status === 0) return "Can't reach the server. Check your internet connection and try again.";
    return (res.body && res.body.error && res.body.error.message) || fallback;
  }
  function onlyDigits(input) { input.addEventListener("input", function () { input.value = input.value.replace(/[^0-9]/g, "").slice(0, 6); }); }
  function busy(btn, on) { btn.disabled = on; btn.setAttribute("aria-busy", on ? "true" : "false"); }

  async function startSecondStep(kind, token) {
    challenge = token;
    form.hidden = true;
    document.getElementById("signin-first-note").hidden = true;
    const intro = document.querySelector(".signin-card > .auth-sub");       // "Students use their roll number ..." is not for this step
    if (intro) intro.hidden = true;
    document.getElementById("auth-title").textContent = kind === "setup" ? "Set up two-step sign-in" : "Enter your code";
    if (kind === "code") {
      codeForm.hidden = false;
      document.getElementById("tfa-code").focus();
      return;
    }
    setupForm.hidden = false;
    const res = await post("/api/v1/auth/2fa/setup", { challenge: challenge });
    if (!res.ok) { const e = document.getElementById("tfa-setup-error"); e.textContent = errorText(res, "Could not start the set-up. Sign in again."); e.hidden = false; return; }
    document.getElementById("tfa-qr").innerHTML = res.body.qr_svg;      // made on our own server from the set-up key
    document.getElementById("tfa-key").textContent = res.body.key;
  }

  async function sendCode(url, input, remember, btn, err) {
    err.hidden = true;
    const code = input.value.replace(/[^0-9]/g, "");
    if (code.length !== 6) { err.textContent = "Type the 6 digits shown in your authenticator app."; err.hidden = false; input.focus(); return; }
    busy(btn, true);
    const res = await post(url, { challenge: challenge, code: code, remember: remember.checked });
    if (!res.ok) {
      busy(btn, false);
      err.textContent = errorText(res, "That code did not work. Try the next one.");
      err.hidden = false;
      if (res.status === 401 && /sign in again/i.test(err.textContent)) setTimeout(function () { window.location.reload(); }, 2500);
      input.select();
      return;
    }
    goIn(res.body);
  }
  [document.getElementById("tfa-code"), document.getElementById("tfa-setup-code")].forEach(onlyDigits);
  codeForm.addEventListener("submit", function (e) {
    e.preventDefault();
    sendCode("/api/v1/auth/2fa/verify", document.getElementById("tfa-code"), document.getElementById("tfa-remember"),
      document.getElementById("tfa-code-btn"), document.getElementById("tfa-code-error"));
  });
  setupForm.addEventListener("submit", function (e) {
    e.preventDefault();
    sendCode("/api/v1/auth/2fa/setup/confirm", document.getElementById("tfa-setup-code"), document.getElementById("tfa-setup-remember"),
      document.getElementById("tfa-setup-btn"), document.getElementById("tfa-setup-error"));
  });
  document.querySelectorAll(".tfa-back").forEach(function (b) { b.addEventListener("click", function () { window.location.reload(); }); });

  // Coming back with the Back button after signing in: reset the busy state.
  window.addEventListener("pageshow", function (e) { if (e.persisted) setBusy(false); });
})();