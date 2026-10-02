import json
import unittest
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

from telethon.tl.types import Message, PeerChannel, PeerUser

from app.models import iso_time, normalize, parse_message
from tests.fakes import event, settings


class ParserTests(unittest.TestCase):
    def test_text_entities_forward_reply_and_identity(self):
        record = parse_message(event(), settings())
        self.assertEqual(record["text"], "原文\nUG-2530 🐈")
        self.assertEqual(record["sender_id"], 42)
        self.assertEqual(record["sender_username"], "alice")
        self.assertEqual(record["chat_type"], "supergroup")
        self.assertEqual(record["chat_title"], "test group")
        self.assertEqual(record["reply_to_message_id"], 5)
        self.assertEqual(record["raw_json"]["fwd_from"], {"from_id": 987})
        self.assertEqual(record["message_date"], "2026-10-02T01:31:00.000000+00:00")

    def test_important_or_matching(self):
        for chats, users, expected in [
            ([], [], ""),
            ([-100123], [], "chat_id"),
            ([], [42], "user_id"),
            ([-100123], [42], "chat_id,user_id"),
        ]:
            with self.subTest(expected=expected):
                record = parse_message(
                    event(), settings(chat_ids=frozenset(chats), user_ids=frozenset(users))
                )
                self.assertEqual(record["important_reason"], expected)
                self.assertEqual(record["important"], bool(expected))

    def test_no_inferred_user_for_anonymous_channel_sender(self):
        record = parse_message(event(sender_id=-100999), settings(user_ids=frozenset([100999])))
        self.assertEqual(record["sender_id"], -100999)
        self.assertFalse(record["important"])

    def test_missing_entities_are_null(self):
        source = event(sender_id=None)
        source.sender = source.chat = None
        record = parse_message(source, settings())
        self.assertIsNone(record["sender_username"])
        self.assertIsNone(record["chat_title"])

    def test_media_metadata_without_download(self):
        source = event(
            media=SimpleNamespace(),
            file=SimpleNamespace(name="sample.mp4", mime_type="video/mp4", size=12345),
        )
        record = parse_message(source, settings())
        self.assertTrue(record["has_media"])
        self.assertEqual(record["file_name"], "sample.mp4")
        self.assertEqual(record["file_size"], 12345)
        self.assertEqual(record["mime_type"], "video/mp4")

    def test_normalization_bytes_cycles_and_unsupported(self):
        class Broken:
            def to_dict(self):
                raise ValueError("sensitive content")

            def __repr__(self):
                raise ValueError("must not call repr")

        circular = []
        circular.append(circular)
        value = normalize(
            {
                "bytes": b"\x00\xff",
                "date": datetime(2026, 1, 1, tzinfo=UTC),
                "object": object(),
                "broken": Broken(),
                "cycle": circular,
                "nan": float("nan"),
            }
        )
        encoded = json.dumps(value, allow_nan=False)
        self.assertNotIn("sensitive", encoded)
        self.assertEqual(value["bytes"]["data"], "AP8=")
        self.assertEqual(value["cycle"], [{"_unsupported": "cycle"}])
        self.assertEqual(value["broken"], {"_serialization_error": "Broken"})

    def test_real_telethon_message_snapshot(self):
        source = event()
        source.message = Message(
            id=7,
            peer_id=PeerChannel(123),
            from_id=PeerUser(42),
            date=datetime(2026, 10, 2, tzinfo=UTC),
            message="原文",
        )
        source.raw_text = "原文"
        record = parse_message(source, settings())
        self.assertEqual(record["raw_json"]["peer_id"]["channel_id"], 123)
        json.dumps(record, allow_nan=False)

    def test_timezone_conversion(self):
        local = datetime(2026, 10, 2, 9, 31, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(iso_time(local), "2026-10-02T01:31:00.000000+00:00")


if __name__ == "__main__":
    unittest.main()
