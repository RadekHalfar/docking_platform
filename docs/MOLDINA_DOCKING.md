# Moldina Molecular Docking Guide

## Overview

Moldina is an advanced molecular docking tool that uses Particle Swarm Optimization (PSO) for improved binding pose prediction. This guide covers using Moldina through the MCP server interface.

## What is Moldina?

Moldina builds on AutoDock Vina but replaces the iterated local search algorithm with Particle Swarm Optimization (PSO), which can:
- Find better binding poses through swarm intelligence
- Handle complex binding sites more effectively
- Provide more diverse binding mode ensembles
- Use population-based optimization for better global search

### Moldina vs Vina

| Feature | AutoDock Vina | Moldina |
|---------|--------------|---------|
| Algorithm | Iterated Local Search | Particle Swarm Optimization |
| Speed | Faster | Slower (more thorough) |
| Binding Affinity | Good | Often better |
| Pose Diversity | Limited | More diverse |
| Best For | High-throughput screening | Accurate binding prediction |

## Installation

**Docker users don't need any of this** — a prebuilt binary is already
committed at `Moldina/moldina` and copied into the `moldina-runner` image
by `docker/moldina_runner.Dockerfile`. See
[Moldina/README.md](../Moldina/README.md) for that binary's provenance and
how to update it. The steps below are only for building your own copy from
source (e.g. to update it, or to run Moldina outside Docker).

### Linux (Native)

```bash
# Clone Moldina repository
git clone https://github.com/psa-lab/Moldina.git
cd Moldina

# Build Moldina
mkdir -p build/linux/release
cd build/linux/release
cmake ../../..
make

# Moldina executable is at: build/linux/release/Moldina
```

### Windows (WSL Required)

Moldina is Linux-only, so on Windows we use WSL (Windows Subsystem for Linux):

```powershell
# Install WSL Ubuntu (if not already installed)
wsl --install Ubuntu

# In WSL Ubuntu terminal:
git clone https://github.com/psa-lab/Moldina.git
cd Moldina
mkdir -p build/linux/release
cd build/linux/release
cmake ../../..
make
```

## Usage

### Python API

```python
from services.moldina_runner import MoldinaRunner

# Initialize runner (auto-detects Moldina and WSL)
runner = MoldinaRunner()

# Run docking
results = runner.run_moldina_docking(
    receptor_path="protein.pdbqt",
    ligand_paths=["ligand1.pdbqt", "ligand2.pdbqt"],
    output_dir="./docking_results",
    center=(10.5, 20.3, -5.2),  # Search box center
    box_size=(25, 30, 35),      # Search box dimensions
    cpu=1,                       # Number of CPUs
    population=500,              # PSO population size
    exhaustiveness=8,            # Search exhaustiveness
    num_modes=15,                # Max binding modes
    energy_range=100.0           # Energy range (kcal/mol)
)

# Results structure
{
    "status": "ok",
    "receptor": "protein.pdbqt",
    "total": 2,
    "successful": 2,
    "failed": 0,
    "results": [
        {
            "ligand_id": "ligand1",
            "ligand_path": "ligand1.pdbqt",
            "output_path": "docking_results/ligand1_out.pdbqt",
            "log_path": "docking_results/ligand1_out.log",
            "best_affinity": -8.5,  # kcal/mol
            "modes": [
                {
                    "mode": 1,
                    "affinity": -8.5,
                    "rmsd_lb": 0.0,
                    "rmsd_ub": 0.0
                },
                ...
            ]
        },
        ...
    ]
}
```

### MCP Server

```python
import requests

# Run docking via MCP
response = requests.post(
    "http://localhost:8004/mcp",
    json={
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tool_call",
        "params": {
            "name": "run_docking",
            "arguments": {
                "receptor_path": "protein.pdbqt",
                "ligand_paths": ["ligand1.pdbqt", "ligand2.pdbqt"],
                "output_dir": "./results",
                "center": [10.5, 20.3, -5.2],
                "box_size": [25, 30, 35],
                "cpu": 1,
                "population": 500,
                "exhaustiveness": 8,
                "num_modes": 15,
                "energy_range": 100.0
            }
        },
        "id": 1
    }
)

result = response.json()
```

