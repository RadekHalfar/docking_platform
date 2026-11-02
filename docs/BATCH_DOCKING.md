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

The CLI takes a natural-language command, so it needs a configured LLM
(`LLM_PROVIDER`/`LLM_MODEL` in `.env`, or `--llm-provider`/`--llm-model`; see
[LLM_ORCHESTRATION.md](LLM_ORCHESTRATION.md)). Without one, it prints a setup
message and exits 1. The GUI's **Batch Processing** drop docks a folder's worth
of files without an LLM.

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
`--ligand-folder` flag: the folder then comes from the flag, not from the LLM's reading of
the command (the rest of the command is still parsed by the LLM).

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
permanent mounts for the same reason: `script-runner` mounts the repo at `/workspace`
(e.g. `./demo_output` is `/workspace/demo_output` there), while the MCP services see
`./demo_output` as `/app/demo_output`. Paths passed to the orchestrator are always
container-side paths.

## Expected result structure

Every matching file in the folder becomes one or more entries in `results.dock.results` of
`workflow_results.json` — one docking result per ligand *molecule*, same as if each had been
passed individually via `--ligand-files`. For `.mol`, `.mol2`, and `.pdbqt` files, and for
single-molecule `.sdf` files, that's exactly one result per file. A `.sdf` file containing
multiple `$$$$`-delimited molecules expands into one result per molecule (e.g. a 5-compound
`screen.sdf` produces 5 entries, named `screen_1` … `screen_5`) — see
[Multi-Molecule SDF Files](LIGAND_PREPARATION.md#multi-molecule-sdf-files) for details.

## Performance guidance

Each ligand is prepared and docked independently, so the search settings (`population`
in PSO mode, `exhaustiveness` and `cpu` in Vina mode) apply per ligand, not to the batch as
a whole. PSO takes seconds per ligand; Vina mode can take minutes, so a folder of more than
~20 ligands in Vina mode can take a long time end-to-end.

By default, docking is **strictly sequential** — one Moldina invocation at a time, never
more than one ligand in flight — because batching many ligands into a single Moldina
process previously triggered the Linux OOM killer once the container's memory limit was
exceeded (see `services/moldina_runner/__init__.py`). A very large batch (hundreds of
ligands — e.g. an entire multi-molecule SDF screen, see
[Multi-Molecule SDF Files](LIGAND_PREPARATION.md#multi-molecule-sdf-files)) can therefore
take hours, and the whole `dock` step is one long-running MCP call. The runner writes
`docking_progress.json` as ligands finish, so the GUI shows live per-ligand progress, but
the results themselves arrive when the call completes (or is stopped).

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
parallel workers"** number input. Its maximum is `moldina-runner`'s configured
`limits.cpus` above (`ui/worker_cap.py::detect_moldina_worker_cap()`, which parses
`docker-compose.yml` directly — bind-mounted into the `ui` container alongside the rest
of the repo). It follows the server's own rule: a fractional limit is rounded down but
never below 1 (`'0.5'` → 1), and the result is never above the machine's core count
(`cpus: '10'` on a 4-core host → 4). In Vina mode the maximum is that limit divided by
**CPUs per ligand** (`ui/worker_cap.py::max_workers_for()`, the server's own clamp below),
so workers × CPUs never exceeds it; Moldina (PSO) always docks with 1 CPU per ligand, and
the GUI sends `cpu=1` for it whatever the hidden field holds. The default is the selected
algorithm's measured best from the [benchmark baselines](#benchmark-baseline-2026-10-05)
below, never above that maximum (`ui/worker_cap.py::default_worker_count()`,
`min(MEASURED_BEST_WORKERS[algorithm], cap // cpu)`); switching the **Docking algorithm**
selector (or "use vina" in chat) resets the worker count to the new algorithm's default,
while loading a session keeps the count it was saved with. The caption under the control
states both: "Docking service is limited to N CPUs (docker-compose.yml). Benchmarked best
for Moldina (PSO): 8 workers at 1 CPU each (sampled peak ~612 MiB memory each; …)".
When the detected limit isn't the one the benchmark ran at (`MEASURED_AT_CPUS`, 10), the
caption says so ("Benchmarked best for … at a 10-CPU limit, which may not hold here: …") —
re-run the benchmark for your limits. It falls back to the `ui` container's own `os.cpu_count()`
only if the compose file can't be found or parsed, or has no positive `limits.cpus`, so
a parsing failure never blocks GUI startup. The fallback is not silent: the expander
shows a warning that the CPU limit couldn't be read (with the reason) and that the real
limit may be lower. Note this is still just a **UX suggestion**, not the authoritative limit; the real
enforcement happens server-side (below). When enabled, up to that many ligands are docked
concurrently — each is still a fully independent, separately-timed-out Moldina process
(never batched), so this is running several single-ligand invocations at once rather than
reintroducing the original OOM-prone batching. Each worker runs with `cpu` cores (1 for
Moldina PSO, which ignores `--cpu`; the "CPUs per ligand" field for Vina), so `N` workers
use `N × cpu` cores.

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
`min(configured quota, os.cpu_count())` — never trusting either number alone — then
divides that by the CPUs per ligand, so workers × CPUs never exceeds it. That is `cpu`
in Vina mode, whose search really uses `cpu` cores, and 1 in PSO mode, which ignores
`cpu` (Review Stage R1, Phase 15: PSO used to be divided by `cpu` too). This
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

`--docking-algorithm vina` benchmarks Vina's Monte Carlo search instead of PSO (the
default); the server leaves `--population` out of a Vina command. `--cpu N` (default 1)
sets the CPUs per ligand, which only Vina uses; in Vina mode the server then runs at most
`quota // N` workers. Each row records the worker count the server reports it really
ran, and a `NOTE:` line (plus `requested_workers` in the JSON) flags a row the clamp
cut — e.g. `--cpu 4` on a 10-CPU limit runs 2 workers for every count above 2.

Memory is the residual risk, not CPU: a high worker count on a large batch can still
exhaust `limits.memory`. The measured figure is in the baseline below. Use
`--sample-stats` to re-measure it. It polls `docker stats --no-stream` against
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

#### Benchmark baseline (2026-10-05)

This is the baseline for the next change to `moldina-runner`'s resource limits. After any
such change, re-run the benchmark and update this table, `MEASURED_BEST_WORKERS`,
`MEASURED_PEAK_MIB_PER_WORKER` and `MEASURED_AT_CPUS` in `ui/worker_cap.py`, and the JSON
paths in `tests/test_worker_cap.py` (which re-derives the two measurements from the JSON
with the rule below, so the constants can't drift from the evidence).

- **Limits:** `cpus: '10'`, `memory: 8G`. Probe: `os.cpu_count()` inside the container is 20,
  and cgroup v2 `cpu.max` is `1000000 100000`, an effective budget of 10.00 cores.
- **Workload:** 50 ligands from `job_20261001_071808_ee7e7d` (box 20 × 20 × 20 Å), Moldina PSO
  defaults (`--population 500`, `--num-modes 15`, `--energy-range 100`), `cpu=1` per ligand.
  All 300 dockings succeeded.
- **Command:** `python3 benchmark_docking.py --job-id job_20261001_071808_ee7e7d --num-ligands 50 --worker-counts 2,4,6,8,10 --sample-stats --json-out docs/benchmarks/2026-10-05_moldina_cpus10_mem8g.json`
- **Raw data:** [`docs/benchmarks/2026-10-05_moldina_cpus10_mem8g.json`](benchmarks/2026-10-05_moldina_cpus10_mem8g.json).
  The idle baseline was 37.6 MiB, and no memory samples failed.

| Workers | Wall clock (s) | s/ligand | Speed-up | Peak memory (MiB) | Mean memory (MiB) | Peak memory/worker (MiB) |
|---|---|---|---|---|---|---|
| 1 (sequential) | 113.8 | 2.3 | 1.00× | 647.6 | 376.8 | 610.1 |
| 2 | 58.4 | 1.2 | 1.95× | 1260.5 | 784.5 | 611.5 |
| 4 | 30.8 | 0.6 | 3.69× | 2233.3 | 1532.7 | 548.9 |
| 6 | 26.3 | 0.5 | 4.32× | 3293.2 | 2273.4 | 542.6 |
| **8** | **24.3** | 0.5 | **4.68×** | 3809.3 | 3033.6 | 471.5 |
| 10 | 23.4 | 0.5 | 4.86× | 4963.3 | 3809.6 | 492.6 |

**How the default was chosen.** The rule was fixed before the run: the GUI default is the
smallest worker count whose wall clock is within 5% of the fastest. The fastest was 10
workers (23.4 s), so the threshold was 24.6 s. 8 workers (24.3 s) meets it, and 6 workers
(26.3 s) does not. The memory check passes: 37.6 MiB idle + 8 × 612 MiB (the worst
per-worker figure) ≈ 4.8 GiB, well under 8 GiB. The measured peak at 8 workers was 3.7 GiB.

**Reading the numbers.**
- Scaling is near-linear up to 4 workers (3.69×), then flattens: 4→8 workers buys only
  about 27% more speed. Above about 4 workers, each extra worker is less efficient.
- Memory grows by roughly 0.5–0.6 GiB per worker. Peaks are sampled lower bounds,
  because each `docker stats` call takes about 1–2 s.
- These ligands dock in about 2.3 s each on their own. Ligands that take longer may scale
  differently, so re-benchmark with a representative job if your batches differ a lot.

**Re-run (Review Stage R1, same day).** The run above measured each configuration's wall
clock only after the memory sampler had stopped, so it could include up to one in-flight
`docker stats` call (~1–2 s) — about the size of the 5% margin the rule works with. After
that was fixed, the same command was run again
([`docs/benchmarks/2026-10-05_moldina_cpus10_mem8g_r1_rerun.json`](benchmarks/2026-10-05_moldina_cpus10_mem8g_r1_rerun.json),
idle 45.3 MiB, 300/300 succeeded): 1 / 2 / 4 / 6 / 8 / 10 workers took 115.0 / 56.9 / 30.5 /
24.8 / 22.8 / 23.1 s. 8 workers was the fastest outright and 6 (24.8 s) is outside 5%, so
the default stays 8. The worst per-worker memory was 611.1 MiB (sequential), so 612 MiB
stays too. The table above is kept as the original record; both runs give the same
constants, and `tests/test_worker_cap.py` checks them against both.

#### Vina benchmark baseline (2026-10-06)

The same probe and limits (`cpus: '10'`, `memory: 8G`, cgroup budget 10.00 cores),
measured for `--docking-algorithm vina` (Stage 15.3). This sets
`MEASURED_BEST_WORKERS["vina"]` and `MEASURED_PEAK_MIB_PER_WORKER["vina"]`, and
`tests/test_worker_cap.py` re-derives both from the JSON.

- **Workload:** 10 ligands from `job_20261001_071808_ee7e7d` (box 20 × 20 × 20 Å), Vina
  defaults (`--exhaustiveness 8`, `--num-modes 15`, `--energy-range 100`), `cpu=1` per
  ligand. All 60 dockings succeeded. Vina takes about 62 s per ligand here, against about
  2.3 s for PSO, so 10 ligands rather than 50 keeps the run to about 25 minutes.
- **Command:** `python3 benchmark_docking.py --job-id job_20261001_071808_ee7e7d --num-ligands 10 --worker-counts 2,4,6,8,10 --docking-algorithm vina --cpu 1 --sample-stats --json-out docs/benchmarks/2026-10-06_vina_cpus10_mem8g.json`
- **Raw data:** [`docs/benchmarks/2026-10-06_vina_cpus10_mem8g.json`](benchmarks/2026-10-06_vina_cpus10_mem8g.json).
  The idle baseline was 34.1 MiB, and no memory samples failed.

| Workers | Wall clock (s) | s/ligand | Speed-up | Peak memory (MiB) | Mean memory (MiB) | Peak memory/worker (MiB) |
|---|---|---|---|---|---|---|
| 1 (sequential) | 615.2 | 61.5 | 1.00× | 620.6 | 570.0 | 586.5 |
| 2 | 313.0 | 31.3 | 1.97× | 1206.3 | 1097.6 | 586.1 |
| 4 | 178.3 | 17.8 | 3.45× | 2280.4 | 1957.0 | 561.6 |
| **6** | **134.6** | 13.5 | **4.57×** | 3402.8 | 2785.8 | 561.4 |
| 8 | 141.5 | 14.1 | 4.35× | 4351.0 | 3539.4 | 539.6 |
| 10 | 144.3 | 14.4 | 4.26× | 5166.1 | 3714.1 | 513.2 |

**How the default was chosen.** The same rule as for PSO, fixed before the run. The
fastest was 6 workers (134.6 s), so the threshold was 141.3 s. 8 workers (141.5 s) misses
it by 0.2 s, so the GUI's Vina default is **6**. The memory check passes:
34.1 MiB idle + 6 × 587 MiB ≈ 3.5 GiB, under 8 GiB.

**Reading the numbers.**
- Scaling is near-linear up to 4 workers (3.45×) and stops after 6. With only 10 ligands,
  the 6, 8 and 10 rows mostly differ in how the ligands fall into waves: 6 workers run 6 then
  4, 8 run 8 then 2, and 10 run them all at once, waiting for the slowest. They are within
  about 7% of each other. 6 versus 8 is not a robust difference, so re-benchmark on a larger
  batch before relying on it.
- Memory per worker (~0.5–0.6 GiB) is about the same as PSO's.

**CPUs per ligand.** Two more runs split the same 10 cores differently (each also has its
own sequential row at that `cpu`):

| Split | Wall clock (s) | Sequential at that `cpu` (s) | Raw data |
|---|---|---|---|
| 6 workers × 1 CPU (from the table) | 134.6 | 615.2 | above |
| 5 workers × 2 CPUs | 128.4 | 312.7 | [`…_cpu2.json`](benchmarks/2026-10-06_vina_cpus10_mem8g_cpu2.json) |
| 2 workers × 4 CPUs | 122.4 | 179.5 | [`…_cpu4.json`](benchmarks/2026-10-06_vina_cpus10_mem8g_cpu4.json) |

A single ligand gets about 2.0× faster at 2 CPUs and 3.4× at 4 CPUs. On this 10-ligand
batch, the multi-CPU splits finished 5–9% sooner than 6 × 1, because fewer, faster
ligands leave a shorter tail. On large batches, single-CPU workers should catch up,
because Vina's own threading scales less than linearly. The GUI default stays at 1 CPU
per ligand. For a handful of Vina ligands, raising **CPUs per ligand** is a reasonable
choice.
