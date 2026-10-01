# GUI Guide

The Streamlit UI (`ui/app.py`) is a browser front-end for the docking platform,
served at http://localhost:3000 by the `ui` service in `docker-compose.yml`.
It has three panels — Chat, Input & Parameters, Results — plus a sidebar for
LLM settings and session history.

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

## Saving Parameters Without Docking

Editing a parameter (e.g. the docking box size) does **not** persist to
disk by itself — it only lives in the form until you either click **Run
Docking** (which checkpoints it as part of starting or redocking) or
click **Save Session**, next to **Run Docking**. **Save Session** writes
your current form values to disk as a draft without running or
redocking anything, so they survive switching to a different session
(**Load**) and back. It's disabled when there's no active session to
save into, and reports "No parameter changes to save" if nothing
actually changed.

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

- **Box size X/Y/Z** number inputs — resize the search box live; the +/-
  buttons step by a whole Å at a time.
- Click any receptor atom to set the box **center** — a red marker and label
  show what you clicked, and a live blue box outline shows the resulting
  search region. Residues inside the box are highlighted (orange cartoon
  plus thin side-chain sticks) so you can see exactly which part of the
  protein will be searched.
- Red/green/blue arrows from the box center, labeled **X**/**Y**/**Z**,
  show which box edge each Box size field controls.

If the **Docking Box** fields already have a valid center and size (from a
previous pick, manual entry, or a chat geometry command), the box and axis
arrows appear immediately when the receptor loads — no click needed just
to see what's already set.

The picked center and size are written straight into the same
**Docking Box** center/box-size fields used by manual entry, so **Run
Docking** needs no separate step — type over any of the six fields
afterward if you want to fine-tune the numbers by hand.

This is a lighter, purely in-browser alternative to the CLI's
`pymol-interactive`-based box selection (see
[docs/PYMOL_INTERACTIVE.md](PYMOL_INTERACTIVE.md)) — it doesn't require that
service to be running.

Once a receptor is loaded here (or a job has already run
`prepare_protein`), the chat commands described in
[Auto-Filling the Docking Box from the Protein](#auto-filling-the-docking-box-from-the-protein)
are a faster alternative to clicking: "set up box center as protein
center" / "look across all protein" compute the same kind of values
without needing to click an atom or drag sliders.

## Comparing Sessions

Check two or more sessions in the sidebar's Session History and click
**Compare selected sessions**. The **Compare Sessions** tab in the Results
panel shows a table with ligands as rows and sessions as columns — column
headers use each session's name when it has one, falling back to the job ID
otherwise; cells are the best binding affinity (kcal/mol) for that ligand in
that session, or "N/A" if the ligand wasn't docked in that session. The
strongest predicted binder per ligand (most negative kcal/mol) is
highlighted green — rendered as a plain HTML table (not `st.dataframe`) so
the highlight stays readable in both light and dark theme.
