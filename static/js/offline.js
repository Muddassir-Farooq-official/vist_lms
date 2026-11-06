(function () {
  "use strict";
  // The offline page: says whether the phone is offline or the portal is not answering, and reopens the page by itself.
  var title = document.getElementById("off-title"), text = document.getElementById("off-text"), btn = document.getElementById("off-retry");
  function show() {
    if (navigator.onLine) {
      title.textContent = "The portal is not answering";
      text.textContent = "Your connection works, but the portal did not reply. It may be updating; try again in a minute.";
    }
  }
  show();
  btn.addEventListener("click", function () { btn.disabled = true; btn.textContent = "Trying…"; location.reload(); });
  window.addEventListener("online", function () { location.reload(); });
  // while the portal is not answering, check again every 30 seconds
  setInterval(function () { if (navigator.onLine) location.reload(); }, 30000);
})();