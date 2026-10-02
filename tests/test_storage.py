import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from app.archive import IncompleteArchiveError
from app.models import parse_message
from app.storage import Storage
from tests.fakes import event, settings


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.store = Storage(self.path)
        self.config = settings(self.path, chat_ids=frozenset([-100123]))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def record(self, **kwargs):
        return parse_message(
            event(**kwargs), self.config, received_at="2026-10-02T09:00:00.000000+00:00"
        )

    def lines(self, name="archive", day="2026-10-02"):
        path = self.path / name / f"{day}.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_insert_duplicate_and_chat_scoped_uniqueness(self):
        self.assertTrue(self.store.record_message(self.record()))
        self.assertFalse(self.store.record_message(self.record()))
        self.assertTrue(self.store.record_message(self.record(chat_id=-100456)))
        count = self.store.connection.execute("SELECT count(*) FROM telegram_messages").fetchone()[
            0
        ]
        self.assertEqual(count, 2)
        self.store.flush_archive()
        self.assertEqual(len(self.lines()), 2)
        self.assertEqual(len(self.lines("important")), 1)
        self.assertEqual(self.lines()[0]["text"], "原文\nUG-2530 🐈")

    def test_edit_updates_latest_and_appends_without_rewriting(self):
        self.store.record_message(self.record())
        self.store.flush_archive()
        archive_path = self.path / "archive/2026-10-02.jsonl"
        original = archive_path.read_bytes()
        edit = self.record(text="edited", edit_date=datetime(2026, 10, 2, 10, tzinfo=UTC))
        self.store.record_message(edit, "message_edited")
        self.assertFalse(self.store.record_message(edit, "message_edited"))
        self.store.flush_archive()
        self.assertTrue(archive_path.read_bytes().startswith(original))
        self.assertEqual(self.store.get(-100123, 7)["text"], "edited")
        self.assertEqual(json.loads(self.store.get(-100123, 7)["raw_json"])["message"], "edited")
        self.assertEqual(
            [row["event_type"] for row in self.lines()], ["message_new", "message_edited"]
        )
        self.assertEqual(len(self.lines("important")), 2)

    def test_edit_before_new_and_stale_edit(self):
        new = self.record(text="latest", edit_date=datetime(2026, 10, 2, 10, tzinfo=UTC))
        self.store.record_message(new, "message_edited")
        self.assertFalse(self.store.record_message(self.record()))
        old = self.record(text="old edit", edit_date=datetime(2026, 10, 2, 9, tzinfo=UTC))
        self.store.record_message(old, "message_edited")
        self.assertEqual(self.store.get(-100123, 7)["text"], "latest")
        self.store.flush_archive()
        self.assertFalse(self.lines()[-1]["applied"])

    def test_known_delete_preserves_message_and_important(self):
        self.store.record_message(self.record())
        self.assertTrue(
            self.store.record_deleted(-100123, 7, received_at="2026-10-02T10:00:00+00:00")
        )
        self.assertFalse(self.store.record_deleted(-100123, 7))
        row = self.store.get(-100123, 7)
        self.assertIsNotNone(row["deleted_at"])
        self.assertEqual(row["text"], "原文\nUG-2530 🐈")
        self.store.flush_archive()
        self.assertEqual(self.lines()[-1]["event_type"], "message_deleted")
        self.assertEqual(len(self.lines("important")), 2)

    def test_unknown_chat_delete_never_guesses_even_when_id_is_unique(self):
        self.store.record_message(self.record())
        self.store.record_deleted(None, 7, received_at="2026-10-02T10:00:00+00:00")
        self.assertIsNone(self.store.get(-100123, 7)["deleted_at"])
        self.store.flush_archive()
        self.assertIsNone(self.lines()[-1]["chat_id"])
        self.assertFalse(self.lines()[-1]["chat_id_known"])

    def test_delete_before_new_preserves_tombstone(self):
        self.store.record_deleted(-100123, 7, reason="chat_id")
        self.store.record_message(self.record())
        row = self.store.get(-100123, 7)
        self.assertIsNotNone(row["deleted_at"])
        self.assertEqual(row["text"], "原文\nUG-2530 🐈")
        self.store.record_message(self.record(text="late edit"), "message_edited")
        self.assertIsNotNone(self.store.get(-100123, 7)["deleted_at"])

    def test_archive_failure_recovered_after_restart(self):
        self.store.record_message(self.record())
        with patch.object(self.store.archive, "append", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.store.flush_archive()
        self.assertEqual(self.store.pending_count(), 1)
        self.store.close()
        self.store = Storage(self.path)
        self.store.flush_archive()
        self.assertEqual(self.store.pending_count(), 0)
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(len(self.lines("important")), 1)

    def test_partial_archive_success_retries_important_only(self):
        self.store.record_message(self.record())
        append = self.store.archive.append

        def fail_important(payload, *, important=False):
            if important:
                raise OSError("disk full")
            append(payload)

        with patch.object(self.store.archive, "append", side_effect=fail_important):
            with self.assertRaises(OSError):
                self.store.flush_archive()
        self.store.flush_archive()
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(len(self.lines("important")), 1)
        self.assertEqual(self.lines()[0]["event_id"], self.lines("important")[0]["event_id"])

    def test_outbox_failure_rolls_back_message_transaction(self):
        with patch.object(self.store, "_enqueue", side_effect=sqlite3.OperationalError("full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.record_message(self.record())
        self.assertIsNone(self.store.get(-100123, 7))

    def test_crash_after_append_allows_dedup_by_event_id(self):
        self.store.record_message(self.record())
        row = self.store.connection.execute("SELECT payload FROM archive_outbox").fetchone()
        self.store.archive.append(
            json.loads(row[0])
        )  # Process dies before committing archive_done.
        self.store.close()
        self.store = Storage(self.path)
        self.store.flush_archive()
        lines = self.lines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["event_id"], lines[1]["event_id"])

    def test_torn_line_is_preserved_and_does_not_corrupt_more_records(self):
        path = self.path / "archive/2026-10-02.jsonl"
        path.write_bytes(b'{"partial":')
        self.store.record_message(self.record())
        with self.assertRaises(IncompleteArchiveError):
            self.store.flush_archive()
        self.assertEqual(path.read_bytes(), b'{"partial":')
        self.assertEqual(self.store.pending_count(), 1)

    def test_archive_day_is_event_receipt_date(self):
        self.store.record_message(self.record())
        edit = self.record(text="next day edit")
        edit["received_at"] = "2026-10-03T00:01:00.000000+00:00"
        self.store.record_message(edit, "message_edited")
        self.store.flush_archive()
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(self.lines(day="2026-10-03")[0]["event_type"], "message_edited")

    def test_queries_and_indexes(self):
        self.store.record_message(self.record())
        self.store.record_message(self.record(message_id=8, sender_id=99, text="other"))
        db = self.store.connection
        for condition, params, count in [
            ("chat_id=?", (-100123,), 2),
            ("sender_id=?", (42,), 1),
            ("important=?", (1,), 2),
            ("text LIKE ?", ("%UG-2530%",), 1),
            ("message_date>=? AND message_date<?", ("2026-10-02", "2026-10-03"), 2),
        ]:
            rows = db.execute(
                f"SELECT * FROM telegram_messages WHERE {condition} "
                "ORDER BY message_date DESC LIMIT 100",
                params,
            ).fetchall()
            self.assertEqual(len(rows), count)
        indexes = {row[1] for row in db.execute("PRAGMA index_list(telegram_messages)")}
        self.assertTrue(
            {"idx_chat_date", "idx_sender_date", "idx_important_date", "idx_message_date"}
            <= indexes
        )


if __name__ == "__main__":
    unittest.main()
