# -*- coding: utf-8 -*-
"""
Drive the GUI in a headless browser and time it (Phase 16, Stages 16.0 / 16.8).

This is the scripted half of the GUI performance measurement in
docs/GUI_GUIDE.md, "Performance". It opens the GUI with ?perf, injects
benchmark_gui_latency.js (the same snippet used by hand), and runs the
reference session:

  setup        upload the receptor + ligands, fill the Docking Box fields,
               load the receptor for box picking, time Box X + / − clicks
  running      click Run Docking, time `show history`, sample CPU until done
  completed    sample CPU idle, then time Box X + / −, `show history` and
               Load on the finished session

Latency is the snippet's click-to-DOM-quiet time (median of the samples).
Viewer renders are counted from the `[perf] render` console lines the
?perf flag makes the 3Dmol viewer print.

CPU/memory is not sampled here (no docker inside the browser container).
Instead the driver prints `SAMPLE-START <label>` / `SAMPLE-STOP <label>`
lines, and `benchmark_gui.py --from-driver` reads them on stdin and runs its
docker-stats sampler in between -- one pipeline does both.

Run it inside the Playwright image, sharing the GUI container's network so
localhost is the GUI. From the repo root, in Git Bash:

    MSYS_NO_PATHCONV=1 docker run --rm --network container:mcp-ui \\
        -v "$(pwd -W):/repo:ro" -v "$(pwd -W)/docs/benchmarks:/out" \\
        mcr.microsoft.com/playwright/python:v1.55.0-noble \\
        sh -c "pip install -q playwright==1.55.0 && python -u /repo/benchmark_gui_browser.py \\
               --out /out/<date>_gui_streamlit_browser.json" \\
      | python3 benchmark_gui.py --from-driver --container mcp-ui \\
               --json-out docs/benchmarks/<date>_gui_streamlit_stats.json

This starts a real docking run on the live stack (a new session appears in
the GUI's history). It changes nothing else: the Box X steps are made in
+/− pairs, so the box ends where it started.

The selectors below are Streamlit's (`st-key-<widget key>` classes).
Stage 16.8 adds the NiceGUI equivalents behind --gui.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

REPO_IN_CONTAINER = Path("/repo")
DEFAULT_RECEPTOR = REPO_IN_CONTAINER / "data" / "benchmark_gui" / "receptor.pdb"
DEFAULT_LIGANDS = REPO_IN_CONTAINER / "data" / "benchmark_gui" / "actives_first25.sdf"
DEFAULT_CENTER = ("19.354", "16.789", "29.19")
DEFAULT_BOX = ("20", "20", "20")
LATENCY_SNIPPET = "benchmark_gui_latency.js"

_PERF_RE = re.compile(
    r"\[perf\] render #(?P<n>\d+) mode=(?P<mode>\S+) atoms=(?P<atoms>\d+) "
    r"render=(?P<render>[\d.]+)ms frame=(?P<frame>[\d.]+)ms"
)


def parse_perf_line(text: str) -> Optional[Dict[str, float]]:
    """A viewer `[perf] render ...` console line -> dict, else None."""
    m = _PERF_RE.search(text)
    if not m:
        return None
    return {"n": int(m["n"]), "mode": m["mode"], "atoms": int(m["atoms"]),
            "render_ms": float(m["render"]), "frame_ms": float(m["frame"])}


def summarize_renders(renders: List[Dict[str, float]]) -> Dict[str, float]:
    """Count and median timings of viewer renders in one window."""
    if not renders:
        return {"renders": 0}
    return {
        "renders": len(renders),
        "median_render_ms": statistics.median(r["render_ms"] for r in renders),
        "median_frame_ms": statistics.median(r["frame_ms"] for r in renders),
        "atoms": renders[-1]["atoms"],
    }


def marker(kind: str, label: str) -> None:
    """A line benchmark_gui.py --from-driver acts on."""
    print(f"{kind} {label}", flush=True)


class Driver:
    """The Streamlit GUI, driven through Playwright."""

    def __init__(self, page, quiet_ms: int = 300):
        self.page = page
        self.quiet_ms = quiet_ms
        self.renders: List[Dict[str, float]] = []
        page.on("console", self._on_console)

    def _on_console(self, msg) -> None:
        parsed = parse_perf_line(msg.text)
        if parsed:
            self.renders.append(parsed)

    # ---- waiting -------------------------------------------------------------
    def settle(self, timeout_ms: int = 60000) -> None:
        """Wait until the DOM has been quiet for quiet_ms (the page reacted)."""
        self.page.evaluate(
            """([quiet, timeout]) => new Promise((resolve, reject) => {
                let t = setTimeout(done, quiet);
                const cap = setTimeout(() => { obs.disconnect(); reject(new Error('page never settled')); }, timeout);
                const obs = new MutationObserver(() => { clearTimeout(t); t = setTimeout(done, quiet); });
                function done() { obs.disconnect(); clearTimeout(cap); resolve(); }
                obs.observe(document.body, {subtree: true, childList: true, attributes: true, characterData: true});
            })""",
            [self.quiet_ms, timeout_ms],
        )

    def _wait_sample(self, before: int) -> None:
        self.page.wait_for_function(f"window.__lat.results.length > {before}", timeout=60000)

    # ---- latency -------------------------------------------------------------
    def inject_latency_snippet(self, repo: Path) -> None:
        self.page.evaluate((repo / LATENCY_SNIPPET).read_text(encoding="utf-8"))

    def timed(self, action, label: str, repeats: int) -> Dict[str, float]:
        """Run *action* repeats times, one latency sample each; return the
        snippet's summary plus the viewer renders seen during the samples."""
        self.page.evaluate("window.__lat.results = []; window.__lat.dropped = 0")
        renders_before = len(self.renders)
        for _ in range(repeats):
            before = self.page.evaluate("window.__lat.results.length")
            action()
            self._wait_sample(before)
            self.settle()
        summary = self.page.evaluate(f"window.__lat.summary({json.dumps(label)})")
        summary["viewer"] = summarize_renders(self.renders[renders_before:])
        summary["viewer"]["renders_per_action"] = summary["viewer"]["renders"] / repeats
        print(f"  {label}: median {summary['median_ms']} ms (n={summary['n']}), "
              f"viewer renders/action {summary['viewer']['renders_per_action']:.1f}", flush=True)
        return summary

    # ---- Streamlit controls --------------------------------------------------
    def key(self, widget_key: str):
        return self.page.locator(f".st-key-{widget_key}")

    def fill(self, widget_key: str, value: str) -> None:
        box = self.key(widget_key).locator("input")
        box.fill(value)
        box.press("Enter")
        self.settle()

    def chat(self, text: str) -> None:
        box = self.page.get_by_test_id("stChatInputTextArea")
        box.fill(text)
        box.press("Enter")

    def click_key(self, widget_key: str) -> None:
        # A button with help= renders two <button>s in its container (the
        # tooltip wrapper); click the visible one.
        self.key(widget_key).locator("button:visible").first.click()

    def load_receptor_for_picking(self) -> None:
        self.page.get_by_role("button", name="Load receptor for box picking").click()
        self.key("box_step_x_up").wait_for(timeout=180000)
        self.settle()

    def running_job_id(self) -> str:
        stop = self.page.locator('[class*="st-key-stop_"]').first
        stop.wait_for(timeout=120000)
        cls = stop.get_attribute("class")
        return re.search(r"st-key-stop_(job_\S+)", cls).group(1)


