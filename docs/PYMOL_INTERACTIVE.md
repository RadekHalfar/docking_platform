# PyMOL Interactive Service

## Overview

The PyMOL Interactive service provides a browser-based PyMOL session (via noVNC) and helper HTTP endpoints for:

1. Loading workflow outputs into the live PyMOL GUI
2. Defining docking boxes from visual selections
3. Persisting selected docking box coordinates to the job directory

This service complements, but does not replace, the MCP PyMOL visualizer.

- MCP visualizer: automated script and image generation
- PyMOL interactive: human-in-the-loop visual selection and inspection

Implementation: `services/pymol_interactive/server.py`

## Service Role in Workflow

Typical flow:

1. Run workflow preparation (download + prepare)
2. Open interactive PyMOL desktop in browser
3. Load the job with `POST /api/load_job` (returns immediately; poll
   `GET /api/load_status` until it's done)
4. Select residues/region in PyMOL (`sele` or custom selection)
5. Derive docking box with `POST /api/box/from_selection`
6. Save generated box to `<job_dir>/docking_box.json`
7. Continue docking using the saved center and box size

## Endpoints

Base URL (default): `http://localhost:8090`

### Health Check

`GET /health`

Response:

```json
{
  "status": "ok",
  "service": "pymol_interactive",
  "pymol_rpc": true
}
```

- `pymol_rpc=true` means the internal PyMOL RPC backend is reachable.

### Landing Page

`GET /`

Returns a small HTML page with quick links and listed helper endpoints.

### List Jobs

`GET /api/jobs`

Response:

```json
{
  "jobs": ["job_1774883373879", "job_1774611419801"]
}
```

Jobs are discovered from `ORCHESTRATOR_OUTPUT_DIR` (default `/app/orchestrator_output`).

### Load Job Into Live PyMOL

`POST /api/load_job`

This is **asynchronous**: the request validates the job and, if nothing else
is already loading, starts the load in a background thread and returns
immediately — it does not wait for PyMOL to finish. For a batch of many
ligands, doing the whole load inline could easily take longer than any
caller's HTTP timeout; poll `GET /api/load_status` (below) for the actual
outcome.

Request:

```json
{
  "job_id": "job_1774883373879"
}
```

Response (`202`) — the load has started:

```json
{
  "status": "started",
  "job_id": "job_1774883373879"
}
```

Response (`200`) — a load was already in progress (for this job or another);
no second load was started:

```json
{
  "status": "already_running",
  "job_id": "job_1774883373879"
}
```

Behavior:

1. Locates receptor in `<job>/prepared` (`*.pdbqt` preferred, then `*.pdb`)
2. Locates ligands in `<job>/docking_results/*.pdbqt`, fallback to prepared ligands
3. In the background: reinitializes the PyMOL session, loads the receptor as
   object `receptor`, loads ligands as `ligand_1`, `ligand_2`, ..., and
   applies basic styling (cartoon/sticks/colors) — all issued as a **single**
   consolidated PyMOL command (`cmd.do("cmd1; cmd2; ...")`) rather than one
   round-trip per command, to minimize RPC overhead for large batches
4. Only one load can run at a time against the single live PyMOL session —
   a load request while another is in-flight is rejected with
   `"already_running"` rather than starting a second, interleaving load.
   `POST /api/box/from_selection` shares the same internal lock, so it waits
   for an in-flight load to finish rather than corrupting it.

Possible errors on the kickoff request itself:

- `400` missing `job_id`
- `404` unknown job directory
- `400` receptor/ligands not found

A failure *during* the load (e.g. a PyMOL RPC error) does not show up here —
it surfaces via `GET /api/load_status` instead, since the kickoff request has
already returned by the time the load actually runs.

### Load Status

`GET /api/load_status`

Poll this after `POST /api/load_job` until `status` is `"done"` or `"error"`.

Response:

```json
{
  "status": "running",
  "job_id": "job_1774883373879",
  "started_at": "2026-07-30T12:00:00+00:00",
  "finished_at": null,
  "error": null
}
```

- `status`: `"idle"` (nothing loaded/loading yet), `"running"`, `"done"`, or `"error"`.
- `error`: the exception message if `status == "error"`, otherwise `null`.
- This reflects the state of the *single* live PyMOL session, not a
  per-job queue — there is at most one load in flight at a time.

### Derive Docking Box From Selection

`POST /api/box/from_selection`

Request:

```json
{
  "job_id": "job_1774883373879",
  "selection": "sele"
}
```

- `selection` is optional (default `sele`)

Response:

```json
{
  "status": "ok",
  "job_id": "job_1774883373879",
  "selection": "sele",
  "center": [10.1234, 20.5678, 30.4321],
  "box_size": [18.0, 22.0, 20.0],
  "saved_to": "/app/orchestrator_output/job_1774883373879/docking_box.json"
}
```

Behavior:

1. Uses PyMOL RPC to get selection extent (`min`, `max`)
2. Converts to center and box size
3. Saves result to `<job>/docking_box.json`
4. Adds a red pseudoatom marker (`box_center`) in the session for visual confirmation

Possible errors:

- `400` missing `job_id`
- `500` invalid/missing selection or PyMOL RPC failure

### Derive Docking Box From Corners

`POST /api/box/from_corners`

Request:

```json
{
  "job_id": "job_1774883373879",
  "min": [1.0, 2.0, 3.0],
  "max": [11.0, 12.0, 13.0]
}
```

Response:

```json
{
  "status": "ok",
  "job_id": "job_1774883373879",
  "center": [6.0, 7.0, 8.0],
  "box_size": [10.0, 10.0, 10.0],
  "saved_to": "/app/orchestrator_output/job_1774883373879/docking_box.json"
}
```

Possible errors:

- `400` missing `job_id`
- `400` invalid `min` or `max` arrays (must be 3 numbers)
- `500` conversion or write failure

## Saved Docking Box File

`docking_box.json` content:

```json
{
  "job_id": "job_1774883373879",
  "center": [10.1234, 20.5678, 30.4321],
  "box_size": [18.0, 22.0, 20.0],
  "source": "pymol_interactive"
}
```

## Environment Variables

- `ORCHESTRATOR_OUTPUT_DIR` (default `/app/orchestrator_output`)
- `PYMOL_RPC_URL` (default `http://127.0.0.1:9123`)
- `PYMOL_INTERACTIVE_HOST` (default `0.0.0.0`)
- `PYMOL_INTERACTIVE_PORT` (default `8090`)

## Docker Usage

This service has no profile gate -- it starts automatically with `docker compose up -d` along with everything else. To (re)start just this one service:

```bash
docker compose up -d pymol-interactive
```

Open browser desktop:

- noVNC: `http://localhost:6080/vnc.html`
- Helper API: `http://localhost:8090`

## Quick Example

```bash
# 1) The interactive service is already running (started with `docker compose up -d`);
#    only needed if you stopped it or want to restart just this one service:
# docker compose up -d pymol-interactive

# 2) Kick off the load (returns immediately, doesn't wait for PyMOL)
curl -X POST http://localhost:8090/api/load_job \
  -H "Content-Type: application/json" \
  -d '{"job_id":"job_1774883373879"}'
# -> {"status": "started", "job_id": "job_1774883373879"}

# 3) Poll until it's done (or errors) -- for a large batch this can take a
#    while, since PyMOL still has to load/render every object server-side
until curl -s http://localhost:8090/api/load_status | grep -q '"status": *"done"'; do
  sleep 2
done

# 4) In noVNC PyMOL, create/select region (e.g., sele)

# 5) Generate docking box from selection
curl -X POST http://localhost:8090/api/box/from_selection \
  -H "Content-Type: application/json" \
  -d '{"job_id":"job_1774883373879","selection":"sele"}'
```

## Relationship to Other Docs

- Automated visual outputs: `docs/PYMOL_VISUALIZATION.md`
- Workflow orchestration and clarification: `docs/LLM_ORCHESTRATION.md`
- Parameter and dependency flow: `docs/SERVICE_SELECTION_AND_DEPENDENCIES.md`
- Deployment details: `docs/DOCKER_DEPLOYMENT.md`
