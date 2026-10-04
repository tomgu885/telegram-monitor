import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon import Button, errors
from telethon.tl import types

from telegram_monitor.exceptions import MonitorError
from telegram_monitor.telegram_client import TelethonMenuClient, connect, login, snapshot


def raw_message(
    message_id=100,
    text="menu",
    sender_id=42,
    chat_id=-100123,
    reply=None,
    labels=("RPC 服务",),
    edit_date=None,
    callback=b"navigate",
):
    return SimpleNamespace(
        id=message_id,
        raw_text=text,
        chat_id=chat_id,
        sender_id=sender_id,
        date=datetime.now(UTC),
        edit_date=edit_date,
        reply_to_msg_id=reply,
        buttons=[
            [SimpleNamespace(text=label, button=Button.inline(label, callback))] for label in labels
        ],
        click=AsyncMock(),
    )


class FakeTelegram:
    def __init__(self):
        self.handlers = []
        self.source = raw_message()
        self.sent = []
        self.get_entity = AsyncMock(
            return_value=SimpleNamespace(id=42, username="example_bot", bot=True)
        )
        self.get_input_entity = AsyncMock(return_value=-100123)

    def add_event_handler(self, callback, builder):
        self.handlers.append(callback)

    def remove_event_handler(self, callback):
        self.handlers = [h for h in self.handlers if h != callback]

    async def get_messages(self, peer, *, ids=None, limit=None):
        return self.source if ids else [self.source]

    async def deliver(self, message):
        await self.handlers[0](SimpleNamespace(message=message))

    async def send_message(self, peer, command, **kwargs):
        assert self.handlers  # Must subscribe before sending the command.
        self.sent.append(command)
        await self.deliver(raw_message(102, reply=101))
        return SimpleNamespace(id=101)


def adapter(configuration, client):
    result = TelethonMenuClient(client, configuration.target("deployment"))
    result.peer = -100123
    result.bot = SimpleNamespace(id=42, username="example_bot")
    return result


def test_response_before_send_returns_is_not_lost(configuration):
    fake = FakeTelegram()
    result = asyncio.run(adapter(configuration, fake).open_menu("/menu", 0.1))
    assert result.message_id == 102
    assert not fake.handlers


def test_wrong_sender_chat_old_messages_and_other_replies_are_ignored(configuration):
    fake = FakeTelegram()

    async def send(*args, **kwargs):
        for value in [
            raw_message(102, sender_id=99),
            raw_message(103, chat_id=-100999),
            raw_message(99),
            raw_message(104, reply=999),
        ]:
            await fake.deliver(value)
        old = raw_message(105)
        old.date -= timedelta(days=1)
        await fake.deliver(old)
        await fake.deliver(raw_message(106, reply=101))
        return SimpleNamespace(id=101)

    fake.send_message = send
    assert asyncio.run(adapter(configuration, fake).open_menu("/menu", 0.1)).message_id == 106


def test_original_message_edit_is_a_menu_response(configuration):
    fake = FakeTelegram()
    source = snapshot(fake.source, "example_bot")

    async def click(**kwargs):
        await fake.deliver(raw_message(100, labels=("payment-rpc",), edit_date=datetime.now(UTC)))

    fake.source.click.side_effect = click
    result = asyncio.run(adapter(configuration, fake).click_button(source, source.buttons[0], 0.1))
    assert result.message_id == 100
    assert result.buttons[0].text == "payment-rpc"
    fake.source.click.assert_awaited_once_with(i=0, j=0)
    assert not fake.handlers


def test_callback_data_change_prevents_stale_click(configuration):
    fake = FakeTelegram()
    source = snapshot(fake.source, "example_bot")
    fake.source = raw_message(callback=b"changed-operation")
    with pytest.raises(MonitorError, match="unexpected_menu"):
        asyncio.run(adapter(configuration, fake).click_button(source, source.buttons[0], 0.1))
    fake.source.click.assert_not_awaited()
    assert not fake.handlers


def test_timeout_and_rpc_failure_remove_handlers(configuration):
    for failure in [None, errors.FloodWaitError(None, capture=17)]:
        fake = FakeTelegram()
        fake.send_message = AsyncMock(return_value=SimpleNamespace(id=101), side_effect=failure)
        with pytest.raises(errors.FloodWaitError if failure else MonitorError):
            asyncio.run(adapter(configuration, fake).open_menu("/menu", 0.01))
        assert not fake.handlers
        fake.send_message.assert_awaited_once()


