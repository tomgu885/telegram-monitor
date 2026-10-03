import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import ConfigError, load_settings


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        (self.path / ".env").write_text(
            "TELEGRAM_API_ID=123\nTELEGRAM_API_HASH=secret-hash\nTELEGRAM_PHONE=secret-phone\n"
        )
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_valid_config_and_secret_repr(self):
        (self.path / "config.yaml").write_text(
            "telegram:\n  watch_all: false\nimportant:\n  chat_ids: [-100123]\n  user_ids: [42]\n"
        )
        config = load_settings(self.path)
        self.assertFalse(config.watch_all)
        self.assertEqual(config.important_reason(-100123, 42), "chat_id,user_id")
        self.assertNotIn("secret", repr(config))

    def test_invalid_values_are_rejected_without_echo(self):
        for yaml in [
            'telegram: {watch_all: "false"}',
            "important: {user_ids: [-42]}",
            "important: {chat_ids: [true]}",
            'important: {chat_ids: ["secret"]}',
            "telegram: []",
            "[secret]",
            "broken: [",
        ]:
            with self.subTest(yaml=yaml):
                (self.path / "config.yaml").write_text(yaml)
                with self.assertRaises(ConfigError) as error:
                    load_settings(self.path)
                self.assertNotIn("secret", str(error.exception))

    def test_environment_override(self):
        (self.path / "config.yaml").write_text("{}")
        os.environ["TELEGRAM_API_ID"] = "456"
        self.assertEqual(load_settings(self.path).api_id, 456)

    def test_screenshot_defaults_are_opt_in_and_root_relative(self):
        (self.path / "config.yaml").write_text("{}")
        screenshot = load_settings(self.path).screenshot
        self.assertFalse(screenshot.enabled)
        self.assertFalse(screenshot.allow_groups)
        self.assertFalse(screenshot.keep_failed)
        self.assertEqual(screenshot.screenshot_uids, frozenset())
        self.assertEqual(screenshot.commands, frozenset({"截图", "screenshot"}))
        self.assertEqual(screenshot.temp_dir, self.path / "data/screenshots")
        self.assertEqual(screenshot.cooldown_seconds, 5)

    def test_screenshot_custom_config(self):
        (self.path / "config.yaml").write_text("""
screenshot:
  enabled: true
  screenshot_uids: [42, 43]
  commands: [" 截图 ", "CAPTURE"]
  temp_dir: "data/custom-screenshots"
  allow_groups: true
  keep_failed: true
  cooldown_seconds: 2.5
""")
        screenshot = load_settings(self.path).screenshot
        self.assertTrue(screenshot.enabled)
        self.assertTrue(screenshot.allow_groups)
        self.assertTrue(screenshot.keep_failed)
        self.assertEqual(screenshot.screenshot_uids, frozenset({42, 43}))
        self.assertEqual(screenshot.commands, frozenset({"截图", "capture"}))
        self.assertEqual(screenshot.temp_dir, self.path / "data/custom-screenshots")
        self.assertEqual(screenshot.cooldown_seconds, 2.5)

    def test_invalid_screenshot_settings_fail_closed_without_values(self):
        for value in (
            "[]",
            "null",
            '{enabled: "secret"}',
            "{allow_groups: 1}",
            '{keep_failed: "false"}',
            "{screenshot_uids: [true]}",
            "{screenshot_uids: [-42]}",
            "{screenshot_uids: [0]}",
            "{screenshot_uids: [9223372036854775808]}",
            '{screenshot_uids: ["secret"]}',
            "{screenshot_uids: 42}",
            "{commands: []}",
            '{commands: ["  "]}',
            "{commands: [42]}",
            '{commands: "secret"}',
            '{temp_dir: ""}',
            "{temp_dir: 42}",
            "{cooldown_seconds: true}",
            "{cooldown_seconds: -1}",
            "{cooldown_seconds: 0}",
            "{cooldown_seconds: .nan}",
            "{cooldown_seconds: .inf}",
            "{cooldown_seconds: 86401}",
        ):
            with self.subTest(value=value):
                (self.path / "config.yaml").write_text(f"screenshot: {value}\n")
                with self.assertRaises(ConfigError) as error:
                    load_settings(self.path)
                self.assertNotIn("secret", str(error.exception))


if __name__ == "__main__":
    unittest.main()
