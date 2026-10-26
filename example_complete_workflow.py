# -*- coding: utf-8 -*-
"""
Complete Docking Workflow Example

This script demonstrates the complete workflow from downloading structures
to running Moldina docking and converting the docked poses for viewing.

Workflow Steps:
1. Download protein and ligands from RCSB PDB
2. Prepare protein receptor (add H, clean, convert to PDBQT)
3. Prepare ligands (add H, optimize, convert to PDBQT)
4. Run Moldina docking (PSO-based molecular docking)
5. Convert docked poses to SDF/PDB/MOL2 (open them in the GUI viewer or any molecule viewer)
6. Display results summary

Prerequisites:
- RCSB Downloader service running on port 8001
- Ligand Preparer service running on port 8002
- Protein Preparer service running on port 8003
- Moldina Runner service running on port 8004
- Moldina installed in WSL (Windows) or natively (Linux)
"""
import os
import requests
from pathlib import Path

from common.mcp_client import call_mcp_tool as _call_mcp_tool
from common.path_utils import normalize_path_for_docker


def call_mcp_tool(service_url, tool_name, arguments, request_id="1"):
    """Call an MCP tool and print request context."""
    print(f"\n📡 Calling {tool_name} on {service_url}")
    return _call_mcp_tool(service_url, tool_name, arguments, request_id=request_id)


