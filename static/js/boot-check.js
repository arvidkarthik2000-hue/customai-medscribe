/* If this browser cannot start the page (too old for the app's scripts, or scripts blocked),
   say so on screen instead of showing a page that does nothing. ES5 on purpose: it has to run
   in browsers the rest of the app does not support. The app sets window.__msBoot once its
   scripts have loaded. */
(function () {
  var firstError = "";
  window.addEventListener("error", function (e) {
    if (!firstError && e && e.message) firstError = String(e.message).slice(0, 160);
  }, true);

  function show() {
    if (window.__msBoot || document.getElementById("boot-fail")) return;
    var bar = document.createElement("div");
    bar.id = "boot-fail";
    bar.className = "boot-fail";
    bar.setAttribute("role", "alert");
    var p = document.createElement("p");
    p.appendChild(document.createTextNode("This browser could not start the demo" + (firstError ? " (" + firstError + ")" : "") + ". "));
    var a = document.createElement("a");
    a.href = "/check/";
    a.appendChild(document.createTextNode("Run the screen check"));
    p.appendChild(a);
    p.appendChild(document.createTextNode(" to see what this browser is missing. The demo needs a current Chrome, Edge, Safari or Firefox."));
    bar.appendChild(p);
    document.body.insertBefore(bar, document.body.firstChild);
  }

  function arm() { window.setTimeout(show, 6000); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", arm);
  else arm();
})();
