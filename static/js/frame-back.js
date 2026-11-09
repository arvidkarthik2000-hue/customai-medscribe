/* Pages opened from a link inside a partner's frame (wallboard overlay) have no browser
   Back button around them, so show one. ES5: the PI page loads nothing else. */
(function () {
  var framed;
  try { framed = window.top !== window.self; } catch (e) { framed = true; }
  if (!framed || window.history.length < 2) return;
  function add() {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "btn frame-back";
    b.appendChild(document.createTextNode("← Back"));
    b.onclick = function () { window.history.back(); };
    document.body.insertBefore(b, document.body.firstChild);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", add);
  else add();
})();
