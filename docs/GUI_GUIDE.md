# GUI Guide

The Streamlit UI (`ui/app.py`) is a browser front-end for the docking platform,
served at http://localhost:3000 by the `ui` service in `docker-compose.yml`.
It has three panels — Chat, Input & Parameters, Results — plus a sidebar for
LLM settings and job history.

## Prerequisites

- Docker Desktop
- The platform stack running: `docker compose up -d`
- An LLM backend reachable from the `ui` container (Ollama by default, or a
  cloud/OpenAI-compatible provider — see below)

## Persistent Settings

The sidebar **Settings** panel lets you change the LLM provider, model,
endpoint, and API key without editing `.env` or restarting the container.

- **LLM Provider**: `Ollama (local)`, `OpenAI`, `Anthropic`, or `Other local
  server` (any OpenAI-compatible endpoint — LM Studio, vLLM, Jan, llama.cpp,
  Groq, Together AI).
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
  Other local server). Entered per-session — **never written to disk**.

Click **Apply settings** to rebuild the LLM client immediately and persist
provider/model/endpoint to `~/.docking_platform_settings.json` inside the
container (mounted to `./cache/.docking_platform_settings.json` on the host
via `docker-compose.yml`, so it survives both a browser refresh — which
resets Streamlit's in-memory session — and a container restart).

To reset to the values from `.env` / `docker-compose.yml`, delete
`cache/.docking_platform_settings.json` and refresh the page.

## Naming a Job

The **Input & Parameters** panel has an optional **Job name** field, just
above the **Run Docking** button (60 characters max). It's a free-text label
purely for your own reference — it doesn't need to be unique, and it never
replaces the system-generated job ID, which remains the actual unique
identifier on disk and everywhere else in the app. Leave it blank and the job
just shows up under its job ID, exactly as before this field existed.

## Job History

Every completed job (one whose `workflow_results.json` has
`"status": "completed"`) appears as a row in the sidebar's **Job History**
list, newest first. If you gave the job a name, that name is shown in bold
ahead of the job ID; otherwise the row falls back to the job ID, protein ID,
and ligand count only. The list is rebuilt from disk on every page load, so
it reflects jobs run from the GUI or the CLI (`nlp_orchestrator.py`). Each
row has:

- A **checkbox** — select 2+ jobs to compare (see below).
- A **Load** button — loads that job's results into the **Job Viewer** tab
  (see below), so you can review any past run without re-docking.
- A **Rename** button — toggles an inline text field to change the job's
  name after the fact (e.g. once you know which run turned out to matter).
  Click **Save** to write the new name straight into that job's
  `workflow_results.json`, or **Cancel** to leave it unchanged. Only
  completed jobs already listed here can be renamed; a job still in progress
  keeps the name you gave it at submission until it finishes.

## Viewing a Job (Live or Historical)

The Results panel's **Job Viewer** tab (renamed from "Current Job") shows
either:

- the job you just started with **Run Docking** — progress streams in while
  it runs, then the chart/image/downloads appear on completion; or
- a past job you clicked **Load** on in the sidebar — its results are read
  directly from `workflow_results.json` on disk, so this works for jobs from
  a previous browser session or run via the CLI, with no re-run needed.

If the job has a name, the status caption ("... is running", "... completed",
"Viewing job ...") shows it alongside the job ID, so it stays clear which run
you're looking at even after switching between several loaded jobs.

Once a job's results are showing, you get:

- An affinity bar chart (best binder first), in a fixed-height scrollable
  window so a long chart doesn't push the 3D viewer down the page.
- An in-browser **3Dmol.js** viewer (no PyMOL, no noVNC — renders entirely
  client-side): the prepared receptor as cartoon, and each docked ligand as
  colored sticks (green = best affinity, red = worst). If a ligand has more
  than one docked pose, a **pose** dropdown next to the viewer switches which
  mode is shown, labeled with that pose's affinity.
- A **Docked Poses** download section: a single **Download format** picker
  (`pdbqt` / `pdb` / `sdf` / `mol2`, defaulting to `pdbqt` — Moldina's native
  output) applies to every ligand's download button, so you can grab the
  whole multi-pose file per ligand in whichever format your downstream tool
  expects. PDB/SDF/MOL2 are generated once per job (during the same step
  that used to run PyMOL) via a Meeko PDBQT→RDKit round trip that restores
  proper bond orders, with an OpenBabel fallback if that fails for a
  particular ligand.
- An **Other Files** section below it for anything else in the job directory
  (e.g. `workflow_results.json`).

### Picking the Docking Box Visually

Before running a job, the **Input & Parameters** panel has a **Pick Docking
Box Visually** control: click **Load receptor for box picking** to run just
enough of the workflow to fetch and prepare the receptor (no ligands, no
docking yet), then the same 3Dmol.js viewer appears with:

- **Box size X/Y/Z** sliders — resize the search box live.
- Click any receptor atom to set the box **center** — a red marker and label
  show what you clicked, and a live translucent box overlay shows the
  resulting search region.

The picked center and size are written straight into the same
**Docking Box** center/box-size fields used by manual entry, so **Run
Docking** needs no separate step — type over any of the six fields
afterward if you want to fine-tune the numbers by hand.

This is a lighter, purely in-browser alternative to the CLI's
`pymol-interactive`-based box selection (see
[docs/PYMOL_INTERACTIVE.md](PYMOL_INTERACTIVE.md)) — it doesn't require that
service to be running.

## Comparing Jobs

Check two or more jobs in the sidebar and click **Compare selected jobs**.
The **Compare Jobs** tab in the Results panel shows a table with ligands as
rows and jobs as columns — column headers use each job's name when it has
one, falling back to the job ID otherwise; cells are the best binding
affinity (kcal/mol) for that ligand in that job, or "N/A" if the ligand
wasn't docked in that job. The strongest predicted binder per ligand (most
negative kcal/mol) is highlighted green — rendered as a plain HTML table (not
`st.dataframe`) so the highlight stays readable in both light and dark theme.
