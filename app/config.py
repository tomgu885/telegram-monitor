"""Configuration is rooted at the project, independent of the launch directory."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(ValueError):
    """A safe-to-display configuration error (never includes supplied values)."""


@dataclass(frozen=True)
class ScreenshotSettings:
    enabled: bool = False
    screenshot_uids: frozenset[int] = frozenset()
    commands: frozenset[str] = frozenset({"截图", "screenshot"})
    temp_dir: Path = ROOT / "data" / "screenshots"
    allow_groups: bool = False
    keep_failed: bool = False
    cooldown_seconds: float = 5


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str = field(repr=False)
    phone: str = field(repr=False)
    data_dir: Path = ROOT / "data"
    watch_all: bool = True
    chat_ids: frozenset[int] = frozenset()
    user_ids: frozenset[int] = frozenset()
    screenshot: ScreenshotSettings = field(default_factory=ScreenshotSettings)

    def important_reason(self, chat_id: int, sender_id: int | None) -> str:
        reasons = []
        if chat_id in self.chat_ids:
            reasons.append("chat_id")
        if sender_id is not None and sender_id > 0 and sender_id in self.user_ids:
            reasons.append("user_id")
        return ",".join(reasons)


def load_settings(root: Path = ROOT) -> Settings:
    values = {**dotenv_values(root / ".env"), **os.environ}
    try:
        api_id = int(values.get("TELEGRAM_API_ID") or "")
        if api_id <= 0:
            raise ValueError
    except ValueError:
        raise ConfigError("请在 .env 设置有效的 TELEGRAM_API_ID（正整数）。") from None
    api_hash = values.get("TELEGRAM_API_HASH") or ""
    phone = values.get("TELEGRAM_PHONE") or ""
    if not api_hash.strip() or not phone.strip():
        raise ConfigError("请在 .env 配置 TELEGRAM_API_HASH 和 TELEGRAM_PHONE。")
    try:
        config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raise ConfigError(
            "无法读取 config.yaml；请复制 config.example.yaml 并检查 YAML。"
        ) from None
    if not isinstance(config, dict):
        raise ConfigError("config.yaml 必须是 YAML mapping。")
    telegram = config.get("telegram", {})
    important = config.get("important", {})
    if not isinstance(telegram, dict) or not isinstance(important, dict):
        raise ConfigError("telegram 和 important 必须是 YAML mapping。")
    watch_all = telegram.get("watch_all", True)
    if type(watch_all) is not bool:
        raise ConfigError("telegram.watch_all 必须是布尔值 true/false。")

    def ids(name: str) -> frozenset[int]:
        entries = important.get(name, [])
        if not isinstance(entries, list) or any(
            type(item) is not int
            or item == 0
            or not -(2**63) < item < 2**63
            or (name == "user_ids" and item < 0)
            for item in entries
        ):
            raise ConfigError(f"important.{name} 必须是有效的整数 ID 列表。")
        return frozenset(entries)

    return Settings(
        api_id,
        api_hash.strip(),
        phone.strip(),
        root / "data",
        watch_all,
        ids("chat_ids"),
        ids("user_ids"),
        load_screenshot_settings(config.get("screenshot", {}), root),
    )


def load_screenshot_settings(config: object, root: Path) -> ScreenshotSettings:
    if not isinstance(config, dict):
        raise ConfigError("screenshot 必须是 YAML mapping。")
    flags = {}
    for name in ("enabled", "allow_groups", "keep_failed"):
        value = config.get(name, False)
        if type(value) is not bool:
            raise ConfigError(f"screenshot.{name} 必须是布尔值 true/false。")
        flags[name] = value
    entries = config.get("screenshot_uids", [])
    if not isinstance(entries, list) or any(
        type(item) is not int or not 0 < item < 2**63 for item in entries
    ):
        raise ConfigError("screenshot.screenshot_uids 必须是正整数 ID 列表。")
    commands = config.get("commands", ["截图", "screenshot"])
    if (
        not isinstance(commands, list)
        or not commands
        or any(not isinstance(item, str) or not item.strip() for item in commands)
    ):
        raise ConfigError("screenshot.commands 必须是非空命令字符串列表。")
    cooldown = config.get("cooldown_seconds", 5)
    if (
        type(cooldown) not in (int, float)
        or not 0 < cooldown <= 86400
        or not math.isfinite(cooldown)
    ):
        raise ConfigError("screenshot.cooldown_seconds 必须是大于 0 且不超过 86400 的秒数。")
    directory = config.get("temp_dir", "data/screenshots")
    if not isinstance(directory, str) or not directory.strip() or "\0" in directory:
        raise ConfigError("screenshot.temp_dir 必须是非空路径字符串。")
    return ScreenshotSettings(
        **flags,
        screenshot_uids=frozenset(entries),
        commands=frozenset(item.strip().lower() for item in commands),
        temp_dir=root / directory,
        cooldown_seconds=float(cooldown),
    )
