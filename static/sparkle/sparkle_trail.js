/* sparkle_trail.js — from sparkle-that-page (wan-huiyan/cutify-this), "fairydust" variant, adapted for Pomodo:
 * - the board's own colours (gold, terracotta, cheek pink, leaf green, cream); the other variants are dropped;
 * - the trail follows a fine pointer only, and a mouse click or tap puffs dust (not in text fields);
 * - the animation loop stops when no dust is moving, and start()/destroy() let magic.js switch it.
 * Sits under the nav and the toast (z-index 45). Off with reduced motion. */
(function () {
  "use strict";
  var COLOURS = ["#f6c453", "#e57c68", "#f6a593", "#8cc07f", "#fff3c4"];
  var MAX = 80;
  var layer = null, pool = [], live = [], raf = 0, mx = 0, my = 0, moved = false, lastMove = 0;

  function spawn(x, y, n, spread) {
    for (var k = 0; k < n && pool.length; k++) {
      var el = pool.pop(), c = COLOURS[(Math.random() * COLOURS.length) | 0];
      el.style.color = c;
      el.style.textShadow = "0 0 6px " + c;
      var ang = Math.random() * Math.PI * 2, dist = Math.random() * spread;
      live.push({ el: el, x: x, y: y, vx: Math.cos(ang) * dist, vy: Math.sin(ang) * dist - 0.4, life: 0, ttl: 36 + Math.random() * 20 });
      el.style.opacity = "1";
    }
    if (!raf) raf = requestAnimationFrame(tick);
  }
  function onMove(e) { mx = e.clientX; my = e.clientY; moved = true; if (!raf) raf = requestAnimationFrame(tick); }
  function onClick(e) {
    if (e.detail === 0) return; // a keyboard "click" has no position
    if (e.target.closest && e.target.closest("input, textarea, select")) return;
    spawn(e.clientX, e.clientY, 22, 7);
  }
  function tick() {
    raf = 0;
    if (moved && performance.now() - lastMove > 16) { spawn(mx, my, 2, 2); lastMove = performance.now(); moved = false; }
    for (var i = live.length - 1; i >= 0; i--) {
      var p = live[i]; p.life++; p.x += p.vx; p.y += p.vy; p.vy += 0.12; p.vx *= 0.96;
      var t = p.life / p.ttl;
      p.el.style.transform = "translate(" + p.x + "px," + p.y + "px) scale(" + (1 - t) + ")";
      p.el.style.opacity = String(1 - t);
      if (p.life >= p.ttl) { p.el.style.opacity = "0"; pool.push(p.el); live.splice(i, 1); }
    }
    if ((live.length || moved) && !raf) raf = requestAnimationFrame(tick);  // rest when nothing moves
  }

  function start() {
    if (layer || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    layer = document.createElement("div");
    layer.setAttribute("aria-hidden", "true");
    layer.className = "stp-trail";
    layer.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:45;overflow:hidden";
    for (var i = 0; i < MAX; i++) {
      var s = document.createElement("span");
      s.textContent = "•";
      s.style.cssText = "position:absolute;left:0;top:0;font-size:13px;line-height:1;opacity:0;transform:translate(-50%,-50%)";
      layer.appendChild(s); pool.push(s);
    }
    document.body.appendChild(layer);
    if (matchMedia("(pointer: fine)").matches) addEventListener("mousemove", onMove, { passive: true });
    addEventListener("click", onClick, { passive: true });
  }
  function destroy() {
    if (!layer) return;
    cancelAnimationFrame(raf); raf = 0;
    removeEventListener("mousemove", onMove); removeEventListener("click", onClick);
    layer.remove(); layer = null; pool = []; live = []; moved = false;
  }

  window.SparkleThatPage = window.SparkleThatPage || {};
  window.SparkleThatPage.sparkleTrail = { start: start, destroy: destroy };
})();
