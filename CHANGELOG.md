# Changelog

Real, dated changes to `docking_platform`, condensed from the project's
working plan (`dev_plan.md`). See `workplan_creation_guide.md` for how the
plan itself is structured, and `dev_plan.md` for what's currently in
progress.

## v1.0 — Interactive Docking Workflow (Phases 1–10)

The CLI (`nlp_orchestrator.py --interactive`) evolved from a one-shot
"parse → execute → print → exit" tool into a persistent, human-in-the-loop
docking session: start a job, iterate on ligands/box/visualization, save
multiple named result variants, inspect history, undo, and end only when the
user decides to stop.

- **Phase 1 — Persistent Interactive Session Loop.** The CLI stays alive
  after the first workflow execution instead of exiting, keeping one active
  job in session and accepting follow-up commands (`help`, `state`, `end`,
  `quit`). Verified: session survives a successful docking run; `state`
  reports the active job and parameters; `end`/`quit` exit cleanly.
- **Phase 2 — Intent Classification for Follow-Up Commands.** A classifier
  in `workflow_llm_parser.py` distinguishes a brand-new workflow request from
  a session command (parameter update, `history`, `undo`, `save results`,
  `end`), with deterministic fallbacks for the control commands. Verified:
  `change ligand to HEM` → `update_parameters`, `save results as hem_box_a`
  → `save_results`, `show history` → `show_history`, `end session` →
  `end_session`.
- **Phase 3 — Dependency-Aware Partial Rerun Engine.** `rerun_planner.py`'s
  invalidation map (`PARAM_INVALIDATION_MAP`) computes the minimal ordered
  set of steps to re-execute after a parameter change, instead of always
  redoing the whole 5-step pipeline. Verified: a ligand change reruns
  download/prepare_ligands/dock/visualize only; a box change reruns dock/
  visualize only; a prepared protein is reused untouched when only ligands
  change.
- **Phase 4 — Revision History and Undo/Restore.** `WorkflowStateManager`
  gained `list_revisions()` / `load_checkpoint()` / `restore_revision()` on
  top of its existing hybrid "latest files + immutable checkpoints" model,
  plus `history` / `undo <revision>` CLI commands. Verified: revision history
  is monotonic; undo restores the expected parameters and runtime state;
  restoring a revision doesn't resurrect stale values from outside it.
- **Phase 5 — Save Results Intent and Named Output Bundles.**
  `save_results_bundle()` (`interactive_session.py`) copies the current
  docking/visualization artifacts into `job_dir/saved_results/<name>/` with
  a `metadata.json` snapshot (parameters, revision, timestamp, included
  artifacts), with name validation and overwrite protection. Verified: a
  docking-only, visualization-only, and full-scope save each succeed; a
  duplicate name requires explicit overwrite; metadata records the correct
  revision/parameters.
- **Phase 6 — PyMOL-Driven Interactive Refinement — SKIPPED.** Deferred
  indefinitely (2026-09-12): the GUI's in-browser 3Dmol.js viewer and
  client-side visual box picker already cover "repeated box refinement
  without leaving the session" without a PyMOL/noVNC round-trip. Revisit
  only if a concrete need for PyMOL-specific scripting resurfaces.
- **Phase 7 — Session UX and Reliability Polish.** The active job id is
  shown at every prompt, each turn ends with a concise summary, expensive
  reruns and overwrites require confirmation, and `help` documents every
  supported command. Verified: prompts reflect the current job id;
  confirm/cancel works for rerun and overwrite flows; an invalid command
  doesn't terminate the session.
- **Phase 9 — Ligand Search by Name.** `RCSBDownloader.search_ligand_by_name()`
  (new `search_ligands` MCP tool on `rcsb_downloader`) resolves a common name
  ("adenosine triphosphate", "aspirin") to ranked RCSB component-ID
  candidates via the RCSB Search REST API, wired into the session as a
  `search_ligand` intent that lets the user pick a result and feeds directly
  into the existing partial-rerun path as a `ligand_ids` change. Verified via
  mocked RCSB responses (success, empty, non-200, timeout) plus an opt-in
  live integration test confirming a real search for "adenosine
  triphosphate" surfaces `ATP`.
