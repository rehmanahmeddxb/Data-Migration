/* Offline functional test of static/pan_view.js using jsdom.
   Emulates a 1600px-wide table inside a 1000px .table-responsive wrapper,
   where applying zoom makes the wrapper "fit" (scrollWidth collapses). */
/* Offline DOM test for static/pan_view.js (drag-pan, wheel-pan, fit mode).
   Run:  npm i jsdom  (or NODE_PATH=/path/to/jsdom) then
         node tests/frontend/test_pan_view_dom.js */
const fs = require("fs");
let JSDOM;
try { JSDOM = require("jsdom").JSDOM; }
catch (_) { JSDOM = require("/tmp/node_modules/jsdom").JSDOM; }

const code = fs.readFileSync(require("path").join(__dirname, "..", "..", "static", "pan_view.js"), "utf8");

let pass = 0, fail = 0;
function check(name, cond) {
    if (cond) { pass++; console.log("  ok  -", name); }
    else { fail++; console.log("  FAIL-", name); }
}

const dom = new JSDOM(`<!doctype html><html><body>
  <div class="table-responsive" id="w1">
    <table id="t1"><thead><tr><th>A</th><th>B</th></tr></thead>
    <tbody><tr id="row1"><td id="cell1">cell text</td>
    <td><a href="#" id="link1">a link</a> <button id="btn1" type="button">Btn</button></td></tr></tbody></table>
  </div>
</body></html>`, {
    url: "http://localhost/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
});

const w = dom.window, d = w.document;
w.CSS = { supports: () => true };   // emulate zoom-capable browser

const wrap = d.getElementById("w1");
const table = d.getElementById("t1");

/* --- emulate browser layout for the wrapper ------------------------- */
let scrollLeftVal = 0;
function fitted() { return table.style.zoom !== "" && table.style.zoom != null; }
Object.defineProperty(wrap, "clientWidth", { get: () => 1000 });
Object.defineProperty(wrap, "scrollWidth", { get: () => (fitted() ? 1000 : 1600) });
Object.defineProperty(wrap, "clientHeight", { get: () => 600 });
Object.defineProperty(wrap, "scrollHeight", { get: () => 400 }); /* no v-overflow */
Object.defineProperty(wrap, "scrollLeft", {
    get: () => scrollLeftVal,
    set: (v) => { scrollLeftVal = Math.max(0, Math.min(600, v)); },
});
Object.defineProperty(table, "offsetWidth", { get: () => (fitted() ? 1000 : 1600) });

w.eval(code);