def main():
    """Run complete docking workflow"""
    
    print("=" * 80)
    print("MOLDINA DOCKING WORKFLOW")
    print("=" * 80)
    
    # Configuration
    PROTEIN_ID = "6lu7"  # SARS-CoV-2 Main Protease
    LIGAND_IDS = ["ATP", "HEM"]  # Example ligands
    OUTPUT_BASE = "./demo_output"
    
    # MCP service URLs
    RCSB_URL = "http://localhost:8001"
    LIGAND_PREP_URL = "http://localhost:8002"
    PROTEIN_PREP_URL = "http://localhost:8003"
    MOLDINA_URL = "http://localhost:8004"
    
    # Create output directory
    os.makedirs(OUTPUT_BASE, exist_ok=True)
    
    # Step 1: Download structures from RCSB
    print("\n" + "=" * 80)
    print("STEP 1: Download structures from RCSB PDB")
    print("=" * 80)
    
    download_result = call_mcp_tool(
        RCSB_URL,
        "download_structures",
        {
            "protein_id": PROTEIN_ID,
            "ligand_ids": LIGAND_IDS,
            "formats": {
                "protein": "pdb",
                "ligand": "sdf"
            },
            "save_dir": normalize_path_for_docker(os.path.join(OUTPUT_BASE, "downloaded"))
        }
    )
    
    download_data = download_result
    protein_pdb = download_data["protein_path"]
    ligand_sdfs = download_data["ligand_paths"]
    
    print(f"\n✅ Downloaded protein: {protein_pdb}")
    print(f"✅ Downloaded {len(ligand_sdfs)} ligands:")
    for ligand in ligand_sdfs:
        print(f"   - {ligand}")
    
    # Step 2: Prepare protein
    print("\n" + "=" * 80)
    print("STEP 2: Prepare protein for docking")
    print("=" * 80)
    
    protein_prep_result = call_mcp_tool(
        PROTEIN_PREP_URL,
        "prepare_proteins",
        {
            "protein_paths": [normalize_path_for_docker(protein_pdb)],
            "output_dir": normalize_path_for_docker(os.path.join(OUTPUT_BASE, "prepared_proteins")),
            "ph": 7.4,
            "remove_waters": True,
            "remove_heteroatoms": True,
            "add_hydrogens": True
        }
    )
    
    protein_data = protein_prep_result
    receptor_pdbqt = protein_data["prepared_proteins"][0]["pdbqt_path"]
    
    print(f"\n✅ Prepared protein: {receptor_pdbqt}")
    print(f"   Initial atoms: {protein_data['prepared_proteins'][0]['initial_atoms']}")
    print(f"   Final atoms: {protein_data['prepared_proteins'][0]['final_atoms']}")
    print(f"   Atoms removed: {protein_data['prepared_proteins'][0]['atoms_removed']}")
    print(f"   Method: {protein_data['prepared_proteins'][0]['conversion_method']}")
    
    # Step 3: Prepare ligands
    print("\n" + "=" * 80)
    print("STEP 3: Prepare ligands for docking")
    print("=" * 80)
    
    ligand_prep_result = call_mcp_tool(
        LIGAND_PREP_URL,
        "prepare_ligands",
        {
            "ligand_paths": [normalize_path_for_docker(p) for p in ligand_sdfs],
            "output_dir": normalize_path_for_docker(os.path.join(OUTPUT_BASE, "prepared_ligands")),
            "ph": 7.4,
            "add_hydrogens": True,
            "optimize": True
        }
    )
    
    ligand_data = ligand_prep_result
    ligand_pdbqts = [lig["pdbqt_path"] for lig in ligand_data["prepared_ligands"]]
    
    print(f"\n✅ Prepared {len(ligand_pdbqts)} ligands:")
    for lig in ligand_data["prepared_ligands"]:
        print(f"   - {lig['id']}: {lig['rotatable_bonds']} rotatable bonds ({lig['conversion_method']})")
    
    # Step 4: Run Moldina docking
    print("\n" + "=" * 80)
    print("STEP 4: Run Moldina docking (PSO-based)")
    print("=" * 80)
    
    # Docking parameters (you should adjust these based on your protein)
    #CENTER = [10.5, 20.3, -5.2]  # Search box center (adjust for your protein)
    CENTER = [0, 10.0, 55.0]  # Search box center (adjust for your protein)
    BOX_SIZE = [25.0, 30.0, 35.0]  # Search box dimensions in Angstroms
    
    print(f"\n⚙️  Docking parameters:")
    print(f"   Center: {CENTER}")
    print(f"   Box Size: {BOX_SIZE}")
    print(f"   Algorithm: Moldina (PSO)")
    print(f"   Population: 500")
    print(f"   CPU: 4, Exhaustiveness: 16 (used only by docking_algorithm='vina'; PSO ignores both)")
    
    docking_result = call_mcp_tool(
        MOLDINA_URL,
        "run_docking",
        {
            "receptor_path": normalize_path_for_docker(receptor_pdbqt),
            "ligand_paths": [normalize_path_for_docker(p) for p in ligand_pdbqts],
            "output_dir": normalize_path_for_docker(os.path.join(OUTPUT_BASE, "docking_results")),
            "center": CENTER,
            "box_size": BOX_SIZE,
            "cpu": 4,
            "population": 500,
            "exhaustiveness": 16,
            "num_modes": 15,
            "energy_range": 100.0,
            "docking_algorithm": "moldina",
        }
    )
    
    docking_data = docking_result
    
    print(f"\n✅ Docking completed!")
    print(f"   Total ligands: {docking_data['total']}")
    print(f"   Successful: {docking_data['successful']}")
    print(f"   Failed: {docking_data['failed']}")
    
    # Step 5: Convert docked poses for viewing (what the workflow's
    # "visualize" step does; the GUI shows these files with 3Dmol.js)
    print("\n" + "=" * 80)
    print("STEP 5: Convert docked poses")
    print("=" * 80)

    pose_results = [
        {**result, "output_path": normalize_path_for_docker(result["output_path"])}
        for result in docking_data["results"]
        if result["output_path"] and os.path.exists(result["output_path"])
    ]
    viz_dir = os.path.join(OUTPUT_BASE, "visualization")

    if pose_results:
        print(f"\n📊 Converting {len(pose_results)} docked pose file(s)...")

        try:
            conversion = call_mcp_tool(
                LIGAND_PREP_URL,
                "convert_docked_poses",
                {
                    "pose_results": pose_results,
                    "output_dir": normalize_path_for_docker(viz_dir),
                },
            )

            print(f"\n✅ Converted {len(conversion['converted'])} ligand(s):")
            for entry in conversion["converted"]:
                print(f"   {entry['ligand_id']}: {entry.get('sdf_path', 'no SDF written')}")
            for error in conversion["errors"]:
                print(f"   ⚠️  {error['ligand_id']}: {error['error']}")

        except Exception as e:
            print(f"\n⚠️  Pose conversion failed: {e}")
            print(f"   Continuing with workflow...")
    else:
        print(f"\n⚠️  No valid docked poses to convert")

    # Step 6: Display results
    print("\n" + "=" * 80)
    print("DOCKING RESULTS (sorted by best affinity)")
    print("=" * 80)
    
    results = docking_data["results"]
    
    print(f"\n{'Rank':<6} {'Ligand ID':<12} {'Best Affinity':<18} {'Modes':<8} {'Output File'}")
    print("-" * 80)
    
    for i, result in enumerate(results, 1):
        ligand_id = result["ligand_id"]
        affinity = result["best_affinity"]
        num_modes = len(result["modes"])
        output_file = Path(result["output_path"]).name
        
        # Handle None affinity (no log file)
        affinity_str = f"{affinity:>7.2f} kcal/mol" if affinity is not None else "N/A (see output)"
        
        print(f"{i:<6} {ligand_id:<12} {affinity_str:<18} {num_modes:<8} {output_file}")
        
        # Show top 3 modes for each ligand
        if num_modes > 0:
            print(f"       {'Mode':<6} {'Affinity':<15} {'RMSD (lb/ub)'}")
            for mode in result["modes"][:3]:  # Top 3 modes
                print(f"       {mode['mode']:<6} {mode['affinity']:>7.2f} kcal/mol   {mode['rmsd_lb']:.3f} / {mode['rmsd_ub']:.3f}")
            if num_modes > 3:
                print(f"       ... and {num_modes - 3} more modes")
        print()
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    if results:
        best = results[0]
        print(f"\n🏆 Best result: {best['ligand_id']}")
        if best['best_affinity'] is not None:
            print(f"   Binding affinity: {best['best_affinity']} kcal/mol")
        else:
            print(f"   Binding affinity: See output file for details")
        print(f"   Output: {best['output_path']}")
        print(f"   Log: {best['log_path']}")
    
    print(f"\n📁 All results saved to: {OUTPUT_BASE}")
    print(f"\n✅ Workflow completed successfully!")
    
    print("\n" + "=" * 80)
    print("NEXT STEPS")
    print("=" * 80)
    
    if results:
        print("\n1. View the docked poses:")
        print(f"   SDF/PDB/MOL2 files in {viz_dir}")
        print("   (open them in any molecule viewer, or run the workflow from the GUI at http://localhost:3000)")
        
        print("\n2. Review log files for detailed statistics:")
        for result in results[:3]:  # Top 3
            if result.get('log_path'):
                print(f"   {result['log_path']}")
        
        print("\n3. Analyze binding modes and interactions in your molecule viewer")
    else:
        print("\n⚠️  No docking results to visualize.")
        print("   Check the Moldina output and log files in:")
        print(f"   {os.path.join(OUTPUT_BASE, 'docking_results')}")
    
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.ConnectionError as e:
        print(f"\n❌ Error: Could not connect to MCP server")
        print(f"   Make sure all services are running:")
        print(f"   - python -m services.rcsb_downloader.server")
        print(f"   - python -m services.ligand_preparer.server")
        print(f"   - python -m services.protein_preparer.server")
        print(f"   - python -m services.moldina_runner.server")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
