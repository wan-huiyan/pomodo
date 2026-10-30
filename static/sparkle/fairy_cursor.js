/* fairy_cursor.js — from sparkle-that-page (wan-huiyan/cutify-this), adapted for Pomodo:
 * - a "pomodoro" pixel wand: terracotta stick, gold star, dark outline so it shows on cream and on dark;
 * - a glowing copy of it over things you can click (sparkle.css reads --stp-wand and --stp-wand-link);
 * - start()/destroy() instead of running on load, so magic.js can switch it off and on.
 * No-op on touch/coarse pointers and in forced-colours mode (keeps the system's high-contrast pointer).
 * A still image, so reduced motion does not affect it. */
(function () {
  "use strict";
  var root = document.documentElement;

  // 16-cell grid drawn at 32 px. The star's centre (cell 12, 4) is the hotspot: 24 8 in pixels.
  var STICK = '<rect x="1" y="11" width="4" height="4" fill="#2b201b"/><rect x="3" y="9" width="4" height="4" fill="#2b201b"/>' +
    '<rect x="5" y="7" width="4" height="4" fill="#2b201b"/><rect x="7" y="5" width="4" height="4" fill="#2b201b"/>' +
    '<rect x="2" y="12" width="2" height="2" fill="#7a3b31"/><rect x="4" y="10" width="2" height="2" fill="#d0604d"/>' +
    '<rect x="6" y="8" width="2" height="2" fill="#d0604d"/><rect x="8" y="6" width="2" height="2" fill="#e57c68"/>';
  var STAR = '<rect x="10" y="0" width="4" height="8" fill="#2b201b"/><rect x="8" y="2" width="8" height="4" fill="#2b201b"/>' +
    '<rect x="11" y="1" width="2" height="6" fill="#f6c453"/><rect x="9" y="3" width="6" height="2" fill="#f6c453"/>' +
    '<rect x="11" y="3" width="1" height="1" fill="#fff3c4"/>';
  var GLINTS = '<rect x="15" y="0" width="1" height="1" fill="#f6c453"/><rect x="7" y="0" width="1" height="1" fill="#f6a593"/>' +
    '<rect x="15" y="7" width="1" height="1" fill="#8cc07f"/>';
  function svg(inner) {
    return '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 16 16" shape-rendering="crispEdges">' + inner + "</svg>";
  }
  function cursor(markup, fallback) {
    return 'url("data:image/svg+xml,' + encodeURIComponent(svg(markup)) + '") 24 8, ' + fallback;
  }

  var on = false;
  function start() {
    if (on || !matchMedia("(pointer: fine)").matches || matchMedia("(forced-colors: active)").matches) return;
    on = true;
    root.style.setProperty("--stp-wand", cursor(STICK + STAR, "auto"));
    root.style.setProperty("--stp-wand-link", cursor(STICK + STAR + GLINTS, "pointer"));
    root.classList.add("stp-wand");
  }
  function destroy() {
    on = false;
    root.classList.remove("stp-wand");
    root.style.removeProperty("--stp-wand");
    root.style.removeProperty("--stp-wand-link");
  }

  window.SparkleThatPage = window.SparkleThatPage || {};
  window.SparkleThatPage.fairyCursor = { start: start, destroy: destroy };
})();
