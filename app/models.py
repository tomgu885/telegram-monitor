"""JSON-safe snapshots; no pickle, downloads, or Telegram mutations."""

import base64
import math
from datetime import UTC, date, datetime
from typing import Any

from app.config import Settings


def utc_now() -> str:
    return iso_time(datetime.now(UTC))


def iso_time(value: datetime) -> str:
    # Telegram dates are UTC; naive fake/legacy dates are interpreted as UTC.
    return (
        value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC).isoformat(timespec="microseconds")
    )


def normalize(value: Any, seen: set[int] | None = None, depth: int = 0) -> Any:
    """Keep TL dictionaries, encode bytes, and label unsupported/cyclic fields.

    Do not call arbitrary repr/str: these may throw or expose unrelated state.
    A broken field becomes a marker instead of discarding the whole message.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"_nonfinite_float": str(value)}
    if isinstance(value, datetime):
        return iso_time(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"_encoding": "base64", "data": base64.b64encode(bytes(value)).decode("ascii")}
    if depth > 50:
        return {"_truncated": "maximum_depth"}
    seen = set() if seen is None else seen
    if id(value) in seen:
        return {"_unsupported": "cycle"}
    seen.add(id(value))
    try:
        if isinstance(value, dict):
            return {
                (
                    key
                    if isinstance(key, str)
                    else str(key)
                    if isinstance(key, (int, bool))
                    else f"_key_{index}"
                ): normalize(item, seen, depth + 1)
                for index, (key, item) in enumerate(value.items())
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [normalize(item, seen, depth + 1) for item in value]
        if callable(getattr(value, "to_dict", None)):
            return normalize(value.to_dict(), seen, depth + 1)
        return {"_unsupported": type(value).__name__}
    except Exception:
        return {"_serialization_error": type(value).__name__}
    finally:
        seen.remove(id(value))


def parse_message(event: Any, settings: Settings, *, received_at: str | None = None) -> dict:
    message = event.message
    chat_id = event.chat_id
    if chat_id is None:
        raise ValueError("Message has no chat ID")
    chat = getattr(event, "chat", None)
    sender = getattr(event, "sender", None)
    sender_id = event.sender_id
    reason = settings.important_reason(chat_id, sender_id)
    if getattr(event, "is_private", False):
        chat_type = "private"
    elif getattr(event, "is_channel", False):
        chat_type = "supergroup" if getattr(event, "is_group", False) else "channel"
    elif getattr(event, "is_group", False):
        chat_type = "group"
    else:
        chat_type = "unknown"
    title = getattr(chat, "title", None)
    if title is None and chat_type == "private" and chat is not None:
        title = (
            " ".join(
                filter(None, [getattr(chat, "first_name", None), getattr(chat, "last_name", None)])
            )
            or None
        )
    received_at = received_at or utc_now()
    raw = normalize(message)
    if not isinstance(raw, dict):
        raw = {"message_object": raw}
    raw.update(chat_id=chat_id, sender_id=sender_id)
    # Preserve core fields even if message.to_dict() itself failed.
    for name in ("id", "date", "message", "reply_to", "entities", "media", "fwd_from", "edit_date"):
        raw.setdefault(name, normalize(getattr(message, name, None)))
    file = getattr(message, "file", None)
    media = getattr(message, "media", None)
    edited_at = getattr(message, "edit_date", None)
    return {
        "telegram_message_id": message.id,
        "chat_id": chat_id,
        "chat_type": chat_type,
        "chat_title": title,
        "sender_id": sender_id,
        # Channel/anonymous senders retain their marked negative ID. Never infer a user.
        "sender_username": getattr(sender, "username", None),
        "sender_first_name": getattr(sender, "first_name", None),
        "sender_last_name": getattr(sender, "last_name", None),
        "message_date": iso_time(message.date),
        "received_at": received_at,
        "text": event.raw_text or "",
        "reply_to_message_id": getattr(message, "reply_to_msg_id", None),
        "is_outgoing": bool(getattr(message, "out", False)),
        "important": bool(reason),
        "important_reason": reason,
        "raw_json": raw,
        "has_media": media is not None,
        "media_type": type(media).__name__ if media is not None else None,
        "file_name": getattr(file, "name", None),
        "mime_type": getattr(file, "mime_type", None),
        "file_size": getattr(file, "size", None),
        "edited_at": iso_time(edited_at) if edited_at else None,
        "deleted_at": None,
        "created_at": received_at,
    }
