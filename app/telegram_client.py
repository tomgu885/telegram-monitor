"""Telethon recorder with explicitly configured screenshot replies."""

import asyncio
import contextlib
import getpass
import logging
import signal
import sqlite3
import sys
import warnings
from collections.abc import Callable

from telethon import TelegramClient, errors, events

from app.config import Settings
from app.logging_config import log_failure
from app.models import normalize, parse_message, utc_now
from app.screenshot_handler import ScreenshotHandler
from app.storage import Storage

logger = logging.getLogger("app")
FATAL_AUTH = (
    errors.UnauthorizedError,
    errors.AuthKeyError,
    errors.AuthKeyDuplicatedError,
    errors.ApiIdInvalidError,
    errors.PhoneNumberInvalidError,
    errors.PhoneNumberBannedError,
)


class AuthenticationFailure(RuntimeError):
    pass


def hidden_prompt(label: str) -> str:
    # getpass runs synchronously so no abandoned input thread can prevent exit.
    # Both signals interrupt it, even while asyncio signal handlers are installed.
    if not sys.stdin.isatty():
        raise EOFError

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    previous = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        # Refuse getpass's echoing fallback if the terminal cannot disable echo.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                return getpass.getpass(label)
            except getpass.GetPassWarning:
                raise EOFError from None
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


async def login(client: TelegramClient, settings: Settings) -> None:
    if await client.is_user_authorized():
        return
    logger.info("首次登录需要 Telegram 验证码；输入不会回显。")
    sent = await client.send_code_request(settings.phone)
    for _ in range(3):
        code = hidden_prompt("Telegram 登录验证码: ").strip()
        try:
            await client.sign_in(settings.phone, code, phone_code_hash=sent.phone_code_hash)
            return
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeEmptyError):
            logger.warning("验证码无效，请重试。")
        except errors.PhoneCodeExpiredError:
            raise AuthenticationFailure("验证码已过期，请重新启动获取新验证码。") from None
        except errors.SessionPasswordNeededError:
            for _ in range(3):
                try:
                    await client.sign_in(password=hidden_prompt("Telegram 2FA 密码: "))
                    return
                except errors.PasswordHashInvalidError:
                    logger.warning("2FA 密码无效，请重试。")
            raise AuthenticationFailure("2FA 验证失败，请检查密码后重新启动。") from None
    raise AuthenticationFailure("验证码验证失败，请重新启动。")


class Recorder:
    def __init__(self, settings: Settings, storage: Storage):
        self.settings = settings
        self.storage = storage
        self.pending_writes = 0
        self.screenshots = ScreenshotHandler(settings.screenshot)

    async def persist(self, operation: Callable[[], object]) -> object:
        self.pending_writes += 1
        try:
            while True:
                try:
                    result = operation()
                    self.pending_writes -= 1
                    return result
                except (OSError, sqlite3.Error) as exc:
                    log_failure("SQLite write failed; retrying in 5s (event held in memory)", exc)
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            logger.error("Shutdown interrupted an uncommitted event; recording may be incomplete.")
            raise

    async def message(self, event, event_type: str) -> None:
        received_at = utc_now()
        screenshot_request = event_type == "message_new" and self.screenshots.accepts(event)
        try:
            record = parse_message(event, self.settings, received_at=received_at)
        except Exception as exc:
            log_failure("Message parsing failed; preserving raw event", exc)
            # Preserve evidence when unexpected future Telegram types break parsing.
            await self.persist(
                lambda: self.storage.record_unparsed(
                    event_type, event.chat_id, normalize(event.message), received_at
                )
            )
            return

        def write_message():
            old = self.storage.get(record["chat_id"], record["telegram_message_id"])
            if old and event_type == "message_edited":
                # Sparse edit updates may omit entities. Retain last known identity
                # only for the same author; never overwrite a known value with NULL.
                if record["sender_id"] is None:
                    record["sender_id"] = old["sender_id"]
                if record["sender_id"] == old["sender_id"]:
                    for field in ("sender_username", "sender_first_name", "sender_last_name"):
                        if record[field] is None:
                            record[field] = old[field]
                if record["chat_title"] is None:
                    record["chat_title"] = old["chat_title"]
                reason = self.settings.important_reason(record["chat_id"], record["sender_id"])
                record["important_reason"] = reason
                record["important"] = bool(reason)
            if (
                not self.settings.watch_all
                and not screenshot_request
                and not record["important"]
                and not (old and event_type == "message_edited")
            ):
                return False
            return self.storage.record_message(record, event_type)

        changed = await self.persist(write_message)
        if changed:
            logger.info(
                "[%s] chat=%s sender=%s message=%s event=%s text_length=%s",
                "IMPORTANT" if record["important"] else "MSG",
                record["chat_id"],
                record["sender_id"],
                record["telegram_message_id"],
                event_type,
                len(record["text"]),
            )
            if screenshot_request:
                self.screenshots.submit(event)

    async def deleted(self, event) -> None:
        chat_id = event.chat_id
        raw = normalize(event.original_update)
        received_at = utc_now()
        if chat_id is None:
            # Telegram usually omits the peer for private/basic group deletions.
            # Message IDs from other chats are NEVER used to infer that peer.
            logger.warning("Deletion has no reliable chat_id; SQLite messages remain unchanged.")
        for message_id in event.deleted_ids:
            reason = self.settings.important_reason(chat_id, None)

            def write_deletion(message_id=message_id, reason=reason):
                old = self.storage.get(chat_id, message_id) if chat_id is not None else None
                if not self.settings.watch_all and not reason and not old:
                    return False
                return self.storage.record_deleted(
                    chat_id, message_id, reason=reason, raw=raw, received_at=received_at
                )

            changed = await self.persist(write_deletion)
            if changed:
                logger.info("[DELETE] chat=%s message=%s", chat_id, message_id)


