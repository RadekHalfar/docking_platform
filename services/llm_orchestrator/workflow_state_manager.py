"""
Workflow state persistence utilities.

Hybrid model:
- "latest" files for fast runtime loads
- immutable revision checkpoints for audit/recovery
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from common.file_io import atomic_write_json, load_json_dict

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

    @staticmethod
    def _normalize_manifest(manifest: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Normalize manifest payload shape with required defaults."""
        if not isinstance(manifest, dict):
            return {"current_revision": 0, "events": []}
        manifest.setdefault("current_revision", 0)
        manifest.setdefault("events", [])
        return manifest

    def _read_manifest(self) -> Dict[str, Any]:
        return self._normalize_manifest(load_json_dict(self.manifest_file))

    def _write_manifest(self, manifest: Dict[str, Any]) -> None:
        atomic_write_json(self.manifest_file, self._normalize_manifest(manifest))

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

        atomic_write_json(self.parameters_file, payload)
        checkpoint = self.checkpoints_dir / f"rev_{revision:06d}_parameters.json"
        atomic_write_json(checkpoint, payload)
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

        atomic_write_json(self.state_file, payload)
        checkpoint = self.checkpoints_dir / f"rev_{revision:06d}_state.json"
        atomic_write_json(checkpoint, payload)
        logger.info(f"Saved state checkpoint revision={revision} step={step}")
        return revision

    def _load_json_file(self, path: Path) -> Optional[Dict[str, Any]]:
        data = load_json_dict(path)
        if data is None and path.exists():
            logger.warning(f"Failed to load JSON file {path}")
        return data

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
