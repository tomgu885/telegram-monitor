"""Menu use cases, independent of Telethon and CLI rendering."""

import json
import logging
from typing import Protocol

from telegram_monitor.config import ButtonMatch, Environment, Target
from telegram_monitor.exceptions import MonitorError
from telegram_monitor.matcher import dangerous, require_navigation, select_button
from telegram_monitor.models import Button, Message
from telegram_monitor.state import CursorStore

logger = logging.getLogger(__name__)


class MenuClient(Protocol):
    async def get_message(self, message_id: int) -> Message: ...
    async def open_menu(self, command: str, wait_seconds: float) -> Message: ...
    async def click_button(
        self, message: Message, button: Button, wait_seconds: float
    ) -> Message: ...


class Menu:
    def __init__(
        self,
        client: MenuClient,
        env: Environment,
        target: Target,
        store: CursorStore,
        wait_seconds: float,
    ):
        self.client = client
        self.env = env
        self.target = target
        self.store = store
        self.timeout = wait_seconds

    def remember(self, message: Message):
        self.store.write(
            {
                "environment": self.env.environment,
                "chat_id": self.target.chat_id,
                "bot_username": self.target.bot_username.lower().lstrip("@"),
                "message_id": message.message_id,
                "revision": message.revision,
            }
        )

    async def open(self) -> dict:
        if dangerous(self.env.entry.command.split("@")[0]):
            raise MonitorError("action_not_allowed", reason="unsafe_entry_command")
        self.store.clear()
        message = await self.client.open_menu(self.env.entry.command, self.timeout)
        self.remember(message)
        return {"status": "ok", **message.payload()}

    async def current(self, message_id: int | None, *, refresh: bool = False) -> Message:
        cursor = None
        if message_id is None:
            cursor = self.store.read()
            if (
                cursor.get("environment") != self.env.environment
                or cursor.get("chat_id") != self.target.chat_id
                or cursor.get("bot_username") != self.target.bot_username.lower().lstrip("@")
            ):
                raise MonitorError("unexpected_menu", reason="menu_context_changed")
            message_id = cursor["message_id"]
        if message_id <= 0:
            raise MonitorError("invalid_config", reason="invalid_message_id")
        message = await self.client.get_message(message_id)
        if cursor and not refresh and cursor.get("revision") != message.revision:
            raise MonitorError("unexpected_menu", reason="menu_changed_refresh_buttons_first")
        return message

    async def buttons(self, message_id: int | None) -> dict:
        message = await self.current(message_id, refresh=True)
        self.remember(message)
        return {"status": "ok", **message.payload()}

    async def click(
        self,
        message_id: int | None,
        rule: ButtonMatch | None = None,
        index: int | None = None,
    ) -> dict:
        message = await self.current(message_id)
        button = select_button(message.buttons, rule=rule, index=index)
        require_navigation(button, self.env.entry.safe_buttons)
        logger.info(
            "click env=%s service=%s message_id=%s button=%s",
            self.env.environment,
            "-",
            message.message_id,
            json.dumps(button.text, ensure_ascii=False),
        )
        # Once an RPC is attempted, an old cursor must not enable an accidental retry.
        self.store.clear()
        next_message = await self.client.click_button(message, button, self.timeout)
        self.remember(next_message)
        return {"status": "ok", "clicked": button.text, "next_message": next_message.payload()}

    async def back(self, message_id: int | None) -> dict:
        if self.env.entry.back_button is None:
            raise MonitorError("invalid_config", reason="back_button_not_configured")
        return await self.click(message_id, rule=self.env.entry.back_button)
