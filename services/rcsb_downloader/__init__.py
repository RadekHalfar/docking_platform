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
import re
import logging
import shutil
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
    SEARCH_URL = os.getenv("RCSB_SEARCH_URL", "https://search.rcsb.org/rcsbsearch/v2/query")
    
    def __init__(self, cache_dir: Optional[str] = None):
        """
        Initialize the RCSB downloader.
        
        Args:
            cache_dir: Directory to cache downloaded files
        """
        self.cache_dir = cache_dir
        self._ligand_name_cache: Dict[str, List[Dict[str, Any]]] = {}
        if cache_dir:
            ensure_directory(cache_dir)

    @staticmethod
    def _is_probable_chem_comp_id(value: str) -> bool:
        """Return True when *value* looks like an RCSB chemical component ID."""
        if not value:
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9]{1,4}", value.strip()))

    def search_ligands(self, name: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """Search RCSB chemical components by ligand common name.

        Returns a ranked list of candidates with component IDs and scores.
        """
        query = (name or "").strip()
        if not query:
            return []

        cache_key = query.lower()
        if cache_key in self._ligand_name_cache:
            return self._ligand_name_cache[cache_key][:max_results]

        rows = max(1, min(int(max_results or 10), 50))
        # Only text_chem service is valid for return_type "chem_comp".
        # text service + chem_comp return type is rejected with 400 by RCSB.
        # mol_definition is the documented return_type for CCD searches.
        # chem_comp is undocumented and causes RCSB to inject a default
        # sort_by=score that its own schema then rejects with 400.
        payload_candidates = [
            {
                "query": {
                    "type": "terminal",
                    "service": "text_chem",
                    "parameters": {
                        "attribute": "chem_comp.name",
                        "operator": "contains_words",
                        "value": query,
                    },
                },
                "return_type": "mol_definition",
            },
            {
                "query": {
                    "type": "terminal",
                    "service": "text_chem",
                    "parameters": {
                        "attribute": "chem_comp.name",
                        "operator": "contains_phrase",
                        "value": query,
                    },
                },
                "return_type": "mol_definition",
            },
        ]

        data: Optional[Dict[str, Any]] = None
        last_error: Optional[str] = None
        for payload in payload_candidates:
            try:
                response = requests.post(self.SEARCH_URL, json=payload, timeout=30)
                response.raise_for_status()
                data = response.json()
                break
            except requests.HTTPError as e:
                body = (e.response.text or "")[:400]
                last_error = f"{e}; response={body}"
            except requests.RequestException as e:
                last_error = str(e)

        if data is None:
            raise RuntimeError(f"Failed to search ligands for '{query}': {last_error or 'unknown error'}")

        candidates: List[Dict[str, Any]] = []
        for entry in data.get("result_set", []):
            identifier = str(entry.get("identifier", "")).strip().upper()
            if not identifier:
                continue
            candidates.append(
                {
                    "component_id": identifier,
                    "name": identifier,
                    "score": entry.get("score"),
                }
            )

        self._ligand_name_cache[cache_key] = candidates
        return candidates[:rows]

    def resolve_ligand_name(self, ligand_name: str) -> Optional[str]:
        """Resolve a ligand name to the top-ranked RCSB chemical component ID."""
        token = (ligand_name or "").strip()
        if not token:
            return None
        if self._is_probable_chem_comp_id(token):
            return token.upper()

        candidates = self.search_ligands(token, max_results=1)
        if not candidates:
            return None
        cid = candidates[0].get("component_id", "").strip().upper()
        return cid or None
    
    def _cache_hit(self, filename: str, dest_path: str, source_url: str) -> Optional[Dict[str, Any]]:
        """Return a cached-result dict if *filename* exists in the cache, else None."""
        if not self.cache_dir:
            return None
        cache_path = os.path.join(self.cache_dir, filename)
        if not os.path.exists(cache_path):
            return None
        shutil.copy(cache_path, dest_path)
        return {
            "file_path": dest_path,
            "checksum": compute_file_checksum(dest_path),
            "source_url": source_url,
            "cached": True,
        }

    def _cache_save(self, file_path: str, filename: str) -> None:
        """Copy *file_path* into the cache directory under *filename*."""
        if self.cache_dir:
            shutil.copy(file_path, os.path.join(self.cache_dir, filename))

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
        cached = self._cache_hit(filename, file_path, url)
        if cached:
            logger.info(f"Using cached file: {os.path.join(self.cache_dir, filename)}")
            return cached

        # Download file
        logger.info(f"Downloading {url}")
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            raise RuntimeError(f"Failed to download {pdb_id}: {str(e)}")

        with open(file_path, 'wb') as f:
            f.write(response.content)
        self._cache_save(file_path, filename)

        checksum = compute_file_checksum(file_path)
        logger.info(f"Downloaded {pdb_id} to {file_path}")
        return {
            "file_path": file_path,
            "checksum": checksum,
            "source_url": url,
            "cached": False,
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
        cached = self._cache_hit(filename, file_path, url)
        if cached:
            logger.info(f"Using cached ligand: {os.path.join(self.cache_dir, filename)}")
            return cached

        # Download file
        logger.info(f"Downloading ligand {url}")
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            raise RuntimeError(f"Failed to download ligand {ligand_id}: {str(e)}")

        with open(file_path, 'wb') as f:
            f.write(response.content)
        self._cache_save(file_path, filename)

        checksum = compute_file_checksum(file_path)
        logger.info(f"Downloaded ligand {ligand_id} to {file_path}")
        return {
            "file_path": file_path,
            "checksum": checksum,
            "source_url": url,
            "cached": False,
        }
    
    def download_structures(
        self,
        protein_id: Optional[str] = None,
        ligand_ids: Optional[List[str]] = None,
        enable_name_resolution: bool = False,
        formats: Optional[Dict[str, str]] = None,
        save_dir: str = "./data"
    ) -> Dict[str, Any]:
        """
        Download protein and ligands in one call.

        This is the main tool method exposed via MCP.

        Args:
            protein_id: PDB identifier. Omit to skip the protein download
                (e.g. when the caller supplies a local protein file instead).
            ligand_ids: List of ligand identifiers (optional)
            enable_name_resolution: Resolve ligand names via RCSB search when not chem_comp-like IDs (disabled by default)
            formats: Dict with 'protein' and 'ligand' format specs
            save_dir: Base directory to save files

        Returns:
            Dictionary with protein_path, ligand_paths, metadata
        """
        ligand_ids = ligand_ids or []
        formats = formats or {}
        
        protein_format = formats.get("protein", "pdb")
        ligand_format = formats.get("ligand", "sdf")

        resolved_ligands: List[str] = []
        for ligand_id in ligand_ids:
            token = str(ligand_id).strip()
            if not token:
                continue

            if enable_name_resolution and not self._is_probable_chem_comp_id(token):
                try:
                    resolved = self.resolve_ligand_name(token)
                except RuntimeError as exc:
                    logger.warning(f"Ligand name resolution failed for '{token}': {exc}")
                    resolved = None
                if resolved:
                    logger.info(f"Resolved ligand '{token}' to component ID '{resolved}'")
                    resolved_ligands.append(resolved)
                else:
                    logger.warning(f"Could not resolve ligand name '{token}', using literal token")
                    resolved_ligands.append(token)
            else:
                resolved_ligands.append(token.upper() if self._is_probable_chem_comp_id(token) else token)
        
        # Download protein (skipped when no protein_id is supplied)
        protein_result = None
        if protein_id:
            protein_result = self.download_protein(
                protein_id, save_dir, protein_format
            )

        # Download ligands
        ligand_results = []
        for ligand_id in resolved_ligands:
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
            "protein_path": protein_result["file_path"] if protein_result else None,
            "ligand_paths": [lr["file_path"] for lr in ligand_results],
            "sources": {
                "protein": protein_result["source_url"] if protein_result else None,
                "ligands": [lr["source_url"] for lr in ligand_results]
            },
            "checksums": {
                "protein": protein_result["checksum"] if protein_result else None,
                "ligands": [lr["checksum"] for lr in ligand_results]
            },
            "status": "ok"
        }

        # Save metadata
        metadata_name = f"{protein_id}_download_meta.json" if protein_id else "ligands_download_meta.json"
        metadata_path = os.path.join(save_dir, metadata_name)
        atomic_write_json(metadata_path, result)

        return result
