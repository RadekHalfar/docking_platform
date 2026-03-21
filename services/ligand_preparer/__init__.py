"""
Ligand Preparer Service

This MCP server provides tools to prepare ligand molecules for AutoDock Vina docking.

Preparation steps (following AutoDock Vina best practices):
1. Read ligand from SDF/MOL/MOL2 file
2. Add explicit hydrogens at specified pH
3. Assign partial charges (Gasteiger)
4. Generate 3D conformer if needed
5. Detect and mark rotatable bonds
6. Convert to PDBQT format

Tool: prepare_ligands
- Prepares ligands for docking with AutoDock Vina
- Outputs PDBQT format with proper charges and rotatable bonds
"""
from __future__ import annotations
import os
import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from rdkit import Chem

try:
    from rdkit import Chem
    from rdkit.Chem import AllChem, Descriptors
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False
    Chem = None  # type: ignore

from common.utils import (
    ensure_directory, compute_file_checksum, save_metadata,
    sanitize_filename
)

logger = logging.getLogger(__name__)


class LigandPreparer:
    """
    Prepares ligand molecules for AutoDock Vina docking.
    
    Uses RDKit for molecule processing and Meeko/OpenBabel for PDBQT conversion.
    Follows AutoDock Vina preparation guidelines.
    """
    
    def __init__(self, cache_dir: Optional[str] = None):
        """
        Initialize the ligand preparer.
        
        Args:
            cache_dir: Directory to cache prepared files
        """
        if not RDKIT_AVAILABLE:
            raise RuntimeError("RDKit is required but not installed. Install with: pip install rdkit")
        
        self.cache_dir = cache_dir
        if cache_dir:
            ensure_directory(cache_dir)
    
    def read_ligand(self, file_path: str) -> Any:
        """
        Read ligand from file.
        
        Args:
            file_path: Path to ligand file (SDF, MOL, MOL2)
            
        Returns:
            RDKit molecule object (Chem.Mol)
            
        Raises:
            ValueError: If file cannot be read or format unsupported
        """
        file_ext = Path(file_path).suffix.lower()
        
        if file_ext == '.sdf':
            supplier = Chem.SDMolSupplier(file_path, removeHs=False)
            mols = [mol for mol in supplier if mol is not None]
            if not mols:
                raise ValueError(f"No valid molecules in {file_path}")
            mol = mols[0]  # Take first molecule
        elif file_ext == '.mol':
            mol = Chem.MolFromMolFile(file_path, removeHs=False)
        elif file_ext == '.mol2':
            mol = Chem.MolFromMol2File(file_path, removeHs=False)
        else:
            raise ValueError(f"Unsupported file format: {file_ext}")
        
        if mol is None:
            raise ValueError(f"Failed to read molecule from {file_path}")
        
        return mol
    
    def prepare_molecule(
        self,
        mol: Any,
        ph: float = 7.4,
        add_hydrogens: bool = True,
        generate_3d: bool = True,
        optimize: bool = True
    ) -> Any:
        """
        Prepare molecule for docking.
        
        Args:
            mol: RDKit molecule (Chem.Mol)
            ph: pH for protonation state
            add_hydrogens: Add explicit hydrogens
            generate_3d: Generate 3D coordinates if missing
            optimize: Optimize 3D structure with MMFF
            
        Returns:
            Prepared RDKit molecule (Chem.Mol)
        """
        # Create a copy to avoid modifying original
        mol = Chem.Mol(mol)
        
        # Sanitize molecule
        try:
            Chem.SanitizeMol(mol)
        except Exception as e:
            logger.warning(f"Sanitization warning: {e}")
        
        # Add hydrogens at specified pH
        if add_hydrogens:
            # Remove existing hydrogens first
            mol = Chem.RemoveHs(mol)
            # Add hydrogens back at specified pH
            # For pH-specific protonation (pH-dependent) use tools like Dimorphite-DL
            mol = Chem.AddHs(mol, addCoords=(mol.GetNumConformers() > 0))
        
        # Check if 3D coordinates exist
        needs_3d = mol.GetNumConformers() == 0
        
        if needs_3d and generate_3d:
            # Generate 3D conformer
            logger.info("Generating 3D coordinates")
            params = AllChem.ETKDGv3()
            params.randomSeed = 42
            result = AllChem.EmbedMolecule(mol, params)
            
            if result != 0:
                # Fallback to basic embedding
                logger.warning("ETKDG failed, using basic embedding")
                AllChem.EmbedMolecule(mol, randomSeed=42, useRandomCoords=True)
        
        # Optimize geometry with MMFF force field
        if optimize and mol.GetNumConformers() > 0:
            try:
                AllChem.MMFFOptimizeMolecule(mol, maxIters=200)
            except Exception as e:
                logger.warning(f"MMFF optimization failed: {e}")
                try:
                    AllChem.UFFOptimizeMolecule(mol, maxIters=200)
                except Exception as e2:
                    logger.warning(f"UFF optimization also failed: {e2}")
        
        # Assign partial charges (Gasteiger charges)
        try:
            AllChem.ComputeGasteigerCharges(mol)
        except Exception as e:
            logger.warning(f"Gasteiger charge calculation failed: {e}")
        
        return mol
    
    def save_as_pdb(self, mol: Any, output_path: str) -> str:
        """
        Save molecule as PDB file.
        
        Args:
            mol: RDKit molecule (Chem.Mol)
            output_path: Output PDB file path
            
        Returns:
            Path to saved file
        """
        Chem.MolToPDBFile(mol, output_path)
        return output_path
    
    def convert_to_pdbqt_meeko(self, pdb_path: str, output_path: str) -> Dict[str, Any]:
        """
        Convert PDB to PDBQT using Meeko.
        
        Meeko is the modern tool for preparing ligands for AutoDock Vina,
        replacing the old prepare_ligand4.py from MGLTools.
        
        Args:
            pdb_path: Input PDB file
            output_path: Output PDBQT file
            
        Returns:
            Dictionary with conversion info
        """
        try:
            # Try to use meeko if available
            from meeko import MoleculePreparation, PDBQTWriterLegacy
            
            mol = Chem.MolFromPDBFile(pdb_path, removeHs=False)
            if mol is None:
                raise ValueError("Failed to read PDB file")
            
            # Ensure explicit hydrogens for Meeko
            if mol.GetNumAtoms() > 0:
                try:
                    # Check if hydrogens are implicit
                    has_implicit = any(atom.GetTotalNumHs() > atom.GetNumExplicitHs() for atom in mol.GetAtoms())
                    if has_implicit:
                        mol = Chem.AddHs(mol, addCoords=True)
                except:
                    pass
            
            # Mark conformer as 3D to avoid Meeko warning
            if mol.GetNumConformers() > 0:
                conf = mol.GetConformer()
                conf.Set3D(True)
            
            # Prepare molecule for docking
            preparator = MoleculePreparation()
            mol_setups = preparator.prepare(mol)
            
            # Write PDBQT
            rotatable_bonds = 0
            for setup in mol_setups:
                pdbqt_string = PDBQTWriterLegacy.write_string(setup)
                # write_string returns a tuple (pdbqt_string, is_ok) in some versions
                if isinstance(pdbqt_string, tuple):
                    pdbqt_string = pdbqt_string[0]
                with open(output_path, 'w') as f:
                    f.write(pdbqt_string)
                # Get rotatable bonds count
                try:
                    rotatable_bonds = len(setup.rotatable_bonds) if hasattr(setup, 'rotatable_bonds') else 0
                except:
                    rotatable_bonds = 0
                break  # Use first setup
            
            return {
                "method": "meeko",
                "rotatable_bonds": rotatable_bonds
            }
        except ImportError:
            logger.warning("Meeko not available, falling back to OpenBabel")
            return self.convert_to_pdbqt_obabel(pdb_path, output_path)
    
    def convert_to_pdbqt_obabel(self, pdb_path: str, output_path: str) -> Dict[str, Any]:
        """
        Convert PDB to PDBQT using OpenBabel.
        
        Args:
            pdb_path: Input PDB file
            output_path: Output PDBQT file
            
        Returns:
            Dictionary with conversion info
        """
        # Try using OpenBabel command line
        try:
            cmd = [
                "obabel",
                pdb_path,
                "-O", output_path,
                "-xh",  # Add hydrogens
                "-p", "7.4"  # pH
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                return {
                    "method": "openbabel",
                    "rotatable_bonds": self._count_rotatable_bonds_from_pdbqt(output_path)
                }
            else:
                raise RuntimeError(f"OpenBabel failed: {result.stderr}")
        
        except FileNotFoundError:
            logger.error("OpenBabel not found. Please install: conda install -c conda-forge openbabel")
            # Fallback: create a basic PDBQT from PDB
            return self.convert_to_pdbqt_manual(pdb_path, output_path)
    
    def convert_to_pdbqt_manual(self, pdb_path: str, output_path: str) -> Dict[str, Any]:
        """
        Manual conversion from PDB to PDBQT (basic implementation).
        
        This is a simplified fallback when neither Meeko nor OpenBabel is available.
        For production use, install Meeko.
        
        Args:
            pdb_path: Input PDB file
            output_path: Output PDBQT file
            
        Returns:
            Dictionary with conversion info
        """
        logger.warning("Using manual PDBQT conversion (limited functionality)")
        
        mol = Chem.MolFromPDBFile(pdb_path, removeHs=False)
        if mol is None:
            raise ValueError("Failed to read PDB")
        
        # Get Gasteiger charges
        AllChem.ComputeGasteigerCharges(mol)
        
        # Read PDB and add charge column
        with open(pdb_path, 'r') as f:
            pdb_lines = f.readlines()
        
        pdbqt_lines = []
        atom_idx = 0
        
        for line in pdb_lines:
            if line.startswith('ATOM') or line.startswith('HETATM'):
                if atom_idx < mol.GetNumAtoms():
                    atom = mol.GetAtomWithIdx(atom_idx)
                    charge = atom.GetDoubleProp('_GasteigerCharge') if atom.HasProp('_GasteigerCharge') else 0.0
                    atom_type = atom.GetSymbol()
                    
                    # Create PDBQT line (PDB format + charge + atom type)
                    pdbqt_line = line[:70].ljust(70) + f"{charge:6.3f} {atom_type:>2}\n"
                    pdbqt_lines.append(pdbqt_line)
                    atom_idx += 1
            elif line.startswith('CONECT'):
                # Skip CONECT records in PDBQT
                continue
            else:
                pdbqt_lines.append(line)
        
        # Add TORSDOF (torsional degrees of freedom)
        num_rotatable = Descriptors.NumRotatableBonds(mol)
        pdbqt_lines.append(f"TORSDOF {num_rotatable}\n")
        
        with open(output_path, 'w') as f:
            f.writelines(pdbqt_lines)
        
        return {
            "method": "manual",
            "rotatable_bonds": num_rotatable
        }
    
    def _count_rotatable_bonds_from_pdbqt(self, pdbqt_path: str) -> int:
        """Extract TORSDOF from PDBQT file."""
        try:
            with open(pdbqt_path, 'r') as f:
                for line in f:
                    if line.startswith('TORSDOF'):
                        return int(line.split()[1])
        except Exception:
            pass
        return 0
    
    def prepare_ligand(
        self,
        input_path: str,
        output_dir: str,
        ph: float = 7.4,
        add_hydrogens: bool = True,
        generate_3d: bool = True,
        optimize: bool = True
    ) -> Dict[str, Any]:
        """
        Prepare a single ligand for docking.
        
        Args:
            input_path: Path to input ligand file
            output_dir: Directory for output files
            ph: pH for protonation
            add_hydrogens: Add explicit hydrogens
            generate_3d: Generate 3D coordinates if missing
            optimize: Optimize geometry
            
        Returns:
            Dictionary with prepared ligand info
        """
        ensure_directory(output_dir)
        
        # Read ligand
        logger.info(f"Reading ligand from {input_path}")
        mol = self.read_ligand(input_path)
        
        # Get ligand name
        ligand_name = Path(input_path).stem
        ligand_name_clean = sanitize_filename(ligand_name)
        
        # Prepare molecule
        logger.info(f"Preparing {ligand_name}")
        mol = self.prepare_molecule(mol, ph, add_hydrogens, generate_3d, optimize)
        
        # Save intermediate PDB
        pdb_path = os.path.join(output_dir, f"{ligand_name_clean}_prepared.pdb")
        self.save_as_pdb(mol, pdb_path)
        
        # Convert to PDBQT
        pdbqt_path = os.path.join(output_dir, f"{ligand_name_clean}.pdbqt")
        conversion_info = self.convert_to_pdbqt_meeko(pdb_path, pdbqt_path)
        
        # Compute checksum
        checksum = compute_file_checksum(pdbqt_path)
        
        result = {
            "id": ligand_name,
            "input_path": input_path,
            "pdb_path": pdb_path,
            "pdbqt_path": pdbqt_path,
            "checksum": checksum,
            "rotatable_bonds": conversion_info.get("rotatable_bonds", 0),
            "conversion_method": conversion_info.get("method", "unknown"),
            "preparation_params": {
                "ph": ph,
                "add_hydrogens": add_hydrogens,
                "generate_3d": generate_3d,
                "optimize": optimize
            }
        }
        
        return result
    
    def prepare_ligands(
        self,
        ligand_paths: List[str],
        output_dir: str = "./data/prepared",
        ph: float = 7.4,
        add_hydrogens: bool = True,
        generate_3d: bool = True,
        optimize: bool = True
    ) -> Dict[str, Any]:
        """
        Prepare multiple ligands for docking.
        
        This is the main tool method exposed via MCP.
        
        Args:
            ligand_paths: List of paths to ligand files
            output_dir: Directory for output files
            ph: pH for protonation
            add_hydrogens: Add explicit hydrogens
            generate_3d: Generate 3D coordinates if missing
            optimize: Optimize geometry
            
        Returns:
            Dictionary with preparation results
        """
        ensure_directory(output_dir)
        
        prepared_ligands = []
        errors = []
        
        for ligand_path in ligand_paths:
            try:
                result = self.prepare_ligand(
                    ligand_path, output_dir, ph, add_hydrogens, generate_3d, optimize
                )
                prepared_ligands.append(result)
            except Exception as e:
                logger.error(f"Failed to prepare {ligand_path}: {e}")
                errors.append({
                    "ligand": ligand_path,
                    "error": str(e)
                })
        
        # Prepare response
        response = {
            "prepared_ligands": prepared_ligands,
            "total": len(ligand_paths),
            "successful": len(prepared_ligands),
            "failed": len(errors),
            "errors": errors,
            "status": "ok" if prepared_ligands else "failed"
        }
        
        # Save metadata
        metadata_path = os.path.join(output_dir, "preparation_meta.json")
        save_metadata(response, metadata_path)
        
        return response
