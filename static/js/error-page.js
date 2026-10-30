(function () {
  "use strict";
  // "Go back" on the Page not found / Something went wrong screens: back if there is somewhere to go, else the portal.
  var b = document.getElementById("err-back");
  if (!b) return;
  if (window.history.length < 2) { b.hidden = true; return; }
  b.addEventListener("click", function () { window.history.back(); });
})();