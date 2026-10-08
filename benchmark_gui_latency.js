/*
 * GUI interaction latency, measured in the browser (Phase 16, Stage 16.0).
 *
 * Paste this whole file into the browser DevTools console on the GUI's page
 * (Streamlit now, NiceGUI after the port -- it uses nothing framework-specific,
 * so the before/after numbers in docs/GUI_GUIDE.md "Performance" are measured
 * the same way). Then click the control being measured five or more times,
 * waiting for the page to settle between clicks, and run:
 *
 *     __lat.summary("box + click")   // prints median/min/max/n, then resets
 *     __lat.summary("show history", "enter")   // only Enter samples
 *
 * One sample = time from a click (or Enter in a text field, e.g. a chat
 * command) to the last DOM change before QUIET_MS of
 * no changes (the page has finished reacting). Clicks inside an iframe (the
 * Streamlit 3Dmol viewer) are not seen; measure the controls around it.
 *
 * Caveats:
 * - While a docking job runs, the Streamlit page reruns itself every 2 s.
 *   A poll landing inside a sample stretches it, so report the median of
 *   five or more clicks, not one.
 * - A click made before the previous sample settled starts a new sample
 *   and drops the unfinished one (__lat.dropped counts them).
 * - Mutations under QUIET_MS apart chain together, so a spinner or
 *   progress bar that never stops prevents the sample from closing; the
 *   sample then closes at MAX_MS and is marked as capped.
 */
(function () {
  "use strict";
  var QUIET_MS = 300;
  var MAX_MS = 30000;

  if (window.__lat && window.__lat.detach) window.__lat.detach();

  var current = null;
  var lat = { results: [], dropped: 0 };

  function finish(capped) {
    if (!current) return;
    clearTimeout(current.quietTimer);
    clearTimeout(current.maxTimer);
    var ms = current.lastMutation - current.start;
    lat.results.push({ ms: +ms.toFixed(1), kind: current.kind, target: current.target, capped: capped });
    console.info("[lat] " + ms.toFixed(0) + " ms" + (capped ? " (capped)" : "") + "  " + current.kind + " " + current.target);
    current = null;
  }

  var observer = new MutationObserver(function () {
    if (!current) return;
    current.lastMutation = performance.now();
    clearTimeout(current.quietTimer);
    current.quietTimer = setTimeout(function () { finish(false); }, QUIET_MS);
  });
  observer.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });

  function describe(el) {
    var text = (el.innerText || el.getAttribute("aria-label") || "").trim().split("\n")[0].slice(0, 40);
    return el.tagName.toLowerCase() + (text ? ' "' + text + '"' : "");
  }

  function onClick(event) {
    if (current) {
      clearTimeout(current.quietTimer);
      clearTimeout(current.maxTimer);
      lat.dropped += 1;
    }
    var now = performance.now();
    current = { start: now, lastMutation: now, kind: event.type === "keydown" ? "enter" : "click",
                target: describe(event.target) };
    // A click that changes nothing closes as a 0 ms sample after QUIET_MS.
    current.quietTimer = setTimeout(function () { finish(false); }, QUIET_MS);
    current.maxTimer = setTimeout(function () { finish(true); }, MAX_MS);
  }
  // Enter in a text field (e.g. sending a chat command) counts as a click.
  function onKeydown(event) {
    if (event.key === "Enter" && !event.shiftKey && /^(INPUT|TEXTAREA)$/.test(event.target.tagName)) {
      onClick(event);
    }
  }
  document.addEventListener("click", onClick, true);
  document.addEventListener("keydown", onKeydown, true);

  // kind: "click" or "enter" to summarize only those samples (e.g. "enter"
  // for chat commands, so the click that focused the chat box is left out).
  lat.summary = function (label, kind) {
    var picked = lat.results.filter(function (r) { return !kind || r.kind === kind; });
    var ms = picked.map(function (r) { return r.ms; }).sort(function (a, b) { return a - b; });
    var n = ms.length;
    var median = n ? (n % 2 ? ms[(n - 1) / 2] : (ms[n / 2 - 1] + ms[n / 2]) / 2) : null;
    var out = {
      label: label || "", n: n, median_ms: median,
      min_ms: n ? ms[0] : null, max_ms: n ? ms[n - 1] : null,
      capped: picked.filter(function (r) { return r.capped; }).length,
      dropped: lat.dropped,
    };
    console.table([out]);
    lat.results = [];
    lat.dropped = 0;
    return out;
  };

  lat.detach = function () {
    observer.disconnect();
    document.removeEventListener("click", onClick, true);
    document.removeEventListener("keydown", onKeydown, true);
  };

  window.__lat = lat;
  console.info("[lat] ready: click the control 5+ times, then __lat.summary('<label>')");
})();
