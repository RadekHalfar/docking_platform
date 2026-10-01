"""Tests for common/docking_stop.py -- the cross-container stop-docking flag (Stage 13.1b)."""
from common.docking_stop import (
    STOP_FLAG_NAME,
    clear_docking_stop,
    docking_stop_requested,
    request_docking_stop,
    stop_flag_path,
)


def test_stop_flag_path_is_inside_docking_dir(tmp_path):
    assert stop_flag_path(tmp_path) == tmp_path / STOP_FLAG_NAME
    assert stop_flag_path(str(tmp_path)) == tmp_path / STOP_FLAG_NAME


def test_request_then_requested_then_clear(tmp_path):
    assert docking_stop_requested(tmp_path) is False

    assert request_docking_stop(tmp_path) is True
    assert docking_stop_requested(tmp_path) is True

    clear_docking_stop(tmp_path)
    assert docking_stop_requested(tmp_path) is False


def test_request_is_a_noop_when_docking_dir_does_not_exist(tmp_path):
    missing = tmp_path / "job_x" / "docking_results"

    assert request_docking_stop(missing) is False
    assert not missing.exists()  # never created by the requester


def test_request_twice_is_fine(tmp_path):
    assert request_docking_stop(tmp_path) is True
    assert request_docking_stop(tmp_path) is True
    assert docking_stop_requested(tmp_path) is True


def test_clear_without_flag_is_a_noop(tmp_path):
    clear_docking_stop(tmp_path)
    clear_docking_stop(tmp_path / "does_not_exist")
