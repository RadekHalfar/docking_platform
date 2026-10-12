"""NiceGUI GUI entry point (Phase 16). Run: ``python ui_nice/app.py`` with
the repo root on ``PYTHONPATH`` (the container sets ``/workspace``).

Serves the page at ``/`` and a ``/health`` route for the compose
healthcheck. Each browser client gets its own :class:`GuiSession` when its
page is built; nothing per-user lives at module level (finding 8).
"""
from __future__ import annotations

from nicegui import app, ui

from ui.core.state import GuiSession
from ui.worker_cap import detect_moldina_worker_cap
from ui_nice.layout import PAGE_TITLE, build_page

PORT = 3001

# Read once from docker-compose.yml: shared config, not per-user state.
WORKER_CAP = detect_moldina_worker_cap()


@app.get("/health")
def health() -> dict:
    return {"status": "healthy"}


@ui.page("/", title=PAGE_TITLE)
def index() -> None:
    build_page(GuiSession(worker_cap=WORKER_CAP))


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(host="0.0.0.0", port=PORT, reload=False, show=False, title=PAGE_TITLE, favicon="🧬")
