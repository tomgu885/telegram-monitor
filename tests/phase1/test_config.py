import json

import pytest

from telegram_monitor.config import ButtonMatch, Configuration, Environment, read_schema
from telegram_monitor.exceptions import MonitorError


def test_example_config_and_environment(configuration):
    assert configuration.target("deployment").chat_id == -100123
    assert configuration.environment("testa").entry.command == "/menu"
    assert configuration.session == configuration.root / "data/telegram.session"


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


def test_root_shared_config_uses_existing_credentials_and_session(tmp_path, monkeypatch):
    from app.config import load_settings

    (tmp_path / "config.yaml").write_text(
        "telegram: {watch_all: false}\nimportant: {chat_ids: [], user_ids: []}\n"
        "screenshot: {enabled: false}\n"
        "targets:\n  testa: {chat_id: -100123, bot_username: example_bot}\n"
        "  uat: {chat_id: -456, bot_username: uat_bot}\n"
    )
    (tmp_path / ".env").write_text(
        "TELEGRAM_API_ID=12345\nTELEGRAM_API_HASH=" + "a" * 32 + "\nTELEGRAM_PHONE=+12345\n"
    )
    for key in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_PHONE"):
        monkeypatch.delenv(key, raising=False)
    for env in ("testa", "uat"):
        (tmp_path / f"{env}.yaml").write_text(f"environment: {env}\nentry: {{command: /menu}}\n")
    cli = Configuration(tmp_path / "config.yaml")
    recorder = load_settings(tmp_path)
    assert cli.root == tmp_path
    assert cli.session == recorder.data_dir / "telegram.session"
    assert cli.credentials() == (recorder.api_id, recorder.api_hash)
    assert cli.environment_names() == ["testa", "uat"]
    assert cli.environment("testa").target == "testa"
    assert cli.environment("uat").target == "uat"
    assert cli.target("testa").chat_id != cli.target("uat").chat_id
    with pytest.raises(MonitorError) as exc:
        cli.target(None)
    assert exc.value.details["reason"] == "target_required_use_chat_option"


def test_auth_config_loads_without_groups_and_null_groups_are_blocked(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("telegram: {watch_all: true}\n")
    assert Configuration(path).session == tmp_path / "data/telegram.session"
    path.write_text("targets: {testa: {chat_id: null, bot_username: example_bot}}\n")
    config = Configuration(path)
    with pytest.raises(MonitorError) as exc:
        config.target("testa")
    assert exc.value.details["reason"] == "chat_id_not_configured"


def test_legacy_explicit_config_keeps_relative_paths(tmp_path):
    directory = tmp_path / "config"
    directory.mkdir()
    path = directory / "telegram.yaml"
    path.write_text(
        "telegram: {session_file: state/telegram.session}\n"
        "targets: {deployment: {chat_id: -123, bot_username: example_bot}}\n"
    )
    (directory / "testa.yaml").write_text("environment: testa\nentry: {command: /menu}\n")
    config = Configuration(path)
    assert config.session == tmp_path / "state/telegram.session"
    assert config.environment("testa").target == "deployment"


def test_root_menu_paths_accept_strings_and_validate_regex(tmp_path):
    path = tmp_path / "testa.yaml"
    path.write_text(
        "environment: testa\nentry: {command: /menu}\n"
        "services:\n  payment-rpc:\n    menu_path:\n"
        "      - 'RPC 服务'\n      - {match: exact, text: payment-rpc-testa}\n"
    )
    env = read_schema(path, Environment)
    assert env.services["payment-rpc"].menu_path[0].match == "contains"
    path.write_text(
        path.read_text().replace("match: exact, text: payment-rpc-testa", "match: regex, text: '['")
    )
    with pytest.raises(MonitorError, match="invalid_config"):
        read_schema(path, Environment)


def test_empty_service_path_rejected(tmp_path):
    path = tmp_path / "uat.yaml"
    path.write_text("environment: uat\nentry: {command: /menu}\nservices: {rpc: {menu_path: []}}\n")
    with pytest.raises(MonitorError, match="invalid_config"):
        read_schema(path, Environment)
