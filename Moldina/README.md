# Moldina Binary

`moldina` is a prebuilt, dynamically-linked Linux x86-64 executable that
`docker/moldina_runner.Dockerfile` copies into the `moldina-runner` image at
`/usr/local/bin/moldina`. It is committed directly to this repo as a binary
blob rather than built from source at image-build time.

## Source

Upstream source and build instructions are documented in
[docs/MOLDINA_DOCKING.md](../docs/MOLDINA_DOCKING.md#installation):

```bash
git clone https://github.com/It4innovations/moldina-multiple-ligand-molecular-docking-over-autodock-vina.git Moldina
cd Moldina
mkdir -p build/linux/release && cd build/linux/release
cmake ../../.. && make
# executable ends up at: build/linux/release/Moldina
```

Moldina is a fork of [AutoDock Vina](https://vina.scripps.edu/) that
adds Particle Swarm Optimization as an alternative to Vina's Monte Carlo
search — see `docs/MOLDINA_DOCKING.md` for what that changes in practice.
Running `moldina --help` reports `AutoDock Vina aea4fd5-mod`, consistent
with a patched Vina fork; that string doesn't map to an official Vina
release tag, so treat it as this fork's own version marker rather than a
Vina version. Its option set (`--scoring vina|vinardo|ad4`, `--batch`,
`--autobox`, `--max_evals`, `--min_rmsd`, `--write_maps`) is AutoDock Vina
**1.2.x**'s, so the non-PSO search (`docking_algorithm="vina"`) is a 1.2.x-era
Vina Monte Carlo search, not 1.1.2's.

**Not currently wired up:** `docker/moldina_runner.Dockerfile` does not run
the clone/cmake/make steps above — it just `COPY`s this prebuilt binary.
Nothing enforces that this file was actually produced by that source at
that commit; if you rebuild from source, verify the result behaves the
same (see "Updating this binary" below) before replacing this file.

## Provenance of the committed binary

| | |
|---|---|
| File | `Moldina/moldina` |
| Type | ELF 64-bit LSB pie executable, x86-64, dynamically linked, not stripped |
| Size | ~1.8 MB |
| Reported version | `AutoDock Vina aea4fd5-mod` (via `moldina --version`) |
| SHA-256 (current) | `4fa7b2de4fe8920c867017ed49927d186aa48e02d53370b189d2d4fd945f3fbd` |
| Added | commit `01355d3` ("first commit") |
| Replaced | commit `9d70efe` ("update to use newer version of Moldina"), 2026-05-26 — reported `207916a-mod`, SHA-256 `3564df63…dfc7`, ~4.3 MB, statically linked |
| Last replaced | commit `8ed4b94` (2026-08-06; its message is about an unrelated UI change) |

No commit's message records which source commit this was built from, or
who built it — the 2026-08-06 replacement wasn't even mentioned, which is
why this table still listed the previous binary until Stage 15.1
(2026-10-05) re-checked it. If you know that, please fill it in here
— this table only records what could be recovered from the binary and git
history.

## Updating this binary

1. Build from source per the "Source" section above (or obtain a build
   through whatever process was actually used — see the gap noted above).
2. Overwrite `Moldina/moldina`.
3. Verify it still speaks the expected CLI: `chmod +x Moldina/moldina && ./Moldina/moldina --help` should list `--receptor`, `--ligand`, `--config`, `--out`, `--cpu`, `--exhaustiveness`, `--num_modes`, `--energy_range` (the flags `services/moldina_runner/__init__.py::_build_docking_command()` passes it), and it must still accept `--population` and `--search_alg_PSO` (PSO mode) and run without them (Vina mode).
4. Rebuild the image: `docker build -f docker/moldina_runner.Dockerfile -t mcp-moldina-runner:latest .`
   Then run the real-docking test in it, which docks with both searches and
   parses the output (see [docs/DOCKER_DEPLOYMENT.md](../docs/DOCKER_DEPLOYMENT.md#real-docking-tests)):
   `docker run --rm -v "$PWD:/workspace:ro" -w /workspace -e PYTHONPATH=/workspace mcp-moldina-runner:latest python -m pytest tests/test_real_docking.py -p no:cacheprovider`
5. Update the SHA-256, version, and (ideally) the exact source commit in the table above, and note it in your commit message — the previous two commits didn't, which is why this table has gaps.

## Why not Git LFS or a from-source Docker build

Both are the more standard fix for a binary this size, but:
- Migrating *existing* history to Git LFS (`git lfs migrate`) rewrites
  commit hashes — that needs deliberate, coordinated action (force-pushing
  rewritten history affects anyone else's clone), not a drive-by fix.
- Switching `moldina_runner.Dockerfile` to build from source via the steps
  above would add real build time and a new external dependency
  (`github.com/It4innovations/moldina-multiple-ligand-molecular-docking-over-autodock-vina` availability) to every image build; worth
  doing, but as a deliberate change someone verifies end-to-end, not
  bundled into an unrelated fix.
