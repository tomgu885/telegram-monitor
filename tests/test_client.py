import io
import logging
import os
import selectors
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telethon import errors

from app.logging_config import log_failure, setup_logging
from app.storage import Storage
from app.telegram_client import (
    AuthenticationFailure,
    Recorder,
    connection_loop,
    hidden_prompt,
    login,
)
from tests.fakes import event, settings


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Storage(Path(self.temp.name))
        self.addCleanup(self.store.close)

    async def test_watch_all_false_retains_only_or_matches(self):
        config = settings(watch_all=False, chat_ids=frozenset([-100123]), user_ids=frozenset([42]))
        recorder = Recorder(config, self.store)
        await recorder.message(event(chat_id=-100999, sender_id=99), "message_new")
        await recorder.message(event(chat_id=-100999, sender_id=42), "message_new")
        await recorder.message(event(chat_id=-100123, sender_id=99), "message_new")
        self.assertEqual(self.store.pending_count(), 2)
        self.assertEqual(self.store.get(-100999, 7)["important_reason"], "user_id")
        self.assertEqual(self.store.get(-100123, 7)["important_reason"], "chat_id")

    async def test_all_mode_records_outgoing_updates_too(self):
        await Recorder(settings(), self.store).message(event(out=True), "message_new")
        self.assertTrue(self.store.get(-100123, 7)["is_outgoing"])

    async def test_sparse_edit_keeps_known_sender_and_user_filter_match(self):
        recorder = Recorder(settings(watch_all=False, user_ids=frozenset([42])), self.store)
        await recorder.message(event(), "message_new")
        edited = event(sender_id=None, text="new text")
        edited.sender = edited.chat = None
        await recorder.message(edited, "message_edited")
        row = self.store.get(-100123, 7)
        self.assertEqual(row["sender_id"], 42)
        self.assertEqual(row["sender_username"], "alice")
        self.assertEqual(row["chat_title"], "test group")
        self.assertEqual(row["important_reason"], "user_id")
        self.assertEqual(row["text"], "new text")

    async def test_unknown_deletion_does_not_mark_existing_message(self):
        recorder = Recorder(settings(), self.store)
        await recorder.message(event(), "message_new")
        await recorder.deleted(SimpleNamespace(chat_id=None, deleted_ids=[7], original_update={}))
        self.assertIsNone(self.store.get(-100123, 7)["deleted_at"])
        self.assertEqual(self.store.pending_count(), 2)

    async def test_database_write_failure_retries_same_event(self):
        recorder = Recorder(settings(), self.store)
        write = self.store.record_message
        calls = 0

        def flaky(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("sensitive SQL must not be logged")
            return write(*args)

        with (
            patch.object(self.store, "record_message", side_effect=flaky),
            patch("app.telegram_client.asyncio.sleep", new_callable=AsyncMock),
        ):
            await recorder.message(event(), "message_new")
        self.assertIsNotNone(self.store.get(-100123, 7))
        self.assertEqual(recorder.pending_writes, 0)

    async def test_bad_parser_preserves_raw_evidence(self):
        with patch("app.telegram_client.parse_message", side_effect=ValueError("secret")):
            await Recorder(settings(), self.store).message(event(), "message_new")
        self.assertEqual(self.store.pending_count(), 1)
        payload = self.store.connection.execute("SELECT payload FROM archive_outbox").fetchone()[0]
        self.assertIn("message_unparsed", payload)

    async def test_existing_session_never_prompts_or_requests_code(self):
        client = SimpleNamespace(
            is_user_authorized=AsyncMock(return_value=True), send_code_request=AsyncMock()
        )
        with patch("app.telegram_client.hidden_prompt") as prompt:
            await login(client, settings())
        prompt.assert_not_called()
        client.send_code_request.assert_not_called()

    async def test_first_login_with_2fa(self):
        client = SimpleNamespace(
            is_user_authorized=AsyncMock(return_value=False),
            send_code_request=AsyncMock(return_value=SimpleNamespace(phone_code_hash="fake")),
            sign_in=AsyncMock(side_effect=[errors.SessionPasswordNeededError(None), None]),
        )
        with patch("app.telegram_client.hidden_prompt", side_effect=["12345", "test-password"]):
            await login(client, settings())
        self.assertEqual(client.sign_in.await_count, 2)
        client.sign_in.assert_awaited_with(password="test-password")

    async def test_network_failure_reconnects_and_auth_failure_exits(self):
        client = SimpleNamespace(
            connect=AsyncMock(side_effect=[OSError("offline"), None]),
            disconnect=AsyncMock(),
            is_user_authorized=AsyncMock(return_value=True),
            run_until_disconnected=AsyncMock(side_effect=errors.SessionRevokedError(None)),
        )
        with (
            patch("app.telegram_client.asyncio.sleep", new_callable=AsyncMock),
            self.assertRaises(AuthenticationFailure),
        ):
            await connection_loop(client, settings())
        self.assertEqual(client.connect.await_count, 2)
        self.assertEqual(client.disconnect.await_count, 2)

    async def test_rate_limit_waits_for_telegram_retry_period(self):
        client = SimpleNamespace(
            connect=AsyncMock(
                side_effect=[
                    errors.FloodWaitError(None, capture=120),
                    errors.ApiIdInvalidError(None),
                ]
            ),
            disconnect=AsyncMock(),
        )
        with (
            patch("app.telegram_client.asyncio.sleep", new_callable=AsyncMock) as sleep,
            self.assertRaises(AuthenticationFailure),
        ):
            await connection_loop(client, settings())
        sleep.assert_awaited_once_with(120)


class LoggingTests(unittest.TestCase):
    def test_login_input_refuses_noninteractive_echo_fallback(self):
        with (
            patch("app.telegram_client.sys.stdin.isatty", return_value=False),
            patch("app.telegram_client.getpass.getpass") as prompt,
            self.assertRaises(EOFError),
        ):
            hidden_prompt("Code: ")
        prompt.assert_not_called()

    def test_exception_details_and_telethon_messages_do_not_leak(self):
        setup_logging()
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        logging.getLogger().addHandler(handler)
        try:
            try:
                raise ValueError("SECRET-message-phone-hash")
            except ValueError as exc:
                log_failure("Parser failed", exc)
            logging.getLogger("telethon.client.updates").error("SECRET-update")
        finally:
            logging.getLogger().removeHandler(handler)
        self.assertNotIn("SECRET", output.getvalue())
        self.assertIn("ValueError", output.getvalue())


class SignalTests(unittest.TestCase):
    def test_sigint_and_sigterm_flush_and_close_without_network(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig), tempfile.TemporaryDirectory() as directory:
                env = {**os.environ, "RECORDER_TEST_DATA": directory}
                process = subprocess.Popen(
                    [sys.executable, "-m", "tests.signal_runner"],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                try:
                    # Wait for an explicit ready marker without unbounded readline().
                    output = b""
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ)
                        while b"TEST_READY" not in output:
                            self.assertTrue(
                                selector.select(timeout=10), "Fake client startup timed out"
                            )
                            chunk = os.read(process.stdout.fileno(), 8192)
                            self.assertTrue(chunk, output.decode())
                            output += chunk
                    process.send_signal(sig)
                    rest, _ = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 0, (output + rest).decode())
                    path = Path(directory)
                    with sqlite3.connect(path / "messages.db") as db:
                        self.assertEqual(
                            db.execute("SELECT count(*) FROM telegram_messages").fetchone()[0], 1
                        )
                        self.assertEqual(
                            db.execute("SELECT count(*) FROM archive_outbox").fetchone()[0], 0
                        )
                    self.assertEqual(len(list((path / "archive").glob("*.jsonl"))), 1)
                    self.assertTrue((path / "disconnected.marker").exists())
                    self.assertEqual((path / "messages.db").stat().st_mode & 0o777, 0o600)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate()


if __name__ == "__main__":
    unittest.main()
