"""
Common utilities for the docking system.
"""
import hashlib
import logging
from pathlib import Path
from typing import Dict
from datetime import datetime

logger = logging.getLogger(__name__)


def ensure_directory(path: str) -> Path:
    """
    Ensure a directory exists, create if it doesn't.
    
    Args:
        path: Directory path
        
    Returns:
        Path object
    """
    dir_path = Path(path)
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


def compute_file_checksum(file_path: str, algorithm: str = "md5") -> str:
    """
    Compute checksum of a file.
    
    Args:
        file_path: Path to file
        algorithm: Hash algorithm (md5, sha256)
        
    Returns:
        Hex digest of checksum
    """
    hash_obj = hashlib.new(algorithm)
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_obj.update(chunk)
    return hash_obj.hexdigest()


def create_job_directory(base_dir: str, job_id: str) -> Dict[str, Path]:
    """
    Create directory structure for a job.
    
    Structure:
        /data/jobs/<job_id>/
            raw/        - downloaded files
            prepared/   - prepared ligands
            docking/    - docking results
            visuals/    - images and sessions
    
    Args:
        base_dir: Base data directory
        job_id: Job identifier
        
    Returns:
        Dictionary with paths for each subdirectory
    """
    job_root = Path(base_dir) / "jobs" / job_id
    
    subdirs = {
        'root': job_root,
        'raw': job_root / 'raw',
        'prepared': job_root / 'prepared',
        'docking': job_root / 'docking',
        'visuals': job_root / 'visuals'
    }
    
    for subdir in subdirs.values():
        subdir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Created job directory structure: {job_root}")
    return subdirs


def generate_job_id() -> str:
    """
    Generate a unique job ID.
    
    Returns:
        Job ID string (format: job_YYYYMMDD_HHMMSS_<random>)
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_suffix = hashlib.md5(str(datetime.now().timestamp()).encode()).hexdigest()[:6]
    return f"job_{timestamp}_{random_suffix}"


def validate_pdb_id(pdb_id: str) -> bool:
    """
    Validate PDB ID format.
    
    PDB IDs are 4 alphanumeric characters.
    
    Args:
        pdb_id: PDB identifier
        
    Returns:
        True if valid format
    """
    if not pdb_id or len(pdb_id) != 4:
        return False
    return pdb_id.isalnum()


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename to prevent path traversal.
    
    Args:
        filename: Original filename
        
    Returns:
        Sanitized filename
    """
    # Remove path separators and other unsafe characters
    unsafe_chars = ['/', '\\', '..', '~', '$', '&', '|', ';', '<', '>', '`']
    sanitized = filename
    for char in unsafe_chars:
        sanitized = sanitized.replace(char, '_')
    return sanitized
