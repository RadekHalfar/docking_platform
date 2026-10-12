# GUI Guide

The Streamlit UI (`ui/app.py`) is a browser front-end for the docking platform,
served at http://localhost:3000 by the `ui` service in `docker-compose.yml`.
It has three panels — Chat, Input & Parameters, Results — plus a sidebar for
LLM settings and session history.

Phase 16 is moving the GUI to NiceGUI. Until that is finished (Stage 16.8),
the NiceGUI version runs alongside as the `ui-nice` service at
http://localhost:3001: a header with Settings, a collapsible chat drawer on
the right, and the tabs Setup / Results / Sessions. Since Stage 16.4 the
chat drawer takes every command and LLM message, and the Setup tab has the
whole form (inputs, Docking Box, Advanced, Run / Save Session / New
Session / Reset, Stop and live progress). Since Stage 16.5 the Setup tab's
right side has the 3D box picker. Results (16.6) and Sessions with Load
(16.7) are still to come, so use the Streamlit GUI on port 3000 for real
work until then. Where the two
differ, this guide says so.

Uploaded files (either GUI) are each saved in a folder of their own under
`orchestrator_output/uploads/`, so uploading another file with the same
name, from another tab or the other GUI, never changes the file an earlier
session docked. In the NiceGUI GUI each drop of ligand files adds to the
list ("N ligand file(s) ready"); **Clear** empties it.

