"""
Workflow state persistence utilities.

Hybrid model:
- "latest" files for fast runtime loads
- immutable revision checkpoints for audit/recovery
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class WorkflowStateManager:
    """Persists workflow parameters/state with revisioned checkpoints."""

    def __init__(self, job_dir: Path):
        self.job_dir = Path(job_dir)
        self.job_dir.mkdir(parents=True, exist_ok=True)

        self.parameters_file = self.job_dir / "workflow_parameters.json"
        self.state_file = self.job_dir / "workflow_state.json"
        self.manifest_file = self.job_dir / "workflow_manifest.json"
        self.checkpoints_dir = self.job_dir / "checkpoints"
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

        if not self.manifest_file.exists():
            self._write_manifest({"current_revision": 0, "events": []})

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _atomic_write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, path)

    def _read_manifest(self) -> Dict[str, Any]:
        if not self.manifest_file.exists():
            return {"current_revision": 0, "events": []}
        with open(self.manifest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"current_revision": 0, "events": []}
        data.setdefault("current_revision", 0)
        data.setdefault("events", [])
        return data

    def _write_manifest(self, manifest: Dict[str, Any]) -> None:
        self._atomic_write_json(self.manifest_file, manifest)

    def _next_revision(self, event_type: str, details: Dict[str, Any]) -> int:
        manifest = self._read_manifest()
        revision = int(manifest.get("current_revision", 0)) + 1
        manifest["current_revision"] = revision

        event = {
            "revision": revision,
            "timestamp": self._timestamp(),
            "event_type": event_type,
            "details": details,
        }
        events = manifest.setdefault("events", [])
        events.append(event)
        self._write_manifest(manifest)
        return revision

    def get_current_revision(self) -> int:
        manifest = self._read_manifest()
        return int(manifest.get("current_revision", 0))

    def save_parameters_checkpoint(self, parameters: Dict[str, Any], source: str = "unknown") -> int:
        """Persist latest parameters and snapshot a new immutable revision."""
        revision = self._next_revision("parameters_update", {"source": source})
        payload = {
            "revision": revision,
            "timestamp": self._timestamp(),
            "source": source,
            "parameters": parameters,
        }

        self._atomic_write_json(self.parameters_file, payload)
        checkpoint = self.checkpoints_dir / f"rev_{revision:06d}_parameters.json"
        self._atomic_write_json(checkpoint, payload)
        logger.info(f"Saved parameter checkpoint revision={revision} source={source}")
        return revision

    def save_execution_state_checkpoint(self, runtime_state: Dict[str, Any], step: str) -> int:
        """Persist latest runtime state and snapshot a new immutable revision."""
        revision = self._next_revision("state_update", {"step": step})
        payload = {
            "revision": revision,
            "timestamp": self._timestamp(),
            "step": step,
            "state": runtime_state,
        }

        self._atomic_write_json(self.state_file, payload)
        checkpoint = self.checkpoints_dir / f"rev_{revision:06d}_state.json"
        self._atomic_write_json(checkpoint, payload)
        logger.info(f"Saved state checkpoint revision={revision} step={step}")
        return revision

    def _load_json_file(self, path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"Failed to load JSON file {path}: {exc}")
        return None

    def load_latest_parameters(self) -> Optional[Dict[str, Any]]:
        """Load latest parameters (disk is source-of-truth when present)."""
        payload = self._load_json_file(self.parameters_file)
        if not payload:
            return None
        params = payload.get("parameters")
        if isinstance(params, dict):
            return params
        return None

    def load_latest_state(self) -> Optional[Dict[str, Any]]:
        """Load latest runtime state snapshot."""
        payload = self._load_json_file(self.state_file)
        if not payload:
            return None
        state = payload.get("state")
        if isinstance(state, dict):
            return state
        return None
