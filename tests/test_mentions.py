import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from telethon.tl.types import MessageEntityMention, MessageEntityMentionName

from app.mentions import mentioned_issues
from app.storage import Storage
from app.telegram_client import Recorder
from tests.fakes import event, settings

ME = SimpleNamespace(id=100, username="myself")


def mention(text="@MySelf UG-123 UG-456 UG-123", **kwargs):
    return event(text=text, entities=[MessageEntityMention(0, 7)], **kwargs)


class MentionTests(unittest.TestCase):
    def test_keys_dedup_boundaries_and_case(self):
        self.assertEqual(mentioned_issues(mention(), ME), ["UG-123", "UG-456"])
        self.assertEqual(
            mentioned_issues(mention("@myself XUG-1 UG-2x UG-3_ UG-４ ug-5 UG-6，查看UG-7"), ME),
            ["UG-6", "UG-7"],
        )

    def test_emoji_before_username_uses_utf16_offsets(self):
        msg = event(text="🐈 @MYSELF UG-123", entities=[MessageEntityMention(3, 7)])
        self.assertEqual(mentioned_issues(msg, ME), ["UG-123"])

    def test_user_id_mention_without_username(self):
        msg = event(text="Jack UG-9", entities=[MessageEntityMentionName(0, 4, 100)])
        self.assertEqual(mentioned_issues(msg, SimpleNamespace(id=100)), ["UG-9"])
        self.assertEqual(mentioned_issues(msg, SimpleNamespace(id=101)), [])

    def test_active_alternate_username(self):
        me = SimpleNamespace(id=100, usernames=[SimpleNamespace(username="myself", active=True)])
        self.assertEqual(mentioned_issues(mention(), me), ["UG-123", "UG-456"])
        me.usernames[0].active = False
        self.assertEqual(mentioned_issues(mention(), me), [])

    def test_excludes_private_channels_outgoing_other_mentions_and_replies(self):
        for msg in [
            mention(out=True),
            event(text="@myself UG-1", mentioned=True),
            event(text="@other UG-1", entities=[MessageEntityMention(0, 6)]),
        ]:
            self.assertEqual(mentioned_issues(msg, ME), [])
        for private in (True, False):
            msg = mention()
            msg.is_group = False
            msg.is_private = private
            self.assertEqual(mentioned_issues(msg, ME), [])
        self.assertEqual(mentioned_issues(mention(), None), [])
        self.assertEqual(mentioned_issues(mention("@myself hello"), ME), [])


class MentionStorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.store = Storage(self.path)
        self.recorder = Recorder(settings(self.path, watch_all=False), self.store)
        self.recorder.me = ME

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def rows(self):
        return [
            dict(row)
            for row in self.store.connection.execute(
                "SELECT * FROM ug_mentions ORDER BY chat_id, telegram_message_id, issue_key"
            )
        ]

    async def test_records_despite_watch_filter_and_duplicates_across_restart(self):
        await self.recorder.message(mention(), "message_new")
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.rows()[0]["issue_key"], "UG-123")
        self.assertEqual(self.store.get(-100123, 7)["important_reason"], "ug_mention")
        self.store.close()
        self.store = Storage(self.path)
        self.recorder.storage = self.store
        await self.recorder.message(mention(), "message_new")
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.store.pending_count(), 1)
        await self.recorder.message(mention(chat_id=-100456), "message_new")
        self.assertEqual(len(self.rows()), 4)
        self.store.flush_archive()
        lines = [
            json.loads(line)
            for file in (self.path / "important").glob("*.jsonl")
            for line in file.read_text().splitlines()
        ]
        self.assertEqual(lines[0]["ug_mentions"], ["UG-123", "UG-456"])

    async def test_edits_add_keys_and_keep_evidence_after_removal_and_deletion(self):
        await self.recorder.message(mention(), "message_new")
        edit = mention("@myself UG-123 UG-789 changed", edit_date=datetime(2026, 10, 3, tzinfo=UTC))
        await self.recorder.message(edit, "message_edited")
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual(self.rows()[0]["text"], edit.raw_text)
        old = mention("@myself UG-123 UG-000", edit_date=datetime(2026, 10, 2, tzinfo=UTC))
        await self.recorder.message(old, "message_edited")
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual(self.rows()[0]["text"], edit.raw_text)
        await self.recorder.message(
            event(text="removed", edit_date=datetime(2026, 10, 4, tzinfo=UTC)), "message_edited"
        )
        self.store.record_deleted(-100123, 7)
        self.assertEqual(len(self.rows()), 3)
        self.assertIsNotNone(self.store.get(-100123, 7)["deleted_at"])

    async def test_edit_can_create_first_match_with_existing_important_reason(self):
        self.recorder.settings = settings(self.path, chat_ids=frozenset([-100123]))
        await self.recorder.message(event(), "message_new")
        await self.recorder.message(mention(), "message_edited")
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.store.get(-100123, 7)["important_reason"], "chat_id,ug_mention")

    async def test_failed_outbox_rolls_back_issue_rows(self):
        # Stop at the first persistence failure instead of exercising the existing retry loop.
        async def once(operation):
            return operation()

        with (
            patch.object(self.recorder, "persist", side_effect=once),
            patch.object(self.store, "_enqueue", side_effect=sqlite3.OperationalError("full")),
            self.assertRaises(sqlite3.OperationalError),
        ):
            await self.recorder.message(mention(), "message_new")
        self.assertEqual(self.rows(), [])
        self.assertIsNone(self.store.get(-100123, 7))

    async def test_retry_does_not_duplicate_match_reason(self):
        enqueue = self.store._enqueue
        calls = 0

        def flaky(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("full")
            return enqueue(*args)

        with (
            patch.object(self.store, "_enqueue", side_effect=flaky),
            patch("app.telegram_client.asyncio.sleep"),
        ):
            await self.recorder.message(mention(), "message_new")
        self.assertEqual(self.store.get(-100123, 7)["important_reason"], "ug_mention")
        self.assertEqual(len(self.rows()), 2)
