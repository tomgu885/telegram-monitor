import asyncio
from unittest.mock import AsyncMock

import pytest

from telegram_monitor.config import ButtonMatch
from telegram_monitor.exceptions import MonitorError
from telegram_monitor.matcher import require_navigation, select_button
from telegram_monitor.menu import Menu
from telegram_monitor.models import Button
from telegram_monitor.state import CursorStore, session_lock

from .conftest import message


@pytest.mark.parametrize(
    ("mode", "text"),
    [
        ("exact", "🚀 payment-rpc-testa"),
        ("contains", "payment-rpc-testa"),
        ("regex", r"payment-rpc-testa$"),
    ],
)
def test_matching_including_emoji(mode, text):
    button = Button(2, 1, "🚀 payment-rpc-testa")
    assert select_button([button], rule=ButtonMatch(match=mode, text=text)) == button


def test_index_is_row_major_and_zero_based():
    buttons = [Button(0, 0, "a"), Button(0, 1, "b"), Button(1, 0, "c")]
    assert select_button(buttons, index=2).text == "c"


def test_ambiguous_match_does_not_guess():
    with pytest.raises(MonitorError) as exc:
        select_button([Button(0, 0, "RPC A"), Button(0, 1, "RPC B")], rule=ButtonMatch(text="RPC"))
    assert exc.value.code == 32


def test_missing_button_lists_actual_options():
    with pytest.raises(MonitorError) as exc:
        select_button([Button(0, 0, "finance-rpc")], rule=ButtonMatch(text="payment-rpc"))
    assert exc.value.payload() == {
        "status": "error",
        "error": "button_not_found",
        "expected": "payment-rpc",
        "available_buttons": ["finance-rpc"],
    }


@pytest.mark.parametrize(
    "label",
    [
        "发布",
        "删除",
        "回滚",
        "重启",
        "确认",
        "Deploy",
        "destroy",
        "rollback",
        "de\u200bploy",
        "ｄｅｌｅｔｅ",
    ],
)
def test_dangerous_label_denied_even_when_allowlisted(label):
    with pytest.raises(MonitorError) as exc:
        require_navigation(Button(0, 0, label), [label])
    assert exc.value.code == 33


def test_unknown_and_url_buttons_are_not_navigation():
    for button in [Button(0, 0, "unknown"), Button(0, 0, "safe", "unsupported")]:
        with pytest.raises(MonitorError):
            require_navigation(button, ["safe"])


def service(configuration, tmp_path, client):
    return Menu(
        client,
        configuration.environment("testa"),
        configuration.target("deployment"),
        CursorStore(tmp_path / "state", "test"),
        0.1,
    )


def test_menu_path_and_persistent_cursor(configuration, tmp_path):
    client = AsyncMock()
    first = message()
    second = message(101, buttons=[Button(0, 0, "🚀 payment-rpc-testa")])
    final = message(102, buttons=[Button(0, 0, "发布")])
    client.open_menu.return_value = first
    client.get_message.side_effect = [first, second, final]
    client.click_button.side_effect = [second, final]
    menu = service(configuration, tmp_path, client)

    async def run():
        assert (await menu.open())["message_id"] == 100
        # A fresh business object reads the prior invocation's cursor.
        next_menu = service(configuration, tmp_path, client)
        assert (await next_menu.click(None, rule=ButtonMatch(text="RPC")))["status"] == "ok"
        assert (await next_menu.click(None, index=0))["next_message"]["message_id"] == 102
        with pytest.raises(MonitorError, match="action_not_allowed"):
            await next_menu.click(None, index=0)

    asyncio.run(run())
    assert client.click_button.await_count == 2
    assert menu.store.path.stat().st_mode & 0o777 == 0o600


def test_changed_menu_is_refused_until_explicit_refresh(configuration, tmp_path):
    client = AsyncMock()
    client.open_menu.return_value = message()
    client.get_message.return_value = message(revision="changed")
    menu = service(configuration, tmp_path, client)

    async def run():
        await menu.open()
        with pytest.raises(MonitorError, match="unexpected_menu"):
            await menu.click(None, index=0)
        await menu.buttons(None)
        assert menu.store.read()["revision"] == "changed"

    asyncio.run(run())
    client.click_button.assert_not_awaited()


def test_timeout_invalidates_cursor(configuration, tmp_path):
    client = AsyncMock()
    client.open_menu.return_value = client.get_message.return_value = message()
    client.click_button.side_effect = MonitorError("menu_not_found")
    menu = service(configuration, tmp_path, client)

    async def run():
        await menu.open()
        with pytest.raises(MonitorError):
            await menu.click(None, index=0)
        assert not menu.store.path.exists()

    asyncio.run(run())


def test_session_lock_is_exclusive_and_released(tmp_path):
    path = tmp_path / "state/test.session"
    with session_lock(path):
        with pytest.raises(MonitorError, match="session_busy"), session_lock(path):
            pass
    with session_lock(path):
        pass