def run(args) -> dict:
    from playwright.sync_api import sync_playwright

    result = {"url": args.url, "receptor": str(args.receptor), "ligands": str(args.ligands),
              "center": DEFAULT_CENTER, "box": DEFAULT_BOX, "scenarios": {}}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.set_default_timeout(60000)
        d = Driver(page)
        page.goto(f"{args.url}/?perf")
        page.get_by_test_id("stChatInputTextArea").wait_for()
        d.settle()
        d.inject_latency_snippet(args.repo)

        print("setup: upload inputs, box fields, receptor for picking", flush=True)
        page.get_by_text("Local Files", exact=True).click()
        d.settle()
        d.key("upload_protein").locator('input[type="file"]').set_input_files(str(args.receptor))
        d.settle()
        d.key("upload_ligands").locator('input[type="file"]').set_input_files(str(args.ligands))
        d.settle()
        for axis, c, b in zip("xyz", DEFAULT_CENTER, DEFAULT_BOX):
            d.fill(f"param_center_{axis}", c)
            d.fill(f"param_box_{axis}", b)
        d.load_receptor_for_picking()
        setup = result["scenarios"]["setup"] = {}
        setup["box_step"] = _box_steps(d, "setup: box X +/-", args.repeats)

        print("running: Run Docking", flush=True)
        page.get_by_role("button", name="Run Docking").click()
        job_id = d.running_job_id()
        result["job_id"] = job_id
        marker("SAMPLE-START", "running-25")
        t0 = time.monotonic()
        renders_before = len(d.renders)
        running = result["scenarios"]["running"] = {}
        running["show_history"] = d.timed(lambda: d.chat("show history"), "running: show history", args.repeats)
        page.locator(f".st-key-stop_{job_id}").wait_for(state="detached", timeout=args.run_timeout * 1000)
        marker("SAMPLE-STOP", "running-25")
        running["run_seconds_observed"] = round(time.monotonic() - t0, 1)
        running["viewer_while_running"] = summarize_renders(d.renders[renders_before:])
        d.settle()

        print(f"completed: idle {args.idle_seconds} s", flush=True)
        completed = result["scenarios"]["completed"] = {}
        renders_before = len(d.renders)
        marker("SAMPLE-START", "idle-completed-25")
        page.wait_for_timeout(args.idle_seconds * 1000)
        marker("SAMPLE-STOP", "idle-completed-25")
        completed["viewer_while_idle"] = summarize_renders(d.renders[renders_before:])
        completed["show_history"] = d.timed(lambda: d.chat("show history"), "completed: show history", args.repeats)
        completed["load"] = d.timed(lambda: d.click_key(f"job_load_{job_id}"), "completed: Load", args.repeats)
        d.load_receptor_for_picking()
        completed["box_step"] = _box_steps(d, "completed: box X +/-", args.repeats)

        if args.screenshot:
            page.screenshot(path=str(args.screenshot))
        browser.close()
    return result


