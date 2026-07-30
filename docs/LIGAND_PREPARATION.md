# Ligand Preparation for AutoDock Vina

## Overview

This document describes the ligand preparation process implemented in the Ligand Preparer service, following AutoDock Vina best practices.

## Why Preparation Is Needed

AutoDock Vina requires ligands in PDBQT format, which includes:
- **Explicit hydrogens** at the correct protonation state
- **Partial charges** (typically Gasteiger charges)
- **3D coordinates** for the ligand structure
- **Rotatable bonds** marked for flexibility during docking
- **Proper atom types** for force field calculations

## Preparation Pipeline

### 1. Input Formats Supported
- **SDF** (Structure Data Format) - Most common for small molecules. A single
  SDF file may contain multiple `$$$$`-delimited molecule records — see
  [Multi-Molecule SDF Files](#multi-molecule-sdf-files) below.
- **MOL** (MDL Molfile) - always exactly one molecule
- **MOL2** (Tripos Mol2) - always exactly one molecule

### 2. Processing Steps

**Step 1: Read and Sanitize**
```python
supplier = Chem.SDMolSupplier(file_path, removeHs=False)
mols = [mol for mol in supplier if mol is not None]
```
- `SDMolSupplier` parses every record in the file; invalid records are
  skipped (yielded as `None`) rather than raising
- Validates chemical structure, checks valence and aromaticity
- Corrects common issues

**Step 2: Add Hydrogens**
```python
mol = Chem.AddHs(mol)
```
- Adds explicit hydrogen atoms
- pH consideration: Current implementation uses standard protonation
- For pH-specific protonation, consider tools like Dimorphite-DL

**Step 3: Generate 3D Coordinates**
```python
AllChem.EmbedMolecule(mol, AllChem.ETKDGv3())
```
- Uses ETKDG (Experimental Torsion Knowledge Distance Geometry) algorithm
- Generates realistic conformations
- Only if input lacks 3D coordinates

**Step 4: Optimize Geometry**
```python
AllChem.MMFFOptimizeMolecule(mol, maxIters=200)
```
- Uses MMFF94 force field for energy minimization
- Falls back to UFF if MMFF unavailable
- Produces stable, low-energy conformation

**Step 5: Assign Partial Charges**
```python
AllChem.ComputeGasteigerCharges(mol)
```
- Gasteiger-Marsili partial charges
- Standard for AutoDock Vina
- Required for electrostatic calculations

**Step 6: Convert to PDBQT**

Three methods available (in order of preference):

**Method 1: Meeko (Recommended)**
```python
from meeko import MoleculePreparation, PDBQTWriterLegacy
preparator = MoleculePreparation()
mol_setups = preparator.prepare(mol)
pdbqt_string = PDBQTWriterLegacy.write_string(setup)
```
- Modern replacement for MGLTools
- Automatically detects rotatable bonds
- Handles aromatic systems correctly
- Install: `conda install -c conda-forge meeko`

**Method 2: OpenBabel**
```bash
obabel input.pdb -O output.pdbqt -xh -p 7.4
```
- Command-line tool
- Widely available
- Good for batch processing
- Install: `conda install -c conda-forge openbabel`

**Method 3: Manual (Fallback)**
- Basic conversion from PDB
- Adds TORSDOF line
- Limited functionality
- Only used when other methods unavailable

## PDBQT Format Details

### What is PDBQT?

PDBQT is an extended PDB format required by AutoDock Vina. The name stands for:
- **PDB** - Protein Data Bank format (coordinates)
- **Q** - Partial atomic charges
- **T** - Atom types (AutoDock-specific)

## Multi-Molecule SDF Files

A single `.sdf` file may contain more than one molecule, each separated by a
`$$$$` line. `prepare_ligands_from_file()` (used internally by the batch
`prepare_ligands()` MCP tool) prepares **every valid molecule** in the file,
not just the first one:

- **Single-molecule SDF** (the common case): behaves exactly as before — the
  output is named from the input filename, e.g. `aspirin.sdf` →
  `aspirin.pdbqt`.
- **Multi-molecule SDF**: each molecule gets a unique id
  `{filename_stem}_{index}` (1-based), so a 10-compound `screen.sdf` produces
  `screen_1.pdbqt` … `screen_10.pdbqt`. This guarantees output files never
  overwrite each other.
- Each prepared-ligand result also includes `source_file` (the original SDF
  path), `molecule_index`/`molecule_count` (position within the file), and
  `sdf_title` (the molecule's title-line name from the SDF, if present —
  `None` if the record's title line was blank).
- A molecule that fails during preparation (e.g. 3D embedding or PDBQT
  conversion fails) is recorded as an error and does **not** stop the rest of
  the file from being processed — you get every molecule that succeeded, plus
  an `errors` entry naming which molecule (e.g. `"molecule 4/10"`) failed and
  why.
- A record RDKit cannot parse at all (malformed connection table, etc.) is
  silently skipped during reading, the same way a single bad molecule in an
  otherwise-fine file has always been tolerated — it simply isn't counted
  among the file's molecules.

The batch `prepare_ligands()` response distinguishes input files from output
ligands: `total` is the number of input file paths given, while
`total_ligands` is the number of ligands actually produced (which can exceed
`total` when a multi-molecule SDF was expanded).

### Why Two Files? PDB vs PDBQT

The ligand preparation process creates **two output files**:

#### 1. `ligand_prepared.pdb` - Intermediate File
- **Format:** Standard PDB format
- **Purpose:** Intermediate file with prepared 3D structure
- **Contains:**
  - 3D coordinates (optimized)
  - All hydrogens added
  - Standard PDB atom types
- **Used for:**
  - Verification and inspection
  - Visualization in molecular viewers
  - Debugging preparation issues
- **Cannot be used for:** AutoDock Vina docking (missing charges and atom types)

#### 2. `ligand.pdbqt` - Final Docking File ✓
- **Format:** PDBQT (extended PDB)
- **Purpose:** Final file ready for AutoDock Vina
- **Contains everything PDB has PLUS:**
  - **Partial charges** (Gasteiger charges)
  - **AutoDock atom types** (HD, OA, NA, C, etc.)
  - **Rotatable bonds** marked with BRANCH/ENDBRANCH
  - **TORSDOF** line (torsional degrees of freedom)
- **Used for:** AutoDock Vina docking - **THIS IS WHAT YOU NEED!**

**Important:** Always use the `.pdbqt` file for docking, not the `.pdb` file!

### Structure

**Example PDB file (intermediate):**
```
ATOM      1  C1  LIG A   1      10.123  20.456  15.789  1.00  0.00           C
ATOM      2  C2  LIG A   1      11.234  21.567  16.890  1.00  0.00           C
...
END
```

**Example PDBQT file (final for docking):**
```
REMARK  Name = ligand
REMARK  8 active torsions:
ROOT
ATOM      1  C1  LIG A   1      10.123  20.456  15.789  1.00  0.00    -0.123 C 
ATOM      2  C2  LIG A   1      11.234  21.567  16.890  1.00  0.00     0.045 C 
                                                                    ^^^^^^ ^^
                                                                    charge  atom type
BRANCH   1   3
ATOM      3  O1  LIG A   1       9.012  19.345  14.678  1.00  0.00    -0.350 OA
ENDBRANCH  1   3
...
TORSDOF 8
```

**Key differences:**
1. PDBQT has partial charges in column 70-76
2. PDBQT has AutoDock atom types (last column): C, OA, HD, NA, etc.
3. PDBQT has BRANCH/ENDBRANCH for rotatable bonds
4. PDBQT has TORSDOF at the end

### Key Components
- **ATOM/HETATM**: Atom records with coordinates
- **Charge column**: Partial charges (position 70-76)
- **Atom type**: AutoDock atom type (position 77-78)
- **TORSDOF**: Number of rotatable bonds (torsional degrees of freedom)

### Rotatable Bonds
- Single bonds (not in rings)
- Not terminal methyl groups
- Not amide bonds (typically rigid)
- Marked for flexibility during docking

## Best Practices

### 1. Protonation State
- Use physiological pH (7.4) for most cases
- Consider target protein environment
- For specific pH, use specialized tools:
  - Dimorphite-DL
  - OpenBabel's -p flag
  - Epik (Schrödinger)

### 2. Tautomers
- Current implementation uses single tautomer
- For important cases, prepare multiple tautomers
- Tools: RDKit, OpenEye QUACPAC

### 3. Stereochemistry
- Preserve existing stereochemistry
- For unknown stereocenters, consider all possibilities
- Mark stereochemistry explicitly in input files

### 4. Charge State
- Most ligands should be neutral or zwitterionic
- Avoid highly charged species
- Check formal charges with RDKit

### 5. Quality Checks
- Verify 3D coordinates generated
- Check for clashes (atoms too close)
- Ensure reasonable bond lengths/angles
- Validate PDBQT syntax

## Common Issues and Solutions

### Issue 1: "Failed to embed molecule"
**Cause:** Molecule structure invalid or too strained
**Solution:**
- Check input structure validity
- Try simpler embedding method
- Add constraints for known geometry

### Issue 2: "No rotatable bonds detected"
**Cause:** Rigid molecule or wrong detection
**Solution:**
- Verify molecule is not completely rigid (e.g., benzene)
- Check bond orders are correct
- Use visual inspection (PyMOL, Chimera)

### Issue 3: "Gasteiger charges failed"
**Cause:** Unusual atoms or structure
**Solution:**
- Pre-sanitize molecule
- Use alternative charge methods (AM1-BCC, MMFF94)
- Manual charge assignment

### Issue 4: "PDBQT missing hydrogen atoms"
**Cause:** Hydrogens removed during processing
**Solution:**
- Use `removeHs=False` when reading
- Add hydrogens with `addCoords=True`
- Verify hydrogen count before PDBQT conversion

## Validation Checklist

Before using prepared ligands:

- [ ] File exists and is valid PDBQT format
- [ ] All atoms have coordinates
- [ ] Partial charges are present
- [ ] TORSDOF line is present
- [ ] Number of rotatable bonds is reasonable
- [ ] Hydrogens are explicit
- [ ] No major steric clashes
- [ ] Molecular weight is reasonable (<1000 Da typically)

## Example: Preparing Aspirin

```python
from services.ligand_preparer import LigandPreparer

preparer = LigandPreparer()

# Input: aspirin.sdf
result = preparer.prepare_ligand(
    "aspirin.sdf",
    output_dir="./prepared",
    ph=7.4,
    add_hydrogens=True,
    optimize=True
)

print(f"Output: {result['pdbqt_path']}")
print(f"Rotatable bonds: {result['rotatable_bonds']}")
# Expected: 3 rotatable bonds (ester linkage + carboxyl)
```

## Example: Preparing a Multi-Molecule SDF (Compound Screen)

```python
from services.ligand_preparer import LigandPreparer

preparer = LigandPreparer()

# Input: screen.sdf, containing 10 compounds separated by $$$$
results, errors = preparer.prepare_ligands_from_file(
    "screen.sdf",
    output_dir="./prepared",
    ph=7.4,
)

print(f"Prepared {len(results)} of 10 molecules")
for r in results:
    print(f"  {r['id']}: {r['pdbqt_path']} ({r['rotatable_bonds']} rotatable bonds)")
for e in errors:
    print(f"  FAILED {e['ligand']}: {e['error']}")
# Output ids: screen_1, screen_2, ..., screen_10
```

## References

1. [AutoDock Vina Documentation](https://autodock-vina.readthedocs.io/)
2. [Meeko GitHub](https://github.com/forlilab/Meeko)
3. [RDKit Documentation](https://www.rdkit.org/docs/)
4. Trott, O., & Olson, A. J. (2010). AutoDock Vina: improving the speed and accuracy of docking. *Journal of computational chemistry*, 31(2), 455-461.
5. Forli, S., et al. (2016). Computational protein–ligand docking and virtual drug screening with the AutoDock suite. *Nature protocols*, 11(5), 905-919.

## Tools Required

### Essential
- **RDKit**: Molecule processing and preparation
- **Python 3.9+**: Runtime environment

### Recommended
- **Meeko**: Modern PDBQT conversion (replaces MGLTools)
- **OpenBabel**: Alternative conversion tool

### Optional
- **Dimorphite-DL**: pH-specific protonation
- **PyMOL**: Visual validation
- **Open Babel GUI**: Interactive molecule editing

## Installation Commands

### Option 1: Using pip

```bash
# Install chemistry packages
pip install rdkit meeko

# For OpenBabel (conda recommended):
conda install -c conda-forge openbabel

# Install other dependencies
pip install -r requirements.txt
```

### Option 2: Using conda

```bash
# Create conda environment
conda create -n docking python=3.9
conda activate docking

# Install chemistry packages
conda install -c conda-forge rdkit openbabel meeko

# Install other dependencies
pip install -r requirements.txt
```