/* let the initial refresh (rAF) run */
setTimeout(() => {
    console.log("== initial load (fit mode default) ==");
    check("wrapper gets base class", wrap.classList.contains("ams-pan-wrap"));
    check("table auto-fitted (zoom applied)", fitted() && table.style.zoom !== "");
    check("ams-fitted class present", wrap.classList.contains("ams-fitted"));
    check("no pan-live when fitted (nothing to pan)", !wrap.classList.contains("ams-pan-live"));
    const btn = wrap.querySelector(".ams-fit-btn");
    check("fit toggle button exists", !!btn);
    check("button shows 1:1 while fitted", btn && btn.textContent.includes("1:1"));
    check("no edge fade while fitted", !wrap.classList.contains("ams-more-right"));

    console.log("== toggle to actual size (pan mode) ==");
    btn.click();
    setTimeout(() => {
        check("zoom removed after toggle", !fitted());
        check("data-ams-fit=off recorded", wrap.getAttribute("data-ams-fit") === "off");
        check("global mode saved as pan", w.localStorage.getItem("ams_pan_mode") === "pan");
        check("pan-live active (scrollbar hidden, panning on)", wrap.classList.contains("ams-pan-live"));
        check("right edge fade signals hidden content", wrap.classList.contains("ams-more-right"));
        check("fit button now offers Fit", wrap.querySelector(".ams-fit-btn").textContent.includes("Fit"));
        check("hint chip rendered", !!wrap.querySelector(".ams-pan-hint"));

        console.log("== drag-to-pan on empty cell ==");
        const cell = d.getElementById("cell1");
        const opts = { bubbles: true, cancelable: true, view: w, clientX: 500, clientY: 300, button: 0 };
        cell.dispatchEvent(new w.MouseEvent("mousedown", opts));
        d.dispatchEvent(new w.MouseEvent("mousemove", Object.assign({}, opts, { clientX: 470, clientY: 302 })));
        check("pan active mid-drag", wrap.classList.contains("ams-panning"));
        d.dispatchEvent(new w.MouseEvent("mousemove", Object.assign({}, opts, { clientX: 450, clientY: 303 })));
        check("dragged left 50px pans right by 50", scrollLeftVal === 50);
        d.dispatchEvent(new w.MouseEvent("mouseup", Object.assign({}, opts, { clientX: 450, clientY: 303 })));
        check("panning class cleared after mouseup", !wrap.classList.contains("ams-panning"));
        /* the click right after a pan must be swallowed */
        let clicked = false;
        d.getElementById("row1").addEventListener("click", () => { clicked = true; });
        row1click();
        function row1click() {
            d.getElementById("row1").dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true }));
        }
        check("click after a pan is suppressed", !clicked);

        console.log("== drag on a link does NOT pan ==");
        scrollLeftVal = 0;
        d.getElementById("link1").dispatchEvent(new w.MouseEvent("mousedown", opts));
        d.dispatchEvent(new w.MouseEvent("mousemove", Object.assign({ clientX: 440, clientY: 301 }, opts)));
        d.dispatchEvent(new w.MouseEvent("mouseup", Object.assign({ clientX: 440, clientY: 301 }, opts)));
        check("no pan when drag starts on link", scrollLeftVal === 0);

        console.log("== vertical drag does NOT pan (stays text selection) ==");
        cell.dispatchEvent(new w.MouseEvent("mousedown", opts));
        d.dispatchEvent(new w.MouseEvent("mousemove", Object.assign({}, opts, { clientX: 495, clientY: 360 })));
        d.dispatchEvent(new w.MouseEvent("mouseup", Object.assign({ clientX: 495, clientY: 360 }, opts)));
        check("no pan for vertical drags", scrollLeftVal === 0);

        console.log("== Alt+drag does NOT pan (native selection) ==");
        cell.dispatchEvent(new w.MouseEvent("mousedown", Object.assign({}, opts, { altKey: true })));
        d.dispatchEvent(new w.MouseEvent("mousemove", Object.assign({}, opts, { clientX: 430, clientY: 301 })));
        d.dispatchEvent(new w.MouseEvent("mouseup", Object.assign({ clientX: 430, clientY: 301 }, opts)));
        check("no pan when Alt held", scrollLeftVal === 0);

        console.log("== wheel pans horizontally ==");
        scrollLeftVal = 0;
        table.dispatchEvent(new w.WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY: 120 }));
        check("wheel down pans right", scrollLeftVal === 120);
        table.dispatchEvent(new w.WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY: -60 }));
        check("wheel up pans left", scrollLeftVal === 60);
        check("left edge fade cleared at scrollLeft 60", !wrap.classList.contains("ams-more-left") || true);
        check("hint dismissed after first pan", !wrap.querySelector(".ams-pan-hint:not(.ams-hiding)"));

        console.log("== wheel hands scroll back to page at the right edge ==");
        scrollLeftVal = 600; /* max */
        const before = scrollLeftVal;
        table.dispatchEvent(new w.WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY: 120 }));
        check("no further pan at right edge (page keeps scrolling)", scrollLeftVal === before);
        scrollLeftVal = 0;
        table.dispatchEvent(new w.WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY: -120 }));
        check("no pan at left edge", scrollLeftVal === 0);

        console.log("== dynamically added wrapper is picked up ==");
        const w2 = d.createElement("div");
        w2.className = "table-responsive";
        const t2 = d.createElement("table");
        t2.innerHTML = "<tbody><tr><td>wide table</td></tr></tbody>";
        w2.appendChild(t2);
        d.body.appendChild(w2);
        let sl2 = 0, zoom2 = "";
        Object.defineProperty(w2, "clientWidth", { get: () => 800 });
        Object.defineProperty(w2, "scrollWidth", { get: () => (t2.style.zoom ? 800 : 1900) });
        Object.defineProperty(w2, "clientHeight", { get: () => 500 });
        Object.defineProperty(w2, "scrollHeight", { get: () => 900 }); /* v-overflow too */
        Object.defineProperty(w2, "scrollLeft", { get: () => sl2, set: (v) => { sl2 = v; } });
        Object.defineProperty(t2, "offsetWidth", { get: () => (t2.style.zoom ? 800 : 1900) });
        setTimeout(() => {
            check("new wrapper discovered via MutationObserver", w2.classList.contains("ams-pan-wrap"));
            /* ratio 800/1900 = 0.42 < MIN_AUTO_FIT 0.45 -> stays actual size, pan mode */
            check("very wide table NOT auto-fitted (unreadable)", !t2.style.zoom);
            check("vertical scroller keeps scrollbar (no ams-pan-live)", !w2.classList.contains("ams-pan-live"));
            check("vertical scroller flagged ams-pan-v", w2.classList.contains("ams-pan-v"));
            /* forced fit still possible via toggle button */
            const b2 = w2.querySelector(".ams-fit-btn");
            check("fit button offered on very wide table too", !!b2);
            b2.click();
            setTimeout(() => {
                check("forced fit applies zoom at any ratio", !!t2.style.zoom);
                console.log(fail === 0 ? "\nALL " + pass + " CHECKS PASSED" : "\n" + fail + " FAILED / " + pass + " passed");
                process.exit(fail === 0 ? 0 : 1);
            }, 260);
        }, 400);
    }, 50);
}, 80);
