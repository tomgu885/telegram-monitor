"""Latest message state plus a transactional JSONL outbox, with one writer."""

import json
import sqlite3
import uuid
from pathlib import Path

from app.archive import Archive
from app.models import normalize, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_messages (
    id INTEGER PRIMARY KEY,
    telegram_message_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    chat_type TEXT,
    chat_title TEXT,
    sender_id INTEGER,
    sender_username TEXT,
    sender_first_name TEXT,
    sender_last_name TEXT,
    message_date TEXT,
    received_at TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    reply_to_message_id INTEGER,
    is_outgoing INTEGER NOT NULL DEFAULT 0,
    important INTEGER NOT NULL DEFAULT 0,
    important_reason TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    has_media INTEGER NOT NULL DEFAULT 0,
    media_type TEXT,
    file_name TEXT,
    mime_type TEXT,
    file_size INTEGER,
    edited_at TEXT,
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(chat_id, telegram_message_id)
);
CREATE INDEX IF NOT EXISTS idx_chat_date ON telegram_messages(chat_id, message_date);
CREATE INDEX IF NOT EXISTS idx_sender_date ON telegram_messages(sender_id, message_date);
CREATE INDEX IF NOT EXISTS idx_important_date ON telegram_messages(important, message_date);
CREATE INDEX IF NOT EXISTS idx_message_date ON telegram_messages(message_date);
CREATE TABLE IF NOT EXISTS archive_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload TEXT NOT NULL,
    important INTEGER NOT NULL,
    archive_done INTEGER NOT NULL DEFAULT 0,
    important_done INTEGER NOT NULL DEFAULT 0
);
"""


class Storage:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = sqlite3.connect(data_dir / "messages.db", timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(SCHEMA)
        self.archive = Archive(data_dir)

    def get(self, chat_id: int, message_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM telegram_messages WHERE chat_id=? AND telegram_message_id=?",
            (chat_id, message_id),
        ).fetchone()
        return dict(row) if row else None

    def _enqueue(self, payload: dict, important: bool) -> None:
        payload = {"schema_version": 1, "event_id": str(uuid.uuid4()), **payload}
        self.connection.execute(
            "INSERT INTO archive_outbox(payload, important) VALUES (?, ?)",
            (json.dumps(normalize(payload), ensure_ascii=True, allow_nan=False), important),
        )

    def record_message(self, record: dict, event_type: str = "message_new") -> bool:
        if event_type not in ("message_new", "message_edited"):
            raise ValueError("Unknown message event type")
        record = dict(record)
        record["raw_json"] = json.dumps(
            normalize(record["raw_json"]), ensure_ascii=True, sort_keys=True, allow_nan=False
        )
        with self.connection:
            old = self.get(record["chat_id"], record["telegram_message_id"])
            if old and old["message_date"] is not None:
                if event_type == "message_new":
                    return False
                if old["raw_json"] == record["raw_json"] and old["text"] == record["text"]:
                    return False
            # Archive a late edit for history, but never roll the latest SQLite state back.
            stale = bool(
                old
                and old["edited_at"]
                and (record["edited_at"] or record["message_date"]) < old["edited_at"]
            )
            payload = {
                **record,
                "raw_json": json.loads(record["raw_json"]),
                "event_type": event_type,
                "applied": not stale,
            }
            if old:
                payload["deleted_at"] = old["deleted_at"]
            if not stale:
                if old:
                    record["created_at"] = old["created_at"]
                    record["received_at"] = old["received_at"]
                    record["deleted_at"] = old["deleted_at"]
                    columns = [
                        key for key in record if key not in ("chat_id", "telegram_message_id")
                    ]
                    self.connection.execute(
                        "UPDATE telegram_messages SET "
                        + ",".join(f"{key}=?" for key in columns)
                        + " WHERE chat_id=? AND telegram_message_id=?",
                        [record[key] for key in columns]
                        + [record["chat_id"], record["telegram_message_id"]],
                    )
                else:
                    columns = list(record)
                    self.connection.execute(
                        f"INSERT INTO telegram_messages ({','.join(columns)}) "
                        f"VALUES ({','.join('?' for _ in columns)})",
                        [record[key] for key in columns],
                    )
            self._enqueue(payload, bool(record["important"]))
        return True

    def record_deleted(
        self,
        chat_id: int | None,
        message_id: int,
        *,
        reason: str = "",
        raw: object = None,
        received_at: str | None = None,
    ) -> bool:
        now = received_at or utc_now()
        with self.connection:
            old = self.get(chat_id, message_id) if chat_id is not None else None
            if old and old["deleted_at"]:
                return False
            if old:
                reason = old["important_reason"] or reason
                self.connection.execute(
                    "UPDATE telegram_messages SET deleted_at=? "
                    "WHERE chat_id=? AND telegram_message_id=?",
                    (now, chat_id, message_id),
                )
            elif chat_id is not None:
                self.connection.execute(
                    """INSERT INTO telegram_messages
                       (chat_id, telegram_message_id, received_at, created_at, deleted_at,
                        important, important_reason) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (chat_id, message_id, now, now, now, bool(reason), reason),
                )
            # Private/basic-group deletion updates may have no peer. Never guess by message ID.
            self._enqueue(
                {
                    "event_type": "message_deleted",
                    "chat_id": chat_id,
                    "telegram_message_id": message_id,
                    "received_at": now,
                    "deleted_at": now,
                    "important": bool(reason),
                    "important_reason": reason,
                    "raw_json": normalize(raw),
                    "chat_id_known": chat_id is not None,
                },
                bool(reason),
            )
        return True

    def record_unparsed(
        self, source_event_type: str, chat_id: int | None, raw: object, received_at: str
    ) -> None:
        with self.connection:
            self._enqueue(
                {
                    "event_type": "message_unparsed",
                    "source_event_type": source_event_type,
                    "chat_id": chat_id,
                    "raw_json": raw,
                    "received_at": received_at,
                },
                False,
            )

    def flush_archive(self, limit: int = 100) -> int:
        rows = self.connection.execute(
            "SELECT * FROM archive_outbox ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload"])
            if not row["archive_done"]:
                self.archive.append(payload)
                with self.connection:
                    self.connection.execute(
                        "UPDATE archive_outbox SET archive_done=1 WHERE id=?", (row["id"],)
                    )
            if row["important"] and not row["important_done"]:
                self.archive.append(payload, important=True)
                with self.connection:
                    self.connection.execute(
                        "UPDATE archive_outbox SET important_done=1 WHERE id=?", (row["id"],)
                    )
            with self.connection:
                self.connection.execute("DELETE FROM archive_outbox WHERE id=?", (row["id"],))
        return len(rows)

    def pending_count(self) -> int:
        return self.connection.execute("SELECT count(*) FROM archive_outbox").fetchone()[0]

    def close(self) -> None:
        self.connection.close()
