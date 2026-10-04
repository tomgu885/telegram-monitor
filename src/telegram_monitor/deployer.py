"""Single-service deployment. All navigation/result rules come from verified YAML."""

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from telegram_monitor.config import Configuration
from telegram_monitor.exceptions import MonitorError
from telegram_monitor.matcher import require_navigation, select_button
from telegram_monitor.menu import Menu
from telegram_monitor.state import CursorStore, private_directory
from telegram_monitor.watcher import CorrelationMatcher, wait_result

logger = logging.getLogger(__name__)
FORBIDDEN = re.compile(r"删除|销毁|回滚|重启|批量|delete|destroy|rollback|restart|batch", re.I)


def append_history(directory: Path, record: dict):
    private_directory(directory)
    fd = os.open(directory / "deploy-history.jsonl", os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


async def deploy(config: Configuration, client, env_name: str, service_name: str) -> dict:
    env = config.environment(env_name)
    target = config.target(env.target)
    candidates = [
        name
        for name, entry in env.services.items()
        if name == service_name or service_name in entry.aliases
    ]
    if not candidates:
        raise MonitorError("service_not_found", service=service_name)
    if len(candidates) != 1:
        raise MonitorError("invalid_config", reason="ambiguous_service_alias")
    service_name = candidates[0]
    service = env.services[service_name]
    results = env.deployment_result
    if results is None or service_name not in results.correlation:
        raise MonitorError("invalid_config", reason="deployment_result_rules_required")
    if results.sender_username.lower().lstrip("@") != target.bot_username.lower().lstrip("@"):
        raise MonitorError("invalid_config", reason="deployment_sender_must_match_target")
    if len(service.menu_path) < 2 or service.menu_path[-1].match != "exact":
        raise MonitorError("invalid_config", reason="final_action_requires_exact_match")
    if not service.expected_context:
        raise MonitorError("invalid_config", reason="expected_context_required")

    store = CursorStore(config.state_dir, f"{config.path}:{config.session}")
    menu = Menu(client, env, target, store, config.settings.defaults.message_timeout_seconds)
    await menu.open()
    for rule in service.menu_path[:-1]:
        source = await menu.current(None)
        button = select_button(source.buttons, rule=rule)
        require_navigation(button, env.entry.safe_buttons)
        logger.info(
            "deploy navigation env=%s service=%s message_id=%s button=%s",
            env_name,
            service_name,
            source.message_id,
            json.dumps(button.text),
        )
        await menu.click(None, rule=rule)
    source = await menu.current(None)
    if any(text not in source.text for text in service.expected_context):
        raise MonitorError(
            "unexpected_menu",
            reason="deployment_context_mismatch",
            current_message=source.payload(),
        )
    action = select_button(source.buttons, rule=service.menu_path[-1])
    if action.kind not in {"callback", "keyboard"} or FORBIDDEN.search(action.text):
        raise MonitorError("action_not_allowed", reason="not_a_single_service_deployment")

    # Subscribe BEFORE the final click; accept no result from before this boundary.
    async with client.updates() as (queue, overflow):
        boundary = await client.latest_message_id()
        started = datetime.now(UTC)
        matcher = CorrelationMatcher(
            target.chat_id,
            results.sender_username,
            boundary,
            started.replace(microsecond=0),
            source.message_id,
            results.correlation[service_name],
        )
        record = {
            "environment": env_name,
            "service": service_name,
            "telegram_service": service.aliases[0] if service.aliases else service_name,
            "started_at": started.isoformat(),
            "trigger_message_id": source.message_id,
            "after_message_id": boundary,
        }
        # Persist the intent before the RPC. This plus the session/recorder lock
        # makes a second local deployment impossible while this wait is active.
        append_history(config.state_dir, {**record, "status": "triggering"})
        store.clear()
        logger.info(
            "deploy action env=%s service=%s message_id=%s button=%s",
            env_name,
            service_name,
            source.message_id,
            json.dumps(action.text),
        )
        try:
            async with asyncio.timeout(results.timeout_seconds):
                # RPC retries are disabled by the adapter. A callback timeout can
                # still mean the action ran; keep waiting rather than clicking again.
                try:
                    async with asyncio.timeout(config.settings.defaults.message_timeout_seconds):
                        sent_id = await client.trigger_button(source, action)
                        if sent_id:
                            matcher.reply_ids.add(sent_id)
                except TimeoutError:
                    logger.warning(
                        "Deployment callback timed out; waiting for result without retry."
                    )
                status, result, pattern = await wait_result(queue, overflow, matcher, results)
                record.update(
                    status=status,
                    result_message_id=result.message_id,
                    result_text=result.text,
                    matched_pattern=pattern,
                )
                if status == "failed":
                    record["error"] = "deployment_failed"
        except TimeoutError:
            record.update(status="timeout", error="deploy_timeout", action_may_have_completed=True)
        except BaseException:
            append_history(
                config.state_dir, {**record, "status": "unknown", "action_may_have_completed": True}
            )
            raise
        finished = datetime.now(UTC)
        record.update(
            finished_at=finished.isoformat(),
            duration_seconds=round((finished - started).total_seconds(), 3),
            correlation_keys=matcher.keys,
        )
        append_history(config.state_dir, record)
        return record
