# Docking Process Parameters Reference

Complete overview of all parameters used in the molecular docking workflow across all stages.

---

## Table of Contents

1. [Stage 1: Structure Download (RCSB Downloader)](#stage-1-structure-download)
2. [Stage 2: Ligand Preparation](#stage-2-ligand-preparation)
3. [Stage 3: Protein Preparation](#stage-3-protein-preparation)
4. [Stage 4: Molecular Docking (Moldina)](#stage-4-molecular-docking)
5. [Stage 5: Visualization](#stage-5-visualization)
6. [Quick Reference Table](#quick-reference-table)

---

## Stage 1: Structure Download

**Service:** RCSB Downloader (port 8001)  
**Tool:** `download_structures`

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `protein_id` | string | **Yes** | - | PDB identifier (4 characters, e.g., '6lu7') |
| `ligand_ids` | array | No | `[]` | List of ligand identifiers (e.g., `['ATP', 'HEM']`) |
| `formats` | object | No | `{'protein': 'pdb', 'ligand': 'sdf'}` | File formats for download |
| `save_dir` | string | No | `"./data"` | Directory to save downloaded files |

### Format Options

**Protein formats:**
- `pdb` - Protein Data Bank format (recommended)
- `cif` - Crystallographic Information File

**Ligand formats:**
- `sdf` - Structure Data File (recommended)
- `mol` - Molecular structure file

### Example

```json
{
  "protein_id": "6lu7",
  "ligand_ids": ["ATP", "HEM"],
  "formats": {
    "protein": "pdb",
    "ligand": "sdf"
  },
  "save_dir": "/data/downloads"
}
```

### Impact on Docking

- **protein_id**: Determines the target structure for docking
- **ligand_ids**: Defines which small molecules to dock
- **formats**: PDB format is preferred for compatibility with preparation tools

---

## Stage 2: Ligand Preparation

**Service:** Ligand Preparer (port 8002)  
**Tool:** `prepare_ligands`

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `ligand_paths` | array | **Yes** | - | List of paths to ligand files (SDF, MOL, MOL2) |
| `output_dir` | string | No | `"./data/prepared"` | Directory to save prepared PDBQT files |
| `ph` | number | No | `7.4` | pH for protonation state (physiological pH) |
| `add_hydrogens` | boolean | No | `true` | Add explicit hydrogen atoms |
| `generate_3d` | boolean | No | `true` | Generate 3D coordinates if missing |
| `optimize` | boolean | No | `true` | Optimize ligand geometry |

### Parameter Details

#### `ph` (pH Value)
- **Range:** 0.0 - 14.0
- **Physiological:** 7.4 (blood pH)
- **Impact:** Determines protonation state of ionizable groups
- **Common values:**
  - 5.0 - Acidic environment (e.g., stomach)
  - 7.4 - Physiological (most common)
  - 9.0 - Basic environment

#### `add_hydrogens`
- **true:** Adds polar hydrogens for accurate docking
- **false:** Uses existing hydrogens only
- **Impact:** Critical for hydrogen bonding calculations

#### `generate_3d`
- **true:** Creates 3D structure from 2D or 1D representation
- **false:** Uses existing 3D coordinates
- **Impact:** Required if ligand file lacks 3D coordinates

#### `optimize`
- **true:** Minimizes energy, improves geometry
- **false:** Uses coordinates as-is
- **Impact:** Better starting conformations can improve docking results

### Example

```json
{
  "ligand_paths": [
    "/data/downloads/ATP.sdf",
    "/data/downloads/HEM.sdf"
  ],
  "output_dir": "/data/prepared",
  "ph": 7.4,
  "add_hydrogens": true,
  "generate_3d": true,
  "optimize": true
}
```

### Impact on Docking

- **ph**: Affects charge distribution and hydrogen bonding
- **add_hydrogens**: Essential for accurate scoring
- **optimize**: Better conformations may lead to better binding poses

---

## Stage 3: Protein Preparation

**Service:** Protein Preparer (port 8003)  
**Tool:** `prepare_proteins`

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `protein_paths` | array | **Yes** | - | List of paths to protein PDB files |
| `output_dir` | string | **Yes** | - | Directory to save prepared PDBQT files |
| `ph` | number | No | `7.4` | pH for protonation state |
| `add_hydrogens` | boolean | No | `true` | Add explicit hydrogen atoms |
| `remove_waters` | boolean | No | `true` | Remove water molecules |
| `remove_heteroatoms` | boolean | No | `false` | Remove non-protein atoms (ligands, ions, etc.) |
| `keep_residues` | array | No | `null` | List of residue names to keep (e.g., `['HEM', 'NAD']`) |

### Parameter Details

#### `ph` (pH Value)
- Same as ligand preparation
- Should match ligand pH for consistency

#### `add_hydrogens`
- **true:** Adds polar hydrogens to protein
- **false:** Uses existing hydrogens
- **Impact:** Critical for protein-ligand hydrogen bonds

#### `remove_waters`
- **true:** Removes crystallographic waters (recommended)
- **false:** Keeps all water molecules
- **Impact:** Waters can interfere with docking; remove unless specifically relevant

#### `remove_heteroatoms`
- **true:** Removes all non-protein atoms (cofactors, metals, etc.)
- **false:** Keeps heteroatoms
- **Warning:** May remove important cofactors

#### `keep_residues`
- Whitelist of residues to preserve when `remove_heteroatoms=true`
- Example: `['HEM', 'NAD', 'FE']` to keep heme, NAD, and iron
- **Use case:** Preserve cofactors while removing unwanted molecules

### Example

```json
{
  "protein_paths": ["/data/downloads/6lu7.pdb"],
  "output_dir": "/data/prepared",
  "ph": 7.4,
  "add_hydrogens": true,
  "remove_waters": true,
  "remove_heteroatoms": false,
  "keep_residues": null
}
```

### Impact on Docking

- **remove_waters**: Clean protein structure for better docking
- **remove_heteroatoms**: Be careful not to remove important cofactors
- **keep_residues**: Essential for metalloproteins or enzymes with cofactors

---

## Stage 4: Molecular Docking

**Service:** Moldina Runner (port 8004)  
**Tool:** `run_docking`

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `receptor_path` | string | **Yes** | - | Path to receptor PDBQT file |
| `ligand_paths` | array | **Yes** | - | List of paths to ligand PDBQT files |
| `output_dir` | string | **Yes** | - | Directory for docking output files |
| `center` | array | **Yes** | - | Search box center `[x, y, z]` in Angstroms |
| `box_size` | array | **Yes** | - | Search box dimensions `[x, y, z]` in Angstroms |
| `docking_algorithm` | string | No | `"moldina"` | Search algorithm: `"moldina"` (PSO) or `"vina"` (Monte Carlo) — see below |
| `cpu` | integer | No | `1` | Number of CPU cores to use per ligand (Vina mode only — PSO ignores it) |
| `population` | integer | No | `500` | PSO population size (min: 5; Moldina mode only) |
| `exhaustiveness` | integer | No | `8` | Search exhaustiveness (Vina mode only — PSO ignores it) |
| `num_modes` | integer | No | `15` | Maximum number of binding modes |
| `energy_range` | number | No | `100.0` | Max energy difference for modes (kcal/mol) |
| `parallel` | boolean | No | `false` | Dock up to `max_workers` ligands concurrently instead of one at a time |
| `max_workers` | integer | No | `1` | Max ligands docked concurrently when `parallel` is true |

### Critical Parameters

#### `center` - Search Box Center (Critical)
- **Format:** `[x, y, z]` coordinates in Angstroms
- **Purpose:** Defines where docking algorithm searches
- **How to determine:**
  1. Open protein in PyMOL or molecular viewer
  2. Identify binding site/active site
  3. Note center coordinates
  4. Use those values
- **Example:** `[10.5, 20.3, -5.2]`
- **Impact:** Wrong center = docking in wrong location!

#### `box_size` - Search Box Dimensions (Critical)
- **Format:** `[x, y, z]` dimensions in Angstroms
- **Purpose:** Defines size of search space
- **Guidelines:**
  - Small binding site: 20-25 Å per dimension
  - Medium binding site: 25-30 Å per dimension
  - Large binding site: 30-40 Å per dimension
  - Add 5-10 Å margin for ligand flexibility
- **Example:** `[25.0, 30.0, 35.0]`
- **Impact:** 
  - Too small: May miss optimal binding pose
  - Too large: Slower computation, less focused search

#### `docking_algorithm` - Search Algorithm
- **Values:** `"moldina"` (default) or `"vina"`. Aliases are accepted and
  normalised (`pso`, `Moldina (PSO)` → `moldina`; `autodock vina`,
  `monte carlo`, `Vina (Monte Carlo)` → `vina`, case-insensitive); anything
  else is rejected — by the workflow parser's clarification prompt, and by
  `run_docking` itself with a `ValueError`.
- **What changes:** both modes run the same committed `moldina` binary with
  the same `center.txt`. `"moldina"` adds `--population N --search_alg_PSO`
  (Particle Swarm Optimization); `"vina"` omits both, so the binary runs
  AutoDock Vina's own Monte Carlo search. See
  [MOLDINA_DOCKING.md § Vina mode](MOLDINA_DOCKING.md#vina-mode) for exactly
  which Vina that is.
- **Which parameters apply:** `population` is PSO-only. `exhaustiveness` and
  `cpu` are Vina-only: measured on the committed binary, PSO's run time and
  search are unaffected by them (see the same section).
- **Recorded:** in the dock result (`docking_algorithm` key) and in
  `parameters.txt`. Changing it reruns dock and visualize only.
- **Choosing:** PSO is far faster per ligand (seconds vs. ~1.5 minutes for
  6lu7/ATP at the defaults) but in that comparison scored worse
  (-4.3 vs. -5.8 kcal/mol); Vina's search is the more thorough one here.

### Performance Parameters

#### `cpu` - CPU Cores
- **Default/fixed via GUI:** `1` — not currently exposed as a GUI-tunable
  setting; only direct/CLI callers of `run_docking` can override it.
- **Range:** 1 - number of available cores
- **Impact:** Vina mode only — more cores speed up a single ligand's own
  Monte Carlo search. PSO ignores it.
- **Interaction with `max_workers`:** each parallel worker runs a process
  using `cpu` cores, so the server clamps workers to
  `CPU budget // cpu` (see below).

#### `parallel` / `max_workers` - Concurrent Ligands
- **Default:** `parallel=false` (sequential — one ligand docked at a time)
- **Purpose:** Dock up to `max_workers` *different* ligands at the same time
  (each still its own independent, single-`--cpu` Moldina process — not
  batched into one invocation)
- **GUI:** "Parallel ligand docking" checkbox + "Max parallel workers" number
  input in the Advanced expander, capped at `moldina-runner`'s
  configured `deploy.resources.limits.cpus` in `docker-compose.yml` (parsed
  directly by `ui/worker_cap.py::detect_moldina_worker_cap()`, since it's
  bind-mounted into the `ui` container) — falls back to the `ui` container's
  own `os.cpu_count()` only if the compose file can't be found/parsed, and
  then shows a warning with the reason. Either way this is a convenience
  suggestion only, not authoritative (see below). Its default is the
  benchmarked best worker count (`ui/worker_cap.py::MEASURED_BEST_WORKERS`,
  8 at `cpus: '10'`/`8G`), never above the cap — see
  [BATCH_DOCKING.md's benchmark baseline](BATCH_DOCKING.md#benchmark-baseline-2026-10-05)
- **Server-side safety clamp:** `max_workers` is capped to the number of
  ligands in the batch and to `moldina-runner`'s real, schedulable CPU
  budget — `min(cgroup CPU quota, os.cpu_count())`, via
  `_detect_effective_cpu_count()` — divided by `cpu`, so workers × `cpu`
  never exceeds it (e.g. a 10-CPU budget with `cpu=4` allows 2 workers). Neither number is trusted alone: Docker
  Compose's `deploy.resources.limits.cpus` is a scheduling quota, not a CPU
  mask, so `os.cpu_count()` doesn't reflect it and can report far more
  cores than are actually schedulable — but the configured quota isn't
  trustworthy alone either, since deploying the same `cpus:` value on
  smaller hardware than it assumes (e.g. `cpus: '10'` on a 4-core machine)
  would let the quota claim more workers than physically exist if taken at
  face value.
- **Risk:** requesting more workers than the real CPU budget doesn't just
  fail to help — benchmarking on this project showed wall-clock time getting
  *worse* than sequential once concurrent workers meaningfully exceed the
  quota (severe scheduling contention), not just a smaller-than-expected
  speedup. Memory is a secondary, unquantified risk on top of that — start
  modest and watch for failures on large batches.

#### `population` - PSO Population Size
- **Moldina (PSO) mode only** — not passed to the binary in Vina mode.
- **Range:** 5 - 5000 (clamped)
- **Default:** 500
- **Impact:** 
  - Larger = better exploration, slower
  - Smaller = faster, may miss optimal solution
- **Guidelines:**
  - Quick test: 100-200
  - Standard: 500-1000
  - Thorough: 1000-2000

#### `exhaustiveness` - Search Thoroughness
- **Vina mode only** — measured on the committed binary, PSO's run time and
  result don't change between 1 and 16 (it isn't even printed in the PSO
  log header). It is still passed in both modes.
- **Range:** 1 - 64 (clamped)
- **Default:** 8
- **Impact:**
  - Higher = more thorough search, slower
  - Lower = faster, less reliable
- **Guidelines:**
  - Quick test: 4-8
  - Standard: 8-16
  - Publication quality: 16-32

#### `num_modes` - Binding Modes
- **Range:** 1 - 100
- **Default:** 15
- **Purpose:** Number of top-scoring poses to return
- **Impact:** More modes = more output, longer runtime

#### `energy_range` - Energy Window
- **Range:** 1.0 - 1000.0 kcal/mol
- **Default:** 100.0
- **Purpose:** Keep poses within this energy of best pose
- **Typical:** 3-5 kcal/mol for publication
- **High value (100):** Keeps all poses for analysis

### Example

```json
{
  "receptor_path": "/data/prepared/6lu7.pdbqt",
  "ligand_paths": [
    "/data/prepared/ATP.pdbqt",
    "/data/prepared/HEM.pdbqt"
  ],
  "output_dir": "/data/docking_results",
  "center": [10.5, 20.3, -5.2],
  "box_size": [25.0, 30.0, 35.0],
  "cpu": 1,
  "population": 500,
  "exhaustiveness": 16,
  "num_modes": 15,
  "energy_range": 100.0
}
```

### Impact on Docking Quality

**Critical for Success:**
- `center` and `box_size` - Must encompass binding site
- `exhaustiveness` - Higher = more reliable results
- `population` - Adequate size for good sampling

**Performance Trade-offs:**
- More CPU = faster
- Higher exhaustiveness = slower but better
- Larger population = slower but more thorough

---

## Stage 5: Visualization

**Service:** Ligand Preparer (port 8002)
**Tool:** `convert_docked_poses`

As of the PyMOL → 3Dmol.js migration, this stage no longer renders a server-side
image. It converts each ligand's docked-pose PDBQT (all modes, in one
multi-model file) into one multi-pose file per output format, which the GUI's
in-browser 3Dmol.js viewer reads directly and offers as PDBQT/PDB/SDF/MOL2
downloads (see [GUI_GUIDE.md](GUI_GUIDE.md)). The legacy PyMOL Visualizer
(`visualize_docking`, port 8005) still exists and still works if you call it
directly — see [API.md](API.md#5-pymol-visualizer-service) — it's just no
longer part of the default workflow.

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pose_results` | array | **Yes** | - | Per-ligand docking result dicts from `run_docking` (`ligand_id`, `output_path`, `modes`) |
| `output_dir` | string | **Yes** | - | Directory for output files |
| `formats` | array | No | `["sdf", "pdb", "mol2"]` | Which formats to write (PDBQT is never generated here — it's already the native `run_docking` output) |

### How It Works

1. **Parse once per ligand:** the docked-pose PDBQT is parsed with Meeko
   (`PDBQTMolecule` + `RDKitMolCreate`), restoring proper bond
   orders/aromaticity from the `REMARK SMILES` Meeko embedded when the
   ligand was originally prepared.
2. **Write every requested format from that one parse:** SDF and PDB are
   written directly (one record/MODEL per pose); MOL2 is derived from the
   SDF via OpenBabel, so it also gets the restored bond orders rather than
   OpenBabel's own (weaker) geometry-based guess.
3. **Fallback:** if the Meeko round trip fails for a ligand (e.g. it wasn't
   originally prepared by Meeko), each requested format is converted
   independently straight from the PDBQT via OpenBabel — still usable, just
   without per-pose affinity/RMSD metadata.

### `formats` — Output Formats
- **Values:** any of `"sdf"`, `"pdb"`, `"mol2"`
- **Purpose:** each becomes a download option in the GUI's format picker,
  alongside the always-available native PDBQT
- **Example:** `["sdf", "pdb"]` skips MOL2 generation for that job

### Example

```json
{
  "pose_results": [
    {
      "ligand_id": "ATP",
      "output_path": "/data/docking_results/ATP_out.pdbqt",
      "modes": [
        {"mode": 1, "affinity": -8.5, "rmsd_lb": 0.0, "rmsd_ub": 0.0},
        {"mode": 2, "affinity": -7.2, "rmsd_lb": 1.9, "rmsd_ub": 2.7}
      ]
    }
  ],
  "output_dir": "/data/visualization"
}
```

### Impact on Visualization

- **formats**: Controls which download formats are generated; SDF also
  carries the per-pose affinity/RMSD metadata the 3Dmol.js pose selector
  reads
- **Meeko round trip**: Restores real bond orders (not just atom positions),
  which a raw PDBQT alone doesn't carry

---

## Quick Reference Table

### Essential Parameters by Stage

| Stage | Critical Parameters | Default | Notes |
|-------|-------------------|---------|-------|
| **Get Input Files** | `protein_id` | - | Must be valid PDB ID (or `protein_file` for local input) |
| | `ligand_ids` | `[]` | Can download without ligands |
| **Ligand Prep** | `ligand_paths` | - | List of input files |
| | `ph` | `7.4` | Match protein pH |
| | `add_hydrogens` | `true` | Required for docking |
| **Protein Prep** | `protein_paths` | - | List of input files |
| | `ph` | `7.4` | Match ligand pH |
| | `remove_waters` | `true` | Clean structure |
| **Docking** | `receptor_path` | - | Prepared PDBQT |
| | `ligand_paths` | - | Prepared PDBQT list |
| | **`center`** | - | Must determine from structure |
| | **`box_size`** | - | Must encompass binding site |
| | `docking_algorithm` | `"moldina"` | `"moldina"` (PSO) or `"vina"` (Monte Carlo) |
| | `exhaustiveness` | `8` | Higher = better quality (Vina mode only) |
| | `population` | `500` | PSO parameter (Moldina mode only) |
| **Visualization** | `pose_results` | - | Docking output (`run_docking` results) |
| | `output_dir` | - | Where to write converted pose files |
| | `formats` | `["sdf", "pdb", "mol2"]` | PDBQT is always available too (native, no conversion) |

### Parameter Consistency Requirements

1. **pH Values**: Use same pH for ligand and protein preparation
2. **File Formats**: 
   - Input: PDB (protein), SDF/MOL (ligands)
   - Prepared: PDBQT (both)
   - Output: PDBQT (docked poses)
3. **Paths**: Must be accessible to Docker containers (use volume mounts)

### Performance Optimization

The `cpu` values below apply to direct/CLI callers of `run_docking` — the GUI itself
fixes `cpu=1` and no longer exposes it, offering `parallel`/`max_workers` (dock several
ligands concurrently) instead of a per-ligand core count. See
[Optional parallel docking](BATCH_DOCKING.md#optional-parallel-docking).

**Fast (testing):**
```json
{
  "cpu": 2,
  "population": 100,
  "exhaustiveness": 4
}
```
⏱️ Time: ~2-5 minutes per ligand

**Standard (research):**
```json
{
  "cpu": 4,
  "population": 500,
  "exhaustiveness": 16
}
```
⏱️ Time: ~10-20 minutes per ligand

**Thorough (publication):**
```json
{
  "cpu": 8,
  "population": 2000,
  "exhaustiveness": 32
}
```
⏱️ Time: ~30-60 minutes per ligand

---

## Determining Docking Box Parameters

### Method 0: GUI's Visual Box Picker (Easiest)

The Streamlit GUI's **Pick Docking Box Visually** control does this for you
in-browser, no PyMOL required: load the receptor, click an atom to set the
center (a box outline shows the resulting region), and grow/shrink it with
the X/Y/Z − / + buttons (1 Å per click) or by typing in the Docking Box
fields until the box covers the binding site. See
[GUI_GUIDE.md](GUI_GUIDE.md#picking-the-docking-box-visually).

### Method 0b: Chat Command (Auto-Fill from Protein Geometry)

Once a protein is loaded, two chat commands (GUI and CLI `--interactive`
both) compute the box straight from the protein's own atom coordinates —
no clicking, no manual numbers:

- **"set up box center as protein center"** sets `center` to the
  geometric midpoint of the protein's bounding box (`(min+max)/2` per
  axis) — the point that guarantees a bounding-box-sized box is
  actually centered on the whole structure, not just a rough middle.
- **"look across all protein"** sets `box_size` to the protein's
  bounding box extent per axis, **+10%** margin, guaranteeing the box
  covers the entire structure.

This is effectively **blind docking** — searching the whole protein
instead of one focused binding site — which is slower and less targeted
than a small, hand-picked box (see "Do not... Dock in entire protein" in
Best Practices below). It's the right tool when the binding site is
genuinely unknown and a broad first-pass search is wanted, not a
replacement for a focused box when the site is already known. See
[GUI_GUIDE.md](GUI_GUIDE.md#auto-filling-the-docking-box-from-the-protein).

### Method 1: Using PyMOL

```python
# 1. Load your protein
load 6lu7.pdb

# 2. Show as cartoon
hide everything
show cartoon

# 3. Identify binding site (known ligand, active site, etc.)
# If co-crystallized ligand exists:
select binding_site, resn LIG around 5

# 4. Get center of binding site
get_extent binding_site
# Returns: [[x_min, y_min, z_min], [x_max, y_max, z_max]]

# 5. Calculate center:
# center_x = (x_min + x_max) / 2
# center_y = (y_min + y_min) / 2
# center_z = (z_min + z_max) / 2

# 6. Calculate box size:
# size_x = (x_max - x_min) + 10  # Add 10 Å margin
# size_y = (y_max - y_min) + 10
# size_z = (z_max - z_min) + 10
```

### Method 2: Using Literature

- Check publication for binding site residues
- Use known active site coordinates
- Copy from similar proteins

### Method 3: Visual Inspection

1. Open protein in viewer
2. Rotate to view binding pocket
3. Enable coordinate display
4. Note coordinates of pocket center
5. Estimate pocket dimensions

---

## Common Parameter Combinations

### Drug Discovery Screening

```json
{
  "ph": 7.4,
  "exhaustiveness": 8,
  "population": 500,
  "num_modes": 1,
  "energy_range": 3.0
}
```
Purpose: Fast screening, top pose only

### Binding Mode Analysis

```json
{
  "ph": 7.4,
  "exhaustiveness": 16,
  "population": 1000,
  "num_modes": 20,
  "energy_range": 5.0
}
```
Purpose: Explore multiple binding modes

### pH Sensitivity Study

```json
[
  {"ph": 5.0, "exhaustiveness": 16},
  {"ph": 7.4, "exhaustiveness": 16},
  {"ph": 9.0, "exhaustiveness": 16}
]
```
Purpose: Compare binding at different pH

---

## Troubleshooting

### Poor Docking Results

**Check these parameters:**
1. `center` - Is it actually at the binding site?
2. `box_size` - Is it large enough to encompass the pocket?
3. `exhaustiveness` - Try increasing to 16 or 32
4. `population` - Try 1000 or higher
5. `ph` - Are ligand and protein at same pH?
6. `remove_waters` - Did you clean the protein?

### Long Runtime

**Optimize these:**
1. Decrease `exhaustiveness` (8 instead of 32)
2. Decrease `population` (200-300 instead of 1000+)
3. Decrease `num_modes` (5 instead of 20)
4. Increase `cpu` (use more cores)
5. Reduce number of ligands per batch

### Memory Issues

**Solutions:**
1. Reduce `population` size
2. Reduce `box_size` if too large (>40 Å is rarely needed)
3. Process fewer ligands simultaneously
4. Increase Docker memory limit

---

## Best Practices

**Do:**
- Match pH between ligand and protein preparation
- Verify binding site location before docking
- Use adequate exhaustiveness (≥8 for real work)
- Remove crystallographic waters
- Keep important cofactors
- Use ray tracing for publication figures

**Do not:**
- Use pH 7.4 blindly (check protein's functional pH)
- Guess binding site coordinates
- Use exhaustiveness < 4 (unreliable)
- Remove all heteroatoms without checking
- Dock in entire protein (too slow, inaccurate)
- Submit low-DPI images to journals

---

## Parameter Validation Checklist

Before running docking, verify:

- [ ] Protein PDB ID is valid
- [ ] Ligands downloaded successfully  
- [ ] pH values match between stages
- [ ] Hydrogens added to both protein and ligands
- [ ] Waters removed from protein
- [ ] Binding site center is correct (visualize!)
- [ ] Box size encompasses binding site
- [ ] Output directories exist and are writable
- [ ] Sufficient CPU/memory allocated
- [ ] Docker volumes mounted correctly

---

## Additional Resources

- **RCSB PDB:** https://www.rcsb.org/ - Protein structures
- **PubChem:** https://pubchem.ncbi.nlm.nih.gov/ - Ligand structures
- **PyMOL Documentation:** https://pymol.org/ - Visualization
- **AutoDock Vina:** http://vina.scripps.edu/ - Docking methodology
- **RDKit:** https://www.rdkit.org/ - Cheminformatics

---

*Last updated: October 30, 2025*