- **Phase 10 — Multi-Intent Command Sequencing (2026-09-12).** One input can
  now carry multiple intents ("change ligand to HEM and rerun docking"): a
  deterministic splitter (`split_command_sequence()`) cuts on `and`/`then`
  boundaries only where the tail independently parses as its own command,
  with an LLM-assisted second pass reserved for `then`-containing input the
  deterministic pass can't split (validated against hallucination: ≥2
  segments, each a real intent, every original word accounted for). A new
  `_dispatch_intent()` chain executor asks one confirmation for the whole
  sequence, then runs each segment in order, stopping at the first failure
  or `end_session`. Verified: 679 tests collected, 618 passed, 62 skipped
  (Docker-dependent), 0 failures.
  - **Unplanned fixes found during this phase's live manual verification**
    (both blocked *any* docking job from completing, not just chained
    commands):
    1. Non-root service containers (`rcsb_downloader`, `ligand_preparer`,
       `protein_preparer`, `moldina_runner`) couldn't write into job-output
       directories created by root (`script-runner`), because root's default
       umask left them at mode `755`. Fixed by adding
       `common.utils.ensure_shared_directory()` (mkdir then explicit
       `chmod(0o777)`, bypassing umask) at every job-output-subdirectory
       creation site in `services/llm_orchestrator/orchestrator.py`.
       Verified with a real `docker compose --profile dev run --rm
       script-runner` docking run that failed before the fix and completed
       after. Covered by `tests/test_utils.py::test_ensure_shared_directory_*`.
    2. `common/path_utils.py::to_container_path()` never remapped a path on
       native Windows (its "looks like a Windows drive path" early-return
       fired before checking `workspace_root`, and every path looks like a
       Windows drive path on Windows), so a raw host path reached a
       Dockerized service's Linux filesystem unchanged. Fixed by scoping
       that early-return shortcut to non-Windows platforms only. Verified:
       the previously-failing `test_to_container_path_maps_workspace_relative_paths`
       now passes, and a real `python nlp_orchestrator.py --interactive` run
       directly on the host completed end-to-end.

### Server / Multi-Machine Deployment Security Hardening — completed items

Started 2026-08-01 to make the platform safe to run somewhere other than a
single trusted machine. Remaining open items are carried forward in
`dev_plan.md`'s Future Work; what's already shipped:

- **PyMOL selection-string injection RCE — fixed.** `docker/pymol_rpc_server.py`
  now exposes a dedicated `get_extent(selection)` RPC method that calls
  `cmd.get_extent(str(selection))` directly, marshalling the selection as a
  real XML-RPC argument instead of splicing it into a Python source block
  passed to `cmd.do()`. Covered by
  `tests/test_pymol_interactive.py::TestExtentFromSelectionRpc`.
- **`job_id` path traversal — fixed.** `services/pymol_interactive/server.py`'s
  `_resolve_job_dir()` allowlists `job_id` to `[A-Za-z0-9_-]+` before joining
  to `OUTPUT_ROOT`. Covered by
  `tests/test_pymol_interactive.py::TestJobIdPathTraversal`. (`output_dir`/
  `save_dir` on the docking/preparation services remain intentionally
  unrestricted — normal flexibility on a single-user deployment, not a
  defect; `job_id` was different because it names an existing job rather
  than a deliberately user-chosen location.)
- **UI upload filename sanitization — fixed.** `ui/app.py`'s `_save_upload`
  derives `Path(uploaded_file.name).name` and rejects empty/`.`/`..` results
  before joining to `upload_dir`. No automated test — `streamlit` isn't
  installed outside the `ui` Docker image.
- **Flask dev server → production WSGI — fixed.** `common/mcp_base.py`'s
  `run()` and `services/pymol_interactive/server.py` use `waitress.serve()`
  with a bounded thread pool (`MCP_SERVER_THREADS`, default 8) instead of
  Flask's `app.run(threaded=True)`. Covered by
  `tests/test_mcp_base.py::test_run_serves_via_waitress_not_flask_dev_server`.
