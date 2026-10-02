"""Subprocess fixture; it never constructs a real Telegram client."""

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

from app.__main__ import main
from tests.fakes import event, settings

directory = Path(os.environ["RECORDER_TEST_DATA"])


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.handlers = []

    def add_event_handler(self, callback, builder):
        self.handlers.append(callback)

    async def connect(self):
        pass

    async def is_user_authorized(self):
        return True

    async def run_until_disconnected(self):
        await self.handlers[0](event())
        print("TEST_READY", flush=True)
        await asyncio.Event().wait()

    async def disconnect(self):
        (directory / "disconnected.marker").touch()


with (
    patch("app.__main__.load_settings", return_value=settings(directory)),
    patch("app.telegram_client.TelegramClient", FakeClient),
):
    raise SystemExit(main())
