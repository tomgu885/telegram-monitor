"""Deployment result correlation: identity, high-water ID, time, service and build keys."""

import asyncio
import logging
import re
from datetime import datetime

from telegram_monitor.config import Correlation, DeploymentResult, Patterns
from telegram_monitor.exceptions import MonitorError
from telegram_monitor.models import Message

logger = logging.getLogger(__name__)


def match_pattern(text: str, patterns: Patterns) -> str | None:
    for pattern in patterns.contains:
        if pattern in text:
            return pattern
    for pattern in patterns.regex:
        if re.search(pattern, text):
            return pattern
    return None


class CorrelationMatcher:
    def __init__(
        self,
        chat_id: int,
        sender: str,
        boundary: int,
        started: datetime,
        trigger_id: int,
        rules: Correlation,
    ):
        self.chat_id = chat_id
        self.sender = sender.lower().lstrip("@")
        self.boundary = boundary
        self.started = started
        self.reply_ids = {trigger_id}
        self.trigger_id = trigger_id
        self.rules = rules
        self.keys: dict[str, str] = {}

    def observe_trigger(self, message: Message) -> bool:
        if (
            message.chat_id != self.chat_id
            or (message.sender_username or "").lower().lstrip("@") != self.sender
            or message.message_id != self.trigger_id
            or message.edit_date is None
            or message.edit_date < self.started
        ):
            return False
        # The bot can edit the original menu into an acknowledgement. Use its
        # task/build key as context, but never treat the old message as a result.
        for name, pattern in self.rules.key_patterns.items():
            found = re.search(pattern, message.text)
            if found:
                self.keys.setdefault(name, found.group("value"))
        return True

    def accepts(self, message: Message) -> bool:
        if (
            message.chat_id != self.chat_id
            or (message.sender_username or "").lower().lstrip("@") != self.sender
            or message.message_id <= self.boundary
            or message.date < self.started
        ):
            return False
        reply_matches = message.reply_to_msg_id in self.reply_ids
        if message.reply_to_msg_id and not reply_matches:
            return False
        service_matches = any(re.search(p, message.text) for p in self.rules.service_patterns)
        values = {}
        for name, pattern in self.rules.key_patterns.items():
            found = re.search(pattern, message.text)
            if found:
                values[name] = found.group("value")
        # Once the bot announced a build/task, another build of the SAME service
        # must not satisfy the wait. Absence of that key needs an explicit reply.
        if any(name in values and values[name] != value for name, value in self.keys.items()):
            return False
        key_matches = bool(self.keys) and all(values.get(k) == v for k, v in self.keys.items())
        if self.keys and not (key_matches or reply_matches):
            return False
        if not (reply_matches or service_matches or key_matches):
            return False
        self.keys.update(values)
        self.reply_ids.add(message.message_id)
        return True


async def wait_result(queue, overflow, matcher: CorrelationMatcher, config: DeploymentResult):
    while True:
        message = await queue.get()
        if overflow.is_set():
            raise MonitorError("unexpected_menu", reason="deployment_update_overflow")
        trigger_update = matcher.observe_trigger(message)
        if trigger_update and any(
            re.search(r"确认|确定|confirm", b.text, re.I) for b in message.buttons
        ):
            raise MonitorError(
                "unexpected_menu",
                reason="additional_confirmation_required",
                current_message=message.payload(),
            )
        if not matcher.accepts(message):
            continue
        logger.info(
            "Deployment update message_id=%s correlation=%s", message.message_id, matcher.keys
        )
        # A failed build may also include 完成时间; failure takes precedence.
        pattern = match_pattern(message.text, config.failure)
        if pattern:
            return "failed", message, pattern
        pattern = match_pattern(message.text, config.success)
        if pattern:
            return "success", message, pattern
        await asyncio.sleep(0)
