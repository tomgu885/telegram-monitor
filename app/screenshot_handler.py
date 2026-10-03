"""Deterministic authorization, rate limits, serialized capture/send/cleanup."""

import asyncio
import logging
from time import monotonic

from app.config import ScreenshotSettings
from app.logging_config import log_failure
from app.screenshot import capture_main_display, delete_screenshot

logger = logging.getLogger("app")


class ScreenshotHandler:
    def __init__(self, settings: ScreenshotSettings):
        self.settings = settings
        self._lock = asyncio.Lock()
        self._last_request: dict[int, float] = {}
        self._last_capture: dict[int, float] = {}
        self._tasks: set[asyncio.Task] = set()

    def accepts(self, event) -> bool:
        if not self.settings.enabled or getattr(event.message, "out", False):
            return False
        if (event.raw_text or "").strip().lower() not in self.settings.commands:
            return False
        sender = event.sender_id
        if type(sender) is not int or sender <= 0 or sender not in self.settings.screenshot_uids:
            logger.info("[SCREENSHOT] command ignored from unauthorized sender=%s", sender)
            return False
        if event.is_private:
            # Never reply to a different dialog even if an event has inconsistent metadata.
            return event.chat_id == sender
        return bool(self.settings.allow_groups and event.is_group)

    def submit(self, event) -> None:
        """Schedule after persistence without blocking the sequential recorder listener."""
        task = asyncio.create_task(self.handle(event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def handle(self, event) -> None:
        if not self.accepts(event):
            return
        sender = event.sender_id
        now = monotonic()
        previous = self._last_request.get(sender)
        if previous is not None and now - previous < self.settings.cooldown_seconds:
            logger.info("[SCREENSHOT] request rate-limited sender=%s", sender)
            return
        # Reserve before the first await, including while another sender owns the lock.
        self._last_request[sender] = now
        async with self._lock:
            # Prevent queued requests starting captures too close together for the same sender.
            now = monotonic()
            previous_start = self._last_capture.get(sender)
            if previous_start is not None and now - previous_start < self.settings.cooldown_seconds:
                logger.info("[SCREENSHOT] request rate-limited sender=%s", sender)
                return
            self._last_capture[sender] = now
            path = None
            failed = False
            try:
                logger.info("[SCREENSHOT] request sender=%s message=%s", sender, event.id)
                capture = asyncio.create_task(
                    asyncio.to_thread(capture_main_display, self.settings.temp_dir)
                )
                try:
                    path = await asyncio.shield(capture)
                except asyncio.CancelledError:
                    # A worker thread cannot be cancelled. Keep the lock until it exits,
                    # then let finally remove its output before shutdown completes.
                    try:
                        path = await capture
                    except Exception as exc:
                        log_failure("[SCREENSHOT] capture failed during shutdown", exc)
                    raise
                logger.info("[SCREENSHOT] captured path=<redacted> size=%s", path.stat().st_size)
                async with asyncio.timeout(60):
                    if event.is_private:
                        await event.respond(file=str(path), reply_to=event.id, force_document=True)
                    else:
                        # Opt-in group commands still return the image ONLY to the sender.
                        await event.client.send_file(
                            await event.get_input_sender(), str(path), force_document=True
                        )
                logger.info("[SCREENSHOT] sent sender=%s", sender)
            except Exception as exc:
                failed = True
                log_failure(
                    "[SCREENSHOT] request failed; capture errors may require "
                    "Screen Recording permission",
                    exc,
                )
            finally:
                if path is not None:
                    if failed and self.settings.keep_failed:
                        logger.info("[SCREENSHOT] failed image retained path=<redacted>")
                    else:
                        delete_screenshot(path)
