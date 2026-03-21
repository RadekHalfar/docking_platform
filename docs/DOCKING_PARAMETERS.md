# Docking Process Parameters Reference

Complete overview of all parameters used in the molecular docking workflow across all stages.

---

## Table of Contents

1. [Stage 1: Structure Download (RCSB Downloader)](#stage-1-structure-download)
2. [Stage 2: Ligand Preparation](#stage-2-ligand-preparation)
3. [Stage 3: Protein Preparation](#stage-3-protein-preparation)
4. [Stage 4: Molecular Docking (Moldina)](#stage-4-molecular-docking)
5. [Stage 5: Visualization (PyMOL)](#stage-5-visualization)
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
| `cpu` | integer | No | `1` | Number of CPU cores to use |
| `population` | integer | No | `500` | PSO population size (min: 5) |
| `exhaustiveness` | integer | No | `8` | Search exhaustiveness |
| `num_modes` | integer | No | `15` | Maximum number of binding modes |
| `energy_range` | number | No | `100.0` | Max energy difference for modes (kcal/mol) |

### Critical Parameters

#### `center` - Search Box Center ⚠️ **CRITICAL**
- **Format:** `[x, y, z]` coordinates in Angstroms
- **Purpose:** Defines where docking algorithm searches
- **How to determine:**
  1. Open protein in PyMOL or molecular viewer
  2. Identify binding site/active site
  3. Note center coordinates
  4. Use those values
- **Example:** `[10.5, 20.3, -5.2]`
- **Impact:** Wrong center = docking in wrong location!

#### `box_size` - Search Box Dimensions ⚠️ **CRITICAL**
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

### Performance Parameters

#### `cpu` - CPU Cores
- **Range:** 1 - number of available cores
- **Impact:** Linear speedup with more cores
- **Recommendation:** Use 4-8 cores for good balance

#### `population` - PSO Population Size
- **Range:** 5 - 10000+
- **Default:** 500
- **Impact:** 
  - Larger = better exploration, slower
  - Smaller = faster, may miss optimal solution
- **Guidelines:**
  - Quick test: 100-200
  - Standard: 500-1000
  - Thorough: 1000-2000

#### `exhaustiveness` - Search Thoroughness
- **Range:** 1 - 100+
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
  "cpu": 4,
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

**Service:** PyMOL Visualizer (port 8005)  
**Tool:** `visualize_docking`

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `receptor_path` | string | **Yes** | - | Path to receptor file (PDBQT/PDB) |
| `ligand_paths` | array | **Yes** | - | List of docked ligand PDBQT files |
| `output_dir` | string | **Yes** | - | Directory for output files |
| `affinities` | array | No | `null` | Binding affinities for color coding |
| `show_surface` | boolean | No | `true` | Show protein surface |
| `show_binding_site` | boolean | No | `true` | Highlight binding site residues |
| `binding_site_radius` | number | No | `5.0` | Radius for binding site detection (Å) |
| `render_image` | boolean | No | `true` | Render PNG image |
| `image_width` | integer | No | `1920` | Image width in pixels |
| `image_height` | integer | No | `1080` | Image height in pixels |
| `image_dpi` | integer | No | `300` | Image DPI for publications |
| `ray_trace` | boolean | No | `true` | Use ray tracing for high quality |

### Visualization Parameters

#### `affinities` - Binding Affinities
- **Format:** Array of numbers (kcal/mol)
- **Purpose:** Color-code ligands by binding strength
- **Colors:**
  - Green = Best (most negative)
  - Yellow = Medium
  - Red = Worst (least negative)
- **Example:** `[-8.5, -7.2, -6.8]`

#### `show_surface`
- **true:** Display protein molecular surface
- **false:** Show cartoon/ribbon only
- **Impact:** Surface helps visualize binding pocket

#### `show_binding_site`
- **true:** Highlight residues near ligands
- **false:** Show all residues equally
- **Uses:** `binding_site_radius` to determine proximity

#### `binding_site_radius`
- **Range:** 3.0 - 10.0 Å
- **Default:** 5.0 Å
- **Purpose:** Distance threshold for binding site residues
- **Impact:** Larger = more residues highlighted

### Image Rendering Parameters

#### `render_image`
- **true:** Generate PNG image automatically
- **false:** Only create .pml script (open manually)

#### `image_width` & `image_height`
- **Presets:**
  - HD: 1920 × 1080
  - 4K: 3840 × 2160
  - Publication: 2400 × 1800 (square-ish)
- **Impact:** Larger = better quality, longer render time

#### `image_dpi`
- **Common values:**
  - 150 - Screen viewing
  - 300 - Standard publication
  - 600 - High-quality publication
- **Impact:** Higher DPI = sharper images, larger files

#### `ray_trace`
- **true:** High-quality rendering with shadows/reflections
- **false:** Fast OpenGL rendering
- **Impact:** Ray tracing takes longer but looks professional

### Example

```json
{
  "receptor_path": "/data/prepared/6lu7.pdbqt",
  "ligand_paths": [
    "/data/docking_results/ATP_out.pdbqt",
    "/data/docking_results/HEM_out.pdbqt"
  ],
  "output_dir": "/data/visualization",
  "affinities": [-8.5, -7.2],
  "show_surface": true,
  "show_binding_site": true,
  "binding_site_radius": 5.0,
  "render_image": true,
  "image_width": 1920,
  "image_height": 1080,
  "image_dpi": 300,
  "ray_trace": true
}
```

### Impact on Visualization

- **affinities**: Enables quick visual comparison of binding strength
- **render_image**: Docker containers can render without GUI
- **ray_trace**: Publication-quality images
- **binding_site_radius**: Balance between detail and clarity

---

## Quick Reference Table

### Essential Parameters by Stage

| Stage | Critical Parameters | Default | Notes |
|-------|-------------------|---------|-------|
| **Download** | `protein_id` | - | Must be valid PDB ID |
| | `ligand_ids` | `[]` | Can download without ligands |
| **Ligand Prep** | `ligand_paths` | - | List of input files |
| | `ph` | `7.4` | Match protein pH |
| | `add_hydrogens` | `true` | Required for docking |
| **Protein Prep** | `protein_paths` | - | List of input files |
| | `ph` | `7.4` | Match ligand pH |
| | `remove_waters` | `true` | Clean structure |
| **Docking** | `receptor_path` | - | Prepared PDBQT |
| | `ligand_paths` | - | Prepared PDBQT list |
| | **`center`** | - | ⚠️ Must determine from structure |
| | **`box_size`** | - | ⚠️ Must encompass binding site |
| | `exhaustiveness` | `8` | Higher = better quality |
| | `population` | `500` | PSO parameter |
| **Visualization** | `receptor_path` | - | Original or prepared |
| | `ligand_paths` | - | Docking output |
| | `affinities` | `null` | From docking results |
| | `render_image` | `true` | Docker-compatible |

### Parameter Consistency Requirements

1. **pH Values**: Use same pH for ligand and protein preparation
2. **File Formats**: 
   - Input: PDB (protein), SDF/MOL (ligands)
   - Prepared: PDBQT (both)
   - Output: PDBQT (docked poses)
3. **Paths**: Must be accessible to Docker containers (use volume mounts)

### Performance Optimization

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

✅ **DO:**
- Match pH between ligand and protein preparation
- Verify binding site location before docking
- Use adequate exhaustiveness (≥8 for real work)
- Remove crystallographic waters
- Keep important cofactors
- Use ray tracing for publication figures

❌ **DON'T:**
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
