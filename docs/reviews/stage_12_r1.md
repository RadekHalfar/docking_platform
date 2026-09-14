# Review Stage R1 — Stages 12.1–12.3 (chat-driven docking box auto-fill from protein geometry)

**Scope:** commits `34f9dfd` (`feat(common): add protein bounding-box geometry
for chat box auto-fill`, Stage 12.1), `cfba400` (`feat(ui): auto-fill docking
box from protein geometry via chat`, Stage 12.2), and `5277a09` (`feat(cli):
wire protein-geometry box auto-fill into interactive session`, Stage 12.3),
diffed against `f402ddb` (the pre-Phase-12 base), per `dev_plan.md` Phase 12.
Runs `/code-review` (correctness) and `/simplify` (reuse/simplification/
efficiency/altitude, 4 parallel angle-agents) per
`development_process_rules.md` §4. This is Phase 12's only planned review
stage (3 stages total, below the "insert one every 2–3 stages" threshold, so
one stage suffices rather than a separate Tier-2 + Tier-3 split) and covers
both the GUI and CLI surfaces this phase touches.

**Outcome:** 2 correctness findings, 5 quality findings
(reuse/simplification/efficiency/altitude). 6 fixed directly (commit follows
this report — finding #4 was initially deferred, then fixed the same day at
the user's request; see its entry), 1 deferred with a written trigger, 2
accepted as documented risk (one of those an explicit "considered, declined"
rather than a bare skip). Both correctness findings were real, reachable
bugs — a stale box-picker receptor silently computing geometry for the wrong
protein, and a regex broad enough to swap a user's targeted box-resize
request for a full blind-docking box with no error and no LLM fallback to
catch it. Full suite green after all fixes: 781 passed / 14 skipped / 1
deselected, no regressions (`docker compose --profile dev run --rm
script-runner python -m pytest -m "not integration"`, up from 771 passed
before this review's fixes — +4 new
regression-guard tests for the tightened regex).

---

## Correctness (`/code-review`)

### 1. `_resolve_prepared_protein_pdb_path()` could silently compute geometry from a stale, unrelated protein
**Files:** `ui/app.py` (`_find_receptor_pdb_for_geometry`, `_handle_run_docking`),
`ui/session_state.py` (`start_new_session`)
**Triage: fixed immediately.**
The geometry-command resolver prefers `st.session_state["_box_picker_receptor_pdb"]`
(the box picker's last-loaded receptor) over the active session's own
`prepare_protein` result, on the reasoning that it's "freshest." Nothing
cleared that key when a *different* session/protein started. Reachable
sequence: load protein A via **Load receptor for box picking**
(`_box_picker_receptor_pdb` → A's prepared PDB), then change the Protein ID
field to B and click **Run Docking** directly (no active session yet, so no
"New Session" click is even needed to reach this) or click **New Session**
first. Either way, a fresh job for B starts, but `_box_picker_receptor_pdb`
still points at A's file (never deleted from disk). Typing "look across all
protein" or "set up box center as protein center" during B's session resolves
the receptor via the stale key first, silently computing and applying
`center`/`box_size` from **protein A's coordinates**, with no error or
warning. Fixed by extracting the box picker's three-key clear
(`_box_picker_receptor_pdb`/`picked_center`/`_box_picker_last_size`,
previously duplicated between `_handle_pick_box()` and the "Discard picked
receptor" button) into a new shared `ui/session_state.py::clear_box_picker_state()`,
called from `start_new_session()` (closes the "New Session then ask before
Run Docking" window) and from `_handle_run_docking()`'s fresh-job branch
(closes the actually-reachable repro above, which needs no "New Session"
click at all).

### 2. `_WHOLE_PROTEIN_BOX_RE` could swap a targeted box-resize request for a full blind-docking box, with no error and no LLM fallback
**File:** `services/llm_orchestrator/workflow_llm_parser.py`
**Triage: fixed immediately.**
The regex matched via a bare `.search()` anywhere in the input, so any
phrase containing e.g. "cover ... whole protein" matched regardless of what
followed. Verified against real phrasing: `detect_protein_geometry_command("cover
the whole protein pocket")` and `"widen the box to cover the whole protein
active site"` both returned `"box_size"` before this fix — a user asking to
resize the box around a specific pocket/site would have that request
silently reinterpreted as "search the entire protein" (a materially
different, slower, unfocused search), with no error and no chance for the
LLM to catch the mismatch, since `_deterministic_intent()` returns before
`parse_workflow_with_llm`/`parse_followup_with_llm` are ever called. (One
originally-suspected phrase, "widen the box a bit to cover more of the
protein around the pocket", was checked and does **not** actually match the
pre-fix regex — the finding as first raised named the wrong example, but the
underlying risk class was real and reproducible with adjacent phrasing.)
Fixed by anchoring all three alternatives to end-of-string (allowing only a
trailing "box" and/or punctuation): `protein(?:\s+box)?[\s.!?]*$` instead of
a bare `protein\b`. Verified against the full existing positive test table
(all 9 cases still match) plus 4 new negative cases covering this exact
class (`tests/test_session_intents.py`).

---

## Reuse / Simplification / Efficiency / Altitude (`/simplify`, 4 parallel angles)

### 3. `_render_box_picker()` re-implemented the `prepare_protein` → `pdb_path` dict-walk that `resolve_prepared_protein_pdb_path()` exists to consolidate
**File:** `ui/app.py`
**Triage: fixed immediately.**
`resolve_prepared_protein_pdb_path()` (`interactive_session.py`) was written
in Stage 12.2 specifically to be "one definition of 'where is the prepared
protein' instead of two independently-maintained dict-walks" — but the box
picker's own completion handler (`result.get("results", {}).get("prepare_protein",
{}).get("prepared_proteins", [])` then `proteins[0].get("pdb_path")`) was
left untouched, duplicating exactly that logic a third time. Replaced with a
direct call to `resolve_prepared_protein_pdb_path(result)` — same shape
(`result` is the bridge's full `workflow_results.json`-shaped dict), no
behavior change.

### 4. `common/pdb_geometry.py::parse_atom_coordinates()` duplicates `protein_preparer`'s own PDB column parsing
**Files:** `common/pdb_geometry.py`, `services/protein_preparer/__init__.py`
**Triage: originally deferred, with trigger — since fixed at the user's
request (2026-09-14, same day).**
`services/protein_preparer/__init__.py`'s manual-rigid-receptor fallback
(~line 277-302) independently parsed the identical `line[30:38]`/`line[38:46]`/
`line[46:54]` columns with the same malformed-line tolerance. Originally left
unified on the reasoning that the two callers want different record types
(`ATOM`-only here vs. `ATOM`+`HETATM` there) and unifying them looked like a
bigger cross-module change than review scope. On reflection the actual fix
is smaller than that framing suggested: the two callers don't need different
*parsing*, only different *filtering* of the same parsed records. Fixed by
extracting the shared low-level piece — a new `common/pdb_geometry.py::parse_pdb_atom_line()`
returning a `PdbAtomRecord` (all fields either caller needs: `record_type`,
`atom_name`, `res_name`, `chain`, `res_num`, `x`/`y`/`z`, `element`) for one
`ATOM`/`HETATM` line, or `None` for a non-atom or malformed one.
`parse_atom_coordinates()` now filters that to `record_type == "ATOM"`;
`protein_preparer`'s `convert_to_pdbqt_manual()` now calls the same function
and uses both record types, keeping its own PDBQT-line formatting/
AutoDock-type-mapping logic (genuinely specific to that module, correctly
left there). Verified byte-for-byte identical output against the pre-fix
version (git `f402ddb`) for a 3-atom `ATOM`+`HETATM` fixture including a
malformed 4th line (same warning, same skip, same written bytes), plus a
real end-to-end `prepare_protein` run against `6lu7` through the live MCP
stack (`conversion_method: manual_rigid`, `final_atoms: 4798`, geometry
computed from the result matching every prior check:
`center=[-25.544, 12.854, 58.25]`, `box_size=[55.736, 71.983, 65.124]`).
6 new tests (`tests/test_pdb_geometry.py` for `parse_pdb_atom_line()` itself;
`tests/test_protein_preparer.py` for `convert_to_pdbqt_manual()`'s
`HETATM`-inclusion and malformed-line-skip, previously only exercised
indirectly). Full suite green: 781 passed / 14 skipped / 1 deselected, up
from 775, no regressions. The deferral's own trigger comment
(`common/pdb_geometry.py`) and `dev_plan.md` Future Work bullet are removed
below since the condition they were watching for is now moot.

### 5. Duplicated `update_parameters`/`set_protein_geometry` dispatch shape in `nlp_orchestrator.py`
**File:** `nlp_orchestrator.py`
**Triage: fixed immediately.**
Both `_dispatch_intent()` branches repeated the identical
`if changed: session.pending_changed_params |= changed; print(...)` shape,
differing only in a leading confirmation phrase. Extracted to
`_mark_changed_and_hint_rerun(session, changed, prefix="")`, called from
both branches (`prefix="Parameters updated. "` for the pre-existing branch,
default `""` for the new one) — output text unchanged for both, confirmed
by inspection (no test previously asserted on the printed string, and none
was warranted for print-output text — the underlying `changed`/
`pending_changed_params` behavior is what the existing and new
`_handle_set_protein_geometry`/`_handle_update_parameters` tests assert).

### 6. `ui/app.py::_resolve_prepared_protein_pdb_path` named one underscore away from the `interactive_session.py` function it wraps
**File:** `ui/app.py`
**Triage: fixed immediately.**
The GUI's local wrapper (adds the box-picker-preference fallback) and the
shared `resolve_prepared_protein_pdb_path()` it calls into differed only by
a leading underscore — easy to misread as the same function when
grepping/skimming. Renamed the local wrapper to
`_find_receptor_pdb_for_geometry` (all 2 call sites updated); the imported
shared function keeps its name unchanged.

### 7. Double `stat()` syscall per geometry chat command
**File:** `ui/app.py`
**Triage: accepted as a documented risk.**
`_find_receptor_pdb_for_geometry()` calls `.exists()` to validate the
resolved path, then its only caller (`_handle_protein_geometry_command`)
immediately calls `.stat().st_mtime` on the same path for the
`@st.cache_data`-keyed `_read_file_bytes()` call. **Recommendation:** not
worth fixing — this is one extra `stat()` syscall on a small file, triggered
only when the user issues one of these two specific chat commands, not a
hot loop or a per-rerun cost. Fixing it would mean widening
`_find_receptor_pdb_for_geometry()`'s return type to a `(Path, mtime)` tuple
(or catching `FileNotFoundError` instead of pre-checking `.exists()`) for a
gain too small to justify the added interface complexity.

### 8. `_WHOLE_PROTEIN_BOX_RE`'s two "box-and-qualifier" alternatives, considered for collapsing into one order-insensitive check
**File:** `services/llm_orchestrator/workflow_llm_parser.py`
**Triage: accepted as a documented risk (considered, declined).**
The simplification pass flagged that the "whole protein box" and
"box ... whole protein" alternatives both just mean "a box word and a
whole/entire-protein qualifier co-occur," suggesting a single
`_PROTEIN_QUALIFIER_RE.search(text) and _BOX_WORD_RE.search(text)` check
instead of two order-sensitive alternatives. Evaluated directly against
finding #2 above: an order-insensitive `and` re-opens exactly the
just-fixed false-positive hole (e.g. "the box we chose doesn't cover the
whole protein's inactive residues" would match a plain word-cooccurrence
check the same way the unanchored regex used to). **Recommendation:**
decline — keep the two order-sensitive, end-anchored alternatives as fixed
in finding #2; the ordering is doing real correctness work here, not
accidental duplication.

### 9. `set_protein_geometry` has no LLM-classifier fallback tier, unlike every sibling intent
**File:** `services/llm_orchestrator/workflow_llm_parser.py`
**Triage: deferred, with trigger.**
Every other intent in `_deterministic_intent()`/`classify_session_intent()`
is backed by two tiers: the fast regex/prefix match, and an LLM-classifier
fallback (`_INTENT_CLASSIFIER_SYSTEM`) for phrasing the regex misses.
`set_protein_geometry` only got the first tier, so `"set_protein_geometry"`
being present in `_SESSION_INTENTS` is presently unreachable dead weight —
the LLM path can never select it, since the classifier's prompt doesn't
list it. A paraphrase like "put the box in the middle of the receptor"
falls through to `update_parameters`, which correctly declines to invent
`center`/`box_size` but gives no hint this feature exists. **Recommendation:**
this is a deliberate scope decision from Phase 12's own plan
(`dev_plan.md` Context: "Detection is deterministic ... not LLM-based"),
made explicitly to avoid hallucination risk on a numeric-sensitive
operation — not a gap that was missed. The numeric computation stays fully
deterministic either way; only intent *recognition* would gain an LLM tier
if this is picked up (mirroring `use_pymol_box`'s existing two-tier setup).
Worth doing as a real, bounded follow-up, not urgent. Trigger comment added
at `services/llm_orchestrator/workflow_llm_parser.py` (just above the
`detect_protein_geometry_command()` call in `_deterministic_intent()`) and
`dev_plan.md`'s Future Work strengthened to reference this finding.

---

## Exception-handling pass

Every `try`/`except` added by this diff reviewed for the band-aid pattern
`development_process_rules.md` §6 warns against. All four are real I/O/parsing
boundary catches, each converting failure into an explicit typed result
rather than swallowing it:
- `common/path_utils.py::from_container_path()` / `to_container_path()`:
  `except ValueError` from `Path.relative_to()` — expected control flow (not
  under `container_root`), returns the path unchanged.
- `common/pdb_geometry.py::parse_atom_coordinates()`: `except (ValueError,
  IndexError)` per malformed `ATOM` line — documented, intentional tolerance
  for the non-conforming lines real-world PDB files routinely contain, skips
  just that line rather than the whole parse.
- `nlp_orchestrator.py::_handle_set_protein_geometry()` and
  `ui/app.py::_handle_protein_geometry_command()`: `except (OSError,
  ValueError) as exc` around the read-file-then-compute-geometry step —
  converts a real I/O/parse failure into a printed message / chat reply,
  not a silent pass.

No new band-aid catches introduced.

## On not manufacturing findings

Restating `development_process_rules.md` §0/§4 directly: this report
reflects what was actually found and independently verified, not a quota.
Finding #2's originally-suspected repro phrase was checked empirically and
found *not* to reproduce — reported honestly as a wrong example rather than
silently substituted, while the underlying risk class (verified with
adjacent phrasing) was kept because it's real. Findings #7 and #8 are
reported as genuinely declined, with the specific reasoning for declining
each, not padded into "fixed" to raise the count, and not left as a bare
"accepted" label without a recommendation.

## Testing note (pre-existing, not introduced by Phase 12 — closed 2026-09-14)

At review time, `ui/app.py` and `ui/session_state.py` (including this
review's fixes — `clear_box_picker_state()`, `start_new_session()`) had no
automated test coverage: `streamlit` is not installed in the `script-runner`
container this project's whole test suite runs in (`docker compose --profile
dev run --rm script-runner python -m pytest`), confirmed directly
(`ModuleNotFoundError: No module named 'streamlit'`). This was a
pre-existing condition across every GUI-touching stage since Stage 11.2, not
something Phase 12 introduced. Finding #1's fix was verified by direct code
reading plus a live check that `ui.app` and the new functions import and run
cleanly in the actual `ui` container — not by an automated test, and not by
a real browser click-through (no Playwright/chromium-cli available in this
environment).

**Closed the same day, at the user's request.** `Dockerfile.ui` builds `FROM
mcp-docking-base:latest` (the same base `script-runner` uses) and layers
streamlit/pandas/plotly on top — meaning the `ui` container already had
`pytest`/`pytest-mock` too, inherited from that shared base, no new
dependency needed. Added `tests/test_ui_session_state.py` (14 tests) and
`tests/test_ui_app_helpers.py` (13 tests) covering the non-widget
helper/state functions in both files, each guarded with
`pytest.importorskip("streamlit")` so they skip (not error) under the
default `script-runner`/CI suite and only actually run via
`docker compose run --rm ui python -m pytest tests/test_ui_*.py -v` — a
third, explicitly-invoked test tier (see `docs/DOCKER_DEPLOYMENT.md`'s
"GUI (Streamlit) Unit Tests" section, added alongside). This directly
covers Finding #1's fix with a real regression test
(`test_start_new_session_clears_stale_box_picker_receptor`), not just code
inspection. Scope: pure-logic helpers only (`_collect_form_params`,
`_find_receptor_pdb_for_geometry`, `_handle_protein_geometry_command`,
`apply_parsed_params`, `start_new_session`, etc.) — the `_render_*`
widget-rendering functions still have no automated coverage and need a real
Streamlit script-run context (or an `AppTest`-based harness) to test
meaningfully; that gap is now scoped and named rather than blanket "zero
coverage." All 27 new tests pass in the `ui` container; the full
`script-runner` suite stays green with the two new files reported as
skipped (781 passed / 16 skipped / 1 deselected, up from 14 skipped).

---

## Future Work additions

Two bullets added to `dev_plan.md`'s Phase 12 Future Work section, matching
findings #4 and #9 above (each with its own trigger comment in the code, per
`development_process_rules.md` §4).
