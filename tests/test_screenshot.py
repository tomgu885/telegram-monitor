import asyncio
import json
import subprocess
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config import ScreenshotSettings
from app.screenshot import ScreenshotError, capture_main_display
from app.screenshot_handler import ScreenshotHandler
from app.storage import Storage
from app.telegram_client import Recorder
from tests.fakes import event, settings


def request(sender=42, text="截图", private=True, message_id=7, **kwargs):
    result = event(
        chat_id=sender if private else -100123,
        sender_id=sender,
        message_id=message_id,
        text=text,
        **kwargs,
    )
    result.id = message_id
    result.is_private = private
    result.is_group = not private
    result.respond = AsyncMock()
    result.client = SimpleNamespace(send_file=AsyncMock())
    result.get_input_sender = AsyncMock(return_value=sender)
    return result


def fake_capture(command, **kwargs):
    Path(command[-1]).write_bytes(b"mock PNG; no actual screenshot")
    return SimpleNamespace(returncode=0)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "screenshots"
        platform = patch("app.screenshot.sys.platform", "darwin")
        platform.start()
        self.addCleanup(platform.stop)

    def test_fixed_command_unique_private_pngs(self):
        with patch("app.screenshot.subprocess.run", side_effect=fake_capture) as run:
            first = capture_main_display(self.directory)
            second = capture_main_display(self.directory)
        self.assertNotEqual(first, second)
        self.assertEqual(first.suffix, ".png")
        self.assertEqual(first.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertGreater(first.stat().st_size, 0)
        self.assertEqual(
            run.call_args_list[0].args[0], ["/usr/sbin/screencapture", "-m", "-x", str(first)]
        )
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["timeout"], 15)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_failure_missing_empty_timeout_and_oserror_cleanup(self):
        def partial_failure(command, **kwargs):
            fake_capture(command)
            return SimpleNamespace(returncode=1)

        def missing(command, **kwargs):
            Path(command[-1]).unlink()
            return SimpleNamespace(returncode=0)

        for failure in (
            partial_failure,
            missing,
            lambda *a, **kw: SimpleNamespace(returncode=0),
            subprocess.TimeoutExpired("SECRET-path", 15),
            OSError("SECRET-path"),
        ):
            with (
                self.subTest(failure=type(failure).__name__),
                patch("app.screenshot.subprocess.run", side_effect=failure),
            ):
                with self.assertRaises(ScreenshotError) as error:
                    capture_main_display(self.directory)
                self.assertNotIn("SECRET", str(error.exception))
                self.assertIn("Screen Recording permission", str(error.exception))
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_non_macos_never_invokes_command(self):
        with (
            patch("app.screenshot.sys.platform", "linux"),
            patch("app.screenshot.subprocess.run") as run,
            self.assertRaises(ScreenshotError),
        ):
            capture_main_display(self.directory)
        run.assert_not_called()


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "screenshots"
        self.config = ScreenshotSettings(
            enabled=True, screenshot_uids=frozenset({42, 43}), temp_dir=self.directory
        )
        self.handler = ScreenshotHandler(self.config)
        self.addAsyncCleanup(self.handler.close)
        platform = patch("app.screenshot.sys.platform", "darwin")
        platform.start()
        self.addCleanup(platform.stop)
        self.run_patch = patch("app.screenshot.subprocess.run", side_effect=fake_capture)
        self.run = self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    def assert_clean(self):
        self.assertEqual(list(self.directory.glob("*.png")), [])

    async def test_authorized_exact_commands_send_original_private_reply_and_cleanup(self):
        for text in ("截图", "screenshot", "  ScReEnShOt\n"):
            with self.subTest(text=text):
                handler = ScreenshotHandler(self.config)
                incoming = request(text=text)
                await handler.handle(incoming)
                incoming.respond.assert_awaited_once()
                args = incoming.respond.call_args.kwargs
                self.assertEqual(args["reply_to"], 7)
                self.assertTrue(args["force_document"])
                self.assertEqual(set(args), {"file", "reply_to", "force_document"})
                self.assertEqual(Path(args["file"]).parent, self.directory)
                incoming.client.send_file.assert_not_called()
                self.assert_clean()
        self.assertEqual(self.run.call_count, 3)

    async def test_rejects_unauthorized_missing_identity_normal_text_group_and_outgoing(self):
        for incoming in (
            request(sender=99),
            request(sender=None),
            request(sender=-42),
            request(text="普通文字"),
            request(text="帮我截图一下"),
            request(text="please screenshot"),
            request(private=False),
            request(out=True),
        ):
            await self.handler.handle(incoming)
            incoming.respond.assert_not_called()
            incoming.client.send_file.assert_not_called()
        self.run.assert_not_called()

    async def test_disabled_empty_allowlist_channels_and_wrong_private_target(self):
        for config in (
            replace(self.config, enabled=False),
            replace(self.config, screenshot_uids=frozenset()),
        ):
            await ScreenshotHandler(config).handle(request())
        incoming = request()
        incoming.chat_id = 999
        await self.handler.handle(incoming)
        channel = request(private=False)
        channel.is_group = False
        await ScreenshotHandler(replace(self.config, allow_groups=True)).handle(channel)
        self.run.assert_not_called()

    async def test_custom_command(self):
        handler = ScreenshotHandler(replace(self.config, commands=frozenset({"capture"})))
        await handler.handle(request())
        self.run.assert_not_called()
        await handler.handle(request(text=" CAPTURE "))
        self.run.assert_called_once()

    async def test_group_opt_in_sends_only_to_authorized_user(self):
        incoming = request(private=False)
        await ScreenshotHandler(replace(self.config, allow_groups=True)).handle(incoming)
        incoming.respond.assert_not_called()
        incoming.client.send_file.assert_awaited_once()
        self.assertEqual(incoming.client.send_file.call_args.args[0], 42)
        self.assertNotIn("reply_to", incoming.client.send_file.call_args.kwargs)
        self.assert_clean()

    async def test_cooldown_and_expiry(self):
        with patch("app.screenshot_handler.monotonic", return_value=100):
            await self.handler.handle(request())
            await self.handler.handle(request(message_id=8))
        self.assertEqual(self.run.call_count, 1)
        with patch("app.screenshot_handler.monotonic", return_value=105):
            await self.handler.handle(request(message_id=9))
        self.assertEqual(self.run.call_count, 2)

    async def test_capture_failure_does_not_send_and_listener_can_continue(self):
        self.run.side_effect = OSError("SECRET-home-path")
        incoming = request()
        with self.assertLogs("app", "ERROR") as logs:
            await self.handler.handle(incoming)
        self.assertIn("Screen Recording permission", " ".join(logs.output))
        self.assertNotIn("SECRET", " ".join(logs.output))
        incoming.respond.assert_not_called()
        self.assert_clean()
        self.run.side_effect = fake_capture
        await self.handler.handle(request(sender=43))
        self.assertEqual(self.run.call_count, 2)

    async def test_send_failure_cleans_and_logs_only_metadata(self):
        incoming = request()
        incoming.respond.side_effect = RuntimeError("SECRET-message-path")
        with self.assertLogs("app", "INFO") as logs:
            await self.handler.handle(incoming)
        output = " ".join(logs.output)
        self.assertNotIn("SECRET", output)
        self.assertNotIn(str(self.directory), output)
        self.assertNotIn("截图", output)
        self.assert_clean()

    async def test_keep_failed_only_retains_failed_send(self):
        handler = ScreenshotHandler(replace(self.config, keep_failed=True))
        incoming = request()
        incoming.respond.side_effect = OSError("send failure")
        await handler.handle(incoming)
        retained = list(self.directory.glob("*.png"))
        self.assertEqual(len(retained), 1)
        retained[0].unlink()
        await handler.handle(request(sender=43))
        self.assert_clean()

    async def test_concurrent_requests_serialize_capture_send_and_cleanup(self):
        sending = asyncio.Event()
        release = asyncio.Event()
        first = request()

        async def slow_send(**kwargs):
            sending.set()
            await release.wait()
            self.assertTrue(await asyncio.to_thread(Path(kwargs["file"]).exists))

        first.respond.side_effect = slow_send
        task_a = asyncio.create_task(self.handler.handle(first))
        await asyncio.wait_for(sending.wait(), 2)
        task_b = asyncio.create_task(self.handler.handle(request(sender=43)))
        await asyncio.sleep(0)
        self.assertEqual(self.run.call_count, 1)
        self.assertEqual(len(list(self.directory.glob("*.png"))), 1)
        release.set()
        await asyncio.gather(task_a, task_b)
        self.assertEqual(self.run.call_count, 2)
        self.assert_clean()

    async def test_same_sender_concurrent_requests_rate_limited_before_lock(self):
        await asyncio.gather(*(self.handler.handle(request()) for _ in range(10)))
        self.run.assert_called_once()
        self.assert_clean()

    async def test_delayed_queue_cannot_bypass_capture_cooldown(self):
        await self.handler._lock.acquire()
        with patch("app.screenshot_handler.monotonic", return_value=100) as clock:
            first = asyncio.create_task(self.handler.handle(request()))
            await asyncio.sleep(0)
            clock.return_value = 105
            second = asyncio.create_task(self.handler.handle(request(message_id=8)))
            await asyncio.sleep(0)
            clock.return_value = 110
            self.handler._lock.release()
            await asyncio.gather(first, second)
        self.run.assert_called_once()
        self.assert_clean()

    async def test_cancellation_during_capture_waits_for_thread_then_cleans(self):
        started = threading.Event()
        release = threading.Event()

        def slow_capture(command, **kwargs):
            started.set()
            if not release.wait(3):
                raise TimeoutError("mock capture was not released")
            return fake_capture(command)

        self.run.side_effect = slow_capture
        self.handler.submit(request())
        try:
            ready = await asyncio.to_thread(started.wait, 2)
            self.assertTrue(ready)
            closing = asyncio.create_task(self.handler.close())
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
        finally:
            release.set()
        await asyncio.wait_for(closing, 2)
        self.assert_clean()
        self.assertFalse(self.handler._lock.locked())

    async def test_cancellation_during_send_cleans_even_with_keep_failed(self):
        handler = ScreenshotHandler(replace(self.config, keep_failed=True))
        sending = asyncio.Event()
        incoming = request()

        async def waiting_send(**kwargs):
            sending.set()
            await asyncio.Event().wait()

        incoming.respond.side_effect = waiting_send
        handler.submit(incoming)
        await asyncio.wait_for(sending.wait(), 2)
        await handler.close()
        self.assert_clean()

    async def test_persist_before_capture_archives_and_deduplicates(self):
        store = Storage(Path(self.temp.name))
        self.addCleanup(store.close)
        recorder = Recorder(settings(screenshot=self.config, user_ids=frozenset({42})), store)
        self.addAsyncCleanup(recorder.screenshots.close)

        # SQLite connections are tied to the event-loop thread, so inspect on send.
        incoming = request()

        async def verify_send(**kwargs):
            self.assertEqual(store.get(42, 7)["text"], "截图")
            self.assertEqual(store.pending_count(), 1)

        incoming.respond.side_effect = verify_send
        await recorder.message(incoming, "message_new")
        self.assertEqual(store.get(42, 7)["text"], "截图")
        self.assertEqual(store.pending_count(), 1)
        self.run.assert_not_called()
        await asyncio.gather(*recorder.screenshots._tasks)
        self.run.assert_called_once()
        await recorder.message(incoming, "message_new")
        await recorder.message(request(text="screenshot"), "message_edited")
        self.assertEqual(self.run.call_count, 1)
        while store.flush_archive():
            pass
        for name in ("archive", "important"):
            paths = list((Path(self.temp.name) / name).glob("*.jsonl"))
            self.assertEqual(len(paths), 1)
            entries = [json.loads(line) for line in paths[0].read_text().splitlines()]
            self.assertEqual(entries[0]["event_type"], "message_new")
            self.assertEqual(entries[0]["text"], "截图")
        self.assert_clean()

    async def test_slow_upload_does_not_block_recording_other_messages(self):
        store = Storage(Path(self.temp.name))
        self.addCleanup(store.close)
        recorder = Recorder(settings(screenshot=self.config), store)
        self.addAsyncCleanup(recorder.screenshots.close)
        sending = asyncio.Event()
        release = asyncio.Event()
        incoming = request()

        async def slow_send(**kwargs):
            sending.set()
            await release.wait()

        incoming.respond.side_effect = slow_send
        await recorder.message(incoming, "message_new")
        await asyncio.wait_for(sending.wait(), 2)
        try:
            await asyncio.wait_for(
                recorder.message(request(text="ordinary message", message_id=8), "message_new"), 2
            )
            self.assertEqual(store.get(42, 8)["text"], "ordinary message")
        finally:
            release.set()
        await asyncio.gather(*recorder.screenshots._tasks)
        self.assert_clean()

    async def test_watch_filter_still_persists_authorized_requests_before_failed_capture(self):
        store = Storage(Path(self.temp.name))
        self.addCleanup(store.close)
        recorder = Recorder(settings(watch_all=False, screenshot=self.config), store)
        self.addAsyncCleanup(recorder.screenshots.close)
        self.run.side_effect = OSError("denied")
        await recorder.message(request(), "message_new")
        await asyncio.gather(*recorder.screenshots._tasks)
        self.assertIsNotNone(store.get(42, 7))
        self.assertEqual(store.pending_count(), 1)
        while store.flush_archive():
            pass
        self.assertEqual(store.pending_count(), 0)
        self.assert_clean()
