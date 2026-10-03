"""Extract issue keys only from incoming group messages explicitly mentioning us."""

import re

from telethon.tl.types import MessageEntityMention, MessageEntityMentionName

ISSUE_PATTERN = re.compile(r"(?<![A-Za-z0-9_])UG-[0-9]+(?![A-Za-z0-9_])")


def mentioned_issues(event, me) -> list[str]:
    if me is None or not event.is_group or getattr(event.message, "out", False):
        return []
    text = event.raw_text or ""
    issues = list(dict.fromkeys(ISSUE_PATTERN.findall(text)))
    if not issues:
        return []
    usernames = {
        name.casefold()
        for name in [getattr(me, "username", None)]
        + [item.username for item in (getattr(me, "usernames", None) or []) if item.active]
        if name
    }
    encoded = text.encode("utf-16-le")
    for entity in event.message.entities or []:
        if isinstance(entity, MessageEntityMentionName) and entity.user_id == me.id:
            return issues
        if isinstance(entity, MessageEntityMention):
            start, end = entity.offset * 2, (entity.offset + entity.length) * 2
            if start < 0 or end > len(encoded) or end <= start:
                continue
            mention = encoded[start:end].decode("utf-16-le", errors="replace")
            if mention.startswith("@") and mention[1:].casefold() in usernames:
                return issues
    return []
