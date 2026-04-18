"""Telegram command text normalization."""

import unittest

from telegram_bot import normalize_telegram_command_text


class TestTelegramCommandNormalize(unittest.TestCase):
    def test_strips_bot_suffix(self) -> None:
        self.assertEqual(
            normalize_telegram_command_text("/Report@MyPolyBot"),
            "/report",
        )
        self.assertEqual(
            normalize_telegram_command_text("/status@test_bot"),
            "/status",
        )

    def test_first_line_only(self) -> None:
        self.assertEqual(
            normalize_telegram_command_text("REPORT\nextra"),
            "report",
        )


if __name__ == "__main__":
    unittest.main()