- **`ui`/`script-runner` whole-repo bind mount — partially done.** `ui`'s
  `./:/workspace` mount is now `:ro` (verified all of its actual writes land
  in the separately-mounted rw `cache`/`orchestrator_output` paths).
  `script-runner` stays read-write on purpose (opt-in dev tool that needs to
  write `.pytest_cache/`/`test_output/`); full auth (still open, see
  `dev_plan.md`) is what actually protects both from a remote attacker.
- **Non-root `appuser` for four of six services — done.**
  `rcsb_downloader`, `ligand_preparer`, `protein_preparer`, `moldina_runner`
  now run as the non-root `appuser` created in `base.Dockerfile`, verified
  with real `docker build` + `docker run` (whoami/id report `appuser`/uid
  1000, `/health` responds, bind-mounted cache writes succeed).
  `pymol_visualizer`/`pymol_interactive` remain root — see `dev_plan.md`'s
  Future Work for why and what's left.

## v2.0 — GUI Sessions, Box Geometry, Algorithms, NiceGUI groundwork (Phases 11–16)

The Streamlit GUI caught up with the CLI's interactive session (redock,
resume, history, undo, save-as), docking gained a second search algorithm,
parallel docking defaults are now measured rather than guessed, and the GUI
logic was pulled out of Streamlit as the first half of a NiceGUI migration.
`VERSION` (repo root) says `2.0` since Phase 11. Backfilled 2026-10-11 from
`dev_plan.md`; every phase's review report is in `docs/reviews/`.

- **Phase 11 — Session Logging & GUI Session Management (2026-09-12 – 09-14).**
  - Every job writes a human-readable `job.log`: parameter changes as diffs,
    step durations, MCP calls, full tracebacks on failure
    (`common/job_log.py`).
  - `workflow_results.json` now accumulates across redocks instead of being
    overwritten, under a file lock and written atomically.
  - The GUI runs on the CLI's own `InteractiveSession` and
    `WorkflowStateManager`. Changing parameters on an active session plans a
    partial redock behind a "Confirm redock" button, and the sidebar's Load
    resumes a session from disk.
  - GUI wording moved from "Job" to "Session". New Session detaches from
    the current job, Delete asks for confirmation, failed sessions are
    listed, and the Session Log is viewable.
  - Reviews R1 (`stage_11_1_r1.md`) and R2 (`stage_11_2_11_3_r2.md`):
    - R1: 14 findings, 9 fixed.
    - R2: all three risks its stage named were real bugs, among them New
      Session/Load orphaning a still-running job. All fixed.
  - Added the `VERSION` file.
- **Phase 12 — Docking box from protein geometry (2026-09-14).**
  - Two chat commands in both the GUI and the CLI:
    - "set box center as protein center" sets the box centre to the
      protein's bounding-box midpoint;
    - "look across all protein" sizes the box to the whole protein, plus
      10%.
  - Detection is deterministic (regex), never guessed by the LLM.
  - The geometry comes from one shared parser, `common/pdb_geometry.py`,
    which reads ATOM records only.
  - Review R1: 9 findings, 5 fixed. Among them:
    - geometry computed from a stale protein left over from another session;
    - "cover the whole protein pocket" wrongly triggering a whole-protein box.
  - A later fix (2026-09-30) lets a geometry command fetch and prepare the
    protein itself when none is loaded yet.
