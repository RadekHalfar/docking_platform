"""The NiceGUI box picker and 3Dmol viewer element (Phase 16, Stage 16.5).

Ports the box-picker rows of the parity table (tests/test_nicegui_parity.py)
from tests/test_ui_apptest.py; each ported test keeps its AppTest name.
Where AppTest asserted on its viewer recorder, these read what the
:class:`~ui_nice.mol_viewer.MolViewer` element last sent to the browser.
Runs in the ui-nice tier only:

    docker compose run --rm --no-deps ui-nice \
        python -m pytest -c ui_nice/pytest.ini tests/test_nicegui_*.py
"""
import asyncio

import pytest

pytest.importorskip("nicegui")

from nicegui.testing import User  # noqa: E402

from common.pdb_geometry import compute_protein_center_and_box  # noqa: E402
from ui.core.form import BOX_FORM_KEYS  # noqa: E402
from ui_nice.mol_viewer import MolViewer  # noqa: E402
from tests.nicegui_harness import (  # noqa: E402
    active_session_with_two_revisions,
    atom_click,
    click,
    element,
    eventually,
    load_picker_receptor,
    send_chat,
    set_value,
    viewer,
    viewer_shown,
)
from tests.ui_fakes import FIXTURE_PDB, FakeBridge, RunningBridge, prep_done_bridge  # noqa: E402

PARSER = "services.llm_orchestrator.workflow_llm_parser"


def _summary(user) -> str:
    return element(user, "params-summary").content


def _field(user, key):
    return element(user, key).value


async def _picker_with_box_set(user, page):
    load_picker_receptor(page, ("1.0", "2.0", "3.0"), ("20.0", "20.0", "20.0"))
    await user.open("/")
    return await viewer(user)


async def _settle() -> None:
    """Let a few timer ticks pass (50 ms each under the harness)."""
    await asyncio.sleep(0.2)


# ---------------------------------------------------------------------------
# Box picker (ported)
# ---------------------------------------------------------------------------

async def test_box_picker_passes_center_and_size_to_viewer(user: User, page):
    load_picker_receptor(page, ("1.0", "2.0", "3.0"), ("22.0", "24.0", "26.0"))
    await user.open("/")

    shown = await viewer(user)

    assert shown.mode == "box_picker"
    assert shown.center == [1.0, 2.0, 3.0]
    assert shown.box_size == [22.0, 24.0, 26.0]
    assert "ATOM" in shown.receptor_pdb
    assert element(user, "box-picker-caption").text == "Box center: (1.00, 2.00, 3.00)"


async def test_box_picker_without_receptor_offers_load_button(user: User, page):
    await user.open("/")

    await user.should_see("Load receptor for box picking")
    assert not viewer_shown(user)


async def test_deferred_geometry_command_applies_and_shows_reply(user: User, page):
    # A chat geometry command started a fetch/prepare job (no receptor was
    # loaded yet); that job has now finished. The page applies the pending
    # target, drops the bridge, replies in chat and draws the new center.
    expected_center, _ = compute_protein_center_and_box(FIXTURE_PDB.read_text())

    @page.seed
    def _(state):
        state.box_picker_bridge = prep_done_bridge()
        state.pending_geometry_targets = ("center",)

    await user.open("/")

    assert page.state.box_picker_bridge is None
    assert page.state.pending_geometry_targets is None
    assert page.chat_texts()[-1].startswith("Protein center set to")
    assert float(_field(user, "param_center_x")) == expected_center[0]
    assert (await viewer(user)).center == expected_center


async def test_deferred_geometry_command_reports_failed_fetch(user: User, page):
    @page.seed
    def _(state):
        state.box_picker_bridge = FakeBridge(result={"status": "failed", "errors": ["RCSB download failed"]})
        state.pending_geometry_targets = ("box_size",)
        state.form.update(input_mode="rcsb", param_protein_id="6lu7")

    await user.open("/")

    error = element(user, "box-picker-error")
    assert error.visible and error.text == "Could not prepare receptor: RCSB download failed"
    assert page.chat_texts()[-1] == "Could not fetch the protein: RCSB download failed"
    # Streamlit showed it for one rerun; here it stays until the next Load.
    click(user, "load-receptor")
    await eventually(lambda: not element(user, "box-picker-error").visible)


async def test_box_size_step_updates_summary_and_viewer(user: User, page):
    # The picker's + button writes the Docking Box field (Stage 13.5); the
    # summary and the viewer both follow it.
    shown = await _picker_with_box_set(user, page)

    click(user, "box-step-x-up")

    assert "**Box:** 21.000 / 20.0 / 20.0" in _summary(user)
    assert shown.box_size == [21.0, 20.0, 20.0]
    assert _field(user, "param_box_x") == "21.000"

    click(user, "box-step-x-down")
    click(user, "box-step-x-down")

    assert _field(user, "param_box_x") == "19.000"
    assert shown.box_size == [19.0, 20.0, 20.0]


