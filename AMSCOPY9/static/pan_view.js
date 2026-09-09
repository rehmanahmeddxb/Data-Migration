/* AMS Pan & Fit — global table panning for layout.html pages.
   ---------------------------------------------------------------------------
   Goals (yard-PC friendly, fully offline, no dependencies):

   1. DRAG-PAN: press the left mouse button on any empty spot of a wide
      table (anything that is NOT a link / button / input / dropdown …)
      and drag left-right — the table pans sideways exactly like a map.
   2. WHEEL-PAN: hovering a wide table, the mouse wheel pans left-right
      instead of scrolling the page.  When the table reaches its end the
      wheel hands scroll back to the page, so vertical browsing never
      gets trapped.  (Purely-vertical scrollers are never hijacked.)
   3. NO SIDE SCROLL: the horizontal scrollbar is hidden (pan replaces
      it) and, by default, wide tables are auto-zoomed to FIT the screen
      so no horizontal scroll exists at all.  A small "Fit / 1:1" button
      on every wide table toggles between fitted view and actual size;
      the last choice is remembered for the whole app.

   Extras:
     - Alt+drag still allows native text selection inside a table.
     - A drag never fires the click underneath it (row actions, sort
       headers etc. only trigger on a real click, not after a pan).
     - Dynamically loaded tables (modals, fetch refreshes) are picked up
       automatically via a debounced MutationObserver.
   ========================================================================= */

