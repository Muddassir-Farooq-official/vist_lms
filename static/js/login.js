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

    // Success: no banner, no waiting. The login response already says where
    // this person belongs, so go there right away (button stays "Signing in…").
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
  });

  // Coming back with the Back button after signing in: reset the busy state.
  window.addEventListener("pageshow", function (e) { if (e.persisted) setBusy(false); });
})();