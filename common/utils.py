"""
Common utilities for the docking system.
"""
import hashlib
import logging
import secrets
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


def generate_job_id() -> str:
    """
    Generate a unique job ID.

    Returns:
        Job ID string (format: job_YYYYMMDD_HHMMSS_<random>)
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_suffix = secrets.token_hex(3)
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
    if not isinstance(pdb_id, str) or not pdb_id or len(pdb_id) != 4:
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