(function () {
    "use strict";

    var SEL = ".table-responsive, .ui-table-wrap, .ams-pan";

    /* Anything inside here must keep its normal mouse behaviour — the
       user asked to pan from "empty space, anywhere except link areas". */
    var INTERACTIVE_SEL = [
        "a", "button", "input", "select", "textarea", "label", "form",
        "summary", "details", "canvas", "svg", "img[draggable]",
        "[contenteditable]", "[contenteditable='true']",
        ".btn", ".dropdown-menu", ".dropdown-toggle", ".form-control",
        ".form-select", ".form-check-input", ".nav", ".pagination",
        "[data-bs-toggle]", "[data-ui-sheet-target]", "[data-bs-dismiss]",
        "[draggable='true']", "[onclick]", "[role='button']"
    ].join(", ");

    var DRAG_THRESHOLD = 6;      /* px of horizontal travel before panning  */
    var MIN_AUTO_FIT = 0.45;     /* never auto-zoom below 45 % (unreadable) */
    var MODE_KEY = "ams_pan_mode";       /* "fit" | "pan"                    */
    var HINT_MS = 5000;          /* hint chip lifetime                     */
    var ZOOM_OK = (typeof CSS !== "undefined") && CSS.supports &&
                  CSS.supports("zoom", "1");

    var hintDismissed = false;   /* after the first pan, stop showing hints */
    var refreshQueued = false;

    /* ------------------------------------------------------------------ */
    /* helpers                                                             */
    /* ------------------------------------------------------------------ */

    function readMode() {
        try {
            var m = localStorage.getItem(MODE_KEY);
            return m === "pan" ? "pan" : "fit";
        } catch (_) { return "fit"; }
    }
    function writeMode(m) {
        try { localStorage.setItem(MODE_KEY, m); } catch (_) {}
    }

    function hOverflow(w) {
        /* 2 px guard against rounding; when the scrollbar is already
           hidden (ams-pan-live) allow a small hysteresis band so a
           borderline table cannot flap between "fits / overflows" on
           every refresh as the scrollbar space is freed/reclaimed. */
        if (w.classList.contains("ams-pan-live")) {
            return w.scrollWidth > w.clientWidth - 10;
        }
        return w.scrollWidth > w.clientWidth + 2;
    }
    function vOverflow(w) {
        return w.scrollHeight > w.clientHeight + 2;
    }

    function tablesIn(w) {
        var out = [], kids = w.children, i;
        for (i = 0; i < kids.length; i++) {
            if (kids[i].tagName === "TABLE") out.push(kids[i]);
        }
        if (!out.length) {
            var t = w.querySelector("table");
            if (t) out.push(t);
        }
        return out;
    }

    /* Effective fit request for a wrapper:
       per-wrapper override ("on"/"off") beats the global mode. */
    function fitWanted(w) {
        var own = w.getAttribute("data-ams-fit");
        if (own === "on") return true;
        if (own === "off") return false;
        return readMode() === "fit";
    }
    function fitForced(w) {
        return w.getAttribute("data-ams-fit") !== null;
    }

    function hideHints() {
        if (hintDismissed) return;
        hintDismissed = true;
        var chips = document.querySelectorAll(".ams-pan-hint");
        Array.prototype.forEach.call(chips, function (c) {
            c.classList.add("ams-hiding");
            window.setTimeout(function () {
                if (c.parentNode) c.parentNode.removeChild(c);
            }, 550);
        });
    }

    function updateEdges(w) {
        var max = w.scrollWidth - w.clientWidth;
        var sl = w.scrollLeft;
        w.classList.toggle("ams-more-left", !w.classList.contains("ams-fitted") && sl > 1);
        w.classList.toggle("ams-more-right", !w.classList.contains("ams-fitted") && sl < max - 1);
    }

    function ensureControls(w, overflowing) {
        /* Fit toggle — only meaningful on wrappers that can overflow. */
        var btn = w.querySelector(".ams-fit-btn");
        if (overflowing && !btn) {
            btn = document.createElement("button");
            btn.type = "button";
            btn.className = "ams-fit-btn";
            btn.addEventListener("click", function (ev) {
                ev.preventDefault();
                ev.stopPropagation();
                var nowFitted = w.classList.contains("ams-fitted");
                w.setAttribute("data-ams-fit", nowFitted ? "off" : "on");
                /* Remember the preference app-wide so other tables follow. */
                writeMode(nowFitted ? "pan" : "fit");
                refreshAll();
            });
            w.appendChild(btn);
        }
        if (btn) {
            var fitted = w.classList.contains("ams-fitted");
            var zoomPct = Math.round(parseFloat(w.getAttribute("data-ams-zoom") || "1") * 100);
            btn.innerHTML = fitted
                ? '<i class="bi bi-arrows-angle-expand"></i>1:1'
                : '<i class="bi bi-arrows-angle-contract"></i>Fit';
            btn.title = fitted
                ? "Fitted at " + zoomPct + "% — click for actual size, then drag or use the wheel to pan"
                : "Fit table to screen (no side scroll)";
            btn.setAttribute("aria-pressed", fitted ? "true" : "false");
        }

        /* One-time hint chip. */
        var hint = w.querySelector(".ams-pan-hint");
        if (overflowing && !w.classList.contains("ams-fitted") &&
            !hint && !hintDismissed && !w.getAttribute("data-ams-hinted")) {
            hint = document.createElement("span");
            hint.className = "ams-pan-hint";
            hint.innerHTML = '<i class="bi bi-arrows-expand"></i>' +
                "Drag anywhere or scroll wheel to pan";
            w.appendChild(hint);
            w.setAttribute("data-ams-hinted", "1");
            window.setTimeout(function () {
                if (hint && hint.parentNode && !hint.classList.contains("ams-hiding")) {
                    hint.classList.add("ams-hiding");
                    window.setTimeout(function () {
                        if (hint.parentNode) hint.parentNode.removeChild(hint);
                    }, 550);
                }
            }, HINT_MS);
        } else if ((!overflowing || w.classList.contains("ams-fitted")) && hint) {
            hint.parentNode.removeChild(hint);
        }
    }

    /* ------------------------------------------------------------------ */
    /* refresh — (re)measure every wrapper, apply classes, fit zoom        */
    /* ------------------------------------------------------------------ */

    function refreshOne(w) {
        var tables = tablesIn(w);
        w.classList.add("ams-pan-wrap");

        var overflowing = hOverflow(w);

        /* Apply / remove the fit zoom first, then re-evaluate overflow
           because a successful fit removes the horizontal scroll. */
        if (ZOOM_OK && tables.length && (overflowing || w.classList.contains("ams-fitted"))) {
            var wantFit = fitWanted(w);
            if (wantFit) {
                /* Measure natural size with any current zoom removed. */
                var i, t, natural = 0;
                for (i = 0; i < tables.length; i++) {
                    tables[i].style.zoom = "";
                }
                /* force reflow once, then read widths */
                for (i = 0; i < tables.length; i++) {
                    t = tables[i].offsetWidth; /* eslint-disable-line no-unused-expressions */
                }
                for (i = 0; i < tables.length; i++) {
                    natural = Math.max(natural, tables[i].offsetWidth);
                }
                var avail = w.clientWidth;
                var ratio = natural > 0 ? avail / natural : 1;
                if (ratio < 1 && (ratio >= MIN_AUTO_FIT || fitForced(w))) {
                    var z = Math.floor(ratio * 1000) / 1000;
                    for (i = 0; i < tables.length; i++) {
                        tables[i].style.zoom = z;
                    }
                    w.setAttribute("data-ams-zoom", String(z));
                    w.classList.add("ams-fitted");
                } else {
                    w.removeAttribute("data-ams-zoom");
                    w.classList.remove("ams-fitted");
                }
            } else {
                for (var j = 0; j < tables.length; j++) {
                    tables[j].style.zoom = "";
                }
                w.removeAttribute("data-ams-zoom");
                w.classList.remove("ams-fitted");
            }
        } else if (!tables.length || !ZOOM_OK) {
            w.classList.remove("ams-fitted");
        }

        overflowing = hOverflow(w);           /* re-check after fitting  */
        var verticalToo = vOverflow(w);

        /* Live (scrollbar hidden + pan) only for pure horizontal scrollers.
           Vertical scrollers keep their scrollbar and the wheel, but still
           get drag-pan because that cannot trap anything. */
        w.classList.toggle("ams-pan-live", overflowing && !verticalToo);
        w.classList.toggle("ams-pan-v", overflowing && verticalToo);
        if (!overflowing) {
            w.classList.remove("ams-more-left", "ams-more-right");
        }
        updateEdges(w);
        /* Controls must exist while fitted too — otherwise the "1:1"
           button to leave fit mode could never appear. */
        ensureControls(w, overflowing || w.classList.contains("ams-fitted"));
    }

    function refreshAll() {
        var ws = document.querySelectorAll(SEL), i;
        for (i = 0; i < ws.length; i++) refreshOne(ws[i]);
    }

    function queueRefresh() {
        if (refreshQueued) return;
        refreshQueued = true;
        window.requestAnimationFrame(function () {
            refreshQueued = false;
            refreshAll();
        });
    }

    /* ------------------------------------------------------------------ */
    /* 1. drag-to-pan                                                      */
    /* ------------------------------------------------------------------ */

    var drag = null; /* { wrap, startX, startY, startScroll, active } */

    document.addEventListener("mousedown", function (ev) {
        if (ev.button !== 0 || ev.altKey || ev.defaultPrevented) return;
        var w = ev.target.closest ? ev.target.closest(SEL) : null;
        if (!w || !hOverflow(w)) return;
        if (ev.target.closest && ev.target.closest(INTERACTIVE_SEL)) return;
        drag = {
            wrap: w,
            startX: ev.clientX,
            startY: ev.clientY,
            startScroll: w.scrollLeft,
            active: false
        };
    }, true);

    document.addEventListener("mousemove", function (ev) {
        if (!drag) return;
        var dx = ev.clientX - drag.startX;
        var dy = ev.clientY - drag.startY;

        if (!drag.active) {
            /* Horizontal intent only — a vertical drag stays a normal
               text selection; short jitters never start a pan. */
            if (Math.abs(dx) < DRAG_THRESHOLD || Math.abs(dx) < Math.abs(dy) * 1.2) return;
            drag.active = true;
            drag.wrap.classList.add("ams-panning");
            try { window.getSelection().removeAllRanges(); } catch (_) {}
            hideHints();
        }
        ev.preventDefault();
        drag.wrap.scrollLeft = Math.max(0, drag.startScroll - dx);
    }, true);

    function endDrag() {
        if (!drag) return;
        var wasActive = drag.active;
        drag.wrap.classList.remove("ams-panning");
        updateEdges(drag.wrap);
        drag = null;
        if (wasActive) {
            /* Swallow the click that follows a pan so row actions / sort
               headers only fire on a genuine click. */
            document.addEventListener("click", function swallow(ev) {
                ev.stopPropagation();
                ev.preventDefault();
            }, { capture: true, once: true });
        }
    }
    document.addEventListener("mouseup", endDrag, true);
    document.addEventListener("dragstart", function (ev) {
        if (drag && drag.active) ev.preventDefault();
    }, true);

    /* ------------------------------------------------------------------ */
    /* 2. wheel-to-horizontal                                              */
    /* ------------------------------------------------------------------ */

    document.addEventListener("wheel", function (ev) {
        if (ev.defaultPrevented || ev.ctrlKey) return;      /* pinch-zoom   */
        if (ev.deltaX !== 0) return;                        /* native h-scroll / shift+wheel */

        var w = ev.target.closest ? ev.target.closest(SEL) : null;
        if (!w || w.classList.contains("ams-pan-v")) return; /* vertical scroller: leave alone */
        if (!hOverflow(w)) return;                           /* fitted / narrow: nothing to pan */

        var dy = ev.deltaY;
        if (!dy) return;

        var max = w.scrollWidth - w.clientWidth;
        var target = w.scrollLeft + dy;

        /* At an edge in the wheel's direction → hand scroll back to the
           page so the user is never trapped on a wide table. */
        if (dy < 0 && w.scrollLeft <= 0) return;
        if (dy > 0 && w.scrollLeft >= max) return;

        ev.preventDefault();
        w.scrollLeft = Math.max(0, Math.min(max, target));
        updateEdges(w);
        hideHints();
    }, { passive: false });

    /* ------------------------------------------------------------------ */
    /* 3. keep everything in sync                                          */
    /* ------------------------------------------------------------------ */

    /* Scroll events don't bubble, but they do capture down to the node. */
    document.addEventListener("scroll", function (ev) {
        var w = ev.target && ev.target.matches && ev.target.matches(SEL) ? ev.target : null;
        if (w) updateEdges(w);
    }, true);

    var resizeTimer = null;
    window.addEventListener("resize", function () {
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(refreshAll, 120);
    });

    /* Modal bodies, fetch-refreshed tables, lazily loaded sheets — one
       debounced observer covers them all. */
    if (typeof MutationObserver !== "undefined") {
        var mo = null, moTimer = null;
        mo = new MutationObserver(function () {
            window.clearTimeout(moTimer);
            moTimer = window.setTimeout(function () {
                queueRefresh();
            }, 150);
        });
        var start = function () {
            if (document.body) mo.observe(document.body, { childList: true, subtree: true });
            else window.setTimeout(start, 50);
        };
        start();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", queueRefresh);
    } else {
        queueRefresh();
    }
    window.addEventListener("load", queueRefresh);
    if (document.fonts && document.fonts.ready && document.fonts.ready.then) {
        document.fonts.ready.then(queueRefresh);
    }

    /* Public hook for pages that build tables by hand. */
    window.AMSPan = { refresh: refreshAll };
})();