async def test_summary_updates_after_visual_center_pick(user: User, page):
    load_picker_receptor(page, ("", "", ""), ("20.0", "20.0", "20.0"))
    await user.open("/")
    await viewer(user)

    atom_click(user, [4.0, 5.0, 6.0])

    assert "**Center:** 4.000 / 5.000 / 6.000" in _summary(user)


async def test_typing_center_in_docking_box_moves_picker_box(user: User, page):
    shown = await _picker_with_box_set(user, page)

    set_value(user, "param_center_x", "7.5")

    assert shown.center == [7.5, 2.0, 3.0]
    # The typed value stays as typed -- not overwritten from the picker.
    await _settle()
    assert _field(user, "param_center_x") == "7.5"


async def test_typing_box_size_in_docking_box_resizes_picker_box(user: User, page):
    shown = await _picker_with_box_set(user, page)

    set_value(user, "param_box_x", "30")

    assert shown.box_size == [30.0, 20.0, 20.0]
    assert _field(user, "param_box_x") == "30"


@pytest.mark.parametrize("typed", ["-1", "0", "abc", ""])
async def test_undrawable_box_size_shows_placeholder_without_crash(user: User, page, typed):
    shown = await _picker_with_box_set(user, page)
    assert not element(user, "box-picker-placeholder").visible

    set_value(user, "param_box_x", typed)

    assert shown.box_size == [20.0, 20.0, 20.0]  # the placeholder
    placeholder = element(user, "box-picker-placeholder")
    assert placeholder.visible and "20 Å placeholder" in placeholder.text
    assert _field(user, "param_box_x") == typed


async def test_small_positive_box_size_is_drawn_as_typed(user: User, page):
    shown = await _picker_with_box_set(user, page)

    set_value(user, "param_box_x", "0.5")

    assert shown.box_size == [0.5, 20.0, 20.0]


async def test_typed_center_survives_after_a_viewer_click(user: User, page):
    # Streamlit's component kept returning its last click, which once
    # reverted a typed center to the clicked atom. A click here is an event,
    # sent once; a typed center must still survive the next redraws.
    shown = await _picker_with_box_set(user, page)
    atom_click(user, [4.0, 5.0, 6.0])
    assert page.state.form["param_center_x"] == "4.000"

    set_value(user, "param_center_x", "7.5")
    await _settle()

    assert _field(user, "param_center_x") == "7.5"
    assert shown.center == [7.5, 5.0, 6.0]


async def test_clicking_the_same_atom_again_after_typing_is_applied(user: User, page):
    shown = await _picker_with_box_set(user, page)
    atom_click(user, [4.0, 5.0, 6.0])
    set_value(user, "param_center_x", "7.5")

    atom_click(user, [4.0, 5.0, 6.0])

    assert page.state.form["param_center_x"] == "4.000"
    assert _field(user, "param_center_x") == "4.000"
    assert shown.center == [4.0, 5.0, 6.0]


async def test_chat_parsed_box_moves_the_picker_box(user: User, page, monkeypatch):
    monkeypatch.setattr(
        f"{PARSER}.parse_workflow_with_llm",
        lambda client, message: {
            "services": ["dock"],
            "parameters": {"protein_id": "6lu7", "center": [9.0, 9.0, 9.0], "box_size": [25.0, 26.0, 27.0]},
        },
    )
    shown = await _picker_with_box_set(user, page)

    await send_chat(user, page, "dock 6lu7 with center 9 9 9 box 25 26 27")

    assert shown.center == [9.0, 9.0, 9.0]
    assert shown.box_size == [25.0, 26.0, 27.0]


async def test_size_step_with_size_unset_draws_the_stepped_box(user: User, page):
    # X + on empty Box size fields wrote X only; the viewer still couldn't
    # draw the box, so it kept the placeholder and the button seemed dead.
    load_picker_receptor(page, ("1.0", "2.0", "3.0"), ("", "", ""))
    await user.open("/")
    shown = await viewer(user)

    click(user, "box-step-x-up")

    assert shown.box_size == [21.0, 20.0, 20.0]
    assert [_field(user, k) for k in BOX_FORM_KEYS[3:]] == ["21.000", "20.000", "20.000"]


async def test_click_on_undrawable_box_docks_the_box_shown(user: User, page):
    # Fields "0, 20, 20": the viewer drew the 20 Å placeholder, but a click
    # kept the 0 -- the box docked wasn't the box shown.
    load_picker_receptor(page, ("", "", ""), ("0", "20", "20"))
    await user.open("/")
    shown = await viewer(user)

    atom_click(user, [4.0, 5.0, 6.0])

    assert [page.state.form[k] for k in BOX_FORM_KEYS[3:]] == ["20.000"] * 3
    assert shown.box_size == [20.0, 20.0, 20.0]