def test_message_from_non_bot_cannot_be_clicked(configuration):
    fake = FakeTelegram()
    fake.source.sender_id = 99
    with pytest.raises(MonitorError, match="unexpected_menu"):
        asyncio.run(adapter(configuration, fake).get_message(100))


def test_callback_payload_not_serialized():
    result = snapshot(raw_message(callback=b"private-callback-data")).payload()
    assert "private-callback-data" not in str(result)
    assert "revision" not in result


def test_login_uses_existing_session_without_prompt(monkeypatch):
    client = SimpleNamespace(is_user_authorized=AsyncMock(return_value=True))
    monkeypatch.setattr(
        "telegram_monitor.telegram_client.hidden_prompt", lambda _: pytest.fail("unexpected prompt")
    )
    asyncio.run(login(client))


def test_login_code_and_2fa_flow(monkeypatch):
    client = SimpleNamespace(
        is_user_authorized=AsyncMock(return_value=False),
        send_code_request=AsyncMock(return_value=SimpleNamespace(phone_code_hash="fake-hash")),
        sign_in=AsyncMock(side_effect=[errors.SessionPasswordNeededError(None), None]),
    )
    answers = iter(["+123456789", "123456", " fake-password "])
    monkeypatch.setattr("telegram_monitor.telegram_client.hidden_prompt", lambda _: next(answers))
    asyncio.run(login(client))
    client.sign_in.assert_awaited_with(password=" fake-password ")


def test_real_telethon_button_types_reject_contact_location_and_url():
    raw = raw_message()
    buttons = [
        Button.inline("navigate", b"next"),
        Button.text("navigate").button,
        Button.request_phone("navigate").button,
        Button.request_location("navigate").button,
        Button.url("navigate", "https://example.com"),
    ]
    raw.buttons = [[SimpleNamespace(text=b.text, button=b) for b in buttons]]
    assert [b.kind for b in snapshot(raw).buttons] == [
        "callback",
        "keyboard",
        "unsupported",
        "unsupported",
        "unsupported",
    ]


def test_snapshot_with_real_telethon_message():
    raw = types.Message(
        id=100,
        peer_id=types.PeerChannel(123),
        from_id=types.PeerUser(42),
        date=datetime.now(UTC),
        message="menu",
        reply_markup=types.ReplyInlineMarkup(
            [types.KeyboardInlineButtonRow([Button.inline("RPC", b"next")])]
        ),
    )
    # Telethon normally supplies its client and cached input peer when fetching messages.
    raw._client = SimpleNamespace()
    raw._input_chat = types.InputPeerChannel(123, 0)
    raw._sender = types.User(id=42, bot=True, access_hash=0)
    result = snapshot(raw, "example_bot")
    assert result.chat_id == -1000000000123
    assert result.buttons[0].kind == "callback"


def test_connection_failure_disconnects_and_releases_lock(configuration, monkeypatch):
    from telegram_monitor.state import session_lock

    monkeypatch.setenv("TELEGRAM_API_HASH", "a" * 32)
    client = AsyncMock()
    client.connect.side_effect = ConnectionError("offline")
    monkeypatch.setattr("telegram_monitor.telegram_client.TelegramClient", lambda *a, **k: client)

    async def run():
        with pytest.raises(ConnectionError):
            async with connect(configuration):
                pytest.fail("connected unexpectedly")

    asyncio.run(run())
    client.disconnect.assert_awaited_once()
    with session_lock(configuration.session):
        pass


def test_watch_streams_new_and_edit_messages_and_unregisters(configuration):
    async def run():
        fake = FakeTelegram()
        fake.disconnected = asyncio.get_running_loop().create_future()
        values = []
        task = asyncio.create_task(adapter(configuration, fake).watch(values.append, "example_bot"))
        await asyncio.sleep(0)
        for sender_id, edit in [(99, None), (42, None), (42, datetime.now(UTC))]:
            event = SimpleNamespace(
                message=raw_message(sender_id=sender_id, edit_date=edit),
                get_sender=AsyncMock(return_value=SimpleNamespace(username="example_bot")),
            )
            await fake.handlers[0](event)
        assert [item["event"] for item in values] == ["message_new", "message_edited"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not fake.handlers

    asyncio.run(run())