def _box_steps(d: Driver, label: str, repeats: int) -> Dict[str, float]:
    """Box X + then −, repeats times: 2 x repeats samples, box size unchanged."""
    steps = iter(["box_step_x_up", "box_step_x_down"] * repeats)
    return d.timed(lambda: d.click_key(next(steps)), label, repeats * 2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Time the GUI in a headless browser (run in the Playwright image).")
    parser.add_argument("--url", default="http://localhost:3000")
    parser.add_argument("--repo", type=Path, default=REPO_IN_CONTAINER, help="Repo root as mounted (default: /repo)")
    parser.add_argument("--receptor", type=Path, default=DEFAULT_RECEPTOR)
    parser.add_argument("--ligands", type=Path, default=DEFAULT_LIGANDS)
    parser.add_argument("--repeats", type=int, default=5, help="Latency samples per action (box steps: 2x)")
    parser.add_argument("--idle-seconds", type=int, default=60, help="Idle window on the finished session")
    parser.add_argument("--run-timeout", type=int, default=1800, help="Seconds to wait for the docking run")
    parser.add_argument("--screenshot", type=Path, default=None, help="Optional final screenshot path")
    parser.add_argument("--out", type=Path, required=True, help="Write the results JSON here")
    args = parser.parse_args()

    result = run(args)
    args.out.write_text(json.dumps(result, indent=2))
    print(f"Wrote {args.out}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
