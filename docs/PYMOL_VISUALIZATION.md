# PyMOL Visualization Documentation

## Overview

The PyMOL Visualizer service creates publication-quality molecular visualizations of docking results. It generates PyMOL scripts (.pml) that can be run to visualize protein-ligand complexes, with support for automatic rendering if PyMOL is installed.

## Features

- **Script Generation**: Creates .pml scripts for automated visualization.
- **Smart Coloring**: Colors ligand poses by binding affinity.
- **Binding Site Highlighting**: Highlights residues near ligands.
- **Image Rendering**: Optionally renders PNG images when PyMOL is available.
- **Session Files**: Saves .pse files for interactive exploration.
- **Customizable**: Supports surface display, transparency, image quality, and related options.

## Workflow Overview

The service generates PyMOL scripts that:

1. **Load Structures**: Loads receptor (protein) and docked ligand poses
2. **Apply Styling**: Applies publication-quality visual styles
   - Receptor: Gray cartoon with optional surface
   - Ligands: Colored sticks (by affinity or default colors)
   - Binding site: Highlighted stick representation
3. **Orient View**: Centers and orients the view on the ligand
4. **Export**: Saves session file (.pse) and optionally renders image (.png)

## Installation

### Required
- Python packages (included in Docker images or install locally via requirements.txt)

### Optional: PyMOL for Rendering
If you want to automatically render images, install PyMOL:

**Windows:**
```bash
# Download PyMOL from https://pymol.org/
# Or use conda:
conda install -c conda-forge pymol-open-source
```

**Linux:**
```bash
sudo apt-get install pymol
# Or:
conda install -c conda-forge pymol-open-source
```

**Note:** The service works **without PyMOL** installed - it will generate .pml scripts that you can run manually later.

## Usage

### Start the Server

```bash
python -m services.pymol_visualizer.server --port 8005
```

With custom PyMOL path:
```bash
python -m services.pymol_visualizer.server --pymol-path "/path/to/pymol"
```

### Basic Example

```python
import requests

# Visualize docking results
response = requests.post("http://localhost:8005/mcp", json={
    "jsonrpc": "2.0",
    "id": "vis_1",
    "method": "tool_call",
    "params": {
        "name": "visualize_docking",
        "arguments": {
            "receptor_path": "./prepared_proteins/6lu7.pdbqt",
            "ligand_paths": [
                "./docking_results/ATP_docked.pdbqt",
                "./docking_results/HEM_docked.pdbqt"
            ],
            "output_dir": "./visualizations",
            "affinities": [-8.5, -7.2],
            "render_image": True
        }
    }
})

result = response.json()["result"]
print(f"Script: {result['script_path']}")
print(f"Session: {result['session_path']}")
if result.get('rendered'):
    print(f"Image: {result['image_path']}")
```

### Direct Python API

```python
from services.pymol_visualizer import PyMOLVisualizer

# Initialize visualizer
visualizer = PyMOLVisualizer()

# Generate visualization
result = visualizer.generate_pymol_script(
    receptor_path="receptor.pdbqt",
    ligand_paths=["ligand1.pdbqt", "ligand2.pdbqt"],
    output_script="visualization.pml",
    affinities=[-8.5, -7.2],
    show_surface=True,
    show_binding_site=True,
    binding_site_radius=5.0,
    image_output="docking.png",
    image_width=1920,
    image_height=1080
)

print(f"Generated script: {result['script_path']}")
```

## Parameters

### visualize_docking Tool

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `receptor_path` | string | Yes | - | Path to receptor file (PDBQT or PDB) |
| `ligand_paths` | array | Yes | - | List of paths to docked ligand files |
| `output_dir` | string | Yes | - | Directory for output files |
| `affinities` | array | No | None | Binding affinities for color coding |
| `show_surface` | boolean | No | true | Show protein surface |
| `show_binding_site` | boolean | No | true | Highlight binding site residues |
| `binding_site_radius` | number | No | 5.0 | Binding site radius (Angstroms) |
| `render_image` | boolean | No | true | Render PNG image if PyMOL available |
| `image_width` | number | No | 1920 | Image width (pixels) |
| `image_height` | number | No | 1080 | Image height (pixels) |
| `image_dpi` | number | No | 300 | Image DPI (publication quality) |
| `ray_trace` | boolean | No | true | Use ray tracing for rendering |

## Output Files

### 1. PyMOL Script (.pml)
**File:** `visualization.pml`  
**Purpose:** PyMOL commands to create visualization  
**Use:** Run manually in PyMOL GUI or command-line

```bash
# Run interactively
pymol visualization.pml

# Run in command-line mode (headless)
pymol -c -Q visualization.pml
```

### 2. PyMOL Session (.pse)
**File:** `visualization.pse`  
**Purpose:** Saved PyMOL session with all loaded structures  
**Use:** Open in PyMOL GUI for interactive exploration

```bash
pymol visualization.pse
```

### 3. Rendered Image (.png) [Optional]
**File:** `docking_visualization.png`  
**Purpose:** High-quality rendered image  
**Use:** Publication figures, presentations, reports  
**Note:** Only created if PyMOL is available and `render_image=true`

## Affinity Color Coding

When you provide affinity values, ligands are colored on a gradient:

- 🟢 **Green**: Best (most negative) binding affinity
- 🟡 **Yellow**: Intermediate affinity
- 🔴 **Red**: Worst (least negative) affinity

This makes it easy to visually identify the best binding poses.

## Visual Style

### Receptor (Protein)
- **Representation**: Cartoon
- **Color**: Gray (70% gray)
- **Transparency**: 30% transparent
- **Surface**: Optional, 70% transparent

