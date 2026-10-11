"""Framework-free GUI logic (Phase 16).

Every function here takes the GUI's state as a :class:`ui.core.state.GuiSession`
(or the Streamlit adapter's view of ``st.session_state`` with the same
attributes, ``ui.session_state.gui_session()``) and never imports a GUI
framework -- the Streamlit page and the NiceGUI app both drive these, and
``tests/test_ui_core_*.py`` run them in the hermetic suite.

Comments here that mention Streamlit ("runs before the layout", "must run
before the widgets render", "rerun") describe the *Streamlit caller's*
constraints, not rules of the core; a NiceGUI caller has none of them.
DEFERRED (Review Stage R1, docs/reviews/stage_16_r1.md, deferred #6):
whoever wires a NiceGUI caller to a function with such a comment (Stages
16.3-16.7) moves the remark to the Streamlit call site in ``ui/app.py`` or
rewords it; 16.8's repo-wide grep for "streamlit" catches the rest.
"""
