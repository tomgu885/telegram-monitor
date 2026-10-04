import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from app import __main__ as entrypoint

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("arguments", [[], ["--check-config"]])
def test_legacy_invocations_keep_recorder_entrypoint(monkeypatch, arguments):
    recorder = Mock(return_value=7)
    monkeypatch.setattr(sys, "argv", ["app", *arguments])
    monkeypatch.setattr(entrypoint, "recorder_main", recorder)
    assert entrypoint.main() == 7
    recorder.assert_called_once_with()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--help"],
        ["auth", "login", "--help"],
        ["auth", "status", "--help"],
        ["menu", "click", "--help"],
        ["watch", "--help"],
    ],
)
def test_python_app_exposes_cli_help_without_credentials(arguments):
    result = subprocess.run(
        [sys.executable, "-m", "app", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout


def test_python_app_preserves_json_and_exit_codes(configuration):
    command = [
        sys.executable,
        "-m",
        "app",
        "--config",
        str(configuration.path),
        "config",
        "validate",
        "--env",
    ]
    valid = subprocess.run(
        command + ["testa"], cwd=ROOT, capture_output=True, text=True, timeout=10
    )
    assert valid.returncode == 0
    assert json.loads(valid.stdout)["status"] == "ok"
    invalid = subprocess.run(
        command + ["missing"], cwd=ROOT, capture_output=True, text=True, timeout=10
    )
    assert invalid.returncode == 22
    assert json.loads(invalid.stdout)["error"] == "invalid_config"
