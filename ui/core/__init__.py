"""Framework-free GUI logic (Phase 16).

Every function here takes the GUI's state as a :class:`ui.core.state.GuiSession`
and never imports a GUI framework: the NiceGUI app (``ui_nice/``) draws
them, and ``tests/test_ui_core_*.py`` run them in the hermetic suite.
"""
