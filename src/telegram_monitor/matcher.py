"""Deterministic button selection, with conservative exploration safety gates."""

import re
import unicodedata

from telegram_monitor.config import ButtonMatch
from telegram_monitor.exceptions import MonitorError
from telegram_monitor.models import Button

DANGEROUS = re.compile(
    r"发布|部署|删除|回滚|重启|确认|执行|销毁|停止|启动|清空|重置|"
    r"deploy|publish|release|delete|destroy|rollback|restart|reboot|confirm|"
    r"execute|remove|terminate|shutdown|stop|start|reset|purge|build|构建",
    re.IGNORECASE,
)


def dangerous(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Cf")
    return bool(DANGEROUS.search(normalized))


def select_button(
    buttons: list[Button], *, rule: ButtonMatch | None = None, index: int | None = None
) -> Button:
    if (rule is None) == (index is None):
        raise MonitorError("invalid_config", reason="select_exactly_one_of_text_or_index")
    if index is not None:
        matches = [buttons[index]] if 0 <= index < len(buttons) else []
        expected = index
    else:
        expected = rule.text
        matches = [
            button
            for button in buttons
            if (
                button.text == rule.text
                if rule.match == "exact"
                else rule.text in button.text
                if rule.match == "contains"
                else re.search(rule.text, button.text) is not None
            )
        ]
    if len(matches) != 1:
        raise MonitorError(
            "unexpected_menu" if matches else "button_not_found",
            expected=expected,
            available_buttons=[b.text for b in buttons],
        )
    return matches[0]


def require_navigation(button: Button, safe_buttons: list[str]) -> None:
    if (
        button.kind not in {"callback", "keyboard"}
        or dangerous(button.text)
        or button.text not in safe_buttons
    ):
        raise MonitorError(
            "action_not_allowed",
            button=button.text,
            reason="phase1_requires_verified_safe_navigation_label",
        )
