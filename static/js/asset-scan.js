(function () {
  "use strict";
  // A scanned asset label opens /dashboard/assets/tag#tag=VIST-IT-0001: send the person to the fixed-asset page of their portal.
  fetch("/api/v1/auth/me", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (me) {
    const roles = (me && me.roles) || [];
    const portal = roles.indexOf("ADMIN") !== -1 ? "admin" : roles.indexOf("ACCOUNTS_OFFICER") !== -1 ? "accounts" : roles.indexOf("PRINCIPAL") !== -1 ? "principal" : null;
    location.replace(portal ? "/dashboard/" + portal + "/assets" + location.hash : "/?reauth=1");
  }).catch(function () { location.replace("/?reauth=1"); });
})();
