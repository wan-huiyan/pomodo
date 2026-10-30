/* click_confetti.js — from sparkle-that-page (wan-huiyan/cutify-this), "star" variant, adapted for Pomodo:
 * - stars in the board's colours, only on a click of an element marked data-burst (the photos and the hello),
 *   plus burst(x, y) for magic.js to fire when you claim an issue (It's mine!); the other variants are dropped;
 * - start()/destroy() instead of running on load.
 * Canvas overlay under the nav and the toast (z-index 46), pointer-events:none. Off with reduced motion. */
(function () {
  "use strict";
  var COLOURS = ["#f6c453", "#e57c68", "#f6a593", "#8cc07f", "#d0604d"];
  var cv = null, ctx, parts = [], raf = 0;

  function size() {  // clientWidth leaves out the scrollbar, as the canvas's own box does
    var dpr = Math.min(window.devicePixelRatio || 1, 2), de = document.documentElement;
    cv.width = de.clientWidth * dpr; cv.height = de.clientHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function burst(x, y, n) {
    if (!cv) return;
    n = n || 24;
    for (var i = 0; i < n; i++) {
      var a = Math.random() * Math.PI * 2, sp = 3 + Math.random() * 6;
      parts.push({ x: x, y: y, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp - 3, life: 0, ttl: 50 + Math.random() * 30, c: COLOURS[i % COLOURS.length], s: 5 + Math.random() * 5, r: Math.random() * 6.28, vr: (Math.random() - 0.5) * 0.3 });
    }
    if (!raf) raf = requestAnimationFrame(tick);
  }
  function tick() {
    ctx.clearRect(0, 0, cv.width, cv.height);
    for (var i = parts.length - 1; i >= 0; i--) {
      var p = parts[i]; p.life++; p.x += p.vx; p.y += p.vy; p.vy += 0.18; p.vx *= 0.99; p.r += p.vr;
      var t = p.life / p.ttl; ctx.save(); ctx.globalAlpha = Math.max(0, 1 - t); ctx.translate(p.x, p.y); ctx.rotate(p.r); ctx.fillStyle = p.c;
      ctx.font = p.s * 2 + "px serif"; ctx.textAlign = "center"; ctx.fillText("★", 0, 0);
      ctx.restore();
      if (p.life >= p.ttl) parts.splice(i, 1);
    }
    raf = parts.length ? requestAnimationFrame(tick) : 0;
  }
  function onClick(e) {
    if (e.detail === 0) return; // keyboard: no burst
    if (!(e.target.closest && e.target.closest("[data-burst]"))) return;
    burst(e.clientX, e.clientY);
  }

  function start() {
    if (cv || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    cv = document.createElement("canvas"); ctx = cv.getContext("2d");
    cv.setAttribute("aria-hidden", "true");
    cv.style.cssText = "position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:46";
    size(); addEventListener("resize", size, { passive: true });
    document.body.appendChild(cv);
    addEventListener("click", onClick, { passive: true });
  }
  function destroy() {
    if (!cv) return;
    removeEventListener("click", onClick); removeEventListener("resize", size);
    cancelAnimationFrame(raf); raf = 0; parts = []; cv.remove(); cv = null;
  }

  window.SparkleThatPage = window.SparkleThatPage || {};
  window.SparkleThatPage.clickConfetti = { start: start, destroy: destroy, burst: burst };
})();
