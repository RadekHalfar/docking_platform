/*
 * GUI interaction latency, measured in the browser (Phase 16, Stage 16.0).
 *
 * Paste this whole file into the browser DevTools console on the GUI's page
 * (it uses nothing framework-specific, so the Stage 16.0 Streamlit and 16.8
 * NiceGUI numbers in docs/GUI_GUIDE.md "Performance" are measured the same
 * way). Then click the control being measured five or more times,
 * waiting for the page to settle between clicks, and run:
 *
 *     __lat.summary("box + click")   // prints median/min/max/n, then resets
 *     __lat.summary("show history", "enter")   // only Enter samples
 *
 * One sample = time from a click (or Enter in a text field, e.g. a chat
 * command) to the last DOM change before QUIET_MS of
 * no changes (the page has finished reacting).
 *
 * Filtered latency (Stage 16.8): set __lat.ignore to a list of CSS
 * selectors, e.g. [".q-ripple", ".q-tooltip"] for NiceGUI's Quasar
 * animations. A sample still closes after QUIET_MS of no DOM changes at
 * all, but median_filtered_ms ends at the last change outside those
 * elements. Empty by default, so median_filtered_ms equals median_ms.
 *
 * Caveats:
 * - While a docking job runs, the page updates its progress every second.
 *   An update landing inside a sample stretches it, so report the median
 *   of five or more clicks, not one.
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
  var lat = { results: [], dropped: 0, ignore: [] };

  function finish(capped) {
    if (!current) return;
    clearTimeout(current.quietTimer);
    clearTimeout(current.maxTimer);
    var ms = current.lastMutation - current.start;
    var filtered = current.lastKept - current.start;
    lat.results.push({ ms: +ms.toFixed(1), filtered_ms: +filtered.toFixed(1), kind: current.kind,
                       target: current.target, capped: capped });
    console.info("[lat] " + ms.toFixed(0) + " ms" + (capped ? " (capped)" : "") + "  " + current.kind + " " + current.target);
    current = null;
  }

  // A node inside (or being) one of the ignored elements.
  function ignoredNode(node) {
    var el = node && node.nodeType === 1 ? node : node && node.parentElement;
    if (!el) return false;
    for (var i = 0; i < lat.ignore.length; i++) {
      var sel = lat.ignore[i];
      if (el.closest(sel) || (node.nodeType === 1 && node.querySelector(sel))) return true;
    }
    return false;
  }

  // A change made only to ignored elements: inside one, or adding or
  // removing them (Quasar inserts the ripple into the button).
  function ignoredRecord(record) {
    if (ignoredNode(record.target)) return true;
    if (record.type !== "childList") return false;
    var nodes = Array.prototype.slice.call(record.addedNodes).concat(Array.prototype.slice.call(record.removedNodes));
    return nodes.length > 0 && nodes.every(ignoredNode);
  }

  var observer = new MutationObserver(function (records) {
    if (!current) return;
    var now = performance.now();
    current.lastMutation = now;
    if (!lat.ignore.length || !records.every(ignoredRecord)) current.lastKept = now;
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
    current = { start: now, lastMutation: now, lastKept: now, kind: event.type === "keydown" ? "enter" : "click",
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
    function median(values) {
      var v = values.slice().sort(function (a, b) { return a - b; });
      var k = v.length;
      return k ? (k % 2 ? v[(k - 1) / 2] : (v[k / 2 - 1] + v[k / 2]) / 2) : null;
    }
    var ms = picked.map(function (r) { return r.ms; }).sort(function (a, b) { return a - b; });
    var n = ms.length;
    var out = {
      label: label || "", n: n, median_ms: median(ms),
      median_filtered_ms: median(picked.map(function (r) { return r.filtered_ms; })),
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
