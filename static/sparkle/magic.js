/* magic.js — starts the board's playful effects (always on; each one stays still with reduced motion).
 * Loads after the effect files (all deferred).
 * Claiming an issue (board.js sends "pomodo:taken"): stars and pixel hearts fly from the button, the button squashes,
 * your photo stamps in and a Claimed! stamp lands on the row. */
(function () {
  "use strict";
  var S = window.SparkleThatPage || {};
  var EFFECTS = ["fairyCursor", "sparkleTrail", "clickConfetti", "scrollIndicator", "tabPeekaboo", "ambientFloaters"];

  function apply(on) {
    EFFECTS.forEach(function (name) {
      var fx = S[name];
      if (!fx) return;
      try { if (on) fx.start(); else fx.destroy(); } catch (e) { /* one effect failing leaves the rest */ }
    });
  }
  function centre(el) { var r = el.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }
  function burstAt(el) {
    if (!S.clickConfetti) return;
    var c = centre(el);
    S.clickConfetti.burst(c[0], c[1], 28);
  }

  // Photos marked data-boing squash and stretch when the mouse arrives or on a tap; the class stays
  // until the animation ends, so it always finishes instead of snapping back.
  function boing(el) {
    if (el.classList.contains("is-boing")) return;  // let a bounce finish
    el.classList.add("is-boing");
  }
  document.addEventListener("pointerover", function (e) {
    var el = e.pointerType === "mouse" && e.target.closest && e.target.closest("[data-boing]");
    if (el && !el.contains(e.relatedTarget)) boing(el);
  });
  // Also on cancel (reduced motion switched on mid-bounce), so nothing replays later.
  ["animationend", "animationcancel"].forEach(function (type) {
    document.addEventListener(type, function (e) {
      var el = e.target.closest && e.target.closest(".is-boing");
      if (el) el.classList.remove("is-boing");
      var t = e.target;
      if (!t.classList) return;
      if (t.classList.contains("claim-heart") || t.classList.contains("claim-stamp")) { t.remove(); return; }
      t.classList.remove("is-pop", "is-claimed", "is-thud");
    });
  });

  document.addEventListener("click", function (e) {
    var photo = e.target.closest && e.target.closest("[data-boing]");
    if (photo) boing(photo);
  });
  function replay(el, cls) {  // restart a CSS animation that may still be on the element
    el.classList.remove(cls);
    void el.offsetWidth;
    el.classList.add(cls);
  }
  // Each piece is decorative and removes itself when its animation ends; with reduced motion none is made.
  function hearts(btn) {
    var c = centre(btn);
    for (var k = 0; k < 12; k += 1) {
      var h = document.createElement("i"), a = -Math.PI / 2 + (k / 11 - 0.5) * Math.PI * 1.4, d = 58 + (k % 4) * 20;
      h.className = "claim-heart" + (k % 3 ? "" : " claim-heart--pink");
      h.setAttribute("aria-hidden", "true");
      h.style.left = c[0] + "px";
      h.style.top = c[1] + "px";
      h.style.setProperty("--dx", Math.round(Math.cos(a) * d) + "px");
      h.style.setProperty("--dy", Math.round(Math.sin(a) * d) + "px");
      h.style.setProperty("--r", (k % 2 ? 1 : -1) * (12 + k * 5) + "deg");
      h.style.animationDelay = (k % 4) * 35 + "ms";
      document.body.appendChild(h);
    }
  }
  function stamp(btn) {
    var item = btn.closest(".item");
    if (!item) return;
    var old = item.querySelector(".claim-stamp");
    if (old) old.remove();
    var s = document.createElement("span");
    s.className = "claim-stamp";
    s.setAttribute("aria-hidden", "true");
    s.textContent = "Claimed!";
    item.appendChild(s);
    replay(item, "is-thud");
  }
  document.addEventListener("pomodo:taken", function (e) {
    var btn = e.detail && e.detail.button;
    if (!btn || !e.detail.on || e.detail.byKey) return;  // no fireworks for a keyboard press
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    burstAt(btn);
    hearts(btn);
    stamp(btn);
    replay(btn, "is-claimed");
    var av = btn.querySelector(".take-on .av");
    if (av) replay(av, "is-pop");
  });

  apply(true);
  // Reduced motion switched on or off while the page is open: restart, so each effect checks again.
  var motion = matchMedia("(prefers-reduced-motion: reduce)");
  var restart = function () { apply(false); apply(true); };
  if (motion.addEventListener) motion.addEventListener("change", restart); else if (motion.addListener) motion.addListener(restart);
  // the hello photo waves once as the page opens
  var hello = document.querySelector(".hello [data-boing]");
  if (hello) setTimeout(function () { boing(hello); }, 600);
})();
