# Stage 16.8 — NiceGUI parity sign-off

Phase 16's migration rule says Streamlit is removed only once the owner's live-browser check confirms parity. This file holds that check:

1. **The feature list** the migration had to cover. It was the owner's requirement of 2026-10-05. It is recorded here because the repo never had a copy.
2. **The browser checklist**, one block per feature, for the owner to run in the NiceGUI GUI.
3. **The test parity table.** Each Streamlit test maps to its NiceGUI or core counterpart. It is moved here from `tests/test_nicegui_parity.py` when the Streamlit tests are deleted.

## 1. Feature list (owner's requirement, 2026-10-05)

**Parity first, no new features.** Port everything the current GUI does:
- input modes (RCSB, local files, folder)
- parameters and Advanced settings, including parallel docking
- chat commands
- box picker and the Docking Box fields
- run, stop and redock
- live progress and the job log
- results: affinity chart, 3D viewer, pose pickers, downloads, comparison table
- session history: load, rename, delete, revisions, undo, save results as
- settings (LLM provider, Ollama models)

**Layout:**
- chat in a persistent, collapsible side drawer;
- tabs Setup / Results / Sessions;
- switch to Results automatically when a run finishes;
- resizable panels (the multi-monitor fix).

**Viewer:** it keeps its scene and camera between updates, and a click goes straight to the box fields.

**Live updates:** by a timer, not by redrawing the page. `ui.aggrid` holds the ligand and pose lists, and downloads are served through routes.

**Technical:**
- per-user state, with nothing blocking the event loop;
- a non-root container with the same mounts.

**Out of scope:** reports, multi-user auth, new analysis.

## 2. Browser checklist

**Where:** `http://localhost:3001`, the `ui-nice` service. Streamlit is still on `http://localhost:3000` for side-by-side comparison.

**Files:** `data/benchmark_gui/receptor.pdb`, `data/benchmark_gui/actives_first25.sdf` and `data/ligands/*.sdf`.

**Before you hand it over:**
- Mark each row ✅ / ❌, and note anything odd.
- Each ❌ is fixed before the cutover.
- Every session the checklist creates can be deleted at the end, in step 34.

### Layout

| # | Steps | Expected | ✅/❌ |
|---|---|---|---|
| 1 | Open the page. | Header with title, Settings and Chat buttons. Chat drawer open on the right, with the greeting. Tabs Setup / Results / Sessions, Setup selected. | |
| 2 | Click the Chat button twice. | The drawer hides, then comes back with its messages intact. | |
| 3 | On Setup, drag the splitter between the form and the viewer side. | Both sides resize. With a receptor loaded (row 12), the viewer follows the new width. | |

### Input modes and parameters

| # | Steps | Expected | ✅/❌ |
|---|---|---|---|
| 4 | **RCSB IDs:** Protein ID `6lu7`, Ligand IDs `ATP`. | "Current parameters" shows the protein and ligand. | |
| 5 | **Local Files:** drop `receptor.pdb`, then `actives_first25.sdf`. | "Protein: receptor.pdb", and "1 ligand file(s) ready". The multi-molecule SDF is split into its 25 molecules when the run prepares the ligands (row 25 shows 25). **Clear** empties each one. | |
| 6 | **Batch Processing:** Protein ID `6lu7`, then drop two or three files from `data/ligands/` in two drops. | The count adds up across the drops. Dropping the same file name again replaces it rather than adding a second copy. | |
| 7 | Docking Box: type the Center X/Y/Z and Box X/Y/Z values. | The summary shows them. | |
| 8 | Advanced: switch the algorithm between Moldina (PSO) and Vina. | With Vina: CPUs per ligand plus the Vina caption. With Moldina: the PSO caption instead. pH and Exhaustiveness are editable. | |
| 9 | Advanced: tick Parallel ligand docking, then set Max parallel workers above the cap. | Workers is enabled only while Parallel is ticked. The worker-cap caption is shown, and the value is clamped to the bound. | |
| 10 | Session name: type a name. | It is limited in length and appears in the summary/session label once a run starts. | |

### Chat commands

| # | Steps | Expected | ✅/❌ |
|---|---|---|---|
| 11 | Type a free-text request, e.g. "dock ATP into 6lu7" (local Ollama). | A "Parsing with LLM..." spinner, then "Got it!" with the fields filled in. | |
| 12 | `use vina`, then `use moldina`. | A reply each time, and the Advanced algorithm radio follows. | |
| 13 | With no receptor loaded: `set box center as protein center`. | "fetching and preparing it now". Later "Protein center set to (…)", with Center X/Y/Z filled in. | |
| 14 | `look across all protein`. | Center and box size are set from the whole protein. | |

### Box picker

| # | Steps | Expected | ✅/❌ |
|---|---|---|---|
| 15 | Local Files inputs from row 5, then **Load receptor for box picking**. | A spinner, then the 3D viewer with the receptor and the box. | |
| 16 | Rotate or zoom the viewer, then click X + / X −, Y and Z. | The box resizes **and the camera stays where you left it**. The Box fields change by 1 Å per click. | |
| 17 | Click a receptor atom. | Center X/Y/Z become that atom's coordinates, and the caption updates. | |
| 18 | Type a new Center X. | The box moves in the viewer. | |
| 19 | **Discard picked receptor**. | The viewer goes, and the Load button is back. | |

