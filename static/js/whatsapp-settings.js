/* SMS & WhatsApp page -> WhatsApp settings (/api/v1/sms/whatsapp). Secrets are never shown: an empty box keeps the saved one. */
(function () {
  "use strict";
  const VA = window.VistAdmin;
  const $ = function (id) { return document.getElementById(id); };
  const API = "/api/v1/sms/whatsapp";
  const STATE = { OFF: ["Off", "cancelled"], META: ["On: WhatsApp Cloud API", "active"], LINK: ["On: provider web link", "active"] };

  function err(r, f) { return (r.body && r.body.error && r.body.error.message) || f; }
  function showMode() {
    const m = $("wa-mode").value;
    document.querySelectorAll(".wa-fields").forEach(function (el) { el.hidden = el.getAttribute("data-mode") !== m; });
    $("wa-body-l").hidden = $("wa-method").value !== "POST";
  }

  function fill(d) {
    const st = d.mode !== "OFF" && !d.ready ? ["Not complete", "pending"] : (STATE[d.mode] || STATE.OFF);
    $("wa-state").textContent = st[0];
    $("wa-state").className = "status-pill " + st[1];
    $("wa-mode").value = d.mode;
    $("wa-docs").checked = !!d.send_documents;
    $("wa-pnid").value = d.meta_phone_number_id || "";
    $("wa-tpl").value = d.meta_template_text || "";
    $("wa-tpl-doc").value = d.meta_template_document || "";
    $("wa-lang").value = d.meta_template_language || "en";
    $("wa-url").value = d.link_url || "";
    $("wa-method").value = d.link_method || "GET";
    $("wa-body").value = d.link_body || "";
    $("wa-sender").value = d.link_sender || "";
    $("wa-marker").value = d.link_success_marker || "";
    $("wa-token").value = ""; $("wa-key").value = "";
    $("wa-token").placeholder = d.has_meta_token ? "Saved — type only to change it" : "";
    $("wa-key").placeholder = d.has_link_api_key ? "Saved — type only to change it" : "";
    $("wa-token-hint").textContent = d.meta_token_unreadable ? "The saved token can no longer be read: enter it again." : "";
    $("wa-key-hint").textContent = "";
    $("wa-docs-note").hidden = !!d.documents_address || d.mode === "OFF";
    $("wa-docs-note").textContent = "Documents need the portal's public web address. Save these settings while using the portal on its https address, and documents will be linked to it.";
    if (d.last_test_at) {
      $("wa-test-result").hidden = false;
      $("wa-test-result").textContent = d.last_test_ok ? "Last test: accepted by WhatsApp." : "Last test failed: " + (d.last_test_error || "");
    }
    showMode();
  }

  async function load() {
    const r = await VA.fetchJson(API);
    if (r.ok) fill(r.body);
  }

  document.addEventListener("vistadmin:ready", function () {
    $("wa-mode").addEventListener("change", showMode);
    $("wa-method").addEventListener("change", showMode);
    $("wa-form").addEventListener("submit", async function (e) {
      e.preventDefault();
      const body = { mode: $("wa-mode").value, send_documents: $("wa-docs").checked, meta_phone_number_id: $("wa-pnid").value,
        meta_template_text: $("wa-tpl").value, meta_template_document: $("wa-tpl-doc").value, meta_template_language: $("wa-lang").value || "en",
        link_url: $("wa-url").value, link_method: $("wa-method").value, link_body: $("wa-body").value, link_sender: $("wa-sender").value,
        link_success_marker: $("wa-marker").value };
      if ($("wa-token").value.trim()) body.meta_token = $("wa-token").value.trim();
      if ($("wa-key").value.trim()) body.link_api_key = $("wa-key").value.trim();
      $("wa-save").disabled = true;
      const r = await VA.fetchJson(API, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      $("wa-save").disabled = false;
      if (!r.ok) { $("wa-error").textContent = err(r, "Could not save."); $("wa-error").hidden = false; return; }
      $("wa-error").hidden = true;
      fill(r.body);
      VA.toast(r.body.mode === "OFF" ? "Saved. WhatsApp is off: messages go by SMS." : "Saved. Messages now go by WhatsApp first.", "success");
    });
    $("wa-test").addEventListener("click", async function () {
      const number = $("wa-test-number").value.trim();
      if (!number) { $("wa-test-number").focus(); return; }
      $("wa-test").disabled = true;
      const r = await VA.fetchJson(API + "/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ number: number }) });
      $("wa-test").disabled = false;
      $("wa-test-result").hidden = false;
      $("wa-test-result").textContent = !r.ok ? err(r, "Could not send.") : r.body.ok ? r.body.detail : "WhatsApp did not take it: " + r.body.detail;
    });
    load();
  });
})();
