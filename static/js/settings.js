(function () {
  "use strict";

  const VA = window.VistAdmin;

  const form = document.getElementById("change-password-form");
  const btn = document.getElementById("change-password-btn");
  const errorBox = document.getElementById("password-error");
  const successBox = document.getElementById("password-success");

  function renderAccount(me) {
    document.getElementById("acct-username").textContent = me.username;
    document.getElementById("acct-roles").textContent = (me.roles || []).join(", ");
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    btn.disabled = true;
    errorBox.hidden = true;
    successBox.hidden = true;

    const currentPassword = document.getElementById("current-password").value;
    const newPassword = document.getElementById("new-password").value;

    const result = await VA.fetchJson("/api/v1/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    });

    btn.disabled = false;

    if (!result.ok) {
      const message =
        (result.body && result.body.error && result.body.error.message) || "Could not change password.";
      errorBox.textContent = message;
      errorBox.hidden = false;
      return;
    }

    form.reset();
    successBox.textContent = "Password updated successfully.";
    successBox.hidden = false;
  });

  document.addEventListener("vistadmin:ready", function (event) {
    renderAccount(event.detail.me);
  });
})();