### Ligands
- **Representation**: Sticks
- **Stick Radius**: 0.2 Angstroms
- **Color**: By affinity (if provided) or default palette

### Binding Site
- **Selection**: Residues within 5Å of first ligand (customizable)
- **Representation**: Sticks
- **Color**: Dark gray
- **Stick Radius**: 0.15 Angstroms

## Example Output

### Response Format

```json
{
  "script_path": "./visualizations/visualization.pml",
  "session_path": "./visualizations/visualization.pse",
  "receptor": "./prepared_proteins/6lu7.pdbqt",
  "num_ligands": 2,
  "ligands": [
    "./docking_results/ATP_docked.pdbqt",
    "./docking_results/HEM_docked.pdbqt"
  ],
  "affinities": [-8.5, -7.2],
  "settings": {
    "show_surface": true,
    "show_binding_site": true,
    "binding_site_radius": 5.0
  },
  "rendered": true,
  "image_path": "./visualizations/docking_visualization.png"
}
```

## Advanced Usage

### Visualize Top N Poses

```python
from services.pymol_visualizer import PyMOLVisualizer

visualizer = PyMOLVisualizer()

# Docking results from Moldina
docking_results = [
    {"output_file": "pose1.pdbqt", "best_affinity": -8.5},
    {"output_file": "pose2.pdbqt", "best_affinity": -7.2},
    {"output_file": "pose3.pdbqt", "best_affinity": -6.8},
    {"output_file": "pose4.pdbqt", "best_affinity": -6.1}
]

# Visualize only top 3 poses
result = visualizer.visualize_docking_results(
    receptor_path="receptor.pdbqt",
    docking_results=docking_results,
    output_dir="./visualizations",
    top_n=3,
    show_surface=True,
    binding_site_radius=7.0
)

print(f"Visualized {result['visualized_results']} of {result['total_results']} poses")
```

### Custom Image Settings

```python
# High-resolution for publication
result = visualizer.generate_pymol_script(
    receptor_path="receptor.pdbqt",
    ligand_paths=["best_pose.pdbqt"],
    output_script="publication.pml",
    image_output="figure.png",
    image_width=3840,   # 4K resolution
    image_height=2160,
    image_dpi=600,      # High DPI
    ray_trace=True      # Maximum quality
)
```

### No Surface Display

```python
# Cleaner view without surface
result = visualizer.generate_pymol_script(
    receptor_path="receptor.pdbqt",
    ligand_paths=["ligand.pdbqt"],
    output_script="clean_view.pml",
    show_surface=False,
    show_binding_site=True
)
```

## Troubleshooting

### Issue: "PyMOL not found"
**Cause**: PyMOL not installed or not in PATH  
**Solution**: 
- Service still works - generates .pml scripts
- Install PyMOL for automatic rendering (see Installation section)
- Or run generated scripts manually: `pymol visualization.pml`

### Issue: Image not rendered
**Check:**
1. Is `rendered: false` in result? PyMOL not available
2. Is `render_image: false` in parameters? Rendering disabled
3. PyMOL timeout? Check PyMOL installation

**Workaround**: Run script manually in PyMOL GUI and export image

### Issue: Ligands not visible
**Check:**
1. Are ligand paths correct and files exist?
2. Do PDBQT files contain valid coordinates?
3. Try opening .pse session in PyMOL GUI to debug

### Issue: Binding site not showing
**Adjust:**
- Increase `binding_site_radius` (default 5.0 Å)
- Check if ligand is actually near protein
- Verify ligand coordinates in PDBQT file

## PyMOL Script Manual Execution

If you prefer to run visualization manually:

```bash
# 1. Generate script only (no rendering)
python -c "
from services.pymol_visualizer import PyMOLVisualizer
viz = PyMOLVisualizer()
viz.generate_pymol_script(
    receptor_path='receptor.pdbqt',
    ligand_paths=['ligand.pdbqt'],
    output_script='viz.pml',
    image_output=None  # Don't render
)
"

# 2. Run in PyMOL GUI (interactive)
pymol viz.pml

# 3. Or run headless (command-line)
pymol -c -Q viz.pml
```

## Integration with Workflow

Typical workflow:

1. **Download**: RCSB Downloader → `protein.pdb`, `ligand.sdf`
2. **Prepare Protein**: Protein Preparer → `receptor.pdbqt`
3. **Prepare Ligands**: Ligand Preparer → `ligand.pdbqt`
4. **Dock**: Moldina Runner → `docked_poses.pdbqt` + affinities
5. **Visualize**: PyMOL Visualizer → `.pml`, `.pse`, `.png`

See `example_complete_workflow.py` for full integration example.

## MCP Tool Definition

```json
{
  "name": "visualize_docking",
  "description": "Create PyMOL visualization of docking results",
  "parameters": {
    "receptor_path": {"type": "string", "required": true},
    "ligand_paths": {"type": "array", "required": true},
    "output_dir": {"type": "string", "required": true},
    "affinities": {"type": "array", "required": false},
    "show_surface": {"type": "boolean", "default": true},
    "show_binding_site": {"type": "boolean", "default": true},
    "binding_site_radius": {"type": "number", "default": 5.0},
    "render_image": {"type": "boolean", "default": true},
    "image_width": {"type": "number", "default": 1920},
    "image_height": {"type": "number", "default": 1080},
    "image_dpi": {"type": "number", "default": 300},
    "ray_trace": {"type": "boolean", "default": true}
  }
}
```

## See Also

- [API Documentation](API.md) - Complete API reference
- [Moldina Docking](MOLDINA_DOCKING.md) - Docking results format
- [Usage Guide](../USAGE_GUIDE.md) - End-to-end examples
- [PyMOL Documentation](https://pymol.org/documentation.html) - PyMOL reference