### Run, stop, redock, progress

| # | Steps | Expected | ✅/❌ |
|---|---|---|---|
| 20 | **Run Docking** on the Local Files inputs (all 25, Moldina). | The run card: "Session … is running...", a progress bar "N / 25 ligands docked" and "Currently docking: …". Run Docking is disabled while it runs. The chat's **Run log** expander streams STEP lines. | |
| 21 | Let it finish. | The page switches to **Results** by itself. The chat says the run completed, and the run log shows "WORKFLOW COMPLETED SUCCESSFULLY" once. | |
| 22 | Change one parameter (e.g. Box X), then **Run Docking** again. | The **Confirm redock** dialog lists the steps to rerun. Cancel closes it with nothing run. Confirm starts the redock. | |
| 23 | During a run, **Stop**. | "Stop requested — stopping…", and the run ends within a few seconds as stopped. | |
| 24 | **Save Session** with no change, then with a change; also **New Session** and **Reset**. | "No parameter changes to save." / "Parameters saved…". New Session keeps the parameters and starts afresh. Reset clears everything. | |

### Results

| # | Steps | Expected | ✅/❌ |
|---|---|---|---|
| 25 | Results tab after row 21. | The status line, "Docked with Moldina (PSO)", and the affinity chart with 25 ligands. | |
| 26 | Click a row in the ligand grid, then one in the pose grid. | The 3D viewer shows that ligand's pose in the receptor. The pose caption reads "Mode N (… kcal/mol)". | |
| 27 | Downloads: change the pose format, then click a pose download and one other file. | Each file downloads with its real name and contents. | |
| 28 | **Session Log** expander, then **Download full log**. | The log's tail is shown, and the whole `job.log` downloads. | |
| 29 | **Save results as…** with `trial_a`, then save `trial_a` again; also try `save results as trial_b` in chat. | The first save succeeds. The second shows the conflict choices (Overwrite / Save as `trial_a_HHMMSS` / Cancel), and each works. The chat command saves too. | |

### Sessions

| # | Steps | Expected | ✅/❌ |
|---|---|---|---|
| 30 | Sessions tab. | Every session is listed (not only the newest 10), plus a Failed Sessions list, and the active session's card. | |
| 31 | **Rename** a session (Save), then **Load** it. | The new name shows in the row, the chat label and the Results status. Load switches to Results and shows that session. | |
| 32 | Revision history: `show history` in chat, then `undo`, then **Restore revision** on an older one. | The table and current revision match the chat's reply. Undo and Restore bring the old parameters back into the form. | |
| 33 | Tick two sessions → **Compare selected sessions**. | Results shows the comparison table, with one column per session. | |
| 34 | **Delete** each test session from this checklist → Confirm. | It disappears from the list. Cancel leaves it. | |

### Settings

| # | Steps | Expected | ✅/❌ |
|---|---|---|---|
| 35 | Settings → provider Ollama. | The Model list is filled from the local Ollama models (or "no models" if Ollama is down). **Apply settings** saves the setting, and a reload keeps it. | |
| 36 | Settings → a provider whose client can't be built (e.g. one needing an API key, with the key left empty) → Apply. | An error is shown, and nothing is saved: the previous settings stay in use. | |

### Headless pre-run (2026-10-25)

Before this checklist was handed over, a Playwright script ran it against
the live `mcp-ui-nice` (headless Chromium 1600 × 1000, on the GUI
container's network). It was a scratch script, not in the repo. Its run used:
- the receptor plus 3 ligands from `data/ligands/` (25 for row 5's upload);
- a reference session for Compare;
- a cleanup of every session, prep job and upload it created.

The result: 34 of 34 automated rows passed, with no page errors.
- **Row 16:** each step logged `[perf] … mode=box`, a box-only redraw.
  That is the code path that keeps the camera; the owner confirmed the
  camera itself in 16.5.
- **Row 17:** a canvas click hit an atom, and Center X changed.
- **Row 27:** every download link, 16 of them in 4 pose formats, was
  served with content.
- **Row 30:** 187 sessions were listed.
- **Row 33:** the comparison had one column per session.

Left to the owner:
- **Row 11:** needs the LLM.
- **Row 35:** only opened, not applied, so the owner's saved settings
  stay as they are.
- **Row 36:** would change the settings.
- **Anything that needs eyes:** the layout's look, the camera, the chart.

The pre-run also surfaced one behaviour, the same in both GUIs, so it is
not a parity gap:
- A **redock stopped with Stop** marks the whole session cancelled. Its
  Results then read "Session … failed. Workflow cancelled by user
  request", and the previous run's results are no longer shown.
- `workflow_results.json` is rewritten by the redock, as it is from
  Streamlit. Whether the earlier pose files survive wasn't checked
  before the test session was deleted.

### Sign-off

- [ ] All rows ✅, or each ❌ fixed and re-checked. Owner: ______ Date: ______

## 3. Test parity table

*(Moved from `tests/test_nicegui_parity.py` at the cutover.)*
