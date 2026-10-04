"""Private local cursor and process locks; no authentication material in cursors."""

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

from telegram_monitor.exceptions import MonitorError


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


@contextmanager
def file_lock(path: Path):
    private_directory(path.parent)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a") as handle:
        os.fchmod(handle.fileno(), 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MonitorError("session_busy") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def session_lock(session: Path):
    with ExitStack() as locks:
        # Also honor the recorder's pre-existing lock, including a recorder
        # process started before this CLI update. Never copy a live session.
        if session.name == "telegram.session":
            locks.enter_context(file_lock(session.parent / "recorder.lock"))
        locks.enter_context(file_lock(session.with_suffix(".session.lock")))
        yield


class CursorStore:
    def __init__(self, directory: Path, identity: str):
        name = hashlib.sha256(identity.encode()).hexdigest()[:20]
        self.path = directory / f"menu-{name}.json"

    def read(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("environment"), str)
                or type(value.get("message_id")) is not int
                or value["message_id"] <= 0
            ):
                raise ValueError
            return value
        except FileNotFoundError:
            raise MonitorError("menu_not_found", reason="run_menu_open_first") from None
        except (ValueError, OSError):
            raise MonitorError("unexpected_menu", reason="invalid_menu_cursor") from None

    def clear(self):
        self.path.unlink(missing_ok=True)

    def write(self, value: dict):
        private_directory(self.path.parent)
        fd, name = tempfile.mkstemp(prefix=".menu-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
