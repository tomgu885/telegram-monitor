"""MTProto adapter. Subscribe before sending, never retry mutating RPCs."""

import asyncio
import getpass
import hashlib
import json
import logging
import os
import signal
import sys
import warnings
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from telethon import TelegramClient, errors, events
from telethon.tl import types

from telegram_monitor.config import Configuration, Target
from telegram_monitor.exceptions import MonitorError
from telegram_monitor.models import Button, Message
from telegram_monitor.state import session_lock

logger = logging.getLogger(__name__)


def hidden_prompt(label: str) -> str:
    if not sys.stdin.isatty():
        raise MonitorError("telegram_auth_error", reason="interactive_terminal_required")

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    # Synchronous terminal input cannot process asyncio's cancellation callback.
    previous = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                return getpass.getpass(label, stream=sys.stderr)
            except (getpass.GetPassWarning, EOFError):
                raise MonitorError(
                    "telegram_auth_error", reason="hidden_input_unavailable"
                ) from None
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


async def login(client):
    if await client.is_user_authorized():
        return
    phone = hidden_prompt("Phone (including country code): ").strip()
    sent = await client.send_code_request(phone)
    for _ in range(3):
        try:
            await client.sign_in(
                phone,
                hidden_prompt("Telegram code: ").strip(),
                phone_code_hash=sent.phone_code_hash,
            )
            return
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeEmptyError):
            logger.warning("Invalid login code; retry.")
        except errors.SessionPasswordNeededError:
            for _ in range(3):
                try:
                    await client.sign_in(password=hidden_prompt("Telegram 2FA password: "))
                    return
                except errors.PasswordHashInvalidError:
                    logger.warning("Invalid 2FA password; retry.")
            break
    raise MonitorError("telegram_auth_error", reason="login_attempts_exhausted")


@asynccontextmanager
async def connect(config: Configuration, *, require_auth: bool = True):
    api_id, api_hash = config.credentials()
    with session_lock(config.session):
        # Session SQLite journals inherit this umask. CLI calls are one per process.
        previous_umask = os.umask(0o077)
        client = None
        try:
            if config.session.exists():
                config.session.chmod(0o600)
            client = TelegramClient(
                str(config.session),
                api_id,
                api_hash,
                flood_sleep_threshold=0,
                request_retries=0,
                raise_last_call_error=True,
                connection_retries=1,
                retry_delay=1,
                auto_reconnect=True,
            )
            async with asyncio.timeout(config.settings.defaults.message_timeout_seconds):
                await client.connect()
                if require_auth and not await client.is_user_authorized():
                    raise MonitorError("telegram_auth_error", reason="run_auth_login")
            yield client
        finally:
            try:
                if client is not None:
                    await client.disconnect()
            finally:
                os.umask(previous_umask)


def snapshot(message, sender_username: str | None = None) -> Message:
    buttons = []
    signature = []
    for row, entries in enumerate(message.buttons or []):
        for column, button in enumerate(entries):
            raw = button.button
            kind = (
                "callback"
                if type(raw) is types.KeyboardInlineButton
                and type(raw.type) is types.InlineButtonTypeCallback
                else "keyboard"
                if type(raw) is types.KeyboardButton and type(raw.type) is types.ButtonTypeDefault
                else "unsupported"
            )
            buttons.append(Button(row, column, button.text, kind))
            # Hash callback data to detect menus whose labels stayed the same.
            signature.append([row, column, raw.to_dict()])
    revision = hashlib.sha256(
        json.dumps([message.raw_text, signature], default=str, sort_keys=True).encode()
    ).hexdigest()
    return Message(
        chat_id=message.chat_id,
        message_id=message.id,
        date=message.date,
        edit_date=message.edit_date,
        sender_id=message.sender_id,
        sender_username=sender_username,
        text=message.raw_text or "",
        reply_to_msg_id=message.reply_to_msg_id,
        buttons=buttons,
        revision=revision,
    )


