# Quick Start: Prepare Ligands for Docking

## ❌ WRONG: You tried this

```python
# This FAILS because 1CRN.pdb is a PROTEIN file, not a ligand!
response = requests.post("http://localhost:8002/mcp", json={
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tool_call",
    "params": {
        "name": "prepare_ligands",
        "arguments": {
            "ligand_paths": ["./test_download/1CRN.pdb"],  # ❌ This is a protein!
            "output_dir": "./data/prepared",
            "ph": 7.4
        }
    }
})
# Result: {'error': 'Unsupported file format: .pdb'}
```

**Why it failed:**
- `1CRN.pdb` is a **PROTEIN** structure (crambin, 46 amino acids)
- The Ligand Preparer expects **SMALL MOLECULES** in SDF/MOL/MOL2 format
- PDB format is for proteins/receptors, NOT ligands

---

## ✅ CORRECT: Do this instead

### Step 1: Get a ligand file (SDF format)

**Option A: Download a ligand from RCSB**

```python
import requests

# Start the RCSB Downloader server first!
# Terminal: python -m services.rcsb_downloader.server

# Download ATP ligand as SDF
response = requests.post("http://localhost:8001/mcp", json={
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tool_call",
    "params": {
        "name": "download_structures",
        "arguments": {
            "protein_id": "",  # Leave empty - we only want ligand
            "ligand_ids": ["ATP"],  # ATP = Adenosine triphosphate
            "save_dir": "./data/ligands"
        }
    }
})

result = response.json()
print(result)
# This creates: ./data/ligands/ATP.sdf ✓
```

**Option B: Download from PubChem**
1. Go to https://pubchem.ncbi.nlm.nih.gov/
2. Search for "aspirin" (or your compound)
3. Click "3D Conformer"
4. Download as "SDF" format
5. Save to `./data/ligands/aspirin.sdf`

### Step 2: Prepare the ligand for docking

```python
import requests

# Make sure Ligand Preparer server is running!
# Terminal: python -m services.ligand_preparer.server

# Now prepare the SDF file for AutoDock Vina
response = requests.post("http://localhost:8002/mcp", json={
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tool_call",
    "params": {
        "name": "prepare_ligands",
        "arguments": {
            "ligand_paths": ["./data/ligands/ATP.sdf"],  # ✓ SDF file!
            "output_dir": "./data/prepared",
            "ph": 7.4
        }
    }
})

result = response.json()
print(result)
```

**Expected output:**
```json
{
  "id": "1",
  "jsonrpc": "2.0",
  "result": {
    "status": "success",
    "ligands": [
      {
        "ligand_name": "ATP",
        "input_file": "./data/ligands/ATP.sdf",
        "output_file": "./data/prepared/ATP.pdbqt",  // ✓ Ready for docking!
        "status": "success",
        "num_atoms": 31,
        "rotatable_bonds": 6,
        "method": "meeko"
      }
    ],
    "summary": {
      "total": 1,
      "successful": 1,
      "failed": 0
    }
  }
}
```

---

## Complete Working Example

Save this as `test_ligand_prep.py`:

