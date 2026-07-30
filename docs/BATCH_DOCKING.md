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
take many minutes end-to-end, since `exhaustiveness` and `cpu` settings apply per ligand,
not to the batch as a whole. For large batches, consider lowering `--exhaustiveness` or
increasing `--cpu` if more cores are available.

Docking itself is **strictly sequential** — one Moldina invocation per ligand, never
parallelized within a batch (see [Multi-Molecule SDF Files](LIGAND_PREPARATION.md#multi-molecule-sdf-files)
for why). A very large batch (hundreds of ligands — e.g. an entire multi-molecule SDF
screen) can therefore take hours, and the whole `dock` step is one long-running call with
no incremental result visibility until it completes or errors: you won't see any docking
results until every ligand in the batch has finished.
