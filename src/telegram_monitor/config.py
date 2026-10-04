"""Strict YAML schemas. Runtime paths are relative to the config directory's parent."""

import os
import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from telegram_monitor.exceptions import MonitorError

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
USERNAME = r"^@?[A-Za-z][A-Za-z0-9_]{3,31}$"
PositiveSeconds = Annotated[float, Field(gt=0, le=86400, allow_inf_nan=False)]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ButtonMatch(Schema):
    match: Literal["exact", "contains", "regex"] = "contains"
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_pattern(self):
        if not self.text.strip():
            raise ValueError("empty button text")
        if self.match == "regex":
            try:
                re.compile(self.text)
            except re.error:
                raise ValueError("invalid regex") from None
        return self


class TelegramSettings(Schema):
    api_id: int | None = Field(default=None, gt=0)
    api_id_env: str = "TELEGRAM_API_ID"
    api_hash_env: str = "TELEGRAM_API_HASH"
    session_file: str = "state/telegram.session"

    @field_validator("api_id_env", "api_hash_env")
    @classmethod
    def env_name(cls, value):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("invalid environment variable name")
        return value

    @field_validator("session_file")
    @classmethod
    def session_path(cls, value):
        if not value.strip() or "\0" in value or not value.endswith(".session"):
            raise ValueError("session_file must end in .session")
        return value


class Target(Schema):
    chat_id: int
    bot_username: str = Field(pattern=USERNAME)

    @field_validator("chat_id")
    @classmethod
    def chat_id_valid(cls, value):
        if not value or not -(2**63) < value < 2**63:
            raise ValueError("invalid chat id")
        return value


class Defaults(Schema):
    message_timeout_seconds: PositiveSeconds = 30
    deploy_timeout_seconds: PositiveSeconds = 900
    poll_interval_seconds: PositiveSeconds = 1


class Settings(Schema):
    telegram: TelegramSettings
    targets: dict[str, Target] = Field(min_length=1)
    defaults: Defaults = Field(default_factory=Defaults)


class Entry(Schema):
    command: str = Field(pattern=r"^/[A-Za-z0-9_]+(?:@[A-Za-z0-9_]+)?$")
    # Exact labels explicitly verified as navigation; never broad regex allowlists.
    safe_buttons: list[str] = Field(default_factory=list)
    back_button: ButtonMatch | None = None

    @field_validator("safe_buttons")
    @classmethod
    def unique_labels(cls, value):
        if any(not text.strip() for text in value) or len(set(value)) != len(value):
            raise ValueError("safe buttons must be nonempty and unique")
        return value


class Environment(Schema):
    environment: str = Field(pattern=NAME.pattern)
    target: str = "deployment"
    entry: Entry


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate mappings rather than silently replacing a definition."""


def unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in result:
                raise ValueError("duplicate YAML key")
            result[key] = loader.construct_object(value_node, deep=deep)
        except TypeError:
            raise ValueError("invalid YAML mapping key") from None
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def read_schema(path: Path, schema):
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
        return schema.model_validate(data)
    except ValidationError as exc:
        # ValidationError normally includes input values, which may be credentials.
        issues = [
            {"field": ".".join(map(str, item["loc"])), "type": item["type"]}
            for item in exc.errors(include_input=False, include_url=False)
        ]
        raise MonitorError("invalid_config", issues=issues) from None
    except (OSError, ValueError, yaml.YAMLError, RecursionError):
        raise MonitorError("invalid_config", reason="unreadable_or_invalid_yaml") from None


class Configuration:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.root = self.path.parent.parent
        self.settings = read_schema(self.path, Settings)
        self.session = (self.root / self.settings.telegram.session_file).resolve()
        self.state_dir = self.session.parent

    def environment(self, name: str) -> Environment:
        if not NAME.fullmatch(name):
            raise MonitorError("invalid_config", reason="invalid_environment_name")
        path = self.path.parent / f"{name}.yaml"
        if not path.is_file():
            raise MonitorError("environment_not_found", environment=name)
        env = read_schema(path, Environment)
        if env.environment != name or env.target not in self.settings.targets:
            raise MonitorError("invalid_config", reason="environment_or_target_mismatch")
        return env

    def credentials(self) -> tuple[int, str]:
        values = {**dotenv_values(self.root / ".env"), **os.environ}
        conf = self.settings.telegram
        try:
            api_id = conf.api_id or int(values.get(conf.api_id_env) or "")
            api_hash = values.get(conf.api_hash_env) or ""
            if api_id <= 0 or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
                raise ValueError
        except (TypeError, ValueError):
            raise MonitorError(
                "invalid_config", reason="missing_or_invalid_api_credentials"
            ) from None
        return api_id, api_hash

    def target(self, name: str) -> Target:
        if name not in self.settings.targets:
            raise MonitorError("invalid_config", reason="target_not_found")
        return self.settings.targets[name]
