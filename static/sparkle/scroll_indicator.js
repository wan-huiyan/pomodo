/* scroll_indicator.js — from sparkle-that-page (wan-huiyan/cutify-this), "mascot" variant, adapted for Pomodo:
 * - the climber is Pomo, rendered by the page into .stp-mascot (so it follows light and dark mode),
 *   and it cheers (class is-end) once you reach the bottom;
 * - it moves with transform, not top, and start()/destroy() let magic.js switch it.
 * Hidden on narrow screens by sparkle.css, where there is no margin for it to climb. Off with reduced motion. */
(function () {
  "use strict";
  var el = null, raf = 0;

  function pct() { var h = document.documentElement.scrollHeight - innerHeight; return h <= 0 ? 0 : Math.min(1, Math.max(0, scrollY / h)); }
  function render() {
    raf = 0;
    if (!el) return;
    var p = pct();
    el.style.transform = "translateY(" + Math.round(p * (innerHeight - el.offsetHeight - 16)) + "px)";
    el.classList.toggle("is-end", p > 0.98);
  }
  function onScroll() { if (!raf) raf = requestAnimationFrame(render); }

  function start() {
    if (el || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    el = document.querySelector(".stp-mascot");
    if (!el) return;
    el.hidden = false;
    addEventListener("scroll", onScroll, { passive: true });
    addEventListener("resize", onScroll, { passive: true });
    render();
  }
  function destroy() {
    if (!el) return;
    removeEventListener("scroll", onScroll); removeEventListener("resize", onScroll);
    cancelAnimationFrame(raf); raf = 0;
    el.hidden = true; el = null;
  }

  window.SparkleThatPage = window.SparkleThatPage || {};
  window.SparkleThatPage.scrollIndicator = { start: start, destroy: destroy };
})();
