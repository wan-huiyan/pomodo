/* tab_title_peekaboo.js — from sparkle-that-page (wan-huiyan/cutify-this), adapted for Pomodo:
 * the tab title changes while you are on another tab and comes back when you return.
 * The favicon is left alone. start()/destroy() let magic.js switch it. */
(function () {
  "use strict";
  var AWAY = "🍅 come back soon!";
  var on = false, origTitle = "";

  function onVis() { document.title = document.hidden ? AWAY : origTitle; }

  function start() {
    if (on) return;
    on = true;
    origTitle = document.title;
    document.addEventListener("visibilitychange", onVis);
  }
  function destroy() {
    if (!on) return;
    on = false;
    document.removeEventListener("visibilitychange", onVis);
    document.title = origTitle;
  }

  window.SparkleThatPage = window.SparkleThatPage || {};
  window.SparkleThatPage.tabPeekaboo = { start: start, destroy: destroy };
})();