Whenever a session is active, the Chat panel shows a small **Session:
name (`job_id`)** line above the "Chat" title, so it stays clear which
session you're currently in — this disappears once you click **New
Session**/**Reset** or before you've started a first session.

## Prerequisites

- Docker Desktop
- The platform stack running: `docker compose up -d`
- An LLM backend reachable from the `ui` container (Ollama by default, or a
  cloud/OpenAI-compatible provider — see below)

## Persistent Settings

The sidebar **Settings** panel lets you change the LLM provider, model,
endpoint, and API key without editing `.env` or restarting the container.

- **LLM Provider**: `Ollama (local)`, `OpenAI`, `Anthropic`,
  `MetaCentrum (e-INFRA CZ)`, or `Other local server` (any OpenAI-compatible
  endpoint — LM Studio, vLLM, Jan, llama.cpp, Groq, Together AI).
- **Model**: for `Ollama (local)`, this is a dropdown populated live from the
  Ollama server's `/api/tags` (i.e. whatever you've `ollama pull`-ed), so you
  don't need to remember exact tag names. It falls back to a free-text field
  if Ollama can't be reached. For every other provider it's a plain text
  field (e.g. `gpt-4o-mini`, `claude-haiku-4-5-20251001`).
- **Endpoint URL** (Ollama only): tucked under an **Advanced** expander,
  collapsed by default, since the bundled `http://ollama:11434` is correct
  for almost everyone. Change it only if you want to point at a different
  Ollama instance — e.g. one running on a separate GPU workstation on your
  network — instead of the container that ships with the platform.
- **Endpoint URL** / **Base URL** (Other local server): always visible, since
  there's no sensible default — these fields *are* how you tell the platform
  where your custom server lives (LM Studio, vLLM, etc.).
- **API Key**: shown only for providers that need one (OpenAI, Anthropic,
  MetaCentrum, Other local server). Entered per-session — **never written to
  disk**.

Click **Apply settings** to rebuild the LLM client immediately and persist
provider/model/endpoint to `~/.docking_platform_settings.json` inside the
container (mounted to `./cache/.docking_platform_settings.json` on the host
via `docker-compose.yml`, so it survives both a browser refresh — which
resets Streamlit's in-memory session — and a container restart).

To reset to the values from `.env` / `docker-compose.yml`, delete
`cache/.docking_platform_settings.json` and refresh the page.

## Chat: Describing Your Workflow

Type a request into the chat box (e.g. "Download 6lu7 and dock ATP with
center 10 20 30 box 25 30 35") and the LLM extracts protein/ligand/box
parameters into the Input & Parameters panel, telling you what's still
missing if anything is.

Follow-up messages in the same chat build on what's already been
collected rather than starting over — e.g. after the message above,
"can you add one atp ligand?" adds a second `ATP` to the existing ligand
list (`["ATP", "ATP"]`) and keeps the protein/center/box you already
gave, instead of forgetting them. Phrasing like "change ligand to HEM"
replaces the list instead of adding to it, and "remove one ATP" removes
one. This follow-up parsing only kicks in once something has already
been extracted in the current chat — the very first message in a chat is
always parsed as a complete, standalone request.

While a session runs, its step-by-step log streams into a **Run log**
expander under the chat, not into the chat messages themselves, so a
message you send mid-run stays as you typed it. In the NiceGUI GUI the chat
input is disabled while the LLM reads a message ("Parsing with LLM...").
If you Load a session, restore a revision, click **Run Docking** or start a
**New Session** before it answers, the answer is not applied: it says the
session changed and asks you to send the message again.

### Auto-Filling the Docking Box from the Protein

Once a protein has been loaded (via **Run Docking**, or the box picker's
**Load receptor for box picking** button — see below), two chat commands
compute the docking box directly from that protein's own structure instead
of you typing or picking coordinates:

- **"set up box center as protein center"** (or "use protein center",
  "center the box on the protein") sets `center` to the midpoint of the
  protein's bounding box.
- **"look across all protein"** (or "cover the whole protein", "whole
  protein box") sets `box_size` to the protein's bounding box extent per
  axis, **+10%** margin, so the box fully covers the structure. This is
  effectively blind docking — a large box searches the whole protein
  instead of one focused site, which is slower and less targeted than a
  hand-picked box around a known binding site (see
  [DOCKING_PARAMETERS.md](DOCKING_PARAMETERS.md#determining-docking-box-parameters)).

Both commands write straight into the same **Docking Box** manual fields
every other box-setting path uses (typed values, the visual picker), so
**Run Docking** needs no separate step afterward, and a value can still be
fine-tuned by hand or overwritten by a later command. If no protein is
loaded yet, the assistant asks you to load one first rather than guessing —
this codebase never invents `center`/`box_size` values. These two commands
are recognized directly by pattern matching, not sent to the LLM (which has
no access to the protein's coordinates and must never guess them either).

## Naming a Session

The **Input & Parameters** panel has an optional **Session name** field, just
above the **Run Docking** button (60 characters max). It's a free-text label
purely for your own reference — it doesn't need to be unique, and it never
replaces the system-generated job ID, which remains the actual unique
identifier on disk and everywhere else in the app. Leave it blank and the
session just shows up under its job ID, exactly as before this field existed.

## Choosing the Docking Algorithm

The **Advanced** expander under the Docking Box starts with a **Docking
algorithm** selector:

- **Moldina (PSO)** (default) — Particle Swarm Optimization. Fast (seconds per
  ligand), always 1 CPU per ligand, and ignores **Exhaustiveness**.
- **Vina (Monte Carlo)** — AutoDock Vina's own search in the same binary.
  Much slower per ligand (about 1.5 minutes for 6lu7/ATP at the defaults),
  but its search is more thorough. A **CPUs per ligand** field appears, and
  both it and Exhaustiveness change how long each ligand takes. In the
  NiceGUI GUI it is a number field that takes whole numbers from 1 up.

The chat commands "use vina", "switch to PSO" and "set algorithm to vina" move
the selector too, without an LLM call (see
[LLM_ORCHESTRATION.md](LLM_ORCHESTRATION.md)). A chat message changes only
the fields it names: one that doesn't name an algorithm, CPUs per ligand,
Exhaustiveness or pH leaves your values in those fields alone.

**Max parallel workers** goes up to the docking service's CPU limit divided by
CPUs per ligand, so workers × CPUs never oversubscribe it. Switching algorithm
resets it to that algorithm's benchmarked best
([BATCH_DOCKING.md](BATCH_DOCKING.md#benchmark-baseline-2026-10-05)); the
caption under it says which. Loading a session keeps the worker count it was
saved with.

The **Current parameters** summary shows the selected algorithm, the results
panel says which one produced a session's results ("Docked with …"), and the
Compare Sessions table has an **Algorithm** row. Sessions from before the
selector existed read as Moldina (PSO), which is what they ran. Switching
algorithm in an active session and clicking **Run Docking** redocks only
docking and visualization.

## Redocking After a Parameter Change

The GUI keeps a continuing **session** for the job you just ran, the same way
the CLI's `--interactive` mode does: `st.session_state.interactive_session`
holds one `InteractiveSession` bound to that job's `WorkflowStateManager`, so
editing parameters and clicking **Run Docking** again doesn't start a
brand-new, unrelated session — it redocks the same one.

- **First click** (no active session yet): behaves exactly as before — a new
  job ID is generated and all five steps run.
- **Later clicks**, with a session already active: the form's current
  parameters are diffed against the last-saved checkpoint. If nothing
  changed, you're told there's nothing to redock. If something did, only the
  steps that actually depend on the changed parameter(s) are planned for
  re-execution (e.g. changing the docking box only reruns **Run docking**
  and **Generate visualization** — protein/ligand preparation is skipped).
  The **Run Docking**/**New Session**/**Reset** buttons are replaced by a
  **rerun plan** summary and a **Confirm redock** / **Cancel** button pair —
  nothing runs until you click **Confirm redock**, mirroring the CLI's
  `[Y/n]` prompt before a partial rerun.
- If a required parameter for one of the planned steps is missing (e.g. you
  cleared the docking box), redocking is blocked with an error naming exactly
  which parameter and step need it, instead of silently failing partway
  through.
- `job.log` and `workflow_results.json` for that session accumulate across
  every redock — the results panel and downloads always reflect the full
  history, not just the most recent partial run.
- Clicking **Run Docking** checkpoints the new parameters straight away,
  before you confirm. If you then **Cancel** (or the run is stopped or
  fails), the session remembers that those parameters were never run to
  completion, and the next **Run Docking** offers the same plan again
  instead of reporting "nothing to redock". The memory itself only lasts
  while the tab is open, but **Load** restores it from the session's
  history on disk: if the session's latest parameters never ran (e.g. a
  restored revision), the next **Run Docking** plans every step they affect.
- If the last run was **stopped** or failed part-way, the steps it never
  reached are planned again on the next **Run Docking** — with the same
  parameters or changed ones.

## Revision History and Undo

Every parameter checkpoint (first run, each redock, each restore) is a
numbered **revision** of the session. While a session is active, the
**Revision history** expander under **Current parameters** shows them as a
table (revision, time, event, details), with a caption naming the revision
the parameters are currently at.

To go back to earlier parameters, pick a revision in the expander's
**Revision** dropdown and click **Restore revision**. The dropdown lists only
revisions that have parameters. Restoring:

- fills the form with that revision's parameters (the box picker's viewer
  shows the restored box),
  replacing everything, including fields the revision didn't set;
- is recorded as a new `undo_restore` revision, so it is in `job.log` and
  can itself be undone;
- discards any **Save Session** draft and any staged redock plan;
- does **not** change the results panel, which still shows the last run.
  Click **Run Docking** afterwards. The plan reruns every step the restored
  parameters affect (normally all of them), even though the form now matches
  the checkpoint.

**Restore revision** is disabled while the session is running: the running
job re-reads the latest parameters before each step, so a restore would
change it mid-run.

The same commands work in chat, without an LLM call: `show history` (or
`history`) posts the table, and `undo 3` (or `undo rev 3`) restores
revision 3. The number has to follow `undo` directly: a bare `undo`, or a
sentence like "undo the box change and set exhaustiveness to 16", asks which
revision instead of guessing. Like the dropdown, chat only restores
revisions marked `[params]` in the table.

## Saving Results Bundles

To keep a copy of a run's results under a name of your own, before a redock
replaces them, open **Save results as…** in the results panel. It sits right
below the 3D viewer, above **Session Log**.
It appears only when you view the **active** session's completed results.
Enter a **Bundle name**, choose what to **Include** (docking + visualization,
docking results only, or visualization only) and click **Save results
bundle**. The bundle is written to

```
orchestrator_output/job_<id>/saved_results/<name>/
  metadata.json        ← name, scope, revision, timestamp, parameters
  docking_results/     ← docking or all
  visualization/       ← visualization or all
  docking_box.json     ← the box from the session's parameters
```

This is the same layout the CLI's `save results as <name>` writes. The chat
replies with the bundle's path and contents. The results panel's **Other
Files** downloads don't list bundle files, since they are copies of files
already listed there.

The button is disabled, with the reason shown under it, while:

- the session is running;
- parameters changed since the last completed run (after a redock plan you
  cancelled, a stopped or failed run, or **Restore revision**). The saved
  results would not match the session's parameters. Click **Run Docking**
  first, or restore the revision the results came from. This is read from
  the session's revision history on disk, so it still applies after you
  **Load** the session again or reload the page;
- the session's last run did not complete.

If the name already exists, choose **Overwrite**, which replaces the whole
bundle (the new copy is written first and swapped in, so a failed overwrite,
e.g. a full disk, keeps the old bundle), or **Save as `<name>_HHMMSS`** to keep both. **Cancel** dismisses
the choice.

Bundle names start with a letter or digit and contain only letters, digits,
`.`, `-` and `_`, up to 64 characters. A few names are reserved, such as
`checkpoints` and `visualization`.

In chat, without an LLM call: `save results as trial_a`, `save docking as
trial_a` or `save visualization as trial_a`. If the name exists, the chat
points you to the same Overwrite / Save as buttons.

Copying a session's results can take a while (half a minute for a large
session on a Docker Desktop bind mount). The NiceGUI GUI copies in the
background: the chat shows "Saving results...", and the chat input,
**Run Docking**, **New Session** and **Reset** are off until the reply
comes, while other tabs keep working.

Not to be confused with **Save Session** (next section), which saves the
form's *parameters* as a draft, not results.

## Saving Parameters Without Docking

Editing a parameter (e.g. the docking box size) does **not** persist to
disk by itself — it only lives in the form until you either click **Run
Docking** (which checkpoints it as part of starting or redocking) or
click **Save Session**, next to **Run Docking**. **Save Session** writes
your current form values to disk as a draft without running or
redocking anything, so they survive switching to a different session
(**Load**) and back. It's disabled when there's no active session to
save into, and reports "No parameter changes to save" if the form matches
the session's last checkpoint. In that case it also removes any draft saved
earlier, so setting a field back and clicking **Save Session** really undoes
the saved edit (otherwise **Load** would bring the draft back).

If you edit a parameter and switch sessions **without** clicking **Save
Session** first, that edit is lost, the same as before this button
existed — it's an explicit, not automatic, save.

## Starting Fresh: New Session vs. Reset

Once a session is active, the button row next to **Run Docking** offers two
different ways to move on, each preserving something different:

- **New Session** — detaches from the current session (job ID, chat, the
  active `WorkflowStateManager`) but keeps your entered parameters and LLM
  settings, so you can immediately start a related but independent docking
  run without retyping the form. Disabled when there's no active session to
  detach from, and while this browser tab has a session actively running —
  detaching from a still-running session would leave it running in the
  background with no way to see it finish or stop it.
- **Reset** — clears everything in the form, including entered parameters
  (only your LLM provider settings survive). Use this when you want a
  genuinely blank slate. Disabled while this browser tab has a session
  actively running, for the same reason as New Session above.

## Session History

Every completed session (one whose `workflow_results.json` has
`"status": "completed"`) appears as a row in the sidebar's **Session
History** list, newest first. If you gave the session a name, that name is
shown in bold ahead of the job ID; otherwise the row falls back to the job
ID, protein ID, and ligand count only. The list is rebuilt from disk on
every page load, so it reflects sessions run from the GUI or the CLI
(`nlp_orchestrator.py`). Each row has:

- A **checkbox** — select 2+ sessions to compare (see below).
- A **Load** button — both shows that session's results in the **Session
  Viewer** tab (see below) *and* resumes it as the active interactive
  session, so you can immediately continue redocking it (with a changed
  parameter, say) instead of only viewing it read-only. This also means a
  browser refresh followed by **Load** picks a session back up exactly where
  you left off, rather than starting a new one on the next "Run Docking".
  It also restores that session's parameters into the **Current
  parameters** panel and the input widgets (preferring anything saved via
  **Save Session** over the last checkpoint, if the two differ), and the
  session indicator above the chat updates to it. Every **Load** button
  is disabled while this browser tab has a session actively running —
  switching sessions mid-run would leave the running one orphaned in the
  background, same as New Session/Reset above.
- A **Rename** button — toggles an inline text field to change the
  session's name after the fact (e.g. once you know which run turned out to
  matter). Click **Save** to write the new name straight into that session's
  `workflow_results.json`, or **Cancel** to leave it unchanged.
- A **Delete** button — toggles a warning plus **Confirm delete** /
  **Cancel**; confirming permanently removes the session's entire output
  directory. Disabled (with an explanatory caption) if this browser tab is
  currently running a redock of that same session — use **Stop** (see
  below) first. This only detects the current tab's own in-progress run; it
  cannot see another browser tab or the CLI acting on the same job ID.

## Failed Sessions

A session that ended with `"status": "failed"` or `"cancelled"` (stopped via
the **Stop** button, see below) doesn't show up in Session History — it
appears instead in the sidebar's **Failed Sessions** list, with the same
Load/Rename/Delete controls, plus a short preview of the first recorded
error in place of the ligand count. This keeps a failed or intentionally
stopped run from silently disappearing while still keeping it out of the
"successful runs" list Session History and job comparison rely on.

## Stopping a Running Session

While a session is running, the results panel shows a **Stop** button next
to the progress bar. What happens depends on the step that's running:

- **During docking**, the stop takes effect within about a second: no
  further ligands start, and any ligand being docked at that moment has
  its Moldina process killed (its result is lost). With hundreds of
  ligands queued, you don't wait for the rest of the batch.
- **During any other step** (download, ligand/protein preparation,
  visualization), that step's single service call finishes first — these
  are short — and no further step starts after it.

The session then settles as `"cancelled"` in `job.log` and
`workflow_results.json` (the chat shows "Session stopped."), and shows up
under **Failed Sessions** so it can be inspected, redocked, or deleted like
any other non-completed session. Ligands docked before the stop keep their
output files in `docking_results/`, but the session isn't treated as
docked: a later redock docks every ligand again.

## Viewing a Session (Live or Historical)

The Results panel's **Session Viewer** tab shows either:

- the session you just started with **Run Docking** — progress streams in
  while it runs, then the chart/image/downloads appear on completion; or
- a past session you clicked **Load** on in the sidebar — its results are
  read directly from `workflow_results.json` on disk, so this works for
  sessions from a previous browser session or run via the CLI, with no
  re-run needed.

If the session has a name, the status caption ("... is running", "...
completed", "Viewing session ...") shows it alongside the job ID, so it
stays clear which run you're looking at even after switching between
several loaded sessions.

Once a session's results are showing, you get:

- An affinity bar chart (best binder first), in a fixed-height scrollable
  window so a long chart doesn't push the 3D viewer down the page.
- An in-browser **3Dmol.js** viewer (no PyMOL, no noVNC — renders entirely
  client-side): the prepared receptor as cartoon, and each docked ligand as
  colored sticks (green = best affinity, red = worst).
- A **Session Log** expander directly below the viewer (running, completed,
  or failed/cancelled) — the last ~500 lines of `job.log`, the same
  human-readable narration the CLI's interactive session writes (start/step/
  finish lines, parameter diffs, full tracebacks on failure), plus a button
  to download the whole file. When a session has no visualization yet (still
  running, or failed before the `visualize` step), the log still appears in
  this same spot — it isn't skipped just because there's nothing to render
  above it.
- If a ligand has more than one docked pose, a **Pose per ligand** dropdown
  below the log switches which mode is shown in the viewer above, labeled
  with that pose's affinity.
- A **Docked Poses** download section: a single **Download format** picker
  (`pdbqt` / `pdb` / `sdf` / `mol2`, defaulting to `pdbqt` — Moldina's native
  output) applies to every ligand's download button, so you can grab the
  whole multi-pose file per ligand in whichever format your downstream tool
  expects. PDB/SDF/MOL2 are generated once per session (during the same step
  that used to run PyMOL) via a Meeko PDBQT→RDKit round trip that restores
  proper bond orders, with an OpenBabel fallback if that fails for a
  particular ligand.
- An **Other Files** section below it for anything else in the session's
  directory (e.g. `workflow_results.json`).

### Picking the Docking Box Visually

Before running a session, the **Input & Parameters** panel has a **Pick
Docking Box Visually** control: click **Load receptor for box picking** to
run just enough of the workflow to fetch and prepare the receptor (no
ligands, no docking yet), then the same 3Dmol.js viewer appears with:

- **X − / X + … Z − / Z +** buttons above the viewer — grow or shrink the
  box along one axis by 1 Å per click (never below 1 Å).
- Click any receptor atom to set the box **center** — a red marker and label
  show what you clicked, and a blue box outline shows the resulting
  search region. Residues inside the box are highlighted (orange cartoon
  plus thin side-chain sticks) so you can see exactly which part of the
  protein will be searched.
- Red/green/blue arrows from the box center, labeled **X**/**Y**/**Z**,
  show which box edge each Box size field controls.

The viewer always draws exactly what the **Docking Box** fields below it
hold — those six fields are the only copy of the box. A click writes the
center into them, the − / + buttons write the size, and anything else that
sets the box (typing, a chat message with coordinates, a geometry command,
**Load**, **Restore revision**) moves the drawn box too. So a box that is
already set shows as soon as the receptor loads, and **Run Docking** needs
no separate step.

While the Box size fields are empty (or hold something that isn't a
positive number), the viewer draws a 20 Å placeholder and says so under
the viewer; a click or a − / + step fills that 20 Å size into all three
fields (a step then changes its own axis, e.g. **X +** gives 21 / 20 / 20),
so the box you saw is the box that gets docked.

In the NiceGUI GUI the picker sits on the right of the Setup tab, next to
the form, and its viewer keeps your camera. Rotate or zoom it, then click
an atom, press − / + or type in the Docking Box fields: only the box
redraws, and the view stays where you left it. Streamlit rebuilt the whole
scene on every change and reset the camera. The clicked atom's label stays
while the box is centred on that atom. A failed receptor load's error
stays shown until the next **Load receptor for box picking**.

This is a lighter, purely in-browser alternative to the CLI's
`pymol-interactive`-based box selection (see
[docs/PYMOL_INTERACTIVE.md](PYMOL_INTERACTIVE.md)) — it doesn't require that
service to be running.

Once a receptor is loaded here (or a job has already run
`prepare_protein`), the chat commands described in
[Auto-Filling the Docking Box from the Protein](#auto-filling-the-docking-box-from-the-protein)
are a faster alternative to clicking: "set up box center as protein
center" / "look across all protein" compute the same kind of values
without needing to click an atom or step the size.

A receptor loaded here belongs to one protein. It is discarded when you
start a **New Session** or a fresh job, and when a **Load** or **Restore
revision** switches to a different protein — so a geometry command never
computes from a protein that is no longer in play. Load the receptor again
for the new protein if you want to pick visually.

## Comparing Sessions

Check two or more sessions in the sidebar's Session History and click
**Compare selected sessions**. The **Compare Sessions** tab in the Results
panel shows a table with ligands as rows and sessions as columns — column
headers use each session's name when it has one, falling back to the job ID
otherwise; cells are the best binding affinity (kcal/mol) for that ligand in
that session, or "N/A" if the ligand wasn't docked in that session. An
**Algorithm** row under the headers names the docking algorithm each session
used. The
strongest predicted binder per ligand (most negative kcal/mol) is
highlighted green — rendered as a plain HTML table (not `st.dataframe`) so
the highlight stays readable in both light and dark theme.

## Performance

Phase 16 replaces this Streamlit GUI with NiceGUI (`dev_plan.md`, Part 9).
To show whether the port helped, the Streamlit GUI was timed before it
(Stage 16.0), and the NiceGUI GUI is timed after it (Stage 16.8). Both use
the same tools and the same reference session.

### Tools

| What | Tool |
|---|---|
| Interaction latency: from a click (or Enter in a text field) until the page stops changing for 300 ms | [`benchmark_gui_latency.js`](../benchmark_gui_latency.js), a DevTools console snippet. By hand: paste it into the console (Chrome blocks the first paste into DevTools; type `allow pasting` and press Enter, then paste again), do the action 5+ times, letting the page settle in between, then run `__lat.summary("<label>")`. For chat commands, run `__lat.summary("<label>", "enter")` instead, so the click that focused the chat box is left out. The median is the figure to record. |
| GUI container CPU% and memory | [`benchmark_gui.py`](../benchmark_gui.py), `docker stats` on `mcp-ui`. Either for a fixed `--duration`, or with `--from-driver` between the driver's `SAMPLE-START` / `SAMPLE-STOP` lines. |
| Viewer re-render time and render count | `?perf` in `ui/components/mol_viewer/index.html` (Streamlit) and `ui_nice/mol_viewer.js` (NiceGUI, `http://localhost:3001/?perf`; there a full scene build logs `mode=box_picker` / `view`, and a box-only redraw `mode=box`). With the GUI opened as `http://localhost:3000/?perf`, each viewer render logs `[perf] render #N mode=… atoms=… render=…ms frame=…ms` to the console and collects it in `__viewerPerf`. Without `?perf`, nothing is recorded. `render` is the synchronous JS time to rebuild and draw the scene; `frame` runs until the next animation frame, so it includes the paint. |
| All of the above, scripted | [`benchmark_gui_browser.py`](../benchmark_gui_browser.py) drives the GUI in headless Chromium (Playwright). It injects the same latency snippet, performs the actions, counts the `[perf]` renders, and prints the sampling cues. |

The scripted measurement is one pipeline, run from the repo root in Git
Bash, with the stack running:

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network container:mcp-ui     -v "$(pwd -W):/repo:ro" -v "$(pwd -W)/docs/benchmarks:/out"     mcr.microsoft.com/playwright/python:v1.55.0-noble     sh -c "pip install -q playwright==1.55.0 && python -u /repo/benchmark_gui_browser.py            --out /out/<date>_gui_streamlit_browser.json --screenshot /out/<date>_gui_streamlit_final.png"   | python3 benchmark_gui.py --from-driver --container mcp-ui            --json-out docs/benchmarks/<date>_gui_streamlit_stats.json
```

The pipeline starts a real docking run on the live stack, so a new session
appears in Session History. It takes about 5 minutes. The Box X steps are
made in + / − pairs, so the box ends where it started.

### Reference session

- **Inputs:** the receptor and the first 25 molecules of the 295-ligand
  session `job_20260817_111507_854b2c`
  (`orchestrator_output/<job>/downloads/receptor.pdb` and
  `actives_final.sdf`). They are copied to the git-ignored
  `data/benchmark_gui/` as `receptor.pdb` and `actives_first25.sdf`.
- **Run settings:** Local Files, box center (19.354, 16.789, 29.19), size
  20 × 20 × 20 Å, default algorithm (Moldina PSO), default Parallel (off),
  so the 25 ligands dock one after another.
- **Machine state:** the laptop on mains power in normal (not
  power-saving) mode, for both 16.0 and 16.8. Power-saving throttles the
  CPU and skews every number.
- **Phases** (the driver's order):
  - **setup**: inputs uploaded and the receptor loaded for box picking.
  - **running**: from Run Docking until the Stop button goes away.
  - **completed**: the finished session, first left idle for 60 s, then
    clicked.
- **Not measured: a 295-ligand session.** Session History lists only the 10
  newest sessions, so `job_20260817_111507_854b2c` can't be loaded from
  it. Measuring a large session needs a fresh 295-ligand run, about
  11 minutes of sequential docking. Add it in 16.8 if the 25-ligand numbers
  leave the question open.

### Results

Stage 16.0 baseline, Streamlit, 2026-10-07, run by `benchmark_gui_browser.py`
(headless Chromium 1600 × 1000).
- **Session:** `job_20261007_144016_08e8a4`, 25 of 25 docked, Moldina,
  1 worker, about 121 s from Run to finish.
- **Raw data:** [`docs/benchmarks/2026-10-07_gui_streamlit_browser.json`](benchmarks/2026-10-07_gui_streamlit_browser.json)
  and [`docs/benchmarks/2026-10-07_gui_streamlit_stats.json`](benchmarks/2026-10-07_gui_streamlit_stats.json).
  In the stats file, `measured_at` is when the pipeline started. The
  per-window `started_at` was added after this run.
- **Latency:** median of 5 actions; Box X ± is 5 pairs, so 10 samples.

| Phase | Measurement | Streamlit (16.0) | NiceGUI (16.8) |
|---|---|---|---|
| setup | Box X ± latency, median (min–max) | 491 ms (186–637) | |
| setup | Viewer renders per Box X ± click; median render | 1.0 (full rebuild); 184 ms | |
| running | `show history` latency, median (min–max) | 4,736 ms (2,492–12,823) | |
| running | `mcp-ui` CPU mean / peak | 10.1% / 27.6% (52 samples, 115 s) | |
| running | `mcp-ui` memory mean / peak | 132 MiB / 141 MiB | |
| completed | `mcp-ui` CPU mean / peak, idle 60 s | 2.0% / 8.7% (26 samples) | |
| completed | `mcp-ui` memory mean / peak, idle | 141 MiB / 142 MiB | |
| completed | Viewer renders while idle | 0 | |
| completed | `show history` latency, median (min–max) | 926 ms (243–954) | |
| completed | Load latency, median (min–max) | 1,026 ms (202–1,087) | |
| completed | Box X ± latency, median (min–max) | 899 ms (220–996) | |
| completed | Viewer renders per Box X ± click; median render | 1.0 (full rebuild); 190 ms | |

What the numbers say about Streamlit:
- **Every box step rebuilds the whole viewer scene** (about 190 ms of JS
  for the 2,186-atom receptor), and that is also what resets the camera.
  Finding 4's fix should take this to an overlay update, not a rebuild.
- **While a job runs, interactions wait for the page's 2 s poll cycle.** A
  `show history` takes 2.5–12.8 s, against under 1 s once the session has
  finished. The snippet also counted one sample as dropped: it was
  superseded before it settled. During polling, the 300 ms quiet window
  is a rough edge, so the running latency is the noisiest figure here.
- **The polling itself costs CPU:** about 10% mean CPU while the job runs,
  against 2% idle.
- **Interactions are slower on a finished session than in setup** (box step
  899 ms vs 491 ms), most likely because each rerun also redraws the results panel, which is
  now full (not separately verified).

### Stage 16.5 interim: the NiceGUI box picker

Measured 2026-10-12 in the setup phase only (the full re-time is 16.8).
- **Setup:** headless Chromium 1600 × 1000 (Playwright 1.55), against a
  side `ui-nice` container on the live stack. Same receptor
  (`data/benchmark_gui/receptor.pdb`, 2,186 atoms) and box (19.354,
  16.789, 29.19; 20 Å).
- **Canvas:** the NiceGUI viewer is 55vh tall, against Streamlit's
  450 px, so each frame costs a little more.

| Measurement | Streamlit (16.0) | NiceGUI (16.5) |
|---|---|---|
| Viewer work per Box X ± click | full rebuild, 184 ms, camera reset | box-only redraw (`mode=box`), median 143 ms, camera kept |
| Click → fields and summary updated | (inside the 491 ms) | about 94 ms |
| Click → viewer redrawn, median of 10 | (inside the 491 ms) | 222 ms |
| `benchmark_gui_latency.js` median (min–max) | 491 ms (186–637) | 726 ms (706–744) |
| Scene build when the receptor loads (once) | 184 ms, every rerun | 496 ms, once |

- **The latency snippet's NiceGUI figure is mostly animation, not work.**
  It waits for 300 ms without DOM changes, and Quasar buttons keep
  changing the DOM after the app is done:
  - the click ripple ends about 760 ms after the click;
  - the hover tooltip of the next button appears about 390 ms after it.

  Streamlit's buttons have neither.
- **For 16.8:** decide whether the snippet should ignore `.q-ripple` and
  `.q-tooltip` mutations. That would leave the 16.0 baseline valid, since
  Streamlit has neither.
- **3Dmol labels are slow:** each `addLabel` draws a texture and
  re-renders, and `removeLabel` re-renders too. A first version that
  recreated the X/Y/Z labels on each step took 298 ms per redraw. The
  viewer now makes them once and only moves them.

## Code Structure (for developers)

Since Phase 16 (Stages 16.1–16.2, plus 16.4's additions) the GUI's logic is
split from Streamlit, so the NiceGUI port (`ui_nice/`, below) reuses it
rather than rewriting it:

- **`ui/core/`** — framework-free, no `streamlit` or `nicegui` import:
  - `state.py`: `GuiSession`, one browser client's state, never module-global.
  - `form.py`: form ↔ workflow parameters, the "Current parameters"
    summary, the worker-count bound, Protein/Ligand ID commits, New Session
    and Reset.
  - `uploads.py`: saving an uploaded file (basename only, in a new
    directory of its own under the shared upload dir) and taking uploads
    into the parameters (NiceGUI appends each drop: `append=True`).
  - `run_control.py`: Run, redock plan/confirm/cancel, Stop, Save Session,
    bridge polling. A run's log lines go to `state.run_log`, a fresh list
    per run, never into the chat messages.
  - `sessions.py`: Load, rename, delete, revision history and undo.
  - `chat.py`: chat commands (geometry, algorithm, history, save) and the
    LLM parse. `parse_message()` is the blocking LLM call and touches no
    state, so a GUI with an event loop can run it on a worker thread and
    then apply the result with `apply_parsed_message()`. Passing it the
    `parse_context()` taken before the call drops a result whose session
    (or job) changed meanwhile. `submit()` takes `apply_geometry` and
    `run_save`, so a GUI with an event loop can do the geometry command's
    receptor read and the save's copy off it; by default both run inline.
  - `box_picker.py`: the box picker (receptor prep job, viewer click, − / +
    steps) and protein geometry, split into `read_protein_geometry()` (the
    file read, no state) and `set_protein_geometry()`.
    `process_box_picker_bridge()`, like `run_control.process_docking_bridge()`,
    returns whether it changed the state.
  - `save_results.py`: Save results as…, its guard and the name-clash flow,
    in three steps: `plan_save()` (checks), `write_bundle()` (the copy, no
    GUI state, so it can run on a worker thread) and `finish_save()` (the
    reply and the name clash). `save_results()` runs all three.
  - `results_view.py`: what the results panel shows, as data: progress,
    poses, download lists, the job log.

  Each action appends its chat replies to `state.messages`, all through
  `ui.core.state.reply()`. It returns an
  `Outcome`: `refresh` to redraw the page, `error` to show next to the
  control. The bridge class and the LLM parse functions are passed in, so
  tests swap in fakes. File reads that Streamlit caches take a `read_bytes`
  callable.
- **`ui/session_state.py`** — the Streamlit adapter. `gui_session()` is a
  view of `st.session_state` with `GuiSession`'s attributes. The page's
  keys are unchanged (`_job_done`, `_pending_rerun`, the `param_*` widget
  keys), so the `ui/core` functions run against it as they are.
- **`ui/app.py`** — the Streamlit page; its handlers are thin wrappers that
  carry out an `Outcome` with `st.error` / `st.rerun()`, and its render
  functions draw `ui/core`'s data with Streamlit widgets.
- **`ui_nice/`** (Stage 16.3 on) — the NiceGUI GUI, served by the `ui-nice`
  service on port 3001 as the non-root `appuser`:
  - `app.py`: the `/` page, which gives each client its own `GuiSession`
    (with the LLM settings and client), and the `/health` route the compose
    healthcheck uses.
  - `layout.py`: `build_page(state, output_base)` draws the header,
    settings dialog, chat drawer, tabs and the Setup splitter, and returns
    them as `PageParts` for the stages that fill them.
  - `controller.py`: `PageController`, one per client. It holds the state,
    `output_base` and the bridge class, and runs the 1 s `ui.timer` that
    folds the background jobs into the state. Every handler calls a
    `ui/core` action and then `sync()`, which has each view update only
    the widgets whose value changed. All clients share one event loop, so
    (Review Stage R2):
    - `sync()` does no file I/O. A view's `poll()` reads what it shows
      from disk in `run.io_bound` (the run's progress), on timer ticks.
    - The timer redraws while a job runs and whenever processing a job
      changed the state, so a run that ends within a second is drawn too.
    - `apply_geometry()` and `run_save()` run the geometry command's
      receptor read and the save's copy in `run.io_bound`. While a save
      runs, `busy` holds the session (chat input, Run, New Session, Reset).
    - `syncing` is set while views write widgets: NiceGUI fires change
      handlers on those writes too, and the handlers ignore them.
  - `box_picker_panel.py` (Stage 16.5): the box picker on the Setup tab's
    right side. It reads the receptor in `run.io_bound`, once per
    receptor; a receptor that can't be read is shown with a Reload button
    and not read again. It draws the Docking Box fields on every `sync()`.
  - `mol_viewer.py` + `mol_viewer.js` (Stage 16.5): the 3Dmol.js viewer
    as a custom element, with the scene code ported from
    `ui/components/mol_viewer/index.html`. One viewer per element: the
    scene is built once, and `set_box()` / `set_ligands()` redraw only the
    box or the poses by `run_method`, so the camera is kept. They never
    use `update()`, which would resend the receptor PDB. The 3Dmol bundle
    is served from `/mol-viewer-vendor`.
  - `setup_tab.py` and `chat_drawer.py`: the Setup form and the chat
    drawer. Widgets and `state.form` are kept in step explicitly (change
    handlers write the form; `sync()` writes it back), not with
    `bind_value`. The LLM parse runs in `run.io_bound`.
  - `pytest.ini`: the `ui-nice` test tier (see
    [DOCKER_DEPLOYMENT.md](DOCKER_DEPLOYMENT.md#gui-nicegui-tests)).
- **`ui/orchestrator_bridge.py`** gives each run its own log. The bridge
  passes `WorkflowOrchestrator` an `on_message` sink that queues the step
  messages line by line (the CLI keeps the default, `print`), and nothing
  touches `sys.stdout`. Before this, each bridge used
  `contextlib.redirect_stdout`, which swaps the process-wide stdout: a
  docking run and a box-picker receptor load at the same time captured
  each other's lines. (Stage 16.1 first replaced it with a thread-routing
  `sys.stdout` proxy; Phase 16's Review Stage R1 replaced that with the
  sink.)
