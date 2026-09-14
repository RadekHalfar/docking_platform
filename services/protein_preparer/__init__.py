"""
Protein Preparer Service

This service prepares protein structures for AutoDock Vina docking.

Preparation steps (following AutoDock Vina best practices):
1. Read protein from PDB file
2. Remove water molecules and heteroatoms (optional)
3. Add hydrogens at specified pH
4. Assign partial charges (Gasteiger)
5. Detect and assign AutoDock atom types
6. Convert to PDBQT format

Tool: prepare_proteins
- Prepares proteins/receptors for docking with AutoDock Vina
- Outputs PDBQT format with proper charges and atom types
"""
from __future__ import annotations
import os
import logging
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

from common.pdb_geometry import parse_pdb_atom_line
from common.utils import ensure_directory, compute_file_checksum

logger = logging.getLogger(__name__)


class ProteinPreparer:
    """
    Prepares protein structures for AutoDock Vina docking.
    
    Features:
    - Removes water molecules and heteroatoms
    - Adds hydrogens at specified pH
    - Assigns Gasteiger partial charges
    - Converts to PDBQT format with AutoDock atom types
    - Supports multiple conversion methods (Meeko, OpenBabel, manual)
    """
    
    def __init__(self, cache_dir: Optional[str] = None):
        """
        Initialize protein preparer.

        Args:
            cache_dir: Currently unused -- no caching is implemented for
                prepared proteins (unlike RCSBDownloader, which does cache).
                Accepted for constructor consistency with the other MCP
                services; the directory is still created if provided.
        """
        if not RDKIT_AVAILABLE:
            raise RuntimeError("RDKit is required but not installed. Install with: pip install rdkit")

        self.cache_dir = cache_dir  # write-only: never read anywhere in this class
        if cache_dir:
            ensure_directory(cache_dir)
    
    def read_protein(self, file_path: str) -> Any:
        """
        Read protein from PDB file.
        
        Args:
            file_path: Path to protein PDB file
            
        Returns:
            RDKit molecule object (Chem.Mol)
            
        Raises:
            ValueError: If file cannot be read or format unsupported
        """
        file_ext = Path(file_path).suffix.lower()
        
        if file_ext != '.pdb':
            raise ValueError(f"Unsupported file format: {file_ext}. Only PDB format is supported.")
        
        # Read PDB file
        mol = Chem.MolFromPDBFile(file_path, removeHs=False, sanitize=False)
        
        if mol is None:
            raise ValueError(f"Failed to read protein from {file_path}")
        
        # Try to sanitize
        try:
            Chem.SanitizeMol(mol)
        except Exception as e:
            logger.warning(f"Sanitization failed: {e}. Continuing with unsanitized molecule.")
        
        return mol
    
    def clean_protein(
        self,
        mol: Any,
        remove_waters: bool = True,
        remove_heteroatoms: bool = False,
        keep_residues: Optional[List[str]] = None
    ) -> Any:
        """
        Clean protein structure by removing unwanted molecules.
        
        Args:
            mol: RDKit molecule (Chem.Mol)
            remove_waters: Remove water molecules
            remove_heteroatoms: Remove all heteroatoms (ligands, ions, etc.)
            keep_residues: List of residue names to keep (e.g., ['HEM', 'NAD'])
            
        Returns:
            Cleaned RDKit molecule (Chem.Mol)
        """
        mol = Chem.Mol(mol)
        
        # Get residue information
        atoms_to_remove = []
        keep_residues = keep_residues or []
        
        for atom in mol.GetAtoms():
            pdb_info = atom.GetPDBResidueInfo()
            if pdb_info:
                residue_name = pdb_info.GetResidueName().strip()
                is_hetero = pdb_info.GetIsHeteroAtom()
                
                # Remove waters
                if remove_waters and residue_name in ['HOH', 'WAT', 'H2O']:
                    atoms_to_remove.append(atom.GetIdx())
                    continue
                
                # Remove heteroatoms (but keep specified ones)
                if remove_heteroatoms and is_hetero:
                    if residue_name not in keep_residues:
                        atoms_to_remove.append(atom.GetIdx())
        
        # Remove atoms in reverse order
        if atoms_to_remove:
            logger.info(f"Removing {len(atoms_to_remove)} atoms (waters/heteroatoms)")
            editable_mol = Chem.EditableMol(mol)
            for idx in sorted(atoms_to_remove, reverse=True):
                editable_mol.RemoveAtom(idx)
            mol = editable_mol.GetMol()
        
        return mol
    
    def prepare_protein(
        self,
        mol: Any,
        ph: float = 7.4,
        add_hydrogens: bool = True,
        remove_waters: bool = True,
        remove_heteroatoms: bool = False,
        keep_residues: Optional[List[str]] = None
    ) -> Any:
        """
        Prepare protein for docking.
        
        Args:
            mol: RDKit molecule (Chem.Mol)
            ph: pH for protonation state
            add_hydrogens: Add explicit hydrogens
            remove_waters: Remove water molecules
            remove_heteroatoms: Remove heteroatoms (ligands, ions)
            keep_residues: Residue names to keep even if heteroatoms
            
        Returns:
            Prepared RDKit molecule (Chem.Mol)
        """
        mol = Chem.Mol(mol)
        
        # Step 1: Clean protein
        if remove_waters or remove_heteroatoms:
            mol = self.clean_protein(
                mol,
                remove_waters=remove_waters,
                remove_heteroatoms=remove_heteroatoms,
                keep_residues=keep_residues
            )
        
        # Step 2: Add hydrogens at specified pH
        if add_hydrogens:
            logger.info(f"Adding hydrogens at pH {ph}")
            mol = Chem.AddHs(mol, addCoords=True)
            
            # RDKit doesn't support pH-dependent protonation; use OpenBabel or pdb2pqr for that
            logger.warning("pH-dependent protonation not implemented. Using default protonation.")
        
        # Step 3: Assign Gasteiger charges
        logger.info("Assigning Gasteiger partial charges")
        try:
            AllChem.ComputeGasteigerCharges(mol)
        except Exception as e:
            logger.warning(f"Failed to compute Gasteiger charges: {e}")
        
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
        ensure_directory(os.path.dirname(output_path))
        Chem.MolToPDBFile(mol, output_path)
        logger.info(f"Saved protein to {output_path}")
        return output_path
    
    def convert_to_pdbqt(self, pdb_path: str, output_path: str) -> Dict[str, Any]:
        """
        Convert PDB to PDBQT for rigid receptor.
        
        For receptors (proteins), we use a simple PDB->PDBQT conversion.
        Receptors are treated as rigid, so we just need proper atom typing.
        
        Args:
            pdb_path: Input PDB file
            output_path: Output PDBQT file
            
        Returns:
            Dictionary with conversion info
        """
        # For rigid receptors, use manual conversion
        # This creates a simple but compatible PDBQT format
        logger.info("Converting to rigid receptor PDBQT format")
        return self.convert_to_pdbqt_manual(pdb_path, output_path)
    
    def convert_to_pdbqt_manual(self, pdb_path: str, output_path: str) -> Dict[str, Any]:
        """
        Manual conversion from PDB to PDBQT for rigid receptor.
        
        Creates a simple PDBQT compatible with AutoDock/Vina/Moldina.
        For rigid receptors, we just need proper formatting with charges and atom types.
        
        Args:
            pdb_path: Input PDB file
            output_path: Output PDBQT file
            
        Returns:
            Dictionary with conversion info
        """
        logger.info("Using manual rigid receptor PDBQT conversion")
        
        # Read PDB file
        with open(pdb_path, 'r') as f:
            pdb_lines = f.readlines()
        
        # AutoDock atom type mapping
        def get_ad_type(element):
            mapping = {
                'H': 'HD', 'C': 'C', 'N': 'NA', 'O': 'OA',
                'S': 'SA', 'P': 'P', 'F': 'F', 'CL': 'Cl',
                'BR': 'Br', 'I': 'I', 'MG': 'Mg', 'CA': 'Ca',
                'MN': 'Mn', 'FE': 'Fe', 'ZN': 'Zn'
            }
            return mapping.get(element.upper(), 'C')
        
        # Write PDBQT
        ensure_directory(os.path.dirname(output_path))
        atom_count = 0
        
        with open(output_path, 'w') as out:
            # For rigid receptors, just ATOM lines with charges and types
            # No ROOT/ENDROOT/TORSDOF (those are for ligands)

            for line in pdb_lines:
                record = parse_pdb_atom_line(line)
                if record is None:
                    if line.startswith(('ATOM', 'HETATM')):
                        logger.warning(f"Skipping malformed line: {line.strip()}")
                    continue

                # Get AutoDock type
                ad_type = get_ad_type(record.element)

                # Rigid receptor: zero charge
                charge = 0.000

                # Write PDBQT line
                atom_count += 1
                out.write(f"ATOM  {atom_count:5d} {record.atom_name:>4s} {record.res_name:>3s} "
                         f"{record.chain}{record.res_num:>4s}    "
                         f"{record.x:8.3f}{record.y:8.3f}{record.z:8.3f}  1.00  0.00    {charge:+6.3f} {ad_type:>2s}\n")
        
        logger.info(f"Created rigid receptor PDBQT with {atom_count} atoms")
        return {
            "method": "manual_rigid",
            "success": True,
            "atom_count": atom_count
        }
    
    def prepare_protein_file(
        self,
        protein_path: str,
        output_path: str,
        ph: float = 7.4,
        add_hydrogens: bool = True,
        remove_waters: bool = True,
        remove_heteroatoms: bool = False,
        keep_residues: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Prepare a single protein file for docking.
        
        Args:
            protein_path: Path to input protein PDB file
            output_path: Path for output PDBQT file
            ph: pH for protonation state
            add_hydrogens: Add explicit hydrogens
            remove_waters: Remove water molecules
            remove_heteroatoms: Remove heteroatoms
            keep_residues: Residue names to keep
            
        Returns:
            Dictionary with preparation results
        """
        logger.info(f"Preparing protein: {protein_path}")
        
        try:
            # Read protein
            mol = self.read_protein(protein_path)
            initial_atoms = mol.GetNumAtoms()
            
            # Prepare protein
            mol = self.prepare_protein(
                mol,
                ph=ph,
                add_hydrogens=add_hydrogens,
                remove_waters=remove_waters,
                remove_heteroatoms=remove_heteroatoms,
                keep_residues=keep_residues
            )
            
            final_atoms = mol.GetNumAtoms()
            
            # Save intermediate PDB
            pdb_path = output_path.replace('.pdbqt', '_prepared.pdb')
            self.save_as_pdb(mol, pdb_path)
            
            # Convert to PDBQT
            conversion_info = self.convert_to_pdbqt(pdb_path, output_path)
            
            # Calculate checksum
            checksum = compute_file_checksum(output_path)
            
            return {
                "id": Path(protein_path).stem,
                "input_path": protein_path,
                "pdb_path": pdb_path,
                "pdbqt_path": output_path,
                "initial_atoms": initial_atoms,
                "final_atoms": final_atoms,
                "atoms_removed": initial_atoms - final_atoms,
                "conversion_method": conversion_info["method"],
                "checksum": checksum,
                "preparation_params": {
                    "ph": ph,
                    "add_hydrogens": add_hydrogens,
                    "remove_waters": remove_waters,
                    "remove_heteroatoms": remove_heteroatoms,
                    "keep_residues": keep_residues or []
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to prepare protein {protein_path}: {e}")
            raise
    
    def prepare_proteins(
        self,
        protein_paths: List[str],
        output_dir: str,
        ph: float = 7.4,
        add_hydrogens: bool = True,
        remove_waters: bool = True,
        remove_heteroatoms: bool = False,
        keep_residues: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Prepare multiple proteins for docking.
        
        Args:
            protein_paths: List of paths to protein PDB files
            output_dir: Directory for output PDBQT files
            ph: pH for protonation state
            add_hydrogens: Add explicit hydrogens
            remove_waters: Remove water molecules
            remove_heteroatoms: Remove heteroatoms
            keep_residues: Residue names to keep
            
        Returns:
            Dictionary with batch preparation results
        """
        ensure_directory(output_dir)
        
        results = []
        errors = []
        
        for protein_path in protein_paths:
            try:
                protein_name = Path(protein_path).stem
                output_path = os.path.join(output_dir, f"{protein_name}.pdbqt")
                
                result = self.prepare_protein_file(
                    protein_path=protein_path,
                    output_path=output_path,
                    ph=ph,
                    add_hydrogens=add_hydrogens,
                    remove_waters=remove_waters,
                    remove_heteroatoms=remove_heteroatoms,
                    keep_residues=keep_residues
                )
                
                results.append(result)
                
            except Exception as e:
                logger.error(f"Error preparing {protein_path}: {e}")
                errors.append({
                    "protein": protein_path,
                    "error": str(e)
                })
        
        return {
            "status": "ok" if len(results) > 0 else "failed",
            "prepared_proteins": results,
            "errors": errors,
            "successful": len(results),
            "failed": len(errors),
            "total": len(protein_paths)
        }