class TelethonMenuClient:
    def __init__(self, client, target: Target):
        self.client = client
        self.target = target
        self.peer = None
        self.bot = None

    async def initialize(self):
        try:
            self.peer = await self.client.get_input_entity(self.target.chat_id)
        except ValueError:
            # Populate Telethon's entity cache for a new independent session.
            async for dialog in self.client.iter_dialogs():
                if dialog.id == self.target.chat_id:
                    self.peer = dialog.input_entity
                    break
            if self.peer is None:
                raise MonitorError("invalid_config", reason="chat_not_accessible") from None
        try:
            self.bot = await self.client.get_entity(self.target.bot_username)
        except ValueError:
            raise MonitorError("invalid_config", reason="bot_not_found") from None
        if not getattr(self.bot, "bot", False):
            raise MonitorError("invalid_config", reason="target_sender_is_not_a_bot")

    def matches(self, message) -> bool:
        return message.chat_id == self.target.chat_id and message.sender_id == self.bot.id

    async def get_message(self, message_id: int) -> Message:
        message = await self.client.get_messages(self.peer, ids=message_id)
        if message is None:
            raise MonitorError("menu_not_found")
        if not self.matches(message):
            raise MonitorError("unexpected_menu", reason="message_chat_or_sender_mismatch")
        return snapshot(message, self.bot.username)

    @asynccontextmanager
    async def updates(self):
        queue = asyncio.Queue(maxsize=1000)
        overflow = asyncio.Event()

        async def receive(event):
            if self.matches(event.message):
                if queue.full():
                    overflow.set()
                else:
                    queue.put_nowait(snapshot(event.message, self.bot.username))

        self.client.add_event_handler(receive, events.NewMessage(chats=self.peer))
        self.client.add_event_handler(receive, events.MessageEdited(chats=self.peer))
        try:
            yield queue, overflow
        finally:
            self.client.remove_event_handler(receive)

    async def transition(
        self, action, wait_seconds: float, source: Message | None = None
    ) -> Message:
        try:
            async with asyncio.timeout(wait_seconds), self.updates() as (queue, overflow):
                latest = await self.client.get_messages(self.peer, limit=1)
                boundary = latest[0].id if latest else 0
                started = datetime.now(UTC).replace(microsecond=0)
                sent_id = await action()
                while True:
                    message = await queue.get()
                    if overflow.is_set():
                        raise MonitorError("unexpected_menu", reason="menu_update_overflow")
                    if not message.buttons:
                        continue
                    timestamp = message.edit_date or message.date
                    if timestamp < started:
                        continue
                    if source and message.message_id == source.message_id:
                        if message.revision != source.revision:
                            return message
                        continue
                    if message.message_id <= max(boundary, sent_id or 0):
                        continue
                    expected_replies = {sent_id, source.message_id if source else None}
                    if message.reply_to_msg_id and message.reply_to_msg_id not in expected_replies:
                        continue
                    return message
        except TimeoutError:
            raise MonitorError(
                "menu_not_found", reason="menu_response_timeout", action_may_have_completed=True
            ) from None

    async def open_menu(self, command: str, wait_seconds: float) -> Message:
        async def send():
            sent = await self.client.send_message(self.peer, command, parse_mode=None)
            return sent.id

        return await self.transition(send, wait_seconds)

    async def click_button(self, message: Message, button: Button, wait_seconds: float) -> Message:
        async def click():
            raw = await self.client.get_messages(self.peer, ids=message.message_id)
            if raw is None or not self.matches(raw):
                raise MonitorError("unexpected_menu", reason="menu_disappeared")
            current = snapshot(raw, self.bot.username)
            if current.revision != message.revision or button not in current.buttons:
                raise MonitorError("unexpected_menu", reason="menu_changed_before_click")
            if button.kind not in {"keyboard", "callback"}:
                raise MonitorError("action_not_allowed", reason="unsupported_button_type")
            result = await raw.click(i=button.row, j=button.column)
            # A reply-keyboard click sends a text message; callback answers are not messages.
            return result.id if isinstance(result, types.Message) else None

        return await self.transition(click, wait_seconds, source=message)

    async def watch(self, emit, sender: str | None = None):
        sender_entity = await self.client.get_entity(sender) if sender else None
        failure = asyncio.get_running_loop().create_future()

        async def receive(event):
            try:
                message = event.message
                if message.chat_id != self.target.chat_id:
                    return
                if sender_entity is not None and message.sender_id != sender_entity.id:
                    return
                who = await event.get_sender()
                value = snapshot(message, getattr(who, "username", None)).payload()
                value["event"] = "message_edited" if event.message.edit_date else "message_new"
                emit(value)
            except Exception as exc:
                if not failure.done():
                    failure.set_exception(exc)

        self.client.add_event_handler(receive, events.NewMessage(chats=self.peer))
        self.client.add_event_handler(receive, events.MessageEdited(chats=self.peer))
        logger.info("Watching configured chat (new messages and edits).")
        try:
            done, _ = await asyncio.wait(
                [self.client.disconnected, failure], return_when=asyncio.FIRST_COMPLETED
            )
            for future in done:
                await future
            raise MonitorError("telegram_connection_error", reason="watch_disconnected")
        finally:
            self.client.remove_event_handler(receive)
            failure.cancel()
