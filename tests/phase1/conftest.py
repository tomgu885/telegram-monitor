from datetime import UTC, datetime

import pytest

from telegram_monitor.config import Configuration
from telegram_monitor.models import Button, Message


@pytest.fixture
def configuration(tmp_path):
    directory = tmp_path / "config"
    directory.mkdir()
    (directory / "telegram.yaml").write_text(
        "telegram:\n  api_id: 12345\n"
        "targets:\n  deployment:\n    chat_id: -100123\n    bot_username: example_bot\n"
    )
    (directory / "testa.yaml").write_text(
        "environment: testa\nentry:\n  command: /menu\n"
        "  safe_buttons: ['RPC 服务', '🚀 payment-rpc-testa', '返回']\n"
        "  back_button: {match: exact, text: 返回}\n"
    )
    return Configuration(directory / "telegram.yaml")


def message(message_id=100, text="menu", buttons=None, revision="revision-1"):
    return Message(
        -100123,
        message_id,
        datetime.now(UTC),
        42,
        "example_bot",
        text,
        buttons=buttons if buttons is not None else [Button(0, 0, "RPC 服务")],
        revision=revision,
    )