# ---------------------------------------------------------------------------
# Box picker panel (new in NiceGUI)
# ---------------------------------------------------------------------------

async def test_load_without_protein_says_what_is_missing(user: User, page):
    await user.open("/")

    click(user, "load-receptor")

    message = element(user, "box-picker-message")
    assert message.visible and message.text.startswith("Provide a protein source first")
    assert page.state.box_picker_bridge is None


async def test_load_prepares_the_receptor_and_shows_the_viewer(user: User, page, monkeypatch):
    bridges = []

    def bridge():
        bridges.append(prep_done_bridge())
        return bridges[-1]

    monkeypatch.setattr("ui.orchestrator_bridge.OrchestratorBridge", bridge)
    page.seed(lambda state: state.form.update(input_mode="rcsb", param_protein_id="6lu7"))
    await user.open("/")

    click(user, "load-receptor")

    shown = await viewer(user)
    assert "ATOM" in shown.receptor_pdb
    assert bridges[0].started_with[1]["services"] == ["get_input_files", "prepare_protein"]
    assert page.state.box_picker_receptor_pdb == str(FIXTURE_PDB)
    assert not element(user, "load-receptor").visible
    assert element(user, "box-picker-caption").text.startswith("Click a receptor atom")


async def test_running_prep_shows_progress_instead_of_the_picker(user: User, page):
    page.seed(lambda state: setattr(state, "box_picker_bridge", RunningBridge()))
    await user.open("/")

    await user.should_see("Preparing receptor for box picking...")
    assert not element(user, "load-receptor").visible


async def test_discard_returns_to_the_load_button(user: User, page):
    await _picker_with_box_set(user, page)

    click(user, "discard-receptor")

    await eventually(lambda: not viewer_shown(user))
    assert element(user, "load-receptor").visible
    assert page.state.box_picker_receptor_pdb is None
    # The box itself stays: it is the Docking Box fields.
    assert _field(user, "param_center_x") == "1.0"


async def test_receptor_gone_from_disk_offers_reload(user: User, page, tmp_path):
    page.seed(lambda state: setattr(state, "box_picker_receptor_pdb", str(tmp_path / "gone.pdb")))
    await user.open("/")

    await user.should_see("Prepared receptor no longer on disk — reload it.")
    assert element(user, "reload-receptor").visible
    assert not element(user, "load-receptor").visible
    await _settle()
    assert not viewer_shown(user)


async def test_new_session_drops_the_picked_receptor(user: User, page, output_base):
    @page.seed
    def _(state):
        active_session_with_two_revisions(state, output_base)
        state.box_picker_receptor_pdb = str(FIXTURE_PDB)

    await user.open("/")
    await viewer(user)

    click(user, "new-session")

    await eventually(lambda: not viewer_shown(user))
    assert element(user, "load-receptor").visible


# ---------------------------------------------------------------------------
# The viewer element
# ---------------------------------------------------------------------------

LIGANDS = {"ATP": {"sdf": "ATP\n  pose\n\nM  END\n$$$$\n", "affinity": -7.1}}


def _sends(monkeypatch, element_):
    calls = []
    monkeypatch.setattr(element_, "run_method", lambda name, *args, **kw: calls.append((name, args)))
    return calls


async def test_view_mode_keeps_the_scene_and_swaps_only_the_ligands(user: User, monkeypatch):
    await user.open("/")
    with user.client:
        mol = MolViewer(FIXTURE_PDB.read_text(), LIGANDS)
    sends = _sends(monkeypatch, mol)

    assert (mol.mode, mol.ligands, mol.center) == ("view", LIGANDS, None)
    assert mol.props["ligands"] == LIGANDS

    mol.set_ligands(LIGANDS)  # unchanged: nothing sent
    other = {"ADP": {"sdf": "ADP\n", "affinity": None}}
    mol.set_ligands(other)

    assert sends == [("setLigands", (other,))]
    assert mol.ligands == other and mol.props["ligands"] == other
    assert "ATOM" in mol.props["receptor-pdb"]  # the scene itself is untouched


async def test_set_box_sends_only_a_changed_box(user: User, monkeypatch):
    await user.open("/")
    with user.client:
        mol = MolViewer(FIXTURE_PDB.read_text(), mode="box_picker", center=[1, 2, 3], box_size=[20, 20, 20])
    sends = _sends(monkeypatch, mol)

    mol.set_box([1, 2, 3], [20, 20, 20])
    mol.set_box([1, 2, 3], [21, 20, 20])

    assert sends == [("setBox", ([1, 2, 3], [21, 20, 20]))]
    assert mol.props["box-size"] == [21, 20, 20]


def test_unknown_viewer_mode_is_rejected():
    with pytest.raises(ValueError):
        MolViewer("", mode="picker")
