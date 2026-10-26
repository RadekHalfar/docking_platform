"""Smoke tests for the NiceGUI page shell (Phase 16, Stage 16.3).

nicegui is only installed in the ui image, so this file skips under the
default suite/CI and really runs via

    docker compose run --rm --no-deps ui \
        python -m pytest -c ui_nice/pytest.ini tests/test_nicegui_*.py

Isolation comes from tests/nicegui_harness.py (loaded by that config).
The Streamlit-to-NiceGUI test mapping is in docs/reviews/stage_16_8_parity.md.
"""
import pytest

pytest.importorskip("nicegui")

import httpx  # noqa: E402  (a nicegui dependency; absent from other images)
from nicegui import app as nicegui_app  # noqa: E402
from nicegui import ui  # noqa: E402
from nicegui.testing import User  # noqa: E402

import ui_nice.layout as layout  # noqa: E402
from ui.core.state import GREETING_MESSAGE  # noqa: E402


async def test_health_route(user: User):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(nicegui_app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


async def test_page_shows_title_tabs_and_greeting(user: User):
    await user.open("/")
    await user.should_see(layout.PAGE_TITLE)
    for tab in (layout.TAB_SETUP, layout.TAB_RESULTS, layout.TAB_SESSIONS):
        await user.should_see(tab)
    # The greeting's first line, as rendered markdown.
    await user.should_see("Describe your docking workflow")
    assert GREETING_MESSAGE["content"].startswith("Hello! Describe your docking workflow")


async def test_setup_tab_has_a_splitter(user: User):
    await user.open("/")
    assert len(user.find(ui.splitter).elements) == 1


async def test_settings_button_opens_the_dialog(user: User):
    # Asserted on the dialog's value: the User fixture's visibility check
    # reads element.visible, which a closed (but once opened) dialog keeps.
    await user.open("/")
    dialog = next(iter(user.find(marker="settings-dialog").elements))
    assert dialog.value is False
    user.find(marker="settings-button").click()
    assert dialog.value is True
    await user.should_see("LLM settings")
    user.find("Close").click()
    assert dialog.value is False


async def test_chat_toggle_collapses_and_reopens_the_drawer(user: User):
    await user.open("/")
    drawer = next(iter(user.find(ui.right_drawer).elements))
    assert drawer.value is True
    user.find(marker="chat-toggle").click()
    assert drawer.value is False
    user.find(marker="chat-toggle").click()
    assert drawer.value is True


async def test_each_client_gets_its_own_state(page, create_user):
    # tests/nicegui_harness.py's page fixture records each page build.
    first, second = create_user(), create_user()
    await first.open("/")
    await second.open("/")
    assert len(page.states) == 2
    a, b = page.states
    assert a is not b
    assert a.form is not b.form and a.run_log is not b.run_log
    a.messages.append({"role": "user", "content": "only for the first client"})
    assert b.messages == [GREETING_MESSAGE]
