import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from telethon import errors
from typer.testing import CliRunner

from telegram_monitor.cli import app
from telegram_monitor.exceptions import MonitorError

from .conftest import message

runner = CliRunner()


def invoke(configuration, *args):
    return runner.invoke(app, ["--config", str(configuration.path), *args])


def test_validate_is_offline_and_detects_all_env_errors(configuration, monkeypatch):
    monkeypatch.setattr("telegram_monitor.cli.connect", lambda *a, **k: pytest.fail("network"))
    result = invoke(configuration, "config", "validate", "--env", "testa")
    assert result.exit_code == 0
    assert json.loads(result.stdout)["credentials_checked"] is False
    (configuration.path.parent / "uat.yaml").write_text("environment: uat\n")
    (configuration.path.parent / "testa.yaml").write_text("environment: testa\n")
    result = invoke(configuration, "config", "validate")
    assert result.exit_code == 22
    assert len(json.loads(result.stdout)["issues"]) == 2


@pytest.mark.parametrize(
    ("error", "code", "name"),
    [
        (MonitorError("menu_not_found"), 30, "menu_not_found"),
        (errors.FloodWaitError(None, capture=42), 42, "telegram_rate_limit"),
        (errors.SessionRevokedError(None), 41, "telegram_auth_error"),
        (ConnectionError("private-secret"), 40, "telegram_connection_error"),
        (RuntimeError("private-secret"), 50, "internal_error"),
    ],
)
def test_errors_are_json_with_stable_exit_and_no_secret(
    configuration, monkeypatch, error, code, name
):
    @asynccontextmanager
    async def failed_connect(*args, **kwargs):
        raise error
        yield  # pragma: no cover

    monkeypatch.setattr("telegram_monitor.cli.connect", failed_connect)
    result = invoke(configuration, "auth", "status", "--json")
    assert result.exit_code == code
    assert json.loads(result.stdout)["error"] == name
    assert "private-secret" not in result.output


def test_menu_commands_across_invocations(configuration, monkeypatch):
    @asynccontextmanager
    async def fake_connect(*args, **kwargs):
        yield object()

    client = AsyncMock()
    client.open_menu.return_value = client.get_message.return_value = message()
    client.click_button.return_value = message(101)
    monkeypatch.setattr("telegram_monitor.cli.connect", fake_connect)
    monkeypatch.setattr("telegram_monitor.cli.TelethonMenuClient", lambda *args: client)
    result = invoke(configuration, "menu", "open", "--env", "testa", "--json")
    assert result.exit_code == 0
    assert json.loads(result.stdout)["message_id"] == 100
    result = invoke(configuration, "menu", "click", "--text", "RPC", "--json")
    assert result.exit_code == 0
    assert json.loads(result.stdout)["clicked"] == "RPC 服务"
    assert "click env=testa" in result.stderr
    client.click_button.assert_awaited_once()


@pytest.mark.parametrize(
    "args", [[], ["--text", "a", "--index", "0"], ["--text", "[", "--match", "regex"]]
)
def test_bad_selector_is_json_error(configuration, args):
    result = invoke(configuration, "menu", "click", *args)
    assert result.exit_code == 22
    assert json.loads(result.stdout)["error"] == "invalid_config"


def test_auth_status_unauthorized_never_logs_in(configuration, monkeypatch):
    client = AsyncMock()
    client.is_user_authorized.return_value = False

    @asynccontextmanager
    async def fake_connect(*args, **kwargs):
        yield client

    monkeypatch.setattr("telegram_monitor.cli.connect", fake_connect)
    result = invoke(configuration, "auth", "status")
    assert result.exit_code == 41
    assert json.loads(result.stdout)["authorized"] is False
    client.send_code_request.assert_not_awaited()
