"""Dedupe helpers for decision-skip Telegram."""

import unittest

from notifications.research_trade_fmt import (
    decision_skip_telegram_allowed,
    decision_skip_telegram_category,
)


class TestDecisionSkipTelegram(unittest.TestCase):
    def test_category_stable_across_edge_noise(self) -> None:
        a = "research_edge edge=-0.1957 < required=0.1288 (implied=0.5543 clob=0.7500)"
        b = "research_edge edge=-0.0857 < required=0.1330 (implied=0.5543 clob=0.6400)"
        self.assertEqual(decision_skip_telegram_category(a), "research_edge")
        self.assertEqual(decision_skip_telegram_category(b), "research_edge")

    def test_dedupe_same_market_category_within_cooldown(self) -> None:
        mid = "m1"
        r1 = "research_edge edge=-0.1 < required=0.2 (implied=0.5 clob=0.7)"
        r2 = "research_edge edge=-0.2 < required=0.2 (implied=0.5 clob=0.71)"
        self.assertTrue(decision_skip_telegram_allowed(mid, r1, 600.0))
        self.assertFalse(decision_skip_telegram_allowed(mid, r2, 600.0))

    def test_different_categories_not_deduped(self) -> None:
        mid = "m2"
        self.assertTrue(
            decision_skip_telegram_allowed(
                mid, "model_prob 0.07 < min_model_prob_for_buy=0.10", 600.0
            )
        )
        self.assertTrue(
            decision_skip_telegram_allowed(
                mid, "research_edge edge=-0.1 < required=0.2", 600.0
            )
        )


if __name__ == "__main__":
    unittest.main()
