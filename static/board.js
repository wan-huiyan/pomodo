/* Pomodo board: small enhancements on a page that is complete without JavaScript.
   - label filter chips for "Up for grabs" (without JS every row shows and the chips stay hidden)
   - nav state and a pointer spotlight on cards
   - GSAP motion, only when GSAP loaded and the reader has not asked for reduced motion.
     Content is never hidden up front: a reveal hides an element only when it first comes into view,
     so if that never happens (print, headless screenshots) everything stays visible. */
(function () {
  "use strict";

  var doc = document;
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var fineHover = window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  function all(sel, root) { return Array.prototype.slice.call((root || doc).querySelectorAll(sel)); }

  /* ---------- filter chips ---------- */
  function initChips() {
    var chips = doc.querySelector("#grabs .chips");  // the Done section has its own chips
    if (!chips) return;
    var rows = all("#grabs .rows--compact > li");
    var shown = doc.querySelector("[data-shown]");
    var labelsOf = rows.map(function (li) {
      var item = li.querySelector(".item");
      try { return JSON.parse(item.getAttribute("data-labels") || "[]"); } catch (e) { return []; }
    });
    chips.hidden = false;
    chips.addEventListener("click", function (e) {
      var btn = e.target.closest(".chip");
      if (!btn) return;
      var want = btn.getAttribute("data-filter");
      all(".chip", chips).forEach(function (c) { c.setAttribute("aria-pressed", String(c === btn)); });
      var n = 0;
      rows.forEach(function (li, k) {
        var ok = !want || labelsOf[k].indexOf(want) !== -1;
        li.hidden = !ok;
        if (ok) n += 1;
      });
      if (shown) shown.textContent = String(n);
      all("#grabs .grab-group").forEach(function (g) { g.hidden = !g.querySelector(".rows--compact > li:not([hidden])"); });
    });
  }

  /* ---------- the toast that It's mine! and Checked in the app report in ---------- */
  var toastTimer;
  function say(msg, bad) {
    var toast = doc.querySelector(".toast");
    if (!toast) return;
    toast.textContent = msg;
    toast.classList.toggle("toast--bad", !!bad);
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.hidden = true; }, bad ? 7000 : 4000);
  }

  // The GitHub read this page shows (see initLive). A change made here adopts the board's new one, so the page
  // never offers to show the reader their own click.
  var version = null, clicks = 0;

  // POST JSON to the board; resolves with the reply, or fails with the server's own words.
  function post(url, body) {
    clicks += 1;  // counted when sent and again when answered, so a poll out at any point in between is ignored
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    }).then(function (r) {
      clicks += 1;
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (d && d.version) version = d.version;
        if (!r.ok) { var err = new Error(d.error || "HTTP " + r.status); err.reply = d; throw err; }
        return d;
      });
    });
  }

  // A failed fetch here usually means the sign-in expired and Google redirected the request.
  function whyFailed(e) {
    return e instanceof TypeError ? "your sign-in may have expired, so reload the page and try again." : e.message;
  }

  /* ---------- It's mine!: assign yourself on GitHub ---------- */
  function initTake() {
    var buttons = all(".take");
    if (!buttons.length || !window.fetch) return;
    function show(btn, on) {
      btn.setAttribute("data-on", String(on));  // the CSS swaps "It's mine!" and "All mine" on this
      btn.setAttribute("aria-label", (on ? "All mine. Give back #" : "It's mine! Assign me #") + btn.getAttribute("data-number"));
      btn.title = on ? "Assigned to you on GitHub. Click to give it back" : "Assign yourself on GitHub";
      // Keep the card's "Assigned to ..." line in step with the button.
      var item = btn.closest(".item"), line = item && item.querySelector(".meta-who");
      if (!line) return;
      var me = btn.getAttribute("data-me"), names = [];
      try { names = JSON.parse(line.getAttribute("data-names") || "[]"); } catch (e) { /* rebuild from nothing */ }
      names = names.filter(function (x) { return x !== me; });
      if (on) names.push(me);
      line.setAttribute("data-names", JSON.stringify(names));
      line.textContent = names.length ? "Assigned to " + names.join(" and ") : "Not assigned yet";
    }
    all(".take-hint").forEach(function (h) { h.hidden = false; });
    buttons.forEach(function (btn) {
      btn.hidden = false;
      btn.addEventListener("click", function (e) {
        if (btn.classList.contains("is-busy")) return;  // not disabled: that would drop keyboard focus
        var n = btn.getAttribute("data-number");
        var want = btn.getAttribute("data-on") !== "true", byKey = e.detail === 0;
        btn.setAttribute("aria-disabled", "true");
        btn.classList.add("is-busy");
        post("/api/issues/" + n + "/assignee", { assigned: want }).then(function () {
          // An issue can show up in more than one card; keep all its buttons in step.
          var copies = all('.take[data-number="' + n + '"]');
          copies.forEach(function (b) { show(b, want); });
          doc.dispatchEvent(new CustomEvent("pomodo:taken", { detail: { button: btn, on: want, byKey: byKey } }));  // sparkle/magic.js celebrates
          // Cards without an "Assigned to" line (Who has what, Up for grabs) only move on the next read.
          var later = copies.some(function (b) { var it = b.closest(".item"); return !(it && it.querySelector(".meta-who")); });
          say((want ? "#" + n + " is yours on GitHub." : "You gave #" + n + " back on GitHub.") +
              (later ? " Reload to see it move." : ""));
        }).catch(function (e) {
          say("Couldn't change #" + n + ": " + whyFailed(e), true);
        }).then(function () {
          btn.removeAttribute("aria-disabled");
          btn.classList.remove("is-busy");
        });
      });
    });
  }

  /* ---------- Checked in the app: the verified label (data-label), with a comment on GitHub ---------- */
  var NOTES_MAX = 1000;  // main.NOTES_MAX
  function initCheck() {
    var buttons = all(".check");
    if (!buttons.length || !window.fetch) return;
    var open = null;  // the notes box, one at a time: { btn, box }

    function show(btn, on) {
      var ref = btn.getAttribute("data-ref");  // "#120" or "PR #136"
      btn.setAttribute("data-on", String(on));  // the CSS swaps "Checked in the app?" and the badge on this
      if (on) btn.removeAttribute("aria-expanded");  // only the "Checked in the app?" state opens a box
      else btn.setAttribute("aria-expanded", String(!!(open && open.btn === btn)));
      btn.setAttribute("aria-label", (on ? "Checked in the app, untick " : "Mark as checked in the app ") + ref);
      btn.title = on ? "Checked in the running app. Click to untick" : "Did you see this fix work in the running app? Mark it as checked";
    }
    function closeBox(focusBack) {
      if (!open) return;
      if (open.btn.getAttribute("data-on") === "true") open.btn.removeAttribute("aria-expanded");  // ticked meanwhile
      else open.btn.setAttribute("aria-expanded", "false");
      open.box.parentNode.removeChild(open.box);
      if (focusBack) open.btn.focus();
      open = null;
    }
    function send(btn, want, notes) {
      var n = btn.getAttribute("data-number");
      btn.setAttribute("aria-disabled", "true");
      btn.classList.add("is-busy");
      return post("/api/issues/" + n + "/checked", { checked: want, notes: notes }).then(function () {
        all('.check[data-number="' + n + '"]').forEach(function (b) { show(b, want); });
        var ref = btn.getAttribute("data-ref");
        say(want ? ref + " is marked as checked in the app on GitHub." : ref + " is no longer marked as checked.");
        return true;
      }).catch(function (e) {
        // The label landed but the comment didn't: show the label as it is now. The notes stay in the box to copy.
        var landed = e.reply && typeof e.reply.checked === "boolean" ? e.reply.checked : null;
        if (landed !== null) all('.check[data-number="' + n + '"]').forEach(function (b) { show(b, landed); });
        say("Couldn't change " + btn.getAttribute("data-ref") + ": " + whyFailed(e), true);
        return false;
      }).then(function (ok) {
        btn.removeAttribute("aria-disabled");
        btn.classList.remove("is-busy");
        return ok;
      });
    }
    function openBox(btn) {
      closeBox(false);
      var n = btn.getAttribute("data-number");
      var box = doc.createElement("div");
      box.className = "check-form";
      box.setAttribute("role", "group");
      box.setAttribute("aria-label", "Mark " + btn.getAttribute("data-ref") + " as checked in the app");
      var label = doc.createElement("label");
      label.htmlFor = "check-notes-" + n;
      label.textContent = "Where you checked and what you saw (optional)";
      var notes = doc.createElement("textarea");
      notes.id = "check-notes-" + n;
      notes.rows = 3;
      notes.maxLength = NOTES_MAX;
      var note = doc.createElement("p");
      note.className = "check-form-note";
      note.textContent = "Adds the " + btn.getAttribute("data-label") + " label on GitHub, with a comment giving your name, today's date and these notes. " +
        "An @name in the notes notifies that person.";
      var row = doc.createElement("div");
      row.className = "check-form-buttons";
      var save = doc.createElement("button");
      save.type = "button";
      save.className = "check-save";
      save.textContent = "Mark as checked";
      var cancel = doc.createElement("button");
      cancel.type = "button";
      cancel.className = "check-cancel";
      cancel.textContent = "Cancel";
      row.appendChild(save);
      row.appendChild(cancel);
      [label, notes, note, row].forEach(function (el) { box.appendChild(el); });
      // Under its "Fixes #N" line, or under the row's title: never inside the title paragraph or between list items.
      var line = btn.closest(".work-fixed > li");
      if (line) line.appendChild(box);
      else btn.closest(".work-title").insertAdjacentElement("afterend", box);
      open = { btn: btn, box: box };
      btn.setAttribute("aria-expanded", "true");
      notes.focus();
      cancel.addEventListener("click", function () { closeBox(true); });
      box.addEventListener("keydown", function (e) { if (e.key === "Escape") closeBox(true); });
      save.addEventListener("click", function () {
        if (btn.classList.contains("is-busy")) return;
        save.disabled = true;
        send(btn, true, notes.value.trim()).then(function (ok) {
          if (ok && open && open.btn === btn) closeBox(true);  // another issue's box may have opened meanwhile
          else if (!ok) save.disabled = false;  // the notes stay, to try again or copy
        });
      });
    }

    buttons.forEach(function (btn) {
      var badge = btn.previousElementSibling;
      if (badge && badge.classList.contains("tag--checked")) badge.parentNode.removeChild(badge);  // the button is the badge now
      if (btn.getAttribute("data-on") !== "true") btn.setAttribute("aria-expanded", "false");
      btn.hidden = false;
      btn.addEventListener("click", function () {
        if (btn.classList.contains("is-busy")) return;  // not disabled: that would drop keyboard focus
        if (btn.getAttribute("data-on") !== "true") {
          if (open && open.btn === btn) closeBox(true);
          else openBox(btn);
          return;
        }
        var n = btn.getAttribute("data-number");
        if (window.confirm("Untick " + btn.getAttribute("data-ref") + "? This removes the " + btn.getAttribute("data-label") + " label on GitHub and adds a comment saying you unticked it.")) {
          send(btn, false, "");
        }
      });
    });
  }

  /* ---------- Recently done: what's new since your last visit ---------- */
  // "Last visit" is the GitHub read the page showed, not the browser clock: Cloud Run can serve a read a few minutes
  // old, and something closed after that read was never seen. A reload (or Back) in the same tab keeps the marks.
  function initDone() {
    var box = doc.getElementById("done");
    var rows = all(".work-row", box || doc);
    if (!box || !rows.length) return;
    function get(store, key) { try { return window[store].getItem(key); } catch (e) { return null; } }
    function put(store, key, v) { try { window[store].setItem(key, v); return true; } catch (e) { return false; } }
    var stamp = doc.querySelector(".fetched time");
    var shown = stamp ? stamp.getAttribute("datetime") : "";
    var shownMs = Date.parse(shown);
    var nav = window.performance && performance.getEntriesByType ? performance.getEntriesByType("navigation")[0] : null;
    var again = nav && (nav.type === "reload" || nav.type === "back_forward");
    var since = again ? get("sessionStorage", "pomodo:since") : null;
    if (!since) {  // also after a reload of a first visit: compare with the read that first load saw
      since = get("localStorage", "pomodo:seen") || "";
      put("sessionStorage", "pomodo:since", since);
    }
    var seenMs = Date.parse(get("localStorage", "pomodo:seen") || "");
    // An unreadable or older "seen" is replaced; a newer one (another tab saw a later read) is kept.
    var remembers = isFinite(shownMs) && (isFinite(seenMs) && seenMs >= shownMs || put("localStorage", "pomodo:seen", shown));
    var sinceMs = Date.parse(since);
    rows.forEach(function (r) {
      if (isFinite(sinceMs) && Date.parse(r.getAttribute("data-at")) > sinceMs) {
        r.classList.add("is-new");
        r.querySelector(".new-badge").hidden = false;
      }
    });
    var line = box.querySelector("[data-since]");
    var who = "", names = {};
    all(".who-filter button", box).forEach(function (b) { names[b.getAttribute("data-who")] = b.getAttribute("data-name") || b.textContent.trim(); });
    function sayNew() {
      if (!line) return;
      if (!isFinite(sinceMs)) {
        line.hidden = !remembers;  // with storage blocked there will be no next time to promise
        line.textContent = "Nothing is marked yet. From your next visit, anything finished after now is marked New.";
        return;
      }
      line.hidden = false;
      var n = rows.filter(function (r) { return r.classList.contains("is-new") && (!who || r.getAttribute("data-who") === who); }).length;
      var when = new Date(sinceMs).toLocaleString("en-GB", { timeZone: line.getAttribute("data-zone") || undefined, weekday: "short", day: "numeric",
                                                             month: "short", hour: "2-digit", minute: "2-digit" });
      var whose = who ? " by " + names[who] : "";
      var from = Date.parse(line.getAttribute("data-from")), first = line.getAttribute("data-first");
      line.textContent = "";
      if (isFinite(from) && sinceMs < from) {  // longer away than the calendar looks back
        line.textContent = "Everything here is new" + whose + " since you last looked (" + when + ")." + (first ? " The board goes back to " + first + "." : "");
      } else if (n) {
        var b = doc.createElement("b");
        b.textContent = String(n);
        line.appendChild(b);
        line.appendChild(doc.createTextNode(" new" + whose + " since you last looked (" + when + ")."));
      } else {
        line.textContent = "Nothing new" + whose + " since you last looked (" + when + ").";
      }
    }
    // The person chips belong to initCalendar, which says who is picked.
    box.addEventListener("pomodo:who", function (e) { who = e.detail.who; sayNew(); });
    var tools = box.querySelector(".done-tools"), filter = box.querySelector(".who-filter");
    if (filter) filter.hidden = false;
    sayNew();
    if (tools) tools.hidden = false;
    if (line) line.setAttribute("aria-live", "polite");  // after the first sentence, so only changes are read out
  }

  /* ---------- Recently done: the month calendar, the day timeline, and whose work to show ---------- */
  // The calendar has one grid per month (only one shown) and one timeline per day (only one shown). This owns the
  // person chips: it hides the other person's rows, swaps the per-person texts, recolours the days, and tells
  // initDone who is picked (the "pomodo:who" event) so the since line follows.
  function initCalendar() {
    var box = doc.getElementById("done");
    var card = box && box.querySelector(".cal-card");
    if (!card) return;
    var months = all(".cal-month-wrap", card), days = all("button.cal-day", card), panels = all(".tl", box);
    var heads = all("[data-month-variant]", box), empty = box.querySelector(".who-empty");
    var chips = all(".who-filter button", box), tip = card.querySelector(".cal-tip");
    var byDate = {}, names = {}, who = "";
    days.forEach(function (b) { byDate[b.getAttribute("data-date")] = b; });
    chips.forEach(function (c) { names[c.getAttribute("data-who")] = c.getAttribute("data-name") || c.textContent.trim(); });
    var said = doc.createElement("p");  // read out when a day is picked
    said.className = "sr-only";
    said.setAttribute("aria-live", "polite");
    card.appendChild(said);

    function panel(date) { return doc.getElementById("tl-" + date); }
    function key(w) { return w || "all"; }
    function monthOf(b) { return b.closest(".cal-month-wrap").getAttribute("data-month"); }
    function rove(t) { days.forEach(function (x) { x.tabIndex = x === t ? 0 : -1; }); }

    // Rows finished close together sit close; a pause of an hour or more says how long it was. Newest is on top,
    // so the row under a pause was finished that much earlier.
    function spaceOut(list) {
      all(".tl-gap", list).forEach(function (g) { g.remove(); });
      var rows = all(".tl-row", list).filter(function (r) { return !r.hidden; });
      all(".tl-row", list).forEach(function (r) { r.classList.toggle("is-top", r === rows[0]); });
      for (var k = 1; k < rows.length; k++) {
        var mins = Math.round((Date.parse(rows[k - 1].getAttribute("data-at")) - Date.parse(rows[k].getAttribute("data-at"))) / 60000);
        if (mins < 10) continue;
        var gap = doc.createElement("li");
        gap.className = "tl-gap" + (rows[k].classList.contains("zone-night") ? " zone-night" : "");
        gap.setAttribute("aria-hidden", "true");
        if (mins >= 60) {
          var h = Math.floor(mins / 60), m = mins % 60;
          gap.classList.add("is-long");
          gap.style.setProperty("--gap", "34px");
          var label = doc.createElement("span");
          label.textContent = h + " h" + (m ? " " + m + " min" : "") + " earlier";
          gap.appendChild(label);
        } else {
          gap.style.setProperty("--gap", Math.round(Math.min(22, Math.max(4, mins * 0.4))) + "px");
        }
        rows[k].parentNode.insertBefore(gap, rows[k]);
      }
    }

    function rise(p) {
      if (!window.gsap || reduce) return;
      var parts = p.querySelectorAll(".tl-head, .tl-row:not([hidden]), .tl-divider, .tl-gap, .tl-foot:not([hidden]), .tl-empty:not([hidden])");
      gsap.fromTo(parts, { y: 14, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.42, stagger: 0.03, ease: "power3.out",
                                                    overwrite: "auto", clearProps: "transform,opacity,visibility" });
    }

    // "Nothing by Kofi in October." when the person picked has no row in the month on show.
    function sayEmpty() {
      if (!empty) return;
      var wrap = months.filter(function (m) { return !m.hidden; })[0];
      var month = wrap.getAttribute("data-month");
      var any = !who || all(".tl-row", box).some(function (r) {
        return r.getAttribute("data-who") === who && r.closest(".tl").getAttribute("data-date").slice(0, 7) === month;
      });
      empty.textContent = "Nothing by " + (names[who] || "this person") + " in " + wrap.querySelector(".cal-month").textContent.split(" ")[0] + ".";
      empty.hidden = any;
    }

    // Show one month's grid and headline. The tab stop goes to the day picked if it is in this month, else the first day.
    function showMonth(month) {
      months.forEach(function (m) { m.hidden = m.getAttribute("data-month") !== month; });
      heads.forEach(function (h) { h.hidden = h.getAttribute("data-month-variant") !== month; });
      tip.hidden = true;
      var here = days.filter(function (b) { return monthOf(b) === month; });
      rove(here.filter(function (b) { return b.getAttribute("aria-pressed") === "true"; })[0] || here[0]);
      sayEmpty();
    }

    function select(date, focus) {
      var b = byDate[date], p = panel(date);
      if (!b || !p) return;
      showMonth(monthOf(b));
      days.forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
      rove(b);
      panels.forEach(function (x) { x.hidden = x !== p; });
      if (focus) b.focus();
      said.textContent = "Showing " + b.getAttribute("data-label-" + key(who));
      rise(p);
      var top = p.getBoundingClientRect().top;
      if (top < 80) window.scrollTo({ top: window.scrollY + top - 100, behavior: reduce ? "auto" : "smooth" });
    }

    days.forEach(function (b) {
      b.addEventListener("click", function () { select(b.getAttribute("data-date")); });
      b.addEventListener("keydown", function (e) {
        var step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
        var mine = days.filter(function (x) { return monthOf(x) === monthOf(b); }), t = null;
        if (e.key === "Home") t = mine[0];
        else if (e.key === "End") t = mine[mine.length - 1];
        else if (step) {  // move by calendar days, skipping days that cannot be picked; at the edge, stay
          var d = new Date(b.getAttribute("data-date") + "T12:00:00Z");
          for (var n = 0; n < 40 && !t; n++) {
            d.setUTCDate(d.getUTCDate() + step);
            var iso = d.toISOString().slice(0, 10);
            if (byDate[iso]) t = byDate[iso];
            else if (iso < days[0].getAttribute("data-date") || iso > days[days.length - 1].getAttribute("data-date")) break;
            else if (Math.abs(step) === 7) break;
          }
        }
        if (!t) return;
        e.preventDefault();
        if (monthOf(t) !== monthOf(b)) showMonth(monthOf(t));  // the month follows the focus; focus never lands in a hidden one
        rove(t);
        t.focus();
      });
      function tipOn() {
        tip.textContent = b.getAttribute("data-label-" + key(who));
        tip.hidden = false;
        var half = tip.offsetWidth / 2, x = b.offsetLeft + b.offsetWidth / 2;  // centred on the day, kept inside the card
        tip.style.left = Math.max(half + 6, Math.min(card.clientWidth - half - 6, x)) + "px";
        tip.style.top = b.offsetTop + "px";
      }
      b.addEventListener("pointerenter", tipOn);
      b.addEventListener("focus", tipOn);
      b.addEventListener("pointerleave", function () { tip.hidden = true; });
      b.addEventListener("blur", function () { tip.hidden = true; });
    });

    all(".cal-arrow", card).forEach(function (a) {
      a.addEventListener("click", function () {
        var step = parseInt(a.getAttribute("data-step"), 10), at = months.indexOf(a.closest(".cal-month-wrap"));
        var to = months[at + step];
        if (!to) return;
        showMonth(to.getAttribute("data-month"));
        // the arrow pressed has just gone out of sight with its month: keep the focus on the same arrow in the new one
        var again = all(".cal-arrow", to).filter(function (x) { return x.getAttribute("data-step") === a.getAttribute("data-step"); })[0];
        (again && !again.disabled ? again : all(".cal-arrow:not(:disabled)", to)[0]).focus();
      });
    });

    all("[data-go]", box).forEach(function (g) {
      g.addEventListener("click", function () { select(g.getAttribute("data-go"), true); });
    });

    // A day gets a dot when the timeline it opens has a New row the filter shows.
    function markNew() {
      days.forEach(function (b) {
        var p = panel(b.getAttribute("data-date"));
        b.querySelector(".cal-new").hidden = !(p && all(".tl-row", p).some(function (r) { return !r.hidden && r.querySelector(".work-row.is-new"); }));
      });
    }

    function recolour() {
      var k = key(who);
      days.forEach(function (b) {
        var n = b.getAttribute("data-n-" + k);
        b.className = b.className.replace(/\blvl-\d\b/, "lvl-" + b.getAttribute("data-level-" + k));
        b.querySelector(".cal-n").textContent = n === "0" ? "" : n;
        b.setAttribute("aria-label", b.getAttribute("data-label-" + k));
        var mark = b.querySelector(".cal-mark");
        if (mark) mark.style.display = (b.classList.contains("is-weekend") ? n !== "0" : b.getAttribute("data-late-" + k) !== "0") ? "" : "none";
      });
      panels.forEach(function (p) {
        all("[data-who-variant]", p).forEach(function (v) { v.hidden = v.getAttribute("data-who-variant") !== who; });
        all(".tl-row", p).forEach(function (r) { r.hidden = !!who && r.getAttribute("data-who") !== who; });
        var list = p.querySelector(".tl-list");
        if (list) {
          list.hidden = !all(".tl-row", list).some(function (r) { return !r.hidden; });
          spaceOut(list);
        }
      });
      markNew();
      sayEmpty();
      var on = panels.filter(function (p) { return !p.hidden; })[0];
      if (on) rise(on);
    }

    chips.forEach(function (c) {
      c.addEventListener("click", function () {
        who = c.getAttribute("data-who");
        chips.forEach(function (x) { x.setAttribute("aria-pressed", String(x === c)); });
        recolour();
        box.dispatchEvent(new CustomEvent("pomodo:who", { detail: { who: who } }));
      });
    });

    // A pull request that closed many issues shows two, then "and N more", so the day stays quick to scan.
    all(".tl-row .work-fixed", box).forEach(function (ul) {
      var n = ul.children.length - 2;
      if (n < 2) return;
      ul.classList.add("is-folded");
      var more = doc.createElement("button");
      more.type = "button";
      more.className = "fixes-more";
      more.setAttribute("aria-expanded", "false");
      more.textContent = "and " + n + " more";
      more.addEventListener("click", function () {
        var open = ul.classList.toggle("is-open");
        more.setAttribute("aria-expanded", String(open));
        more.textContent = open ? "show fewer" : "and " + n + " more";
      });
      ul.parentNode.insertBefore(more, ul.nextSibling);
    });
    all(".tl-list", box).forEach(spaceOut);
    markNew();  // after initDone has marked the New rows
  }

  /* ---------- nav: lift on scroll, mark the section in view ---------- */
  function initNav() {
    var nav = doc.querySelector(".nav");
    if (!nav) return;
    var onScroll = function () { nav.classList.toggle("is-scrolled", window.scrollY > 24); };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();

    if (!("IntersectionObserver" in window)) return;
    var links = {};
    all(".nav-links a").forEach(function (a) { links[a.getAttribute("href").slice(1)] = a; });
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        var a = links[en.target.id];
        if (!a || !en.isIntersecting) return;
        all(".nav-links a").forEach(function (x) { x.removeAttribute("aria-current"); });
        a.setAttribute("aria-current", "true");
        var strip = a.parentNode.parentNode;  // the <ul>, which scrolls sideways on a phone
        if (strip.scrollWidth > strip.clientWidth) {
          strip.scrollTo({ left: a.offsetLeft - strip.offsetLeft - 40, behavior: reduce ? "auto" : "smooth" });  // same offsetParent
        }
      });
    }, { rootMargin: "-40% 0px -55% 0px" });
    Object.keys(links).forEach(function (id) {
      var s = doc.getElementById(id);
      if (s) io.observe(s);
    });
  }

  /* ---------- pointer spotlight on cards ---------- */
  function initSpotlight() {
    if (!fineHover) return;
    all(".card").forEach(function (card) {
      card.addEventListener("pointermove", function (e) {
        var r = card.getBoundingClientRect();
        card.style.setProperty("--mx", (e.clientX - r.left) + "px");
        card.style.setProperty("--my", (e.clientY - r.top) + "px");
      });
    });
  }

  /* ---------- GSAP ---------- */
  function initMotion() {
    var gsap = window.gsap, ST = window.ScrollTrigger;
    if (reduce || !gsap || !ST) return;
    gsap.registerPlugin(ST);

    var vh = window.innerHeight;
    var belowFold = function (el) { return el.getBoundingClientRect().top > vh; };
    var done = "transform,opacity,visibility,transition";

    // One-shot effects run the first time an element comes into view. IntersectionObserver follows
    // layout changes on its own, so opening a <details> or filtering rows needs no ScrollTrigger.refresh(),
    // which scrolls the page to the top while it measures (and could leave the reader there).
    function whenSeen(els, bottomMargin, fn) {
      if (!els.length || !("IntersectionObserver" in window)) return;
      var io = new IntersectionObserver(function (entries) {
        var seen = entries.filter(function (e) { return e.isIntersecting; }).map(function (e) { return e.target; });
        seen.forEach(function (el) { io.unobserve(el); });
        if (seen.length) fn(seen);
      }, { rootMargin: "0px 0px " + bottomMargin + " 0px" });
      els.forEach(function (el) { io.observe(el); });
    }

    // Cards and headings rise in as they enter. Already-visible ones are left alone (no flash on load).
    whenSeen(gsap.utils.toArray(".reveal").filter(belowFold), "0px", function (batch) {
      gsap.set(batch, { transition: "none" });
      gsap.fromTo(batch, { autoAlpha: 0, y: 56, scale: 0.96 },
        { autoAlpha: 1, y: 0, scale: 1, duration: 0.95, ease: "power3.out", stagger: 0.09, overwrite: true, clearProps: done });
    });

    // List rows slide in with a short stagger. overwrite "auto": a row that Up for grabs' re-sort glides into view
    // keeps that glide (y) while it fades in (x).
    whenSeen(gsap.utils.toArray(".reveal-row").filter(belowFold), "0px", function (batch) {
      gsap.set(batch, { transition: "none" });
      gsap.fromTo(batch, { autoAlpha: 0, x: -28 },
        { autoAlpha: 1, x: 0, duration: 0.6, ease: "power2.out", stagger: 0.045, overwrite: "auto", clearProps: done });
    });

    // Numbers count up to their real value once, when first seen. The HTML already holds the real value.
    whenSeen(all("[data-count]").filter(function (el) { return parseInt(el.getAttribute("data-count"), 10) > 0; }), "-40px", function (els) {
      els.forEach(function (el) {
        var end = parseInt(el.getAttribute("data-count"), 10), o = { v: 0 };
        gsap.to(o, {
          v: end, duration: 1.3, ease: "power2.out",
          onUpdate: function () { el.textContent = String(Math.round(o.v)); },
          onComplete: function () { el.textContent = String(end); }
        });
      });
    });

    // Progress bars fill from the left.
    whenSeen(all("[data-fill]"), "-40px", function (els) {
      els.forEach(function (el) {
        gsap.fromTo(el.firstElementChild || el, { scaleX: 0 }, { scaleX: 1, duration: 1.5, ease: "power3.out", clearProps: "transform" });
      });
    });

    // Hero mascot drifts up as the hero scrolls away.
    if (doc.querySelector(".hero-art")) {
      gsap.to(".hero-art", {
        yPercent: -30, rotation: -6, ease: "none",
        scrollTrigger: { trigger: ".hero", start: "top top", end: "bottom top", scrub: 0.6 }
      });
    }

    // Buttons lean toward the pointer.
    if (fineHover) {
      all("[data-magnet]").forEach(function (btn) {
        var xTo = gsap.quickTo(btn, "x", { duration: 0.5, ease: "power3" });
        var yTo = gsap.quickTo(btn, "y", { duration: 0.5, ease: "power3" });
        btn.addEventListener("pointermove", function (e) {
          var r = btn.getBoundingClientRect();
          xTo((e.clientX - r.left - r.width / 2) * 0.22);
          yTo((e.clientY - r.top - r.height / 2) * 0.32);
        });
        btn.addEventListener("pointerleave", function () { xTo(0); yTo(0); });
      });
    }

    // Recently done: a burst of pixel confetti the first time it comes into view, and on a tap of Pomo.
    var confetti = doc.querySelector(".confetti");
    if (confetti) {
      var colours = ["var(--accent)", "var(--m-leaf)", "var(--m-cheek)", "var(--sage-ink)", "#e0907c", "var(--m-body)"];
      var burst = function () {
        for (var k = 0; k < 26; k += 1) {
          var p = doc.createElement("i");
          p.style.background = colours[k % colours.length];
          confetti.appendChild(p);
          gsap.timeline({ onComplete: p.remove.bind(p) })
            .fromTo(p, { x: 0, y: 0, rotation: 0, autoAlpha: 1, scale: gsap.utils.random(0.6, 1.3) },
              { x: gsap.utils.random(-170, 170), y: gsap.utils.random(-170, -50), rotation: gsap.utils.random(-200, 200), duration: 0.65, ease: "power2.out" })
            .to(p, { y: "+=" + gsap.utils.random(120, 220), autoAlpha: 0, duration: 0.9, ease: "power1.in" });
        }
      };
      whenSeen(all(".done-head"), "-20%", burst);
      var pomo = doc.querySelector(".done-pomo-wrap");
      if (pomo) pomo.addEventListener("click", burst);
    }

  }

  /* ---------- same-page links glide ---------- */
  // Smooth scrolling is on only while a click on a #link glides. With scroll-behavior: smooth on the whole page,
  // a page opened at #section (the order switch) glided down from the top as it loaded, and ScrollTrigger's
  // measuring at load stopped the glide partway, often at the top. Keyboard clicks (detail 0) jump at once, and
  // so does a click before the page has loaded, which that measuring would stop the same way.
  function initGlide() {
    if (reduce) return;
    var root = doc.documentElement, timer = 0;
    function off() { root.style.scrollBehavior = ""; clearTimeout(timer); }
    doc.addEventListener("click", function (e) {
      var a = e.target.closest && e.target.closest('a[href^="#"]');
      if (!a || a.getAttribute("href") === "#" || e.detail === 0 || doc.readyState !== "complete") return;
      root.style.scrollBehavior = "smooth";
      clearTimeout(timer);
      timer = setTimeout(off, 1500);
      window.addEventListener("scrollend", off, { once: true });
    });
  }

  /* ---------- Up for grabs: re-sort in place ---------- */
  // The two links work without JavaScript (a page load at #grabs). With it, a plain click re-sorts the rows where
  // they are and keeps the page still: a page load painted at the top first, then jumped back to the section.
  function initOrder() {
    var section = doc.getElementById("grabs"), sw = section && section.querySelector(".order-switch");
    if (!sw || !window.history || !history.replaceState) return;
    sw.addEventListener("click", function (e) {
      var a = e.target.closest("a");
      if (!a || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      var order = /[?&]order=newest(&|#|$)/.test(a.getAttribute("href")) ? "newest" : "updated";
      all("a", sw).forEach(function (x) { if (x === a) x.setAttribute("aria-current", "true"); else x.removeAttribute("aria-current"); });
      history.replaceState(history.state, "", a.getAttribute("href"));
      if (section.getAttribute("data-order") === order) return;
      var key = order === "newest" ? "data-created" : "data-updated";
      var rows = all(".rows--compact > li", section);
      var before = rows.map(function (li) { return li.getBoundingClientRect().top; });
      var y = window.scrollY;
      section.style.overflowAnchor = "none";  // scroll anchoring would follow a moved row and scroll the page
      all(".rows--compact", section).forEach(function (ol) {
        all(".rows--compact > li", ol).sort(function (p, q) {  // newest first; ties to the higher number, as board.py
          return (Date.parse(q.getAttribute(key)) - Date.parse(p.getAttribute(key))) ||
            (parseInt(q.getAttribute("data-number"), 10) - parseInt(p.getAttribute("data-number"), 10));
        }).forEach(function (li) { ol.appendChild(li); });
      });
      section.setAttribute("data-order", order);  // the CSS shows "Opened" or "Updated" to match
      var said = section.querySelector("[data-order-said]");  // in the count's live region, for screen readers
      if (said) said.textContent = order === "newest" ? ", most recently opened first" : ", most recently updated first";
      if (window.scrollY !== y) window.scrollTo(0, y);
      section.style.overflowAnchor = "";
      var gsap = window.gsap, vh = window.innerHeight;
      if (!gsap || reduce) return;
      // Rows near the screen glide from where they were to where they are now.
      rows.forEach(function (li, k) {
        var now = li.getBoundingClientRect().top, dy = before[k] - now;
        if (!dy || li.hidden || Math.min(now, before[k]) > vh * 1.5 || Math.max(now, before[k]) < -vh * 0.5) return;
        gsap.fromTo(li, { y: dy, transition: "none" },
          { y: 0, duration: 0.55, ease: "power3.out", overwrite: "auto", clearProps: "transform,transition" });
      });
    });
  }

  /* ---------- Board updated: ask every minute whether GitHub has changed since this page was drawn ---------- */
  function initLive() {
    var box = doc.querySelector(".updated");
    version = doc.body.getAttribute("data-version");
    if (!box || !version || !window.fetch) return;
    var asking = false;
    function check() {
      // Not while the tab is hidden, and not again once the pill is up.
      if (asking || doc.hidden || box.firstChild) return;
      asking = true;
      var at = clicks;  // a change made here while this is out may be newer than its answer: ask again next time
      fetch("/api/version", { credentials: "same-origin", cache: "no-store" }).then(function (r) {
        return r.ok ? r.json() : null;
      }).then(function (d) {
        if (clicks !== at) return;
        if (d && d.version && d.version !== version) offer();
      }).catch(function () {
        // Offline, or the sign-in ran out (the board answers 401): say nothing, ask again later.
      }).then(function () { asking = false; });
    }
    function offer() {
      var btn = doc.createElement("button");
      btn.type = "button";
      btn.className = "updated-btn";
      btn.innerHTML = '<span class="dot" aria-hidden="true"></span>Board updated <span class="updated-go">Show it</span>';
      btn.addEventListener("click", function () {
        btn.disabled = true;
        location.reload();
      });
      box.appendChild(btn);
    }
    setInterval(check, 60000);
    doc.addEventListener("visibilitychange", check);
  }


  function start() {
    // Each enhancement is optional (the page is complete without it), so one failing must not stop the rest.
    [initChips, initOrder, initTake, initCheck, initDone, initCalendar, initNav, initSpotlight, initGlide, initMotion, initLive].forEach(function (init) {
      try { init(); } catch (e) { if (window.console) console.error(e); }
    });
  }

  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", start);
  else start();
})();
