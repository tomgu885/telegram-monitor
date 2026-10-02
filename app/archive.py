"""Append and fsync one event at a time. The SQLite outbox owns retry state."""

import json
import os
from pathlib import Path


class IncompleteArchiveError(OSError):
    """A previous crash left a partial line; preserve it for manual recovery."""


class Archive:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        for name in ("archive", "important"):
            (data_dir / name).mkdir(parents=True, exist_ok=True, mode=0o700)

    def append(self, payload: dict, *, important: bool = False) -> None:
        day = payload["received_at"][:10]  # UTC receipt date, including edits/deletions.
        path = self.data_dir / ("important" if important else "archive") / f"{day}.jsonl"
        # Do not silently join a new record to a torn line or rewrite old archive bytes.
        if path.exists() and path.stat().st_size:
            with path.open("rb") as reader:
                reader.seek(-1, os.SEEK_END)
                if reader.read(1) != b"\n":
                    raise IncompleteArchiveError("Archive has an incomplete final line")
        line = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        with path.open("ab") as writer:
            writer.write((line + "\n").encode("utf-8"))
            writer.flush()
            os.fsync(writer.fileno())
        # Persist the directory entry as well when this is a newly created daily file.
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
