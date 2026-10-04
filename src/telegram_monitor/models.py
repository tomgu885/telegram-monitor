"""Transport independent message snapshots; no session or callback payload in JSON."""

from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Button:
    row: int
    column: int
    text: str
    kind: str = "callback"


@dataclass(frozen=True)
class Message:
    chat_id: int
    message_id: int
    date: datetime
    sender_id: int | None
    sender_username: str | None
    text: str
    reply_to_msg_id: int | None = None
    buttons: list[Button] = field(default_factory=list)
    edit_date: datetime | None = None
    revision: str = field(default="", repr=False)

    def payload(self) -> dict:
        result = asdict(self)
        result.pop("revision")
        result["date"] = self.date.isoformat()
        result["edit_date"] = self.edit_date.isoformat() if self.edit_date else None
        return result
