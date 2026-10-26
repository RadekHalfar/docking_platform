"""Small widget helpers shared by the NiceGUI views (Phase 16)."""
from __future__ import annotations

from nicegui import ui

# markdown2's "code-friendly": "_" and "__" are no emphasis, only "*" and
# "**" are. Without it a file name or step such as check164_crystal_ligand
# or prepare_ligands lost its underscores to italics (found in Stage 16.4's
# real-data check). The chat replies only ever use "*" for emphasis.
MARKDOWN_EXTRAS = [*ui.markdown.default_extras, "code-friendly"]


def markdown(content: str = "") -> ui.markdown:
    """A ``ui.markdown`` for chat-style text: "_" inside a word stays."""
    return ui.markdown(content, extras=MARKDOWN_EXTRAS)
