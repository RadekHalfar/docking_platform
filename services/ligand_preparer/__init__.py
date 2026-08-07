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
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, TYPE_CHECKING

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
    ensure_directory, compute_file_checksum,
    sanitize_filename
)
from common.file_io import atomic_write_json

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
            cache_dir: Currently unused -- no caching is implemented for
                prepared ligands (unlike RCSBDownloader, which does cache).
                Accepted for constructor consistency with the other MCP
                services; the directory is still created if provided.
        """
        if not RDKIT_AVAILABLE:
            raise RuntimeError("RDKit is required but not installed. Install with: pip install rdkit")

        self.cache_dir = cache_dir  # write-only: never read anywhere in this class
        if cache_dir:
            ensure_directory(cache_dir)
    
    def _read_sdf_records(self, file_path: str) -> List[Any]:
        """
        Read all valid molecule records from an SDF file.

        Unlike read_ligand(), this does not discard extra records — an SDF
        with multiple `$$$$`-delimited molecules yields one Chem.Mol per
        valid record, in file order.

        Raises:
            ValueError: If no valid molecules are found in the file
        """
        supplier = Chem.SDMolSupplier(file_path, removeHs=False)
        mols = [mol for mol in supplier if mol is not None]
        if not mols:
            raise ValueError(f"No valid molecules in {file_path}")
        return mols

    def read_ligand(self, file_path: str) -> Any:
        """
        Read a single representative ligand molecule from file.

        For multi-molecule SDF files this intentionally returns only the
        first record — use prepare_ligands_from_file() to process every
        molecule in a multi-molecule SDF.

        Args:
            file_path: Path to ligand file (SDF, MOL, MOL2)

        Returns:
            RDKit molecule object (Chem.Mol)

        Raises:
            ValueError: If file cannot be read or format unsupported
        """
        file_ext = Path(file_path).suffix.lower()

        if file_ext == '.sdf':
            mol = self._read_sdf_records(file_path)[0]
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
                except Exception:
                    logger.debug("AddHs failed for molecule; continuing without explicit H")
            
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
                except Exception:
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
        # Try using OpenBabel command line. Both "the binary is missing" and
        # "the binary ran but failed" are the same underlying situation from
        # the caller's perspective -- obabel didn't produce a usable
        # PDBQT -- so both fall back to the manual converter the same way,
        # rather than one falling back silently and the other raising.
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
                logger.error(f"OpenBabel failed (exit {result.returncode}): {result.stderr}")

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
    
    _SUPPORTED_POSE_FORMATS = ("sdf", "pdb", "mol2")

    def convert_docked_poses(
        self,
        pose_results: List[Dict[str, Any]],
        output_dir: str,
        formats: Tuple[str, ...] = _SUPPORTED_POSE_FORMATS,
    ) -> Dict[str, Any]:
        """
        Convert each ligand's docked-pose PDBQT (all Vina/Moldina modes) into
        one multi-pose file per requested format (default: SDF, PDB, MOL2 --
        PDBQT itself is never included here since it's already the native,
        unconverted file the `dock` step wrote).

        Uses Meeko's PDBQT->RDKit round trip (the REMARK SMILES Meeko embeds
        when it originally wrote the ligand PDBQT) to restore proper bond
        orders/aromaticity -- something the PDBQT file alone doesn't carry --
        parsing the PDBQT once and writing every requested format from the
        same in-memory molecule. Falls back to OpenBabel (no per-pose
        affinity/RMSD metadata, and formats converted independently straight
        from the PDBQT) if the Meeko round trip fails, e.g. for ligands that
        weren't prepared by Meeko originally.

        Args:
            pose_results: Per-ligand docking result dicts as produced by
                MoldinaRunner (``ligand_id``, ``output_path`` pointing at the
                multi-model docked-pose PDBQT, and ``modes`` -- a list of
                {mode, affinity, rmsd_lb, rmsd_ub} in pose order).
            output_dir: Directory to write ``<ligand_id>_poses.<format>``
                files to.
            formats: Which formats to write; unrecognized values are ignored.

        Returns:
            Dictionary with per-ligand conversion results and any errors.
        """
        ensure_directory(output_dir)
        formats = tuple(f for f in formats if f in self._SUPPORTED_POSE_FORMATS) or self._SUPPORTED_POSE_FORMATS

        converted = []
        errors = []

        for pose in pose_results:
            ligand_id = pose.get("ligand_id")
            pose_path = pose.get("output_path")
            if not pose_path or not os.path.exists(pose_path):
                continue

            modes = pose.get("modes") or []
            clean_id = sanitize_filename(ligand_id)
            output_paths = {
                fmt: os.path.join(output_dir, f"{clean_id}_poses.{fmt}") for fmt in formats
            }

            try:
                method = self._write_poses_meeko(pose_path, modes, output_paths)
            except Exception as e:
                logger.warning(f"Meeko pose conversion failed for {ligand_id}: {e}; falling back to OpenBabel")
                try:
                    method = self._write_poses_obabel(pose_path, output_paths)
                except Exception as e2:
                    logger.error(f"OpenBabel pose conversion also failed for {ligand_id}: {e2}")
                    errors.append({"ligand_id": ligand_id, "error": str(e2)})
                    continue

            entry = {
                "ligand_id": ligand_id,
                "num_poses": len(modes) or None,
                "conversion_method": method,
            }
            for fmt, path in output_paths.items():
                if os.path.exists(path):
                    entry[f"{fmt}_path"] = path
            converted.append(entry)

        return {
            "converted": converted,
            "errors": errors,
            "status": "ok" if converted else "failed",
        }

    def _write_poses_meeko(
        self,
        pose_pdbqt_path: str,
        modes: List[Dict[str, Any]],
        output_paths: Dict[str, str]
    ) -> str:
        """
        Round-trip a docked-pose PDBQT back to RDKit once (bond orders
        restored via the embedded Meeko REMARK SMILES), then write every
        requested format from that single parse: SDF/PDB directly (one pose
        per record/MODEL, tagged with its affinity/RMSD from the parsed
        docking log -- PDB has no property mechanism to carry it, only SDF
        does), and MOL2 derived from the SDF this call just wrote (OpenBabel
        has no bond-order perception of its own to lose that way, unlike
        converting straight from PDBQT).
        """
        from meeko import PDBQTMolecule, RDKitMolCreate

        pdbqt_mol = PDBQTMolecule.from_file(pose_pdbqt_path, skip_typing=True)
        mol_list = RDKitMolCreate.from_pdbqt_mol(pdbqt_mol)
        if not mol_list:
            raise ValueError("Meeko produced no RDKit molecules from pose file")
        mol = RDKitMolCreate.combine_rdkit_mols(mol_list)

        # MOL2 has no RDKit writer -- it's derived below from an SDF, so one
        # must exist even if the caller didn't request "sdf" as an output.
        sdf_path = output_paths.get("sdf")
        temp_sdf_fd = None
        if sdf_path is None and "mol2" in output_paths:
            base_dir = os.path.dirname(next(iter(output_paths.values())))
            temp_sdf_fd, sdf_path = tempfile.mkstemp(suffix=".sdf", dir=base_dir)
        pdb_path = output_paths.get("pdb")

        sdf_writer = Chem.SDWriter(sdf_path) if sdf_path else None
        pdb_writer = Chem.PDBWriter(pdb_path, flavor=1) if pdb_path else None
        try:
            for conformer in mol.GetConformers():
                conf_id = conformer.GetId()
                mode = modes[conf_id] if conf_id < len(modes) else {}
                mol.SetProp("mode_index", str(mode.get("mode", conf_id + 1)))
                mol.SetProp("affinity", str(mode.get("affinity", "")))
                mol.SetProp("rmsd_lb", str(mode.get("rmsd_lb", "")))
                mol.SetProp("rmsd_ub", str(mode.get("rmsd_ub", "")))
                if sdf_writer:
                    sdf_writer.write(mol, confId=conf_id)
                if pdb_writer:
                    pdb_writer.write(mol, confId=conf_id)
        finally:
            if sdf_writer:
                sdf_writer.close()
            if pdb_writer:
                pdb_writer.close()

        if "mol2" in output_paths:
            self._sdf_to_mol2(sdf_path, output_paths["mol2"])

        if temp_sdf_fd is not None:
            os.close(temp_sdf_fd)
            os.remove(sdf_path)

        return "meeko"

    def _sdf_to_mol2(self, sdf_path: str, mol2_path: str) -> None:
        """Derive MOL2 from an already-written SDF (which carries the
        Meeko-restored bond orders) rather than letting OpenBabel re-derive
        connectivity from the raw PDBQT on its own."""
        cmd = ["obabel", sdf_path, "-O", mol2_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0 or not os.path.exists(mol2_path):
            raise RuntimeError(
                f"OpenBabel SDF->MOL2 conversion failed (exit {result.returncode}): {result.stderr}"
            )

    def _write_poses_obabel(self, pose_pdbqt_path: str, output_paths: Dict[str, str]) -> str:
        """
        Fallback pose conversion via OpenBabel when the Meeko round trip
        fails. No per-pose affinity/RMSD metadata (OpenBabel doesn't know
        about it) -- the viewer falls back to showing poses without per-pose
        scores. Each requested format is converted independently straight
        from the PDBQT (there's no shared RDKit mol to derive them from, the
        way the Meeko path does).
        """
        for fmt, path in output_paths.items():
            cmd = ["obabel", pose_pdbqt_path, "-O", path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0 or not os.path.exists(path):
                raise RuntimeError(
                    f"OpenBabel pose conversion to {fmt} failed (exit {result.returncode}): {result.stderr}"
                )
        return "openbabel"

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
    
    def _prepare_mol(
        self,
        mol: Any,
        ligand_id: str,
        input_path: str,
        output_dir: str,
        ph: float,
        add_hydrogens: bool,
        generate_3d: bool,
        optimize: bool,
        extra: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Prepare an already-read RDKit Mol into PDB + PDBQT + a result dict.

        Shared by prepare_ligand() (single molecule per file) and
        prepare_ligands_from_file() (one or more molecules per file).
        """
        ligand_id_clean = sanitize_filename(ligand_id)

        logger.info(f"Preparing {ligand_id}")
        mol = self.prepare_molecule(mol, ph, add_hydrogens, generate_3d, optimize)

        # Save intermediate PDB
        pdb_path = os.path.join(output_dir, f"{ligand_id_clean}_prepared.pdb")
        self.save_as_pdb(mol, pdb_path)

        # Convert to PDBQT
        pdbqt_path = os.path.join(output_dir, f"{ligand_id_clean}.pdbqt")
        conversion_info = self.convert_to_pdbqt_meeko(pdb_path, pdbqt_path)

        # Compute checksum
        checksum = compute_file_checksum(pdbqt_path)

        result = {
            "id": ligand_id,
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
        if extra:
            result.update(extra)

        return result

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

        For multi-molecule SDF files this only prepares the first molecule —
        use prepare_ligands_from_file() to prepare every molecule in a file.

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

        logger.info(f"Reading ligand from {input_path}")
        mol = self.read_ligand(input_path)
        ligand_name = Path(input_path).stem

        return self._prepare_mol(
            mol, ligand_name, input_path, output_dir,
            ph, add_hydrogens, generate_3d, optimize
        )

    def prepare_ligands_from_file(
        self,
        input_path: str,
        output_dir: str,
        ph: float = 7.4,
        add_hydrogens: bool = True,
        generate_3d: bool = True,
        optimize: bool = True
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Prepare every ligand molecule contained in a single input file.

        For .mol/.mol2 (always one molecule) and single-molecule SDF files,
        this behaves exactly like prepare_ligand() and returns one result
        with the same id/filenames as before. For an SDF containing multiple
        `$$$$`-delimited molecules, each valid molecule is prepared with a
        unique id ("{stem}_{index}") so output files never collide; a
        molecule that fails to prepare is recorded as an error without
        aborting the rest of the file.

        Returns:
            (results, errors) — results is a list of prepared-ligand dicts
            (possibly empty), errors is a list of {"ligand", "error"} dicts
            for molecules that failed to prepare.
        """
        ensure_directory(output_dir)

        file_ext = Path(input_path).suffix.lower()
        stem = Path(input_path).stem

        if file_ext != '.sdf':
            result = self.prepare_ligand(
                input_path, output_dir, ph, add_hydrogens, generate_3d, optimize
            )
            return [result], []

        mols = self._read_sdf_records(input_path)

        if len(mols) == 1:
            result = self._prepare_mol(
                mols[0], stem, input_path, output_dir,
                ph, add_hydrogens, generate_3d, optimize
            )
            return [result], []

        results: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        total = len(mols)
        for index, mol in enumerate(mols, start=1):
            ligand_id = f"{stem}_{index}"
            sdf_title = mol.GetProp("_Name") if mol.HasProp("_Name") else ""
            try:
                result = self._prepare_mol(
                    mol, ligand_id, input_path, output_dir,
                    ph, add_hydrogens, generate_3d, optimize,
                    extra={
                        "source_file": input_path,
                        "molecule_index": index,
                        "molecule_count": total,
                        "sdf_title": sdf_title or None,
                    }
                )
                results.append(result)
            except Exception as e:
                logger.error(
                    f"Failed to prepare molecule {index}/{total} from {input_path}: {e}"
                )
                errors.append({
                    "ligand": f"{input_path} (molecule {index}/{total})",
                    "error": str(e)
                })

        return results, errors

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

        This is the main tool method exposed via MCP. A single input path
        pointing at a multi-molecule SDF file expands into multiple prepared
        ligands — see prepare_ligands_from_file().

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
                mol_results, mol_errors = self.prepare_ligands_from_file(
                    ligand_path, output_dir, ph, add_hydrogens, generate_3d, optimize
                )
                prepared_ligands.extend(mol_results)
                errors.extend(mol_errors)
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
            "total_ligands": len(prepared_ligands),
            "successful": len(prepared_ligands),
            "failed": len(errors),
            "errors": errors,
            "status": "ok" if prepared_ligands else "failed"
        }
        
        # Save metadata
        metadata_path = os.path.join(output_dir, "preparation_meta.json")
        atomic_write_json(metadata_path, response)
        
        return response
