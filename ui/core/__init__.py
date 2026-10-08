"""Framework-free GUI logic (Phase 16).

Every function here takes the GUI's state as a :class:`ui.core.state.GuiSession`
(or the Streamlit adapter's view of ``st.session_state`` with the same
attributes, ``ui.session_state.gui_session()``) and never imports a GUI
framework -- the Streamlit page and the NiceGUI app both drive these, and
``tests/test_ui_core_*.py`` run them in the hermetic suite.
"""