## Configuration Parameters

`run_docking` takes `receptor_path`, `ligand_paths`, `output_dir`, `center`,
`box_size` (required) and `cpu`, `population`, `exhaustiveness`, `num_modes`,
`energy_range`, `parallel`, `max_workers` (optional). For the full parameter
table — types, defaults, ranges, tuning guidance, and the `cpu` vs.
`parallel`/`max_workers` distinction — see
[DOCKING_PARAMETERS.md § Stage 4: Molecular Docking](DOCKING_PARAMETERS.md#stage-4-molecular-docking).

## Search Box Configuration

### Finding the Search Box Center

```python
from rdkit import Chem
from rdkit.Chem import AllChem

# Load protein and ligand
protein = Chem.MolFromPDBFile("protein.pdb")
ligand = Chem.MolFromMolFile("reference_ligand.sdf")

# Get ligand center of mass
conf = ligand.GetConformer()
pos = conf.GetPositions()
center = pos.mean(axis=0)

print(f"Center: {center}")  # [x, y, z]
```

### Determining Box Size

```python
import numpy as np

# Calculate bounding box
pos = conf.GetPositions()
min_coords = pos.min(axis=0)
max_coords = pos.max(axis=0)

# Add buffer (e.g., 10 Angstroms)
buffer = 10.0
box_size = max_coords - min_coords + 2 * buffer

print(f"Box size: {box_size}")
```

## WSL Path Handling

The runner automatically handles Windows ↔ WSL path conversion:

```python
# Windows paths are automatically converted
runner.run_moldina_docking(
    receptor_path=r"C:\Users\you\protein.pdbqt",  # Becomes /mnt/c/Users/you/protein.pdbqt
    ligand_paths=[r"C:\Users\you\ligand.pdbqt"],
    output_dir=r"C:\Users\you\results"
)
```

## Output Files

### Generated Files

For each ligand, Moldina creates:

1. **{ligand}_out.pdbqt** - Docked poses in PDBQT format
2. **{ligand}_out.log** - Docking log with affinities and statistics
3. **moldina_config.txt** - Moldina configuration used
4. **ligands.txt** - List of ligand files processed

### Log File Format

```
Scoring function : vina
Rigid receptor: protein.pdbqt
Grid center: X 10.500 Y 20.300 Z -5.200
Grid size  : X 25 Y 30 Z 35

mode |   affinity | dist from best mode
     | (kcal/mol) | rmsd l.b.| rmsd u.b.
-----+------------+----------+----------
   1       -8.5      0.000      0.000
   2       -8.2      1.234      3.456
   3       -7.9      2.345      4.567
```

## Performance Tuning

### Fast Screening (High Throughput)

```python
results = runner.run_moldina_docking(
    receptor_path=receptor,
    ligand_paths=ligands,
    output_dir=output_dir,
    center=center,
    box_size=box_size,
    cpu=8,
    population=200,        # Smaller population
    exhaustiveness=8,      # Standard exhaustiveness
    num_modes=5,           # Fewer modes
    energy_range=3.0       # Only best modes
)
```

### Accurate Binding (Detailed Study)

```python
results = runner.run_moldina_docking(
    receptor_path=receptor,
    ligand_paths=ligands,
    output_dir=output_dir,
    center=center,
    box_size=box_size,
    cpu=8,
    population=1000,       # Larger population
    exhaustiveness=32,     # High exhaustiveness
    num_modes=20,          # More modes
    energy_range=100.0     # All modes
)
```

### Batch Processing

```python
import glob

# Get all ligands
ligands = glob.glob("ligands/*.pdbqt")

# Process in batches of 100
batch_size = 100
for i in range(0, len(ligands), batch_size):
    batch = ligands[i:i+batch_size]
    
    results = runner.run_moldina_docking(
        receptor_path="protein.pdbqt",
        ligand_paths=batch,
        output_dir=f"./batch_{i//batch_size}",
        center=center,
        box_size=box_size,
        cpu=8
    )
    
    # Process results...
```

## Interpreting Results

### Binding Affinity

- **More negative = stronger binding**
- Typical ranges:
  - Strong binders: < -10 kcal/mol
  - Moderate binders: -10 to -7 kcal/mol
  - Weak binders: -7 to -5 kcal/mol
  - Non-binders: > -5 kcal/mol

### RMSD Values

- **rmsd_lb** (lower bound): RMSD to best mode
- **rmsd_ub** (upper bound): RMSD to best mode (different calculation)
- Low RMSD (~0-2 Å): Similar poses
- High RMSD (>5 Å): Different binding modes

### Ranking Ligands

```python
# Sort by best affinity
sorted_results = sorted(
    results["results"],
    key=lambda x: x["best_affinity"]
)

# Top 10 binders
top_10 = sorted_results[:10]
for i, res in enumerate(top_10, 1):
    print(f"{i}. {res['ligand_id']}: {res['best_affinity']} kcal/mol")
```

## Troubleshooting

### WSL Issues

**Problem**: "WSL not found"
```powershell
# Install WSL
wsl --install Ubuntu

# Verify installation
wsl --list --verbose
```

**Problem**: "Moldina not found"
```bash
# In WSL, check Moldina path
which Moldina

# Or build Moldina
cd ~/Moldina
mkdir -p build/linux/release
cd build/linux/release
cmake ../../..
make
```

### Docking Failures

**Problem**: "Docking failed with error"
- Check PDBQT files are valid
- Verify search box parameters
- Ensure population ≥ 5

**Problem**: "No poses found"
- Increase population size
- Increase exhaustiveness
- Enlarge search box
- Check ligand is dockable (rotatable bonds)

### Performance Issues

**Problem**: Docking very slow
- Reduce population size
- Reduce exhaustiveness
- Use fewer CPUs (paradoxically can help)
- Process ligands in smaller batches

## Best Practices

1. **Use PSO for Important Ligands**
   - Use Moldina for detailed studies
   - Use Vina for high-throughput screening

2. **Validate Search Box**
   - Always verify center and size
   - Include known binding site
   - Add buffer around ligand

3. **Tune Population Size**
   - Start with population = 100 × rotatable_bonds
   - Increase for complex ligands
   - Decrease for simple ligands

4. **Check Multiple Modes**
   - Don't rely only on best mode
   - Review top 3-5 modes
   - Look for consensus binding patterns

5. **Compare with Vina**
   - Run both Vina and Moldina
   - Compare binding affinities
   - Look for agreement in binding modes

## Complete Example

```python
from services.protein_preparer import ProteinPreparer
from services.ligand_preparer import LigandPreparer
from services.moldina_runner import MoldinaRunner

# 1. Prepare protein
protein_prep = ProteinPreparer()
protein_result = protein_prep.prepare_proteins(
    protein_paths=["6lu7.pdb"],
    output_dir="./prepared_proteins",
    remove_waters=True,
    add_hydrogens=True
)
receptor = protein_result["prepared_proteins"][0]["pdbqt_path"]

# 2. Prepare ligands
ligand_prep = LigandPreparer()
ligand_result = ligand_prep.prepare_ligands(
    ligand_paths=["ligand1.sdf", "ligand2.sdf"],
    output_dir="./prepared_ligands"
)
ligands = [r["pdbqt_path"] for r in ligand_result["prepared_ligands"]]

# 3. Run Moldina docking
runner = MoldinaRunner()
docking_results = runner.run_moldina_docking(
    receptor_path=receptor,
    ligand_paths=ligands,
    output_dir="./moldina_results",
    center=(10.5, 20.3, -5.2),
    box_size=(25, 30, 35),
    cpu=8,
    population=500,
    exhaustiveness=16
)

# 4. Analyze results
for result in docking_results["results"]:
    print(f"{result['ligand_id']}: {result['best_affinity']} kcal/mol")
```

## References

- Moldina GitHub: https://github.com/psa-lab/Moldina
- PSO Algorithm: Kennedy & Eberhart (1995)
- AutoDock Vina: Trott & Olson (2010)
- WSL Documentation: https://docs.microsoft.com/en-us/windows/wsl/