- **Phase 13 — GUI revision controls + render-level GUI tests (2026-09-30 – 10-02).**
  - **13.1:** a Streamlit `AppTest` harness runs the real page hermetically.
    It found a real bug on its first run.
  - **13.1b (unplanned, from a user report):** Stop now halts docking within
    about a second. `moldina-runner` kills in-flight ligands and starts no
    more when it sees a stop flag in the job's `docking_results/`. Before,
    every remaining ligand was docked first.
  - **13.2 / 13.3:** GUI parity with the CLI for `show history`,
    `undo <rev>` and `save results as <name>`, as buttons and as typed chat
    commands, including the name-clash flow.
  - **Review R1** also covered four earlier commits that had never been
    reviewed. Correctness: 11 findings, 10 fixed, e.g. a picker box size
    silently docked with the wrong box, and a session stopped in its first
    run could never dock again.
  - **13.4 / 13.5 (R1's deferrals):** the chat history is drawn last, so
    every reply shows in the same page run. The Docking Box fields are the
    only box state; the picker steps them with − / + buttons.
  - The owner's live-browser check passed on 2026-10-02.
- **Phase 14 — Parallel docking defaults & transparency (2026-10-05).**
  - **Worker cap:** read from `docker-compose.yml` into
    `WorkerCap(cap, source, reason)`, with no more silent fallback. The
    GUI caption states where the limit comes from and warns on the fallback.
  - **Benchmark:** `benchmark_docking.py --sample-stats` samples container
    memory and CPU.
  - **Measured default:** benchmarked on 50 ligands (10 CPUs / 8 GB), 8
    workers is the best, at about 612 MiB each. The GUI default became
    `min(8, cap)`. Raw data is in
    `docs/benchmarks/2026-10-05_moldina_cpus10_mem8g.json`.
  - **Review R1** (`stage_14_r1.md`, widened to cover 13.4/13.5):
    - correctness: 11 findings, 10 fixed;
    - quality: 9 fixed, 3 deferred with triggers.
- **Phase 15 — Selectable docking algorithm: Moldina (PSO) | Vina (Monte Carlo) (2026-10-05 – 10-07).**
  - **New parameter:** `docking_algorithm`, either `moldina` (default) or
    `vina`. Vina mode runs the same `moldina` binary without the PSO flags,
    so no new binary or image is needed.
  - **Where it flows:** through the MCP tool, the orchestrator, checkpoints,
    `parameters.txt`, the results and the comparison table. Old sessions
    read as Moldina.
  - **Chat and CLI commands:** "use vina", "dock with moldina".
  - **GUI:** an algorithm selector, plus "CPUs per ligand" for Vina only.
    The worker limit becomes `cap // cpu`, and the server clamps workers the
    same way.
  - **Vina benchmark:** best at 6 workers, recorded per algorithm next to
    Moldina's.
  - **Review R1 with a Cross-Service Compatibility Review**
    (`stage_15_r1.md`): 9 correctness fixes, including:
    - a timeout too short for Vina;
    - chat messages resetting the user's CPU, exhaustiveness and pH fields;
    - a CLI bug from 2026-04-14 where a cancelled first workflow broke every
      later rerun.

    Parameter defaults are now filled in one place.
  - **Verified:** the owner's browser check passed on 2026-10-07. The fresh
    full-stack rebuild was done on 2026-10-11 (see Phase 16): health check,
    stop integration test, and a GUI-driven 2-ligand Vina run recording
    `workers: 2`.
- **Phase 16 — GUI migration to NiceGUI (in progress, started 2026-10-07).**
  - **16.0:** the Streamlit GUI's baseline timing, scripted and headless
    (`benchmark_gui*.py`, `?perf` marks in the viewer), and a spike
    answering five NiceGUI questions. Baseline numbers are in
    `docs/GUI_GUIDE.md` "Performance".
  - **16.1 / 16.2:** the page's controller logic moved, unchanged, into
    framework-free `ui/core/` (state, form, run control, sessions, chat, box
    picker, save-as, results view). Streamlit's `ui/app.py` is now a thin
    adapter over it, and the core runs in the hermetic suite. The bridge
    stopped swapping the process-wide stdout, so concurrent runs no longer
    capture each other's log lines.
  - **Review R1 (2026-10-08 – 10-11, `stage_16_r1.md`):**
    - **Behaviour preservation:** confirmed function by function, and no
      GUI-framework import reaches `ui/core`.
    - **Logging:** the orchestrator writes to a per-run `on_message` sink.
    - **Fixed:**
      - a geometry command during a running receptor prep started a second
        prep;
      - the same command could use an older protein than the one being
        prepared;
      - several queued commands kept only the last;
      - a run's final log lines ("WORKFLOW COMPLETED SUCCESSFULLY") never
        reached the chat;
      - the CLI's `show history` named a different revision than the GUI's
        and now reports the parameters revision.
    - **Fresh stack:** rebuilt and verified (all images; `down`/`up` under
      the same compose project).
  - **16.3 (2026-10-12):** the `ui-nice` service (NiceGUI, port 3001,
    non-root `appuser`) with the page layout, the `ui-nice` test tier, and a
    parity table mapping every Streamlit test to its NiceGUI replacement.
    Upload, settings, `saved_results/`, checkpoint dirs and `job.log` became
    writable by both GUIs' users.
  - **16.4 (2026-10-12):** NiceGUI's Setup tab (inputs and uploads, Docking
    Box, Advanced with "CPUs per ligand" as a number field, Run / Save
    Session / New Session / Reset, a redock confirm dialog, Stop and live
    progress on a 1 s timer) and the chat drawer (every command; the LLM
    parse runs off the event loop).
    - **Both GUIs:** a run's log goes to its own **Run log** expander under
      the chat. Before, it was appended to the last chat message, so a
      message sent mid-run got the log attached.
    - **NiceGUI only:** an LLM answer is not applied if the session changed
      while it was being parsed, and Run Docking is disabled while a run is
      going.
  - **16.5 (2026-10-12):** NiceGUI's box picker on the Setup tab's right
    side (Load receptor, atom click → box center, − / + size steps,
    Discard), with the 3Dmol.js viewer ported as a custom element.
    - **The viewer keeps its camera:** the scene is built once, and a box
      change redraws only the box. Streamlit rebuilt everything and reset
      the view on every change.
    - **The box redraw is cheaper:** about 143 ms, against Streamlit's
      184 ms rebuild, because the axis labels are made once and then moved.
    - **The viewer element also has a results mode** (affinity-coloured
      poses, swappable without moving the camera), for Stage 16.6.
  - **Review Stage R2 (2026-10-12)**, covering 16.3–16.5
    (`docs/reviews/stage_16_r2.md`):
    - **One tab no longer freezes the others** (NiceGUI). The slow work now
      runs off the event loop all clients share:
      - `save results as` (it took 34–37 s for a real session on a Docker
        Desktop bind mount);
      - the geometry command's receptor read (35–80 ms);
      - the progress read (~10 ms per tab per second).

      While a save runs, that tab's chat input, Run, New Session and Reset
      are off.
    - **Both GUIs:**
      - Each upload is kept in a folder of its own, so a same-named upload
        from another tab no longer replaces the molecule an earlier session
        docks.
      - Either GUI can overwrite a results bundle the other saved.
    - **NiceGUI fixes:**
      - A run that ended within a second is now drawn.
      - Ligand drops add up, as in Streamlit.
      - An unreadable receptor is reported once instead of being retried
        forever.
      - A page sync no longer starts nested syncs.
      - Reset clears the box picker's message.
      - An LLM answer is dropped after a Load of a session with no saved
        parameters.
  - **16.6 (2026-10-12):** NiceGUI's Results tab.
    - **What it shows:** the status, the "Docked with" caption, the
      affinity chart, the viewer beside **Ligands** and pose grids (a click
      shows a pose, and the camera is kept), **Save results as…** with the
      conflict buttons, the Session Log, the downloads, and Compare
      Sessions.
    - **It opens by itself** when a run finishes, completed or failed.
    - **Downloads are links** to a `/files/<job_id>/<path>` route that
      streams the file. Nothing is held in memory, and only files inside
      that session's folder are served (not `saved_results/`).
    - **Saves from the panel and the conflict buttons** copy in the
      background, as the chat's `save results as` does since R2.
    - **Result files are read once** per result, and `job.log` only when
      it changes, never on every timer tick.
  - **Next:** Stage 16.7, the Sessions tab and settings.
