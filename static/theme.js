/* theme.js — the sky switch: light (sun) or dark (moon and stars).
 * Loads in <head> without defer, so a stored choice is on <html data-theme> before the page paints.
 * With no choice stored, board.css follows the computer's setting, and so does the switch.
 * The choice is kept in this browser only (localStorage "pomodo:theme"). */
(function () {
  "use strict";
  var KEY = "pomodo:theme", root = document.documentElement;
  var system = matchMedia("(prefers-color-scheme: dark)");
  try {
    var saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") root.setAttribute("data-theme", saved);
  } catch (e) { /* storage blocked: follow the computer */ }

  function isDark() {
    var t = root.getAttribute("data-theme");
    return t ? t === "dark" : system.matches;
  }
  function sync() {
    document.querySelectorAll(".sky-toggle").forEach(function (b) {
      b.hidden = false;
      b.setAttribute("aria-checked", String(isDark()));
    });
  }

  document.addEventListener("click", function (e) {
    if (!e.target.closest || !e.target.closest(".sky-toggle")) return;
    var t = isDark() ? "light" : "dark";
    root.setAttribute("data-theme", t);
    try { localStorage.setItem(KEY, t); } catch (err) { /* this visit only */ }
    sync();
  });
  if (system.addEventListener) system.addEventListener("change", sync); else if (system.addListener) system.addListener(sync);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", sync); else sync();
})();
