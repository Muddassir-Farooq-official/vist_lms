(function () {
  "use strict";

  const form = document.getElementById("change-password-form");
  const btn = document.getElementById("change-password-btn");
  const label = document.getElementById("change-label");
  const formError = document.getElementById("password-error");
  const intro = document.getElementById("cp-intro");
  const fields = {
    current: { input: document.getElementById("current-password"), error: document.getElementById("current-error") },
    next: { input: document.getElementById("new-password"), error: document.getElementById("new-error") },
    confirm: { input: document.getElementById("confirm-password"), error: document.getElementById("confirm-error") },
  };

  function show(el, msg) { el.textContent = msg; el.hidden = false; }
  function hide(el) { el.textContent = ""; el.hidden = true; }
  function invalid(f, msg) { f.input.setAttribute("aria-invalid", "true"); show(f.error, msg); }
  function clearField(f) { f.input.removeAttribute("aria-invalid"); hide(f.error); }
  function clearAll() { hide(formError); Object.keys(fields).forEach(function (k) { clearField(fields[k]); }); }
  function busy(on) {
    btn.setAttribute("aria-busy", on ? "true" : "false");
    btn.disabled = on;
    label.textContent = on ? "Saving\u2026" : "Save and continue";
  }

  Object.keys(fields).forEach(function (k) {
    fields[k].input.addEventListener("input", function () { hide(formError); clearField(fields[k]); });
  });

  document.querySelectorAll(".auth-pw-toggle").forEach(function (t) {
    t.addEventListener("click", function () {
      const input = document.getElementById(t.getAttribute("data-target"));
      const reveal = input.type === "password";
      input.type = reveal ? "text" : "password";
      t.textContent = reveal ? "Hide" : "Show";
      t.setAttribute("aria-pressed", reveal ? "true" : "false");
      input.focus();
    });
  });

  async function fetchJson(url, options) {
    const resp = await fetch(url, Object.assign({ credentials: "same-origin" }, options || {}));
    let body = null;
    try { body = await resp.json(); } catch (e) { body = null; }
    return { ok: resp.ok, status: resp.status, body: body };
  }

  // Two ways to be here: forced (first login, no way out) or voluntary (opened from the menu).
  const voluntary = new URLSearchParams(window.location.search).get("from") === "menu";

  // Who is this? (Only to greet them and to bounce people who don't belong here.)
  async function checkSession() {
    const result = await fetchJson("/api/v1/auth/me");
    if (!result.ok || !result.body) {
      window.location.replace("/?reauth=1");
      return;
    }
    if (!result.body.must_change_password && !voluntary) {
      window.location.replace("/"); // nothing to change; "/" sends them to their portal
      return;
    }
    if (result.body.must_change_password) {
      intro.textContent = "Welcome, " + result.body.username + ". Choose a password only you know before you continue.";
    } else {
      intro.textContent = "Choose a new password. Any other device you are signed in on will be signed out.";
      document.getElementById("cp-cancel-wrap").hidden = false;
      document.querySelector("h1").textContent = "Change your password";
    }
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (btn.disabled) return;
    clearAll();

    const current = fields.current.input.value;
    const next = fields.next.input.value;
    const confirm = fields.confirm.input.value;

    if (!current) { invalid(fields.current, "Enter your current password."); fields.current.input.focus(); return; }
    if (next.length < 10) { invalid(fields.next, "Use at least 10 characters."); fields.next.input.focus(); return; }
    if (next === current) { invalid(fields.next, "Choose a password different from the current one."); fields.next.input.focus(); return; }
    if (next !== confirm) { invalid(fields.confirm, "The two new passwords don't match."); fields.confirm.input.focus(); return; }

    busy(true);
    let result;
    try {
      result = await fetchJson("/api/v1/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
    } catch (e) {
      busy(false);
      show(formError, "Can't reach the server. Check your internet connection and try again.");
      return;
    }

    if (!result.ok) {
      busy(false);
      const serverMessage = result.body && result.body.error && result.body.error.message;
      show(formError, serverMessage || "Could not change the password. Please try again.");
      return;
    }

    // Done. No banner, no pause: "/" reads the (freshly re-issued) session and
    // sends this person to the right portal.
    try { sessionStorage.removeItem("vist_me"); } catch (e) { /* ignore */ }
    window.location.replace("/");
  });

  checkSession();
})();