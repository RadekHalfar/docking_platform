"""Tests for ui/session_state.py's GuiSession view (Stage 16.1).

The Streamlit page passes ``gui_session()`` -- a view of ``st.session_state``
-- to the ``ui/core`` functions in place of a ``GuiSession``. These check
that the view has every ``GuiSession`` attribute, under the page's
long-standing keys. Same streamlit-only invocation as
tests/test_ui_session_state.py.
"""
import dataclasses

import pytest

st = pytest.importorskip("streamlit")

from ui import session_state
from ui.core.state import GuiSession
from ui.worker_cap import WorkerCap


@pytest.fixture(autouse=True)
def _clean_session_state():
    st.session_state.clear()
    yield
    st.session_state.clear()


def test_view_maps_every_gui_session_field():
    fields = {f.name for f in dataclasses.fields(GuiSession)}
    computed = {"form", "worker_cap"}  # properties of the view, not session_state keys
    assert set(session_state._STATE_KEYS) == fields - computed


def test_view_reads_and_writes_the_existing_keys():
    view = session_state.gui_session()
    view.job_done = True
    view.pending_rerun = {"steps": ["dock"]}
    assert st.session_state["_job_done"] is True
    assert st.session_state["_pending_rerun"] == {"steps": ["dock"]}
    assert view.pending_rerun == {"steps": ["dock"]}


def test_none_removes_absent_style_keys_but_stores_plain_ones():
    view = session_state.gui_session()
    view.pending_rerun = {"steps": ["dock"]}
    view.pending_rerun = None
    view.bridge = None
    assert "_pending_rerun" not in st.session_state
    assert "bridge" in st.session_state and st.session_state["bridge"] is None


def test_absent_keys_read_as_defaults():
    view = session_state.gui_session()
    assert view.job_name == ""
    assert view.job_done is False
    assert view.save_conflict is None


def test_form_is_session_state_and_cap_is_read_live(monkeypatch):
    view = session_state.gui_session()
    assert view.form is st.session_state
    monkeypatch.setattr(session_state, "WORKER_CAP", WorkerCap(3, "compose"))
    assert view.worker_cap == WorkerCap(3, "compose")
    assert view.form_defaults is session_state._WIDGET_DEFAULTS


def test_unknown_attribute_raises():
    view = session_state.gui_session()
    with pytest.raises(AttributeError):
        view.no_such_field
    with pytest.raises(AttributeError):
        view.no_such_field = 1
