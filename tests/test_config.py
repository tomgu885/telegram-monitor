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


if __name__ == "__main__":
    unittest.main()
