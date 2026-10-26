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
               --out /out/<date>_gui_nicegui_browser.json" \\
      | python3 benchmark_gui.py --from-driver --container mcp-ui \\
               --json-out docs/benchmarks/<date>_gui_nicegui_stats.json

This starts a real docking run on the live stack (a new session appears in
the GUI's history). It changes nothing else: the Box X steps are made in
+/− pairs, so the box ends where it started.

Selectors are roles, labels and texts (NiceGUI's `.mark()` markers exist
only for its User fixture, not in the page). Load and the box picker are on
the Sessions and Setup tabs; the tab click before a timed action is not
counted (`Driver.timed(prepare=...)`). The scenarios are the ones the
Streamlit GUI was timed with in Stage 16.0 (the Streamlit selectors were
removed with it in 16.8), so the numbers compare like for like.

The latency snippet also reports a *filtered* median that ignores Quasar's
button ripple and tooltip (animation, not the page's work; Stage 16.8). The
raw median is the one to compare with Streamlit's.
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
DEFAULT_URL = "http://localhost:3000"
# Quasar's click ripple and tooltip: DOM changes the filtered latency ignores.
NICEGUI_LATENCY_IGNORE = [".q-ripple", ".q-tooltip", '[id^="q-portal--tooltip"]']
_JOB_ID_RE = re.compile(r"job_\d{8}_\d{6}_[0-9a-f]+")

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


def format_timed(label: str, summary: dict) -> str:
    """One progress line for a timed action."""
    line = f"  {label}: median {summary['median_ms']} ms (n={summary['n']})"
    filtered = summary.get("median_filtered_ms")
    if filtered is not None and filtered != summary["median_ms"]:
        line += f", filtered {filtered} ms"
    return line + f", viewer renders/action {summary['viewer']['renders_per_action']:.1f}"


class Driver:
    """Timing: perf console lines, settling, the latency snippet. The
    controls are in :class:`NiceGuiDriver`."""

    latency_ignore: List[str] = []

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
        self.page.evaluate("ignore => { window.__lat.ignore = ignore; }", self.latency_ignore)

    def timed(self, action, label: str, repeats: int, prepare=None) -> Dict[str, float]:
        """Run *action* repeats times, one latency sample each; return the
        snippet's summary plus the viewer renders seen during the samples.
        *prepare* (e.g. a tab click) runs before each action, untimed: its
        samples and renders are left out."""
        self.page.evaluate("window.__lat.results = []; window.__lat.dropped = 0")
        renders: List[Dict[str, float]] = []
        for _ in range(repeats):
            if prepare is not None:
                kept = self.page.evaluate("window.__lat.results.length")
                prepare()
                self._wait_sample(kept)
                self.settle()
                self.page.evaluate(f"window.__lat.results.length = {kept}")
            before = self.page.evaluate("window.__lat.results.length")
            renders_before = len(self.renders)
            action()
            self._wait_sample(before)
            self.settle()
            renders += self.renders[renders_before:]
        summary = self.page.evaluate(f"window.__lat.summary({json.dumps(label)})")
        summary["viewer"] = summarize_renders(renders)
        summary["viewer"]["renders_per_action"] = summary["viewer"]["renders"] / repeats
        print(format_timed(label, summary), flush=True)
        return summary


class NiceGuiDriver(Driver):
    """The NiceGUI GUI, by role, label and text."""

    latency_ignore = NICEGUI_LATENCY_IGNORE

    def open(self, url: str) -> None:
        self.page.goto(f"{url}/?perf")
        self.chat_input().wait_for()
        self.settle()

    def chat_input(self):
        return self.page.get_by_placeholder("Describe your workflow...")

    def chat(self, text: str) -> None:
        box = self.chat_input()
        box.fill(text)
        box.press("Enter")

    def tab(self, name: str) -> None:
        self.page.get_by_role("tab", name=name, exact=True).click()

    def setup_inputs(self, receptor: Path, ligands: Path) -> None:
        self.tab("Setup")
        self.settle()
        self.page.get_by_text("Local Files", exact=True).click()
        self.settle()
        uploaders = self.page.locator(".q-uploader:visible")
        uploaders.filter(has_text="Protein file").locator('input[type="file"]').set_input_files(str(receptor))
        self.page.get_by_text(f"Protein: {receptor.name}", exact=True).wait_for()
        uploaders.filter(has_text="Ligand file(s)").locator('input[type="file"]').set_input_files(str(ligands))
        self.page.get_by_text("ligand file(s) ready").locator("visible=true").wait_for()
        self.settle()
        for name, c, b in zip("XYZ", DEFAULT_CENTER, DEFAULT_BOX):
            self.page.get_by_label(f"Center {name}", exact=True).fill(c)
            self.page.get_by_label(f"Box {name}", exact=True).fill(b)
        self.settle()

    def load_receptor_for_picking(self) -> None:
        self.tab("Setup")
        self.settle()
        load = self.page.get_by_role("button", name="Load receptor for box picking")
        if load.is_visible():
            load.click()
        self.page.get_by_role("button", name="X +", exact=True).wait_for(timeout=180000)
        self.settle()

    def box_step(self, up: bool) -> None:
        self.page.get_by_role("button", name="X +" if up else "X \u2212", exact=True).click()

    def _run_card(self):
        # The card with the Stop button; the chat says "is running" too.
        return self.page.locator(".q-card").filter(has=self.page.get_by_role("button", name="Stop", exact=True))

    def start_run(self) -> str:
        self.page.get_by_role("button", name="Run Docking").click()
        card = self._run_card()
        card.wait_for(timeout=120000)
        return _JOB_ID_RE.search(card.inner_text()).group(0)

    def wait_run_end(self, job_id: str, timeout_s: int) -> None:
        self._run_card().wait_for(state="hidden", timeout=timeout_s * 1000)

    def prepare_load(self) -> None:
        self.tab("Sessions")

    def load_session(self, job_id: str) -> None:
        load = self.page.get_by_role("button", name="Load", exact=True)
        row = self.page.locator(".nicegui-row").filter(has_text=job_id).filter(has=load)
        row.get_by_role("button", name="Load", exact=True).first.click()


def run(args) -> dict:
    from playwright.sync_api import sync_playwright

    result = {"gui": "nicegui", "url": args.url, "receptor": str(args.receptor),
              "ligands": str(args.ligands), "center": DEFAULT_CENTER, "box": DEFAULT_BOX, "scenarios": {}}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.set_default_timeout(60000)
        d = NiceGuiDriver(page)
        d.open(args.url)
        d.inject_latency_snippet(args.repo)

        print("setup: upload inputs, box fields, receptor for picking", flush=True)
        d.setup_inputs(args.receptor, args.ligands)
        d.load_receptor_for_picking()
        setup = result["scenarios"]["setup"] = {}
        setup["box_step"] = _box_steps(d, "setup: box X +/-", args.repeats)

        print("running: Run Docking", flush=True)
        job_id = d.start_run()
        result["job_id"] = job_id
        marker("SAMPLE-START", "running-25")
        t0 = time.monotonic()
        renders_before = len(d.renders)
        running = result["scenarios"]["running"] = {}
        running["show_history"] = d.timed(lambda: d.chat("show history"), "running: show history", args.repeats)
        d.wait_run_end(job_id, args.run_timeout)
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
        completed["load"] = d.timed(lambda: d.load_session(job_id), "completed: Load", args.repeats,
                                    prepare=d.prepare_load)
        d.load_receptor_for_picking()
        completed["box_step"] = _box_steps(d, "completed: box X +/-", args.repeats)

        if args.screenshot:
            page.screenshot(path=str(args.screenshot))
        browser.close()
    return result


def _box_steps(d: Driver, label: str, repeats: int) -> Dict[str, float]:
    """Box X + then −, repeats times: 2 x repeats samples, box size unchanged."""
    steps = iter([True, False] * repeats)
    return d.timed(lambda: d.box_step(next(steps)), label, repeats * 2)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Time the GUI in a headless browser (run in the Playwright image).")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"The GUI's address (default: {DEFAULT_URL})")
    parser.add_argument("--repo", type=Path, default=REPO_IN_CONTAINER, help="Repo root as mounted (default: /repo)")
    parser.add_argument("--receptor", type=Path, default=DEFAULT_RECEPTOR)
    parser.add_argument("--ligands", type=Path, default=DEFAULT_LIGANDS)
    parser.add_argument("--repeats", type=int, default=5, help="Latency samples per action (box steps: 2x)")
    parser.add_argument("--idle-seconds", type=int, default=60, help="Idle window on the finished session")
    parser.add_argument("--run-timeout", type=int, default=1800, help="Seconds to wait for the docking run")
    parser.add_argument("--screenshot", type=Path, default=None, help="Optional final screenshot path")
    parser.add_argument("--out", type=Path, required=True, help="Write the results JSON here")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    result = run(args)
    args.out.write_text(json.dumps(result, indent=2))
    print(f"Wrote {args.out}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