async def connection_loop(client: TelegramClient, settings: Settings) -> None:
    authenticated = False
    while True:
        delay = 5
        try:
            await client.connect()
            if authenticated and not await client.is_user_authorized():
                raise AuthenticationFailure("Telegram session 已失效，请重新启动并登录。")
            await login(client, settings)
            authenticated = True
            logger.info("Connected to Telegram")
            logger.info("Listening for messages...")
            await client.run_until_disconnected()
            logger.warning("Telegram disconnected; retrying in 5s.")
        except AuthenticationFailure:
            raise
        except FATAL_AUTH as exc:
            raise AuthenticationFailure(
                f"Telegram 认证失败 ({type(exc).__name__})，请检查凭据或账号/session 状态。"
            ) from None
        except EOFError:
            raise AuthenticationFailure("首次登录需要交互式终端；当前无法读取验证码。") from None
        except errors.FloodWaitError as exc:
            delay = max(5, exc.seconds)
            logger.warning("Telegram rate limit; retrying after %s seconds.", delay)
        except Exception as exc:
            log_failure("Telegram connection failed; retrying in 5s", exc)
        finally:
            await client.disconnect()
        await asyncio.sleep(delay)


async def archive_loop(storage: Storage) -> None:
    while True:
        try:
            count = storage.flush_archive()
        except (OSError, sqlite3.Error) as exc:
            log_failure("Archive flush failed; durable outbox retained, retrying in 5s", exc)
            await asyncio.sleep(5)
        else:
            await asyncio.sleep(0 if count == 100 else 1)


async def run(settings: Settings, storage: Storage) -> None:
    client = TelegramClient(
        str(settings.data_dir / "telegram.session"),
        settings.api_id,
        settings.api_hash,
        auto_reconnect=True,
        connection_retries=5,
        retry_delay=2,
        request_retries=5,
        sequential_updates=True,
        catch_up=True,
    )
    recorder = Recorder(settings, storage)

    async def new_message(event):
        await recorder.message(event, "message_new")

    async def edited_message(event):
        await recorder.message(event, "message_edited")

    # Register BEFORE connect so catch-up updates cannot bypass the handlers.
    client.add_event_handler(new_message, events.NewMessage())
    client.add_event_handler(edited_message, events.MessageEdited())
    client.add_event_handler(recorder.deleted, events.MessageDeleted())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    tasks = [
        asyncio.create_task(connection_loop(client, settings)),
        asyncio.create_task(archive_loop(storage)),
        asyncio.create_task(stop.wait()),
    ]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task  # Propagate fatal authentication or archive task failures.
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await recorder.screenshots.close()
        await client.disconnect()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(ValueError):
                loop.remove_signal_handler(sig)
        if recorder.pending_writes:
            raise RuntimeError("Shutdown with uncommitted Telegram events")
