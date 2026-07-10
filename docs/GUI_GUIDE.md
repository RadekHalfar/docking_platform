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

## Job History

Every completed job (one whose `workflow_results.json` has
`"status": "completed"`) appears as a row in the sidebar's **Job History**
list, newest first, labeled with the job ID, protein ID, and ligand count.
The list is rebuilt from disk on every page load, so it reflects jobs run
from the GUI or the CLI (`nlp_orchestrator.py`). Each row has:

- A **checkbox** — select 2+ jobs to compare (see below).
- A **Load** button — loads that job's results into the **Job Viewer** tab
  (see below), so you can review any past run without re-docking.

## Viewing a Job (Live or Historical)

The Results panel's **Job Viewer** tab (renamed from "Current Job") shows
either:

- the job you just started with **Run Docking** — progress streams in while
  it runs, then the chart/image/downloads appear on completion; or
- a past job you clicked **Load** on in the sidebar — its results are read
  directly from `workflow_results.json` on disk, so this works for jobs from
  a previous browser session or run via the CLI, with no re-run needed.

Once a job's results are showing, you get:

- An affinity bar chart (best binder first).
- An **Image** / **PyMOL** tab: the PNG visualization, and a live PyMOL
  session (via `pymol-interactive`'s noVNC iframe) with that job's protein
  and ligand structures loaded automatically.
- Download buttons for the job's `.pdbqt`, `.pml`, `.json`, and `.png` files.

## Comparing Jobs

Check two or more jobs in the sidebar and click **Compare selected jobs**.
The **Compare Jobs** tab in the Results panel shows a table with ligands as
rows and jobs as columns; cells are the best binding affinity (kcal/mol) for
that ligand in that job, or "N/A" if the ligand wasn't docked in that job.
The strongest predicted binder per ligand (most negative kcal/mol) is
highlighted green — rendered as a plain HTML table (not `st.dataframe`) so
the highlight stays readable in both light and dark theme.
