# Moldina Binary

`moldina` is a prebuilt, statically-linked Linux x86-64 executable that
`docker/moldina_runner.Dockerfile` copies into the `moldina-runner` image at
`/usr/local/bin/moldina`. It is committed directly to this repo as a binary
blob rather than built from source at image-build time.

## Source

Upstream source and build instructions are documented in
[docs/MOLDINA_DOCKING.md](../docs/MOLDINA_DOCKING.md#installation):

```bash
git clone https://github.com/psa-lab/Moldina.git
cd Moldina
mkdir -p build/linux/release && cd build/linux/release
cmake ../../.. && make
# executable ends up at: build/linux/release/Moldina
```

Moldina is a fork of [AutoDock Vina](https://vina.scripps.edu/) that
replaces Vina's iterated local search with Particle Swarm Optimization —
see `docs/MOLDINA_DOCKING.md` for what that changes in practice. Running
`moldina --help` reports `AutoDock Vina 207916a-mod`, consistent with a
patched Vina fork; that string doesn't map to an official Vina release tag,
so treat it as this fork's own version marker rather than a Vina version.

**Not currently wired up:** `docker/moldina_runner.Dockerfile` does not run
the clone/cmake/make steps above — it just `COPY`s this prebuilt binary.
Nothing enforces that this file was actually produced by that source at
that commit; if you rebuild from source, verify the result behaves the
same (see "Updating this binary" below) before replacing this file.

## Provenance of the committed binary

| | |
|---|---|
| File | `Moldina/moldina` |
| Type | ELF 64-bit LSB executable, x86-64, statically linked |
| Size | ~4.3 MB |
| Reported version | `AutoDock Vina 207916a-mod` (via `moldina --help`) |
| SHA-256 (current) | `3564df63cee0e131db5908b0ebbb37544880aecdeec304e9d703509622acfdc7` |
| Added | commit `01355d3` ("first commit") |
| Last replaced | commit `9d70efe` ("update to use newer version of Moldina"), 2026-05-26 |

Neither commit's message records which commit of `psa-lab/Moldina` this
was built from, or who built it. If you know that, please fill it in here
— this table only records what could be recovered from the binary and git
history.

## Updating this binary

1. Build from source per the "Source" section above (or obtain a build
   through whatever process was actually used — see the gap noted above).
2. Overwrite `Moldina/moldina`.
3. Verify it still speaks the expected CLI: `chmod +x Moldina/moldina && ./Moldina/moldina --help` should list `--receptor`, `--ligand`, `--center_x/y/z`, `--size_x/y/z`, `--cpu`, `--population`, `--exhaustiveness`, `--num_modes`, `--energy_range` (the flags `services/moldina_runner/__init__.py` passes it).
4. Rebuild the image: `docker build -f docker/moldina_runner.Dockerfile -t mcp-moldina-runner:latest .`
5. Update the SHA-256, version, and (ideally) the exact source commit in the table above, and note it in your commit message — the previous two commits didn't, which is why this table has gaps.

## Why not Git LFS or a from-source Docker build

Both are the more standard fix for a binary this size, but:
- Migrating *existing* history to Git LFS (`git lfs migrate`) rewrites
  commit hashes — that needs deliberate, coordinated action (force-pushing
  rewritten history affects anyone else's clone), not a drive-by fix.
- Switching `moldina_runner.Dockerfile` to build from source via the steps
  above would add real build time and a new external dependency
  (`github.com/psa-lab/Moldina` availability) to every image build; worth
  doing, but as a deliberate change someone verifies end-to-end, not
  bundled into an unrelated fix.
