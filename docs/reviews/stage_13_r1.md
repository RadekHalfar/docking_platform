# Review Stage R1 — Phase 13 (Stages 13.1, 13.1b, 13.2, 13.3) — Tier-3 final review

**Scope:** `0e08e98..62f4243`, i.e. everything since Phase 12's Review Stage
R1 closed. That is wider than Phase 13's own four stage commits, on purpose:
four commits landed after Phase 12's R1 and never got review scrutiny —
`2666c77` (PDB atom-line parsing unified), `ec0fc7b` (GUI helper test tier),
`dff7826` (GUI geometry commands auto-fetch the protein + box-picker UX) and
`c7598a4` (3Dmol viewer box rendering). Per `development_process_rules.md`
§4 ("a review labeled final that predates real shipped changes is not
final"), that gap is named here and closed by this review rather than left
between two reviews. Plus the three items Stage 13.3 carried into this
stage's scope (see "Carried items").

**Method** (`development_process_rules.md` §4): `/code-review` (correctness,
high effort) over the whole range; `/simplify` (four parallel angle-agents:
reuse, simplification, efficiency, altitude) over the same range; a
dedicated exception-handling pass; a re-check that Phase 12 R1's GUI-side
fixes still hold. Every `/code-review` candidate was verified against the
real code before triage — the Streamlit-runtime ones with throwaway AppTest
repros in the `ui` container, which then became the regression tests.

**Two rules restated, not just pointed at:** exceptions are never used as a
band-aid — a broad catch belongs only at a real I/O/protocol boundary and
must turn the failure into an explicit failed state, never make it vanish;
and no finding is manufactured to look thorough — candidates that turned out
not worth acting on are reported as such (#10), not dressed up.

**Outcome:** 11 correctness findings (10 from `/code-review`, 1 from the
Phase 12 re-check) — 10 fixed immediately, 1 accepted as risk. 23 quality
findings from `/simplify` — 10 fixed, 3 deferred with written triggers, 10
declined with reasons. 3 carried items — all fixed. Exception pass: clean.
Test results are in "Verification" at the end.

---

## Correctness (`/code-review` + Phase 12 re-check)

### 1. A box size typed into the picker silently reverted — and was written into the real docking box
**Files:** `ui/session_state.py`, `ui/app.py` (`_render_box_picker`)
**Triage: fixed immediately.** *Verified by repro.*
The 2026-10-01 widget-persistence fix (`_PERSISTED_WIDGET_KEYS`) excluded the
picker's size inputs on the claim they are "re-synced from the Docking Box
fields anyway". Nothing re-synced them every run. Any run cut short by
`st.rerun()` before the parameters panel renders — a chat message, a
finished run — dropped `picker_box_x/y/z`, the number inputs came back at
their `value=20.0` default (or an older API-set value), and the picker's
resize mirror (`_box_picker_last_size != [bx, by, bz]`) then wrote that
value into `param_box_*`, the fields actually docked with. Repro: picker size
typed as 61, one chat message, Docking Box X back to the old value. Fix: the
three keys are seeded in `init()` (`_PICKER_BOX_DEFAULTS`, 20.0) and added
to `_PERSISTED_WIDGET_KEYS`; the number inputs no longer pass `value=`
(see #8). Test: `test_picker_box_size_survives_early_rerun_in_chat_column`.

### 2. A session stopped (or failed) during its first run could never dock again
**Files:** `ui/app.py` (`_handle_redock_request`)
**Triage: fixed immediately.** *Verified by repro.*
Stage 13.1b made Stop-during-dock the common path. A stopped first run has
`services_executed = [get_input_files, prepare_ligands, prepare_protein]`
and `pending` is empty (fresh `activate()`). Run Docking with the same
parameters: a diff of defaults vs. the validator's output still produced
some "change", but `compute_rerun_steps(..., original_services=executed)`
intersected dock/visualize away — "Parameters updated, but no steps need to
be re-executed". Changing the center: same message, after a checkpoint had
already been written. Save was also blocked, so the only exit was a new
session. (Pre-existing since Phase 11 for a *failed* first run; 13.1b made it
routine.) Fix: when the last run's status isn't `completed`, the steps it
never reached are always planned, and are allowed through the
`original_services` intersection. Tests:
`test_redock_after_stopped_first_run_docks_again` (same / changed params).

### 3. A restored-but-never-run revision was a dead end after a Load
**Files:** `ui/app.py` (`_resume_session`)
**Triage: fixed immediately** (this is also carried item 1). *Verified by repro.*
Restore revision N, then Load the session (or reload the page and Load):
`activate()` resets `pending`, the form equals the checkpoint, so Run
Docking said "nothing to redock" — while Save said "parameters have not been
run yet — click Run Docking first". First fix attempt (consult
`parameters_ran()` inside the redock) was rejected by the existing
`test_cancelled_redock_plan_is_offered_again` once its fixture was made
realistic: it widened a precisely-known `pending = {center}` to a full
rerun. Final fix: seed `pending` from disk at the one place it is lost —
`_resume_session()` sets `pending |= CORE_PARAMS` when `parameters_ran()` is
False. In-tab redocks keep the exact per-key plan. Test:
`test_redock_after_load_of_never_run_restore_plans_full_rerun` (uses the
real sidebar Load button).

### 4. A stopped redock was merged as if dock had run, next to the previous run's visualize
**Files:** `services/llm_orchestrator/orchestrator.py` (results merge in `execute_workflow`)
**Triage: fixed immediately.** *Verified by reading + test.*
13.1b records a stopped dock's partial result but deliberately leaves `dock`
out of `services_executed`. The end-of-run merge with the existing
`workflow_results.json` then rebuilt `services_executed` from the merged
result *keys*, so `dock` counted as executed again, and the previous full
run's `visualize` result stayed beside the partial dock — Load showed mixed
old/new results. This is also the first real occurrence of Stage 11.1 R1
finding #2's "stale merge cannot occur in practice yet". Fix: a step this
call was asked to run but didn't finish is not executed, and its older
result is dropped unless this call recorded its own (the partial dock is
kept); `services_executed` = this run's executed steps + previously executed
steps this call didn't request. The finding-#2 comment now notes that this
case is handled. Test: `test_stopped_redock_does_not_merge_dock_as_executed`.

### 5. A failed overwrite destroyed the existing bundle
**Files:** `services/llm_orchestrator/interactive_session.py` (`save_results_bundle`)
**Triage: fixed immediately.** *Verified by reading + test.*
Stage 13.3's overwrite `rmtree`'d the old bundle before copying, and the
`OSError` handler removed the partial new one — a full disk turned
"Overwrite" into "delete". Fix: write into `saved_results/.<name>.partial`,
then rename the old bundle aside, swap the new one in, and remove the old
one; on `OSError` the partial copy is removed and a moved-aside old bundle
is put back. Valid bundle names can't start with `.`, so the temp names
never collide (and the downloads list skips `saved_results/` entirely).
Test: `test_save_bundle_failed_overwrite_keeps_the_old_bundle`.

### 6. `undo` took the first number anywhere in the sentence
**Files:** `services/llm_orchestrator/workflow_llm_parser.py` (`_deterministic_intent`, shared by CLI + GUI)
**Triage: fixed immediately.** *Verified by reading.*
"undo the box change and set exhaustiveness to 16" → `undo_revision`, name
`16`. Since Stage 13.2 the GUI acts on that directly (restores revision 16
if it exists, overwriting the form); before 13.2 the message went to the LLM
parser. The CLI splits that example at "and", but "undo the change from 3
minutes ago" would hit it too. Fix: the revision is only taken when it
directly follows `undo` (`undo 3`, `undo rev 3`, `undo to revision #3`);
anything else gives name `None`, so the CLI prompts and the GUI asks which
revision. Tests: four new cases in `test_session_intents.py`. (The other part
of the candidate — the exact word `log` meaning `show history` — is the CLI's
documented alias and an exact match only; not a bug.)

### 7. GUI chat `undo N` on a runtime-state-only revision rolled back the state silently
**Files:** `ui/app.py` (`_undo_to_revision`)
**Triage: fixed immediately (GUI); CLI accepted as is.** *Verified by reading + test.*
`restore_revision()` overwrites `workflow_state.json` from a state-only
checkpoint; the GUI replied "parameters unchanged (runtime state restored)"
and marked nothing pending, so a later visualize-only redock could run
against a rolled-back state with no docking results. The panel's dropdown
already offers only parameter revisions; chat was the odd path out. Fix:
the GUI refuses such a revision *before* restoring anything. The CLI keeps
its existing behaviour — **recommendation: leave it**: it is pre-Phase-13,
documented, and the CLI explicitly prints which state it restored, so it's a
deliberate power-user tool rather than a silent trap. Test:
`test_chat_undo_of_state_only_revision_is_refused`.

### 8. Streamlit "default value but also set via the Session State API" warning on a plain receptor load
**Files:** `ui/app.py` (`_render_box_picker`)
**Triage: fixed immediately** (by #1's fix). *Verified by repro.*
When **Load receptor for box picking** completed with the Docking Box
fields already filled, `_seed_picker_box_from_manual_inputs()` wrote
`picker_box_*` in the same run that then created the number inputs with
`value=20.0` — Streamlit logs the policy warning (server log, not the page).
Gone now that the inputs take their default from session state only. Test:
`test_receptor_load_with_box_fields_set_logs_no_session_state_warning`
(records Streamlit's policy logger).

### 9. Revision history panel listed the revisions twice per render
**Files:** `ui/app.py` (`_render_revision_history`, `_format_history_markdown`)
**Triage: fixed immediately.** Two manifest reads + 2N `exists()` stats
per rerun (incl. the 2 s poll while a job runs) instead of one. Now listed
once; `_format_history_markdown()` takes the list.

### 10. `parse_pdb_atom_line()` drops a short ATOM line with a blank atom name
**Files:** `common/pdb_geometry.py`
**Triage: accepted as risk. Recommendation: leave as-is.** True in
principle: since `2666c77`, a line truncated before column 77 *and* with a
blank atom name raises `IndexError` on `atom_name[0]` and is skipped, where
the old geometry parser read x/y/z only. But the atom name is a mandatory
PDB field, and the only files this reads are receptors our own
`protein_preparer` wrote. Changing it would also change `protein_preparer`'s
manual-rigid output, which 2666c77 verified byte-for-byte. Not worth it.

### 11. Phase 12 R1's stale-receptor fix didn't cover Load or Restore
**Files:** `ui/app.py` (`_load_params_into_form`)
**Triage: fixed immediately.** *Found by the Phase 12 re-check.*
Phase 12 R1 finding #1 clears the box-picker receptor on **New Session** and
on a fresh job. A sidebar **Load** (which predates that review) and Phase
13's **Restore revision** both switch the protein without clearing it, so
"use protein center" after loading a different-protein session computed from
the old receptor — exactly the bug that finding fixed. Fix:
`_load_params_into_form()` (shared by Load and Restore) clears the picker
state when the protein source (`protein_id`/`protein_file`) changes; same
protein keeps it. Test:
`test_restore_drops_box_picker_receptor_only_for_another_protein`.

Phase 12 R1's other GUI-side fix (the end-anchored `_WHOLE_PROTEIN_BOX_RE`)
still holds, unchanged.

---

## Carried items (from Stage 13.3)

- **GUI redock should use `parameters_ran()`** — fixed as #3 (by seeding
  `pending` on Load, see there for why not inside the redock).
- **CLI `save results as` should use `parameters_ran()`** — **fixed.** Its
  `pending` is per-process, so a revision restored in the GUI or an earlier
  CLI process was saved over results it never produced — the exact Stage
  13.3 bug, CLI side. `_handle_save_results()` now refuses and names the
  revision. Test: `test_save_results_refused_when_latest_parameters_never_ran`.
- **Streamlit `use_container_width` deprecation** — **fixed.** All nine uses
  (8 buttons, 1 `plotly_chart`) → `width="stretch"`, confirmed supported by
  the installed Streamlit 1.61.1 (`inspect.signature`).

---

## Quality (`/simplify`)

### Fixed
1. **`PARAM_INVALIDATION_MAP` hand-copied the file-input entries**
   (`workflow_definition.py`) — now derived from
   `REQUIRED_PARAMETER_ALTERNATIVES`, the one alias table.
2. **Docking Box fields parsed twice** (`_collect_form_params`,
   `_sync_picker_box_from_manual_inputs`; reuse + simplification angles) —
   one `_read_box_fields()` built on `_BOX_WIDGET_KEYS`.
3. **Three copies of the pending-geometry failure reply** in
   `_render_box_picker` — one `failure` variable, one append.
4. **Literal step lists** in `_handle_run_docking` / `_load_params_into_form`
   → `WORKFLOW_STEPS`.
5. **Bundle-name limit and dir name restated** — `_VALID_BUNDLE_NAME_RE` and
   its message are built from `_MAX_BUNDLE_NAME_LEN`; new
   `SAVED_RESULTS_DIRNAME` shared by the bundle code and the downloads list.
6. **Viewer recomputed the half-extents** (`mol_viewer/index.html`) →
   `half: hx/hy/hz`.
7. **Downloads walked every saved bundle only to discard it** (efficiency) —
   `os.walk` prunes `saved_results/` instead of `rglob` + filter (a bundle can
   hold a full copy of a large batch's `docking_results/`).
8. **History listed twice** — see correctness #9.
9. **`picker_box_*` persistence** — see correctness #1.
10. **Test fixtures that no real session produces** (found while fixing):
    `_active_session_with_two_revisions` and two other setups recorded
    parameters as "completed and redocked" with no step ever running after
    them, and `_completed_job_data` had no `services_executed`. Both now
    record what a real run writes; otherwise the pending-flag tests would
    have passed through the new `parameters_ran()` seeding instead of testing
    what they claim to.

### Deferred, with triggers (all three parts in place)
1. **Chat history is drawn before replies are appended** (altitude #1). The
   completion `st.rerun()`, the picker's pending-geometry `st.rerun()` and the
   on_click-only rule for reply buttons all work around this, and early reruns
   are what forced `_PERSISTED_WIDGET_KEYS` (and correctness #1). Fix: fill
   the history container at the end of the script and handle both bridges'
   completion before the layout. **Recommendation: worth doing, as its own
   small stage** — it reorders the whole page's render flow, which a review
   pass shouldn't. Trigger comment: `ui/app.py`, `render_chat()` above the
   completion rerun. Future Work: `dev_plan.md` Phase 13.
   **Done in Stage 13.4** (`dev_plan.md`): history drawn last, bridges
   handled in `_process_bridges()` before the layout, the reply-only reruns
   removed. `_PERSISTED_WIDGET_KEYS` is still needed for the other early
   reruns and for widgets that are only drawn in some runs.
2. **Box geometry stored twice and mirrored both ways** (altitude #2). Three
   Phase 13 fixes and correctness #1 were this mirror drifting. Fix: Docking
   Box fields as the only state. **Recommendation: worth doing, own stage**
   (it changes the picker's UI). Trigger comment: `ui/app.py`,
   `_sync_picker_box_from_manual_inputs()` docstring. Future Work:
   `dev_plan.md` Phase 13.
3. **`pending_changed_params` vs `parameters_ran()` — two sources of truth**
   (altitude #5). Fix: record the parameters revision each completed run used
   and derive pending keys from it — exact and persistent, replacing both and
   the `CORE_PARAMS` over-approximation (#3's Load seeding). **Recommendation:
   do it only on the trigger** — today's pair is correct, just not minimal.
   Trigger comment: `workflow_state_manager.py`, `parameters_ran()`
   docstring. Future Work: `dev_plan.md` Phase 13.

### Declined, with reasons
- **`moldina_runner` cosmetics** — `stopped_count` → bool, merge
  `_DockingProgress.done()`/`stopped()`, required `stop_check` on
  `_dock_one_ligand`, `_ProcessResult` → `subprocess.CompletedProcess`.
  All real but behaviour-neutral, and that service's code is baked into its
  image: each would cost a rebuild of `mcp-docking-base` +
  `mcp-moldina-runner` and a live re-verification of Stop. Not worth it.
- **`_start_box_picker_prep()`'s 3-tuple** — marginal; two call sites read
  fine as is.
- **`_format_history_markdown` flags ternary** and **a `_say()` helper** for
  the ~30 `messages.append(...)` — style only; the latter predates the diff.
- **Shared history-row formatter for CLI + GUI** — the two layouts (aligned
  print vs. markdown table) share three lines; a helper adds coupling for
  nothing.
- **Move undo bookkeeping and the save gate into `InteractiveSession`**
  (reuse #3 / altitude #4) — the real defect behind it (the CLI lacked
  `parameters_ran()`) is fixed above; what's left differs per front end
  (running-bridge check, "Run Docking" vs "rerun" wording). Revisit together
  with deferred #3, which replaces the mechanism these would share.
- **Canonical "form → params" via `validate_workflow()` in redock**
  (altitude #3) — `validate_workflow()` also adds `None` placeholders and
  `missing_required`; the 2026-10-01 defaults merge is the narrow, tested
  equivalent. No current mismatch.
- **Cache revision history / reuse the loaded results in the save gate**
  (efficiency #1 tail, #2) — after #9 it is one small manifest read and one
  results parse per rerun, only while a session is on screen; caching would
  add an invalidation path for a cost nobody can measure.
- **`request_job_stop(job_dir)` in `common/docking_stop.py`**, and the
  duplicated autouse `_clean_session_state` test fixture — trivial either
  way.

---

## Exception-handling pass

Every `try`/`except` added in the range, classified:

| Where | Catch | Verdict |
|---|---|---|
| `common/pdb_geometry.py::parse_pdb_atom_line` | `ValueError, IndexError` → `None` | Parse boundary (external file), documented skip. OK. |
| `interactive_session.py::save_results_bundle` | `OSError` → `success: False` + job.log | Filesystem boundary → explicit failed result. OK (its data-loss ordering was correctness #5). |
| `moldina_runner::_run_cancellable` | `subprocess.TimeoutExpired` | The poll mechanism itself (`communicate(timeout=)`), and the kill-grace escalation. OK. |
| `ui/app.py::_undo_to_revision` | `ValueError` → reply | Invalid user-supplied revision → message. OK. |
| `ui/app.py::_read_box_fields` (was `_sync_picker…`) | `ValueError, TypeError` → `None` | Parsing user-typed text. OK. |

The new `OrchestratorBridge.request_stop()` adds no catch; the bridge's
pre-existing `except Exception` around the worker thread converts failures
into `_error` (a real thread boundary). No band-aids found.

---

## Verification

- **Regression tests fail before, pass after.** Every new test was run
  against `62f4243`'s production code in a temporary worktree: all 11 new
  failing cases failed for their bug, e.g. the picker size reverting, "no
  steps need to be re-executed", `dock` in `services_executed`, the old
  bundle's `metadata.json` gone, `undo_revision` name `16`. All pass with
  the fixes. Bugs #1–#3 and #8 were also reproduced by AppTest before any
  fix was written.
- **Full hermetic suite:** 826 passed / 17 skipped / 2 deselected
  (`docker compose --profile dev run --rm script-runner python -m pytest -m
  "not integration"`), up from 819. A first run hung: four CLI tests faked
  a "completed" session whose parameters had no step run after them. The
  new CLI save guard (correctly) refused, and the scripted `input()` ran
  out inside the loop. Those fakes now record the run.
- **`ui` tier:** 134 passed (`docker compose run --rm ui python -m pytest
  tests/test_ui_*.py`), up from 126.
- **Live Stop (orchestrator merge changed):**
  `tests/test_docking_stop_integration.py` against the live stack —
  stopped 0.6 s after the request, `status=stopped`, 8/8 skipped.
- **Real data, #3:** the real `ui/app.py` driven through AppTest in the
  `ui` container on a **copy** of the owner's session
  `job_20261001_084546_3747f6` (parameters at revision 20, never run — the
  session Stage 13.3 found).
  - **Load:** `pending` = the four core params.
  - **Run Docking:** staged the full 5-step plan with no errors (before the
    fix: "nothing to redock"). Nothing was confirmed or run.
  - **Save results bundle:** still disabled.
  - The copy was removed afterwards.
- **`ui` restarted** against the live stack: healthy, HTTP 200 on
  :3000, `_stcore/health` ok, no errors/deprecation/Session-State warnings
  in its log.
- **Not done:** the click-through of every Phase 13 control in a real
  browser — the owner's check (checklist in the hand-over).
