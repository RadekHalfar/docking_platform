"""NiceGUI GUI entry point (Phase 16). Run: ``python ui_nice/app.py`` with
the repo root on ``PYTHONPATH`` (the container sets ``/workspace``).

Serves the page at ``/`` and a ``/health`` route for the compose
healthcheck. Each browser client gets its own :class:`GuiSession` when its
page is built; nothing per-user lives at module level (finding 8).
"""
from __future__ import annotations

import os
from pathlib import Path

from nicegui import app, ui

from ui.core.state import GuiSession
from ui.settings import build_llm_client, load_settings
from ui.worker_cap import detect_moldina_worker_cap
from ui_nice.layout import PAGE_TITLE, build_page
from ui_nice.mol_viewer import serve_vendor_files

PORT = 3001

# Read once from docker-compose.yml / the environment: shared config, not
# per-user state.
WORKER_CAP = detect_moldina_worker_cap()
OUTPUT_BASE = Path(os.getenv("OUTPUT_BASE", "/workspace/orchestrator_output"))

serve_vendor_files()


@app.get("/health")
def health() -> dict:
    return {"status": "healthy"}


@ui.page("/", title=PAGE_TITLE)
def index() -> None:
    # Settings are re-read per client: another tab may have saved new ones.
    settings = load_settings()
    state = GuiSession(worker_cap=WORKER_CAP, settings=settings, llm_client=build_llm_client(settings))
    build_page(state, OUTPUT_BASE)


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(host="0.0.0.0", port=PORT, reload=False, show=False, title=PAGE_TITLE, favicon="🧬")
