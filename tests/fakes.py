from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings


class FakeMessage(SimpleNamespace):
    def to_dict(self):
        return {"_": "Message", **vars(self)}


def settings(path=Path("unused"), **kwargs):
    return Settings(
        api_id=123, api_hash="test-only-hash", phone="test-only-phone", data_dir=path, **kwargs
    )


def event(chat_id=-100123, sender_id=42, message_id=7, text="原文\nUG-2530 🐈", **kwargs):
    message = FakeMessage(
        id=message_id,
        date=datetime(2026, 10, 2, 1, 31, tzinfo=UTC),
        message=text,
        out=False,
        reply_to_msg_id=5,
        reply_to={"reply_to_msg_id": 5},
        entities=[],
        fwd_from={"from_id": 987},
        media=None,
        file=None,
        edit_date=None,
    )
    for name, value in kwargs.items():
        setattr(message, name, value)
    return SimpleNamespace(
        message=message,
        chat_id=chat_id,
        sender_id=sender_id,
        chat=SimpleNamespace(title="test group"),
        sender=SimpleNamespace(username="alice", first_name="Alice", last_name="A"),
        raw_text=text,
        is_private=False,
        is_channel=True,
        is_group=True,
    )
