"""
Example: How to prepare ligands for docking

This example shows how to:
1. Download a ligand from PubChem or RCSB
2. Prepare it for AutoDock Vina docking
"""
import json
import requests

from common.mcp_client import call_mcp_tool

# Server URLs
RCSB_SERVER = "http://localhost:8001/mcp"
LIGAND_SERVER = "http://localhost:8002/mcp"

def call_mcp(server_url, tool_name, arguments):
    """Helper function to call an MCP tool."""
    try:
        result = call_mcp_tool(server_url, tool_name, arguments)
    except Exception as exc:
        print(f"Error: {exc}")
        return None
    
    return result


def example_1_download_and_prepare_ligand():
    """
    Example 1: Download ATP ligand from RCSB and prepare it for docking
    
    ATP is a common ligand found in many protein structures.
    """
    print("=" * 60)
    print("Example 1: Download and Prepare ATP Ligand")
    print("=" * 60)
    
    # Step 1: Download ATP ligand from RCSB
    print("\n1. Downloading ATP ligand from RCSB...")
    result = call_mcp(RCSB_SERVER, "download_structures", {
        "protein_id": "",  # Empty - we only want the ligand
        "ligand_ids": ["ATP"],
        "save_dir": "./data/example_ligands"
    })
    
    if result and result.get("status") == "success":
        ligand_file = result["ligand_files"][0]
        print(f"   ✓ Downloaded: {ligand_file}")
    else:
        print("   ✗ Failed to download ligand")
        return
    
    # Step 2: Prepare ligand for docking
    print("\n2. Preparing ligand for docking...")
    result = call_mcp(LIGAND_SERVER, "prepare_ligands", {
        "ligand_paths": [ligand_file],
        "output_dir": "./data/prepared_ligands",
        "ph": 7.4,
        "add_hydrogens": True,
        "generate_3d": True,
        "optimize": True
    })
    
    if result and result.get("status") == "success":
        prepared = result["ligands"][0]
        print(f"   ✓ Prepared PDBQT: {prepared['output_file']}")
        print(f"   ✓ Number of atoms: {prepared['num_atoms']}")
        print(f"   ✓ Rotatable bonds: {prepared['rotatable_bonds']}")
        print(f"   ✓ Method used: {prepared['method']}")
        print(f"   ✓ Processing time: {prepared['processing_time']:.2f}s")
    else:
        print("   ✗ Failed to prepare ligand")
        if result:
            print(f"   Error: {result.get('errors', [])}")


def example_2_prepare_local_sdf():
    """
    Example 2: Prepare a ligand from a local SDF file
    
    This assumes you already have an SDF file (e.g., downloaded from PubChem).
    """
    print("\n" + "=" * 60)
    print("Example 2: Prepare Local SDF File")
    print("=" * 60)
    
    # Example SDF files you might have
    sdf_files = [
        "./data/example_ligands/ATP.sdf",
        # Add more ligand files here
    ]
    
    print(f"\n1. Preparing {len(sdf_files)} ligand(s)...")
    result = call_mcp(LIGAND_SERVER, "prepare_ligands", {
        "ligand_paths": sdf_files,
        "output_dir": "./data/prepared_ligands",
        "ph": 7.4
    })
    
    if result:
        print(f"\n   Summary:")
        print(f"   - Total: {result['summary']['total']}")
        print(f"   - Successful: {result['summary']['successful']}")
        print(f"   - Failed: {result['summary']['failed']}")
        
        if result['summary']['successful'] > 0:
            print(f"\n   Prepared ligands:")
            for lig in result['ligands']:
                if lig['status'] == 'success':
                    print(f"   ✓ {lig['ligand_name']}: {lig['output_file']}")
                    print(f"     - Rotatable bonds: {lig['rotatable_bonds']}")


def example_3_download_ligand_from_complex():
    """
    Example 3: Download a ligand from a protein-ligand complex
    
    For example, 1HSG is HIV-1 protease with an inhibitor (MK1).
    """
    print("\n" + "=" * 60)
    print("Example 3: Download Ligand from Protein-Ligand Complex")
    print("=" * 60)
    
    # 1HSG contains HIV protease with MK1 inhibitor
    print("\n1. Downloading MK1 ligand from 1HSG structure...")
    result = call_mcp(RCSB_SERVER, "download_structures", {
        "protein_id": "1HSG",  # HIV-1 protease
        "ligand_ids": ["MK1"],  # The inhibitor
        "save_dir": "./data/example_complex"
    })
    
    if result and result.get("status") == "success":
        print(f"   ✓ Protein: {result['protein_file']}")
        if result.get('ligand_files'):
            print(f"   ✓ Ligand: {result['ligand_files'][0]}")
            
            # Now prepare the ligand
            print("\n2. Preparing MK1 ligand...")
            prep_result = call_mcp(LIGAND_SERVER, "prepare_ligands", {
                "ligand_paths": result['ligand_files'],
                "output_dir": "./data/prepared_ligands",
                "ph": 7.4
            })
            
            if prep_result and prep_result.get("status") == "success":
                prepared = prep_result["ligands"][0]
                print(f"   ✓ Ready for docking: {prepared['output_file']}")
        else:
            print("   ✗ No ligand files downloaded")