```python
#!/usr/bin/env python3
"""
Complete example: Download and prepare a ligand for docking
"""
import requests
import json

def download_ligand(ligand_id="ATP"):
    """Download a ligand from RCSB as SDF."""
    print(f"1. Downloading {ligand_id} ligand...")
    
    response = requests.post("http://localhost:8001/mcp", json={
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tool_call",
        "params": {
            "name": "download_structures",
            "arguments": {
                "protein_id": "",
                "ligand_ids": [ligand_id],
                "save_dir": "./data/ligands"
            }
        }
    })
    
    result = response.json()
    if "result" in result and result["result"].get("ligand_files"):
        sdf_file = result["result"]["ligand_files"][0]
        print(f"   ✓ Downloaded: {sdf_file}")
        return sdf_file
    else:
        print(f"   ✗ Failed: {result}")
        return None

def prepare_ligand(sdf_file):
    """Prepare ligand for AutoDock Vina docking."""
    print(f"\n2. Preparing {sdf_file} for docking...")
    
    response = requests.post("http://localhost:8002/mcp", json={
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tool_call",
        "params": {
            "name": "prepare_ligands",
            "arguments": {
                "ligand_paths": [sdf_file],
                "output_dir": "./data/prepared",
                "ph": 7.4,
                "add_hydrogens": True,
                "generate_3d": True,
                "optimize": True
            }
        }
    })
    
    result = response.json()
    if "result" in result:
        lig = result["result"]["ligands"][0]
        if lig["status"] == "success":
            print(f"   ✓ Prepared: {lig['output_file']}")
            print(f"   ✓ Atoms: {lig['num_atoms']}")
            print(f"   ✓ Rotatable bonds: {lig['rotatable_bonds']}")
            print(f"   ✓ Ready for AutoDock Vina!")
            return lig["output_file"]
        else:
            print(f"   ✗ Failed: {lig.get('error')}")
    else:
        print(f"   ✗ Error: {result.get('error', {}).get('message')}")
    
    return None

if __name__ == "__main__":
    print("=" * 60)
    print("LIGAND PREPARATION WORKFLOW")
    print("=" * 60)
    print("\nMake sure servers are running:")
    print("  Terminal 1: python -m services.rcsb_downloader.server")
    print("  Terminal 2: python -m services.ligand_preparer.server")
    print()
    
    try:
        # Download ligand
        sdf_file = download_ligand("ATP")
        
        if sdf_file:
            # Prepare for docking
            pdbqt_file = prepare_ligand(sdf_file)
            
            if pdbqt_file:
                print("\n" + "=" * 60)
                print("✓ SUCCESS! Your ligand is ready for docking")
                print("=" * 60)
                print(f"\nLigand PDBQT: {pdbqt_file}")
                print("\nNext steps:")
                print("  1. Prepare your protein receptor")
                print("  2. Define docking grid/box")
                print("  3. Run AutoDock Vina")
                
    except requests.exceptions.ConnectionError as e:
        print("\n✗ Connection Error!")
        print("   Make sure servers are running:")
        print("   - http://localhost:8001 (RCSB Downloader)")
        print("   - http://localhost:8002 (Ligand Preparer)")
```

**Run it:**
```bash
python test_ligand_prep.py
```

---

## Understanding File Types

| Format | Used For | Example | Preparer Accepts? |
|--------|----------|---------|-------------------|
| `.pdb` | **Proteins** (receptors) | `1CRN.pdb`, `protein.pdb` | ❌ NO |
| `.sdf` | **Ligands** (small molecules) | `ATP.sdf`, `aspirin.sdf` | ✅ YES |
| `.mol` | **Ligands** (small molecules) | `drug.mol` | ✅ YES |
| `.mol2` | **Ligands** (small molecules) | `ligand.mol2` | ✅ YES |
| `.pdbqt` | **Output** (docking-ready) | `ATP.pdbqt` | Output only |

**Key Point:**
- **Proteins** (receptors): Large, thousands of atoms → `.pdb` format
- **Ligands** (small molecules): Small, tens-hundreds of atoms → `.sdf`/`.mol` format

---

## Common Ligands for Testing

Try these ligands (all available from RCSB):

```python
# Energy-related molecules
ligand_ids = ["ATP", "ADP", "NAD", "FAD"]

# Amino acids / cofactors
ligand_ids = ["HEM", "CLA", "FMN", "COA"]

# Drug molecules from complexes
# (Need to specify protein_id too)
ligand_ids = ["MK1"]  # HIV protease inhibitor from 1HSG
protein_id = "1HSG"
```

---

## Troubleshooting

### Error: "Unsupported file format: .pdb"
**Cause:** You're trying to prepare a protein file  
**Fix:** Use SDF/MOL/MOL2 format for ligands

### Error: "Connection refused"
**Cause:** Server not running  
**Fix:** Start the server: `python -m services.ligand_preparer.server`

### Error: "RDKit is required but not installed"
**Cause:** RDKit not installed  
**Fix:** `pip install rdkit`

### Error: "Failed to read molecule"
**Cause:** Invalid or corrupted ligand file  
**Fix:** Download from a reliable source (RCSB, PubChem)

---

## More Examples

See the complete example file:
```bash
python example_prepare_ligand.py
```

This runs 4 different examples showing various ways to download and prepare ligands.

---

## Need Help?

- **Full guide:** See `USAGE_GUIDE.md`
- **API docs:** See `docs/API.md`  
- **Theory:** See `docs/LIGAND_PREPARATION.md`
- **Installation:** See `docs/INSTALLATION.md`
