"""
RCSB PDB Downloader Service

This MCP server provides tools to download protein structures and ligands
from the RCSB Protein Data Bank.

Tool: download_structures
- Downloads protein PDB/mmCIF files and ligand SDF/MOL files
- Validates PDB IDs and ligand identifiers
- Returns local file paths and metadata
"""
import os
import logging
import requests
from pathlib import Path
from typing import Dict, List, Optional, Any
from common.utils import (
    ensure_directory, compute_file_checksum,
    validate_pdb_id, sanitize_filename
)
from common.file_io import atomic_write_json

logger = logging.getLogger(__name__)


class RCSBDownloader:
    """
    Client for downloading structures from RCSB PDB.
    
    Uses RCSB REST API to fetch:
    - Protein structures (PDB, mmCIF formats)
    - Ligand molecules (SDF, MOL formats)
    - Associated metadata
    """
    
    BASE_URL = "https://files.rcsb.org/download"
    LIGAND_URL = "https://files.rcsb.org/ligands/view"
    
    def __init__(self, cache_dir: Optional[str] = None):
        """
        Initialize the RCSB downloader.
        
        Args:
            cache_dir: Directory to cache downloaded files
        """
        self.cache_dir = cache_dir
        if cache_dir:
            ensure_directory(cache_dir)
    
    def download_protein(
        self,
        pdb_id: str,
        save_dir: str,
        file_format: str = "pdb"
    ) -> Dict[str, Any]:
        """
        Download a protein structure from RCSB PDB.
        
        Args:
            pdb_id: PDB identifier (4 characters, e.g., '1ABC')
            save_dir: Directory to save the file
            file_format: File format ('pdb' or 'cif')
            
        Returns:
            Dictionary with file_path, checksum, source_url
            
        Raises:
            ValueError: If PDB ID is invalid
            RuntimeError: If download fails
        """
        # Validate PDB ID
        if not validate_pdb_id(pdb_id):
            raise ValueError(f"Invalid PDB ID: {pdb_id}")
        
        # Normalize format
        file_format = file_format.lower()
        if file_format == "mmcif":
            file_format = "cif"
        
        if file_format not in ["pdb", "cif"]:
            raise ValueError(f"Unsupported format: {file_format}")
        
        # Construct URL
        pdb_id_upper = pdb_id.upper()
        url = f"{self.BASE_URL}/{pdb_id_upper}.{file_format}"
        
        # Determine save path
        ensure_directory(save_dir)
        filename = f"{pdb_id_upper}.{file_format}"
        file_path = os.path.join(save_dir, filename)
        
        # Check cache
        if self.cache_dir:
            cache_path = os.path.join(self.cache_dir, filename)
            if os.path.exists(cache_path):
                logger.info(f"Using cached file: {cache_path}")
                # Copy from cache
                import shutil
                shutil.copy(cache_path, file_path)
                checksum = compute_file_checksum(file_path)
                return {
                    "file_path": file_path,
                    "checksum": checksum,
                    "source_url": url,
                    "cached": True
                }
        
        # Download file
        logger.info(f"Downloading {url}")
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            raise RuntimeError(f"Failed to download {pdb_id}: {str(e)}")
        
        # Save file
        with open(file_path, 'wb') as f:
            f.write(response.content)
        
        # Save to cache if configured
        if self.cache_dir:
            cache_path = os.path.join(self.cache_dir, filename)
            import shutil
            shutil.copy(file_path, cache_path)
        
        # Compute checksum
        checksum = compute_file_checksum(file_path)
        
        logger.info(f"Downloaded {pdb_id} to {file_path}")
        
        return {
            "file_path": file_path,
            "checksum": checksum,
            "source_url": url,
            "cached": False
        }
    
    def download_ligand(
        self,
        ligand_id: str,
        save_dir: str,
        file_format: str = "sdf"
    ) -> Dict[str, Any]:
        """
        Download a ligand molecule from RCSB.
        
        Args:
            ligand_id: Ligand identifier (e.g., 'ATP', 'HEM')
            save_dir: Directory to save the file
            file_format: File format ('sdf' or 'mol')
            
        Returns:
            Dictionary with file_path, checksum, source_url
            
        Raises:
            RuntimeError: If download fails
        """
        # Sanitize ligand ID
        ligand_id_clean = sanitize_filename(ligand_id)
        
        # Normalize format
        file_format = file_format.lower()
        if file_format not in ["sdf", "mol"]:
            raise ValueError(f"Unsupported ligand format: {file_format}")
        
        # Construct URL (RCSB ligand expo)
        ligand_id_upper = ligand_id.upper()
        url = f"{self.LIGAND_URL}/{ligand_id_upper}_ideal.{file_format}"
        
        # Determine save path
        ensure_directory(save_dir)
        filename = f"{ligand_id_clean}.{file_format}"
        file_path = os.path.join(save_dir, filename)
        
        # Check cache
        if self.cache_dir:
            cache_path = os.path.join(self.cache_dir, filename)
            if os.path.exists(cache_path):
                logger.info(f"Using cached ligand: {cache_path}")
                import shutil
                shutil.copy(cache_path, file_path)
                checksum = compute_file_checksum(file_path)
                return {
                    "file_path": file_path,
                    "checksum": checksum,
                    "source_url": url,
                    "cached": True
                }
        
        # Download file
        logger.info(f"Downloading ligand {url}")
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            raise RuntimeError(f"Failed to download ligand {ligand_id}: {str(e)}")
        
        # Save file
        with open(file_path, 'wb') as f:
            f.write(response.content)
        
        # Save to cache
        if self.cache_dir:
            cache_path = os.path.join(self.cache_dir, filename)
            import shutil
            shutil.copy(file_path, cache_path)
        
        # Compute checksum
        checksum = compute_file_checksum(file_path)
        
        logger.info(f"Downloaded ligand {ligand_id} to {file_path}")
        
        return {
            "file_path": file_path,
            "checksum": checksum,
            "source_url": url,
            "cached": False
        }
    
    def download_structures(
        self,
        protein_id: str,
        ligand_ids: Optional[List[str]] = None,
        formats: Optional[Dict[str, str]] = None,
        save_dir: str = "./data"
    ) -> Dict[str, Any]:
        """
        Download protein and ligands in one call.
        
        This is the main tool method exposed via MCP.
        
        Args:
            protein_id: PDB identifier
            ligand_ids: List of ligand identifiers (optional)
            formats: Dict with 'protein' and 'ligand' format specs
            save_dir: Base directory to save files
            
        Returns:
            Dictionary with protein_path, ligand_paths, metadata
        """
        ligand_ids = ligand_ids or []
        formats = formats or {}
        
        protein_format = formats.get("protein", "pdb")
        ligand_format = formats.get("ligand", "sdf")
        
        # Download protein
        protein_result = self.download_protein(
            protein_id, save_dir, protein_format
        )
        
        # Download ligands
        ligand_results = []
        for ligand_id in ligand_ids:
            try:
                ligand_result = self.download_ligand(
                    ligand_id, save_dir, ligand_format
                )
                ligand_results.append(ligand_result)
            except (requests.RequestException, RuntimeError) as e:
                logger.error(f"Failed to download ligand {ligand_id}: {e}")
                # Continue with other ligands
        
        # Prepare response
        result = {
            "protein_path": protein_result["file_path"],
            "ligand_paths": [lr["file_path"] for lr in ligand_results],
            "sources": {
                "protein": protein_result["source_url"],
                "ligands": [lr["source_url"] for lr in ligand_results]
            },
            "checksums": {
                "protein": protein_result["checksum"],
                "ligands": [lr["checksum"] for lr in ligand_results]
            },
            "status": "ok"
        }
        
        # Save metadata
        metadata_path = os.path.join(save_dir, f"{protein_id}_download_meta.json")
        atomic_write_json(metadata_path, result)
        
        return result
