import json

import pytest

from telegram_monitor.config import ButtonMatch, Configuration, Environment, read_schema
from telegram_monitor.exceptions import MonitorError


def test_example_config_and_environment(configuration):
    assert configuration.target("deployment").chat_id == -100123
    assert configuration.environment("testa").entry.command == "/menu"
    assert configuration.session == configuration.root / "state/telegram.session"


def test_credentials_use_environment_over_dotenv(configuration, monkeypatch):
    (configuration.root / ".env").write_text("TELEGRAM_API_HASH=invalid-secret\n")
    monkeypatch.setenv("TELEGRAM_API_HASH", "a" * 32)
    assert configuration.credentials() == (12345, "a" * 32)


def test_missing_secret_never_appears_in_error(configuration, monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_HASH", "private-secret")
    with pytest.raises(MonitorError) as exc:
        configuration.credentials()
    assert "private-secret" not in json.dumps(exc.value.payload())


@pytest.mark.parametrize("name", ["../outside", "", "/tmp/testa"])
def test_environment_path_traversal(configuration, name):
    with pytest.raises(MonitorError, match="invalid_config"):
        configuration.environment(name)


def test_missing_environment(configuration):
    with pytest.raises(MonitorError) as exc:
        configuration.environment("missing")
    assert exc.value.code == 20


@pytest.mark.parametrize(
    "content",
    [
        "environment: testa\nenvironment: testa\nentry: {command: /menu}",
        "environment: testa\nentry: {command: /menu, back_button: {match: regex, text: '['}}",
        "environment: testa\nentry: {command: /menu, safe_buttons: ['', '']}",
        "environment: testa\nentry: {command: /menu, unknown: true}",
        "environment: testa\nentry: {command: ''}",
        "[]",
        "[",
        "private-secret",
    ],
)
def test_invalid_yaml_schema_does_not_echo_values(tmp_path, content):
    path = tmp_path / "bad.yaml"
    path.write_text(content)
    with pytest.raises(MonitorError) as exc:
        read_schema(path, Environment)
    assert exc.value.code == 22
    assert "private-secret" not in json.dumps(exc.value.payload())


def test_env_name_and_target_must_match(configuration):
    path = configuration.path.parent / "testa.yaml"
    path.write_text("environment: uat\nentry: {command: /menu}\n")
    with pytest.raises(MonitorError, match="invalid_config"):
        configuration.environment("testa")


def test_schema_collects_multiple_missing_fields(tmp_path):
    path = tmp_path / "telegram.yaml"
    path.write_text("telegram: {}\ntargets: {deployment: {}}\n")
    with pytest.raises(MonitorError) as exc:
        Configuration(path)
    assert len(exc.value.details["issues"]) == 2


def test_match_default():
    assert ButtonMatch(text="RPC").match == "contains"
