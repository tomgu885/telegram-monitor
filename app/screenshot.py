"""Fixed macOS main-display capture; no Telegram or user-supplied arguments."""

import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.logging_config import log_failure

CAPTURE_TIMEOUT_SECONDS = 15


class ScreenshotError(RuntimeError):
    """Safe diagnostic without paths, command output, or local account details."""


def delete_screenshot(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log_failure("[SCREENSHOT] temporary file cleanup failed", exc)


def capture_main_display(temp_dir: Path) -> Path:
    if sys.platform != "darwin":
        raise ScreenshotError("Screenshot capture requires macOS.")
    path = None
    try:
        temp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        fd, name = tempfile.mkstemp(prefix=f"screenshot-{stamp}-", suffix=".png", dir=temp_dir)
        path = Path(name)
        os.close(fd)
        result = subprocess.run(
            ["/usr/sbin/screencapture", "-m", "-x", str(path)],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=CAPTURE_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode != 0:
            raise ScreenshotError("screencapture failed; check Screen Recording permission.")
        if not path.is_file() or path.stat().st_size == 0:
            raise ScreenshotError(
                "screencapture produced no image; check Screen Recording permission."
            )
        path.chmod(0o600)
        return path
    except BaseException as exc:
        if path is not None:
            delete_screenshot(path)
        if isinstance(exc, (OSError, subprocess.SubprocessError)):
            raise ScreenshotError(
                "Capture failed or timed out; check Screen Recording permission and storage."
            ) from None
        raise
