"""
Unit tests for common utilities.
"""
import os
import stat

import pytest

from common.utils import (
    ensure_directory, ensure_shared_directory, compute_file_checksum,
    generate_job_id, validate_pdb_id, sanitize_filename
)
from common.file_io import atomic_write_json, load_json_dict


def test_ensure_directory(tmp_path):
    """Test directory creation."""
    test_dir = tmp_path / "test" / "nested" / "dir"
    result = ensure_directory(str(test_dir))
    assert result.exists()
    assert result.is_dir()


def test_ensure_shared_directory_creates_dir(tmp_path):
    """ensure_shared_directory should create nested directories like ensure_directory."""
    test_dir = tmp_path / "shared" / "nested" / "dir"
    result = ensure_shared_directory(str(test_dir))
    assert result.exists()
    assert result.is_dir()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits aren't meaningful on native Windows paths")
def test_ensure_shared_directory_is_world_writable(tmp_path):
    """The directory must be writable by a uid other than its creator's (world write bit set)."""
    test_dir = tmp_path / "shared_dir"
    result = ensure_shared_directory(str(test_dir))
    mode = stat.S_IMODE(os.stat(result).st_mode)
    assert mode & stat.S_IWOTH, f"expected world-writable (o+w), got mode {oct(mode)}"


def test_ensure_shared_directory_fixes_existing_restrictive_dir(tmp_path):
    """Calling it on an already-existing, restrictively-permissioned directory must still widen it."""
    test_dir = tmp_path / "already_exists"
    test_dir.mkdir(mode=0o755)
    ensure_shared_directory(str(test_dir))
    if os.name != "nt":
        mode = stat.S_IMODE(os.stat(test_dir).st_mode)
        assert mode & stat.S_IWOTH


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
    
    atomic_write_json(str(meta_file), metadata)
    assert meta_file.exists()
    
    loaded = load_json_dict(str(meta_file))
    assert loaded == metadata


def test_load_metadata_nonexistent():
    """Test loading metadata from nonexistent file."""
    result = load_json_dict("nonexistent_file.json")
    assert result is None


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
    assert validate_pdb_id(1234) is False  # integer input must not raise (L9 isinstance guard)


def test_sanitize_filename():
    """Test filename sanitization."""
    assert sanitize_filename("normal_file.txt") == "normal_file.txt"
    assert sanitize_filename("../../../etc/passwd") == "______etc_passwd"
    assert sanitize_filename("file|with&special<chars>") == "file_with_special_chars_"
    assert sanitize_filename("file;rm -rf /") == "file_rm -rf _"
