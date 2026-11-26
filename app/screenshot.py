"""Main-display capture with private temporary files."""

import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app import screenshot_wayland
from app.logging_config import log_failure

CAPTURE_TIMEOUT_SECONDS = 15


class ScreenshotError(RuntimeError):
    """Safe diagnostic without paths, command output, or local account details."""


def delete_screenshot(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log_failure("[SCREENSHOT] temporary file cleanup failed", exc)


def capture_main_display(temp_dir: Path, *, local_check: bool = False) -> Path:
    if sys.platform == "darwin":
        command = ["/usr/sbin/screencapture", "-m", "-x"]
        hint = "check Screen Recording permission"
    elif sys.platform == "linux" and os.environ.get("XDG_SESSION_TYPE") == "wayland":
        command = ["/usr/bin/python3", "-I", str(Path(screenshot_wayland.__file__).resolve())]
        if local_check:
            command.append("--authorize")
        hint = "check the GNOME Wayland session and run python -m app --check-screenshot"
    else:
        raise ScreenshotError("Screenshot capture requires macOS or a GNOME Wayland session.")

    path = None
    try:
        temp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        fd, name = tempfile.mkstemp(prefix=f"screenshot-{stamp}-", suffix=".png", dir=temp_dir)
        path = Path(name)
        os.close(fd)
        result = subprocess.run(
            [*command, str(path.resolve())],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60 if local_check else CAPTURE_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode != 0:
            if sys.platform == "linux" and result.returncode in screenshot_wayland.ERRORS:
                raise ScreenshotError(screenshot_wayland.ERRORS[result.returncode])
            raise ScreenshotError(f"Capture failed; {hint}.")
        if not path.is_file() or path.stat().st_size == 0:
            raise ScreenshotError(f"Capture produced no image; {hint}.")
        path.chmod(0o600)
        return path
    except BaseException as exc:
        if path is not None:
            delete_screenshot(path)
        if isinstance(exc, subprocess.TimeoutExpired):
            if local_check and sys.platform == "linux":
                raise ScreenshotError("Screenshot check timed out waiting for GNOME.") from None
            raise ScreenshotError(f"Screenshot timed out; {hint}.") from None
        if isinstance(exc, (OSError, subprocess.SubprocessError)):
            raise ScreenshotError(f"Capture failed; {hint} and check available storage.") from None
        raise
