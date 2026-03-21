# Protein Preparation Documentation

## Overview

The Protein Preparer service prepares protein receptor structures for molecular docking with AutoDock Vina. It handles cleaning, protonation, charge assignment, and conversion to PDBQT format.

## Workflow Overview

The service performs several key steps to prepare proteins:

1. **Read Protein Structure**: Loads protein from PDB file format
2. **Clean Structure**: Removes water molecules and unwanted heteroatoms
3. **Add Hydrogens**: Adds missing hydrogen atoms at specified pH
4. **Assign Charges**: Calculates Gasteiger partial atomic charges
5. **Convert to PDBQT**: Creates AutoDock Vina-compatible PDBQT format
6. **Batch Processing**: Handles multiple proteins in one operation

## Why Prepare Proteins?

Raw protein structures from PDB often contain:
- Water molecules that may interfere with docking
- Co-crystallized ligands that should be removed
- Missing hydrogen atoms (X-ray structures don't show H atoms)
- Incomplete charge information

AutoDock Vina requires:
- Clean protein structure with only receptor atoms
- All hydrogen atoms present
- Partial charges assigned
- PDBQT file format with atom types and charges

## Input Requirements

### Supported Formats
- **PDB (Protein Data Bank)**: `.pdb` files
  - Standard format from RCSB PDB database
  - Contains 3D coordinates of protein atoms
  - May include waters, ligands, and other heteroatoms

### File Types
**Proteins** (receptors):
- Biomolecules that bind to small molecules
- Typically 100-10,000+ atoms
- Example: Kinases, proteases, GPCRs
- Source: RCSB PDB (e.g., 1CRN, 3HB5, 4EK3)

**Not Ligands** (use Ligand Preparer for those):
- Small molecules that bind to proteins
- Typically 10-100 atoms
- Example: Drugs, metabolites, inhibitors
- Source: PubChem, ZINC, ChEMBL

## Output Files

### 1. Intermediate PDB File
- **Extension**: `.pdb`
- **Purpose**: Cleaned protein with hydrogens added
- **Contains**: ATOM/HETATM records with 3D coordinates
- **Use**: Verification, alternative workflows

### 2. Final PDBQT File
- **Extension**: `.pdbqt`
- **Purpose**: Docking input file for AutoDock Vina
- **Contains**: ATOM records + Charges (Q) + atom Types (T)
- **Use**: Direct input to `vina` command

### What is PDBQT?

**PDBQT** = **PDB** + **Q** (Charges) + **T** (atom Types)

Example PDBQT line:
```
ATOM      1  N   ALA A   1      10.123  20.456  30.789  1.00  0.00    -0.416 N
```

Components:
- Standard PDB columns (atom name, residue, coordinates)
- Charge column: `-0.416` (Gasteiger partial charge)
- Atom type: `N` (AutoDock atom type for docking calculations)

### Why Two Files?

| File Type | Purpose | When to Use |
|-----------|---------|-------------|
| **PDB** | Human-readable intermediate | Inspection, verification, PyMOL visualization |
| **PDBQT** | Machine-readable for docking | Input to AutoDock Vina `--receptor` flag |

**Bottom line**: Use the **PDBQT file** for docking. The PDB file is just a checkpoint.

## Usage

### Basic Example

```python
from services.protein_preparer import ProteinPreparer

# Initialize preparer
preparer = ProteinPreparer(cache_dir="./cache")

# Prepare single protein
result = preparer.prepare_protein_file(
    protein_path="1crn.pdb",
    output_path="1crn_prepared.pdbqt",
    ph=7.4,
    add_hydrogens=True,
    remove_waters=True,
    remove_heteroatoms=False
)

print(f"Prepared: {result['pdbqt_path']}")
print(f"Atoms: {result['initial_atoms']} → {result['final_atoms']}")
print(f"Method: {result['conversion_method']}")
```

### Batch Processing

```python
# Prepare multiple proteins
result = preparer.prepare_proteins(
    protein_paths=["1crn.pdb", "3hb5.pdb", "4ek3.pdb"],
    output_dir="./prepared_proteins",
    ph=7.4,
    add_hydrogens=True,
    remove_waters=True
)

print(f"Successful: {result['successful']}/{result['total']}")
for protein in result['prepared_proteins']:
    print(f"  {protein['id']}: {protein['pdbqt_path']}")
```

### Keeping Specific Residues

```python
# Keep important cofactors/ligands while removing waters
result = preparer.prepare_protein_file(
    protein_path="protein_with_heme.pdb",
    output_path="prepared.pdbqt",
    remove_waters=True,
    remove_heteroatoms=True,
    keep_residues=['HEM', 'NAD', 'FAD']  # Keep heme and cofactors
)
```

## Parameters

### prepare_protein_file()

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `protein_path` | str | Required | Path to input PDB file |
| `output_path` | str | Required | Path for output PDBQT file |
| `ph` | float | 7.4 | pH for hydrogen addition |
| `add_hydrogens` | bool | True | Add missing hydrogen atoms |
| `remove_waters` | bool | True | Remove water molecules |
| `remove_heteroatoms` | bool | False | Remove all non-protein atoms |
| `keep_residues` | list | None | Residue names to keep even if heteroatoms |

### prepare_proteins()

Same parameters as above, plus:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `protein_paths` | list | Required | List of input PDB file paths |
| `output_dir` | str | Required | Directory for output files |

## Return Format

### Single Protein Result

```json
{
    "id": "1crn",
    "input_path": "/path/to/1crn.pdb",
    "pdb_path": "/path/to/1crn.pdb",
    "pdbqt_path": "/path/to/1crn.pdbqt",
    "initial_atoms": 327,
    "final_atoms": 642,
    "atoms_removed": 45,
    "conversion_method": "manual_rigid",
    "checksum": "a1b2c3d4e5f6..."
}
```

### Batch Result

```json
{
    "status": "ok",
    "total": 3,
    "successful": 3,
    "failed": 0,
    "prepared_proteins": [
        { "id": "1crn", ... },
        { "id": "3hb5", ... },
        { "id": "4ek3", ... }
    ],
    "errors": []
}
```

## Conversion Methods

For protein receptors, the service uses a **manual rigid receptor** conversion method:

### Manual Rigid Receptor (Default)
- **Purpose**: Creates simple, rigid receptor PDBQT files
- **Why**: Proteins/receptors are treated as rigid in most docking scenarios
- **Method**: Parses PDB ATOM lines and converts to PDBQT format
- **Features**:
  - Maps elements to AutoDock atom types (H→HD, C→C, N→NA, O→OA, S→SA, etc.)
  - Assigns zero charges (rigid receptors don't need detailed charge distributions)
  - Creates simple ATOM-only PDBQT (no ROOT/ENDROOT/TORSDOF markers)
- **Compatibility**: Works with AutoDock Vina, AutoDock, and Moldina
- **Always available**: No extra dependencies beyond RDKit

### Alternative: OpenBabel (Not Recommended)
- **Available but not used**: OpenBabel can convert PDB to PDBQT
- **Issues**: Creates non-standard PDBQT with UNL residues and formatting problems
- **Status**: Code exists as fallback but typically redirects to manual method

**Note**: Meeko is excellent for **ligand preparation** (flexible molecules with rotatable bonds) but is designed for ligands, not rigid receptors. For ligands, use the Ligand Preparer service which uses Meeko.

## MCP Server Usage

Start the MCP server:

```bash
python services/protein_preparer/server.py --port 8003
```

### MCP Tool: prepare_proteins

**Parameters:**
```json
{
    "protein_paths": ["1crn.pdb", "3hb5.pdb"],
    "output_dir": "./prepared",
    "ph": 7.4,
    "add_hydrogens": true,
    "remove_waters": true,
    "remove_heteroatoms": false,
    "keep_residues": ["HEM", "NAD"]
}
```

**Response:**
```json
{
    "status": "ok",
    "total": 2,
    "successful": 2,
    "failed": 0,
    "prepared_proteins": [...],
    "errors": []
}
```

## Common Workflows

### 1. Basic Receptor Preparation
```python
# Standard preparation for docking
result = preparer.prepare_protein_file(
    protein_path="receptor.pdb",
    output_path="receptor.pdbqt",
    remove_waters=True,
    remove_heteroatoms=False  # Keep cofactors
)
```

### 2. Clean Protein Only
```python
# Remove everything except protein
result = preparer.prepare_protein_file(
    protein_path="complex.pdb",
    output_path="clean_protein.pdbqt",
    remove_waters=True,
    remove_heteroatoms=True  # Remove all non-protein
)
```

### 3. Protein with Cofactor
```python
# Keep essential cofactors
result = preparer.prepare_protein_file(
    protein_path="enzyme.pdb",
    output_path="enzyme_prepared.pdbqt",
    remove_waters=True,
    remove_heteroatoms=True,
    keep_residues=['HEM', 'NAD', 'FAD', 'MG']
)
```

### 4. High-Throughput Preparation
```python
import glob

# Find all PDB files
pdb_files = glob.glob("./downloaded/*.pdb")

# Prepare all at once
result = preparer.prepare_proteins(
    protein_paths=pdb_files,
    output_dir="./prepared_proteins",
    remove_waters=True
)

print(f"Prepared {result['successful']} proteins")
```

## Troubleshooting

### Issue: "Unsupported file format"
**Problem**: File is not a PDB file
**Solution**: Only PDB format supported for proteins. Download from RCSB PDB or convert to PDB format.

### Issue: "No molecule found"
**Problem**: PDB file is empty or invalid
**Solution**: Verify PDB file has ATOM or HETATM records. Check file wasn't truncated during download.

### Issue: "All atoms removed"
**Problem**: Protein only had water/heteroatoms that were removed
**Solution**: Check `remove_heteroatoms` setting. Verify input is actually a protein, not a ligand.

### Issue: "Hydrogens not added"
**Problem**: `add_hydrogens=False` was set
**Solution**: Set `add_hydrogens=True` (default). AutoDock Vina requires all hydrogens.

### Issue: Conversion method "manual_rigid" used
**Info**: This is the default and recommended method for rigid receptors
**No action needed**: The manual rigid converter creates clean, compatible PDBQT files

## Best Practices

1. **Always remove waters** unless specifically needed
2. **Keep cofactors** that are part of binding site
3. **Use pH 7.4** for physiological conditions
4. **Verify output** visually in PyMOL before docking
5. **Use manual_rigid** method for receptors (automatic default)
6. **Batch process** multiple proteins for efficiency

## Technical Details

### Hydrogen Addition
- Uses RDKit's `AddHs()` function
- Adds hydrogens at specified pH (default 7.4)
- Note: pH-specific protonation is approximate

### Charge Assignment (Rigid Receptors)
- Zero charges assigned to all atoms
- Rigid receptors don't require detailed charge distributions
- Docking calculations use receptor as static grid

### Atom Type Assignment
- AutoDock atom type mapping
- Based on element: H→HD, C→C, N→NA, O→OA, S→SA, etc.
- Necessary for grid calculations and force field

### Water Removal
- Identifies HOH residues
- Removes all water molecules by default
- Can be kept if needed for binding site

### Heteroatom Handling
- Heteroatoms = non-protein atoms (ligands, ions, etc.)
- `keep_residues` allows selective retention
- Common kept residues: HEM, NAD, FAD, MG, ZN

## Dependencies

Required:
- `rdkit` (2023.9.1+) - Core chemistry operations for structure manipulation

Optional:
- `openbabel-wheel` (3.1.1+) - Alternative conversion (not recommended for rigid receptors)

**Note**: For ligand preparation (flexible molecules with rotatable bonds), use the Ligand Preparer service which requires `meeko`.

Install all:
```bash
pip install rdkit openbabel-wheel
```

## See Also

- [Ligand Preparation](LIGAND_PREPARATION.md) - Prepare small molecules (uses Meeko)
- [API Documentation](API.md) - Complete API reference
- [Usage Guide](../USAGE_GUIDE.md) - End-to-end examples
- [Quick Start](../QUICK_START.md) - Getting started guide