def example_4_batch_preparation():
    """
    Example 4: Prepare multiple ligands at once
    
    Useful for virtual screening with multiple compounds.
    """
    print("\n" + "=" * 60)
    print("Example 4: Batch Ligand Preparation")
    print("=" * 60)
    
    # Download multiple common ligands
    ligands_to_download = ["ATP", "ADP", "NAD", "FAD"]
    
    print(f"\n1. Downloading {len(ligands_to_download)} ligands...")
    downloaded_files = []
    
    for ligand_id in ligands_to_download:
        result = call_mcp(RCSB_SERVER, "download_structures", {
            "protein_id": "",
            "ligand_ids": [ligand_id],
            "save_dir": "./data/batch_ligands"
        })
        
        if result and result.get("ligand_files"):
            downloaded_files.extend(result["ligand_files"])
            print(f"   ✓ {ligand_id}")
    
    if downloaded_files:
        print(f"\n2. Preparing {len(downloaded_files)} ligands for docking...")
        result = call_mcp(LIGAND_SERVER, "prepare_ligands", {
            "ligand_paths": downloaded_files,
            "output_dir": "./data/prepared_batch",
            "ph": 7.4,
            "add_hydrogens": True,
            "generate_3d": True,
            "optimize": True
        })
        
        if result:
            print(f"\n   Results:")
            for lig in result.get("ligands", []):
                status_symbol = "✓" if lig["status"] == "success" else "✗"
                print(f"   {status_symbol} {lig['ligand_name']}: {lig.get('output_file', lig.get('error'))}")


def print_usage_tips():
    """Print helpful tips for ligand preparation."""
    print("\n" + "=" * 60)
    print("TIPS FOR LIGAND PREPARATION")
    print("=" * 60)
    print("""
1. WHERE TO GET LIGANDS:
   - RCSB PDB: Download from protein-ligand complexes
   - PubChem: https://pubchem.ncbi.nlm.nih.gov/ (download as SDF)
   - ZINC Database: https://zinc.docking.org/ (pre-prepared for docking)
   - ChEMBL: https://www.ebi.ac.uk/chembl/
   - Draw your own: Use ChemDraw, Avogadro, etc. and save as SDF/MOL

2. SUPPORTED FILE FORMATS:
   ✓ .sdf  (Structure Data File) - RECOMMENDED
   ✓ .mol  (MDL Molfile)
   ✓ .mol2 (Tripos Mol2)
   ✗ .pdb  (Use PDB for proteins, not ligands)

3. PREPARATION PARAMETERS:
   - ph: Set to physiological pH (7.4) or your target pH
   - add_hydrogens: Always keep true for docking
   - generate_3d: True if your ligand needs 3D coordinates
   - optimize: True for better geometry (MMFF optimization)

4. COMMON LIGANDS FOR TESTING:
   - ATP: Adenosine triphosphate (energy molecule)
   - ADP: Adenosine diphosphate
   - NAD: Nicotinamide adenine dinucleotide
   - FAD: Flavin adenine dinucleotide
   - HEM: Heme group
   - MK1: HIV protease inhibitor (from 1HSG)

5. OUTPUT FILES:
   - Format: PDBQT (required by AutoDock Vina)
   - Contains: Atom coordinates, partial charges, rotatable bonds
   - Ready for: Direct use with AutoDock Vina docking

6. NEXT STEPS AFTER PREPARATION:
   - Verify PDBQT file (check for charges, TORSDOF line)
   - Prepare your protein receptor
   - Define docking grid/box
   - Run docking with AutoDock Vina
   - Analyze and visualize results
""")


if __name__ == "__main__":
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 10 + "LIGAND PREPARATION EXAMPLES" + " " * 21 + "║")
    print("╚" + "=" * 58 + "╝")
    
    print("\nMake sure both servers are running:")
    print("  Terminal 1: python -m services.rcsb_downloader.server")
    print("  Terminal 2: python -m services.ligand_preparer.server")
    print()
    
    try:
        # Run examples
        example_1_download_and_prepare_ligand()
        example_2_prepare_local_sdf()
        example_3_download_ligand_from_complex()
        example_4_batch_preparation()
        
        # Print tips
        print_usage_tips()
        
        print("\n" + "=" * 60)
        print("✓ All examples completed!")
        print("=" * 60)
        print("\nCheck the following directories for output:")
        print("  - ./data/example_ligands/")
        print("  - ./data/prepared_ligands/")
        print("  - ./data/batch_ligands/")
        print("  - ./data/prepared_batch/")
        print()
        
    except requests.exceptions.ConnectionError:
        print("\n✗ Error: Could not connect to servers!")
        print("   Make sure both servers are running:")
        print("   - RCSB Downloader: http://localhost:8001")
        print("   - Ligand Preparer: http://localhost:8002")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
