"""Tests for shared file and path utility helpers."""

import platform
from pathlib import Path

import pytest

from common.file_io import atomic_write_json, load_json_dict
from common.path_utils import from_container_path, normalize_path_for_docker, to_container_path


def test_atomic_write_json_and_load_json_dict_roundtrip(tmp_path):
    """Atomic write helper should persist dict payloads readable by loader."""
    output_path = tmp_path / "nested" / "meta.json"
    payload = {"alpha": 1, "beta": {"x": True}}

    atomic_write_json(output_path, payload)
    loaded = load_json_dict(output_path)

    assert loaded == payload


def test_load_json_dict_returns_none_for_non_object_json(tmp_path):
    """Loader should guard callers from non-dict JSON payloads."""
    output_path = tmp_path / "list_payload.json"
    output_path.write_text("[1, 2, 3]", encoding="utf-8")

    assert load_json_dict(output_path) is None


def test_normalize_path_for_docker_converts_backslashes():
    """Path separator normalization should be platform-agnostic."""
    assert normalize_path_for_docker("demo\\folder\\file.pdb") == "demo/folder/file.pdb"


def test_to_container_path_maps_workspace_relative_paths(tmp_path):
    """Paths under workspace root should be mapped into /app container path."""
    workspace_root = tmp_path / "workspace"
    target_file = workspace_root / "orchestrator_output" / "job_x" / "workflow.json"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("{}", encoding="utf-8")

    mapped = to_container_path(str(target_file), workspace_root=workspace_root)
    assert mapped.endswith("/orchestrator_output/job_x/workflow.json")
    assert mapped.startswith("/app/")


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows drive-letter paths only")
def test_to_container_path_preserves_absolute_windows_drive_style():
    """Absolute Windows paths should stay drive-qualified and slash-normalized."""
    mapped = to_container_path("C:\\Users\\test\\file.pdbqt", workspace_root=Path.cwd())
    assert mapped == "C:/Users/test/file.pdbqt"


# ---------------------------------------------------------------------------
# from_container_path -- reverse direction, Stage 12.3 (CLI reading a
# prepared-protein path an MCP service returned, e.g. "/app/orchestrator_
# output/job_x/..."). Shared by ui/app.py::_resolve_container_path() and
# nlp_orchestrator.py.
# ---------------------------------------------------------------------------

def test_from_container_path_maps_default_container_root(tmp_path):
    local_root = tmp_path / "orchestrator_output"
    mapped = from_container_path(
        "/app/orchestrator_output/job_x/prepared/6lu7_prepared.pdb", local_root
    )
    assert mapped == local_root / "job_x" / "prepared" / "6lu7_prepared.pdb"


def test_from_container_path_custom_container_root(tmp_path):
    local_root = tmp_path / "orchestrator_output"
    mapped = from_container_path(
        "/workspace/orchestrator_output/job_x/file.pdb",
        local_root,
        container_root="/workspace/orchestrator_output",
    )
    assert mapped == local_root / "job_x" / "file.pdb"


def test_from_container_path_returns_unchanged_path_outside_container_root(tmp_path):
    local_root = tmp_path / "orchestrator_output"
    already_local = str(local_root / "job_x" / "file.pdb")
    mapped = from_container_path(already_local, local_root)
    assert mapped == Path(already_local)


def test_from_container_path_normalizes_backslashes(tmp_path):
    local_root = tmp_path / "orchestrator_output"
    mapped = from_container_path(
        "/app/orchestrator_output/job_x\\prepared\\6lu7_prepared.pdb", local_root
    )
    assert mapped == local_root / "job_x" / "prepared" / "6lu7_prepared.pdb"


def test_from_container_path_is_inverse_of_to_container_path(tmp_path):
    """Round-trip: to_container_path() then from_container_path() should
    recover the original local file (this is exactly what happens when the
    orchestrator sends a path to an MCP service and later reads a result
    path that service derived from it)."""
    workspace_root = tmp_path / "workspace"
    local_root = workspace_root / "orchestrator_output"
    target_file = local_root / "job_x" / "prepared" / "6lu7_prepared.pdb"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("ATOM", encoding="utf-8")

    container_path = to_container_path(str(target_file), workspace_root=workspace_root)
    recovered = from_container_path(container_path, local_root)

    assert recovered == target_file
    assert recovered.read_text(encoding="utf-8") == "ATOM"
