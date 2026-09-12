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
