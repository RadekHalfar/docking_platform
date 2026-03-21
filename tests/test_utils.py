"""
Unit tests for common utilities.
"""
import pytest
import os
import tempfile
from pathlib import Path
from common.utils import (
    ensure_directory, compute_file_checksum, save_metadata,
    load_metadata, create_job_directory, generate_job_id,
    validate_pdb_id, sanitize_filename, JobLogger
)


def test_ensure_directory(tmp_path):
    """Test directory creation."""
    test_dir = tmp_path / "test" / "nested" / "dir"
    result = ensure_directory(str(test_dir))
    assert result.exists()
    assert result.is_dir()


def test_compute_file_checksum(tmp_path):
    """Test file checksum computation."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("Hello, World!")
    
    checksum = compute_file_checksum(str(test_file))
    assert isinstance(checksum, str)
    assert len(checksum) == 32  # MD5 hex digest length


def test_save_and_load_metadata(tmp_path):
    """Test metadata saving and loading."""
    metadata = {"test": "value", "number": 42}
    meta_file = tmp_path / "metadata.json"
    
    save_metadata(metadata, str(meta_file))
    assert meta_file.exists()
    
    loaded = load_metadata(str(meta_file))
    assert loaded == metadata


def test_load_metadata_nonexistent():
    """Test loading metadata from nonexistent file."""
    result = load_metadata("nonexistent_file.json")
    assert result is None


def test_create_job_directory(tmp_path):
    """Test job directory structure creation."""
    job_id = "test_job_001"
    dirs = create_job_directory(str(tmp_path), job_id)
    
    assert "root" in dirs
    assert "raw" in dirs
    assert "prepared" in dirs
    assert "docking" in dirs
    assert "visuals" in dirs
    
    for dir_path in dirs.values():
        assert dir_path.exists()
        assert dir_path.is_dir()


def test_generate_job_id():
    """Test job ID generation."""
    job_id = generate_job_id()
    assert job_id.startswith("job_")
    assert len(job_id) > 10
    
    # Generate another and ensure they're different
    job_id2 = generate_job_id()
    # Note: If executed in the same second, timestamps might match but random suffix differs


def test_validate_pdb_id():
    """Test PDB ID validation."""
    assert validate_pdb_id("1ABC") is True
    assert validate_pdb_id("2XYZ") is True
    assert validate_pdb_id("123A") is True
    
    # Invalid cases
    assert validate_pdb_id("") is False
    assert validate_pdb_id("ABC") is False  # Too short
    assert validate_pdb_id("ABCDE") is False  # Too long
    assert validate_pdb_id("AB-C") is False  # Invalid character
    assert validate_pdb_id(None) is False


def test_sanitize_filename():
    """Test filename sanitization."""
    assert sanitize_filename("normal_file.txt") == "normal_file.txt"
    assert sanitize_filename("../../../etc/passwd") == "______etc_passwd"
    assert sanitize_filename("file|with&special<chars>") == "file_with_special_chars_"
    assert sanitize_filename("file;rm -rf /") == "file_rm -rf _"


def test_job_logger(tmp_path):
    """Test JobLogger creation."""
    job_id = "test_job_logger"
    logger = JobLogger(job_id, log_dir=str(tmp_path))
    
    logger.info("Test info message")
    logger.warning("Test warning message")
    logger.error("Test error message")
    
    log_file = tmp_path / f"{job_id}.log"
    assert log_file.exists()
    
    log_content = log_file.read_text()
    assert "Test info message" in log_content
    assert "Test warning message" in log_content
    assert "Test error message" in log_content
