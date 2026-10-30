/* ambient_floaters.js — from sparkle-that-page (wan-huiyan/cutify-this), "fairydust" variant, adapted for Pomodo:
 * - ten dots in the board's colours; the layer sits behind the cards (z-index -1), so dust never crosses the text;
 * - start()/destroy() instead of running on load; the other variants are dropped.
 * Off with reduced motion. */
(function () {
  "use strict";
  var COLOURS = ["#f6c453", "#e57c68", "#f6a593", "#8cc07f", "#fff3c4"];
  var COUNT = 10;
  var layer = null, items = [], raf = 0;

  function reset(p, top) {
    p.x = Math.random() * innerWidth; p.y = top ? -24 : Math.random() * innerHeight;
    p.vy = 0.2 + Math.random() * 0.5; p.vx = (Math.random() - 0.5) * 0.4; p.sway = Math.random() * 6.28;
    p.el.style.fontSize = (10 + Math.random() * 10) + "px";
    p.el.style.opacity = String(0.35 + Math.random() * 0.45);
  }
  function tick() {
    for (var i = 0; i < items.length; i++) {
      var p = items[i]; p.sway += 0.02; p.y += p.vy; p.x += p.vx + Math.sin(p.sway) * 0.4;
      if (p.y > innerHeight + 24) reset(p, true);
      p.el.style.transform = "translate(" + p.x + "px," + p.y + "px)";
    }
    raf = requestAnimationFrame(tick);
  }

  function start() {
    if (layer || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    layer = document.createElement("div");
    layer.setAttribute("aria-hidden", "true");
    layer.className = "stp-floaters";
    layer.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:-1;overflow:hidden";
    for (var i = 0; i < COUNT; i++) {
      var el = document.createElement("span"), c = COLOURS[(Math.random() * COLOURS.length) | 0];
      el.textContent = "•";
      el.style.cssText = "position:absolute;left:0;top:0;line-height:1;will-change:transform;color:" + c + ";text-shadow:0 0 7px " + c;
      layer.appendChild(el);
      var p = { el: el }; reset(p, false); items.push(p);
    }
    document.body.appendChild(layer);
    raf = requestAnimationFrame(tick);
  }
  function destroy() {
    if (!layer) return;
    cancelAnimationFrame(raf); raf = 0;
    layer.remove(); layer = null; items = [];
  }

  window.SparkleThatPage = window.SparkleThatPage || {};
  window.SparkleThatPage.ambientFloaters = { start: start, destroy: destroy };
})();
