# Batch Docking from a Folder

Dock one protein against every ligand file found in a local directory using
`--ligand-folder`, instead of listing individual `--ligand-files` or RCSB `ligand_ids`.

## Supported file formats and discovery rules

`scan_ligand_folder()` (`common/file_utils.py`) finds files with these extensions,
case-insensitively:

- `.sdf`
- `.mol`
- `.mol2`
- `.pdbqt`

The folder is scanned **recursively** (subdirectories are included). Matching files are
returned in sorted order. Any other file type (`.txt`, `.log`, `.json`, etc.) is ignored.

If the folder contains no matching files, the workflow fails with an error before any
RCSB download or preparation step runs.

## `ligand_folder` supersedes `ligand_files` and `ligand_ids`

When `ligand_folder` is set, it is the **only** source of ligands for that run — any
`--ligand-files` paths or LLM-extracted `ligand_ids` are ignored entirely. This mirrors
how `protein_file` always wins over `protein_id` for the protein side: one ligand source
per run, chosen by precedence, not merged.

(Local `ligand_files` and RCSB `ligand_ids` *can* be combined with each other — see
[Local File Input](LLM_ORCHESTRATION.md#local-file-input) — but `ligand_folder` overrides
both when present.)

## CLI usage

```bash
# Protein from RCSB, all ligands from a local folder
python nlp_orchestrator.py --ligand-folder /data/ligands \
  "download 6lu7, prepare and dock, center 10 20 30 box 25 30 35"

# Local protein file + local ligand folder — no RCSB download at all
python nlp_orchestrator.py --protein-file /data/6lu7.pdb --ligand-folder /data/ligands \
  "prepare and dock, center 10 20 30 box 25 30 35"
```

Inside a Docker container, mount the folder and point `--ligand-folder` at the mounted path:

```bash
docker compose --profile dev run --rm \
  -v /your/local/ligands:/data/ligands \
  script-runner \
  python nlp_orchestrator.py --ligand-folder /data/ligands \
  "download 6lu7, prepare and dock, center 10 20 30 box 25 30 35"
```

`--ligand-folder` also works with `--interactive` mode, applying to every `new_workflow`
request issued in that session.

### Natural-language extraction

`ligand_folder` can also be extracted directly from a plain-English request, without the
`--ligand-folder` flag, by phrasing it explicitly as a folder/directory of ligands:

```bash
python nlp_orchestrator.py \
  "Dock protein 6lu7 against all ligands in /data/ligands, center 10 20 30 box 25 30 35"
```

The LLM recognizes phrasing like "all ligands in `<path>`" or "every ligand file in `<path>`"
and sets `ligand_folder` instead of `ligand_ids`. As with the CLI flag, the path must be
container-side when running in Docker (see "Why the path appears twice" above). If the LLM
fails to extract the folder (uncommon phrasing, unusual path format), fall back to the
`--ligand-folder` flag, which is always reliable since it bypasses LLM parsing entirely.

### Why the path appears twice

In the Docker example above, `/data/ligands` is written twice: once in `-v
/your/local/ligands:/data/ligands` and once in `--ligand-folder /data/ligands`. This is
not accidental duplication — the two flags are evaluated by two different things at two
different times:

- `-v HOST_PATH:CONTAINER_PATH` is read by `docker compose` **on the host**, before the
  container starts. It decides what host directory becomes visible inside the container,
  and at what path.
- `--ligand-folder PATH` is read by `nlp_orchestrator.py` **inside the already-running
  container**. It has no visibility into the host filesystem at all — it can only see
  whatever `-v` already mounted.

Because the script runs after the mount is set up, it cannot discover or set its own
mount; the container-side path has to be supplied to it explicitly, and that path must
match the target half of the `-v` flag. If the two don't match, `--ligand-folder` will
point at an empty or nonexistent directory inside the container even though the host
folder is correct.

This mirrors `--protein-file` and `--ligand-files`, which rely on the project's existing
permanent mounts (`./demo_output:/app/demo_output`, etc. in `docker-compose.yml`) for the
same reason — paths passed to the orchestrator are always container-side paths.

## Expected result structure

Every matching file in the folder becomes one or more entries in `results.dock.results` of
`workflow_results.json` — one docking result per ligand *molecule*, same as if each had been
passed individually via `--ligand-files`. For `.mol`, `.mol2`, and `.pdbqt` files, and for
single-molecule `.sdf` files, that's exactly one result per file. A `.sdf` file containing
multiple `$$$$`-delimited molecules expands into one result per molecule (e.g. a 5-compound
`screen.sdf` produces 5 entries, named `screen_1` … `screen_5`) — see
[Multi-Molecule SDF Files](LIGAND_PREPARATION.md#multi-molecule-sdf-files) for details.

## Performance guidance

Each ligand is prepared and docked independently. A folder with more than ~20 ligands can
take many minutes end-to-end, since `exhaustiveness` applies per ligand, not to the batch
as a whole.

By default, docking is **strictly sequential** — one Moldina invocation at a time, never
more than one ligand in flight — because batching many ligands into a single Moldina
process previously triggered the Linux OOM killer once the container's memory limit was
exceeded (see `services/moldina_runner/__init__.py`). A very large batch (hundreds of
ligands — e.g. an entire multi-molecule SDF screen, see
[Multi-Molecule SDF Files](LIGAND_PREPARATION.md#multi-molecule-sdf-files)) can therefore
take hours, and the whole `dock` step is one long-running call with no incremental result
visibility until it completes or errors.

### Optional parallel docking

`moldina-runner`'s resource budget is set in `docker-compose.yml`
(`services.moldina-runner.deploy.resources`):

```yaml
deploy:
  resources:
    limits:
      cpus: '10'      # hard scheduling quota — see below
      memory: 8G      # hard cap; exceeding this gets the container OOM-killed
    reservations:
      cpus: '2'
      memory: 2G
```

`limits` is the enforced ceiling (a cgroup CFS CPU quota + a hard memory cap).
`reservations` is a soft/scheduling-hint value that isn't strongly enforced on a
single-host `docker compose up` setup (no Swarm) — see the "Why the CPU limit didn't do
what I expected" note below for why `limits.cpus` in particular needs care when changed.
These are also the values the GUI reads to size its parallel-worker control — see below.

The GUI's Advanced expander has a **"Parallel ligand docking"** checkbox and a **"Max
parallel workers"** number input, pre-filled/capped from `moldina-runner`'s configured
`limits.cpus` above (`ui/worker_cap.py::detect_moldina_worker_cap()`, which parses
`docker-compose.yml` directly — bind-mounted into the `ui` container alongside the rest
of the repo). The caption under the control says so: "Docking service is limited to N
CPUs (docker-compose.yml)". It falls back to the `ui` container's own `os.cpu_count()`
only if the compose file can't be found or parsed, or has no positive `limits.cpus`, so
a parsing failure never blocks GUI startup. The fallback is not silent: the expander
shows a warning that the CPU limit couldn't be read (with the reason) and that the real
limit may be lower. Note this is still just a **UX suggestion**, not the authoritative limit; the real
enforcement happens server-side (below). When enabled, up to that many ligands are docked
concurrently — each is still a fully independent, separately-timed-out Moldina process
(never batched), so this is running several single-ligand invocations at once rather than
reintroducing the original OOM-prone batching. Each worker requests only 1 CPU core
(Moldina's own `--cpu` parallelism is fixed and not user-configurable), so `N` workers
request `N` cores total.

#### Why the CPU limit didn't do what I expected

The worker count is clamped server-side to the number of ligands and to
`moldina-runner`'s real, schedulable CPU budget — **not just** `os.cpu_count()` alone.
Docker Compose's `deploy.resources.limits.cpus` is enforced as a cgroup CFS *scheduling
quota*, not a CPU affinity mask, so `os.cpu_count()` inside the container (and the GUI's
own naive detection, before the fix described above) does **not** reflect it on its own —
both can report far more cores than are actually available. The clamp therefore reads the
cgroup quota directly
(`services/moldina_runner/__init__.py:_detect_effective_cpu_count()`; cgroup v2's
`cpu.max`, falling back to v1's `cpu.cfs_quota_us`/`cpu.cfs_period_us`) and takes
`min(configured quota, os.cpu_count())` — never trusting either number alone. This
matters if you raise `cpus:` further: a higher quota doesn't require or reserve that many
real cores, so deploying the same config on smaller hardware than it assumes (e.g.
`cpus: '10'` run on a 4-core machine) would otherwise let the quota alone claim more
workers than physically exist; `os.cpu_count()` catches that case as the lower, binding
bound. Falls back to plain `os.cpu_count()` only if no quota is configured at all, or
neither cgroup file is readable (e.g. running outside a container). Requesting more
workers than this real budget doesn't just fail to help — benchmarking on this project
showed it makes things significantly *slower* than sequential once concurrent workers
meaningfully outnumber the real quota (severe scheduling contention), so this clamp
exists to prevent exactly that, not just to avoid overshoot. A dedicated benchmark script
(`benchmark_docking.py`, repo root) measures sequential vs. parallel wall-clock time
across worker counts against a previously-completed job's already-prepared files, and
also prints this same cgroup-quota-vs-`os.cpu_count()` diagnostic. Run it on the host
with `python3` (it needs `requests` and `docker` on PATH):

```bash
python3 benchmark_docking.py --job-id <prepared job> --worker-counts 2,4,6,8,10 \
    --sample-stats --json-out bench.json
```

Memory is the residual risk, not CPU: no numeric per-ligand memory budget is recorded yet,
so a high worker count on a large batch can still exhaust `limits.memory`. Use
`--sample-stats` to measure it. It polls `docker stats --no-stream` against
`moldina-runner` on a background thread while each configuration runs, failed ones
included, and adds peak memory, mean memory and peak memory per worker to the table and
the JSON. Peak memory per worker is `(peak − idle baseline) / min(workers, ligands)`. The
idle baseline is one sample taken before the first configuration. If the server clamps the
worker count further to the CPU quota printed above, the real per-worker figure is higher.
Each `docker stats` call takes about 1–2 s, so the peaks are sampled lower bounds rather
than exact values, and very short runs get only a few samples. Failed samples are counted
and reported, not hidden. Start with a modest worker count, re-run the benchmark with
`--sample-stats` after changing `cpus`/`memory`, and reduce the worker count if docking
jobs start failing unexpectedly. This setting is available via the
`parallel`/`max_workers` parameters on the `run_docking` MCP tool for CLI/direct callers
too — both default to off/1 (sequential) if omitted.
