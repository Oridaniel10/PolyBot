"""Unit tests for strategy/research_signal.py."""

import unittest

from forecast.parse_title import parse_highest_temp_title
from strategy.research_signal import (
    edge_implied_vs_market,
    research_edge_decision,
    round_trip_fee_prob_drag,
)


class TestResearchSignal(unittest.TestCase):
    def test_round_trip_fee_drag_positive_at_mid(self) -> None:
        self.assertGreater(round_trip_fee_prob_drag(0.5, 0.05), 0.0)

    def test_edge_positive_when_model_hotter_than_market(self) -> None:
        self.assertAlmostEqual(edge_implied_vs_market(0.7, 0.6), 0.1)

    def test_research_edge_gate_blocks_when_edge_too_low(self) -> None:
        p = parse_highest_temp_title(
            "Will the highest temperature in London be 17°C on April 14?"
        )
        self.assertIsNotNone(p)
        d = research_edge_decision(
            p,
            17.0,
            0.65,
            sigma_c=2.0,
            min_edge=0.50,
            min_edge_after_fees_add=0.0,
            fee_rate_for_drag=0.05,
            gate_enabled=True,
            soft_match_enabled=False,
            soft_band=0.04,
            soft_edge_factor=0.75,
            disagree_extra_edge=0.0,
            disagree_gap=0.08,
        )
        self.assertFalse(d.passes_gate)
        self.assertIn("research_edge", d.skip_reason)

    def test_soft_boost_raises_edge_when_implied_above_floor(self) -> None:
        p = parse_highest_temp_title(
            "Will the highest temperature in Atlanta be between 84-85°F on April 16?"
        )
        self.assertIsNotNone(p)
        d = research_edge_decision(
            p,
            28.5,
            0.615,
            sigma_c=1.2,
            min_edge=0.08,
            min_edge_after_fees_add=0.0,
            fee_rate_for_drag=0.05,
            gate_enabled=True,
            soft_match_enabled=False,
            soft_band=0.04,
            soft_edge_factor=0.75,
            disagree_extra_edge=0.0,
            disagree_gap=0.08,
            implied_soft_floor=0.30,
            implied_soft_boost_mult=1.0,
        )
        self.assertGreater(d.implied_yes, 0.30)
        self.assertGreater(d.edge_soft_boost, 0.2)
        self.assertAlmostEqual(d.edge, d.edge_raw + d.edge_soft_boost, places=6)
        self.assertTrue(d.passes_gate)

    def test_no_soft_boost_when_implied_at_or_below_floor(self) -> None:
        p = parse_highest_temp_title(
            "Will the highest temperature in Atlanta be between 84-85°F on April 16?"
        )
        self.assertIsNotNone(p)
        d = research_edge_decision(
            p,
            22.0,
            0.50,
            sigma_c=1.2,
            min_edge=0.08,
            min_edge_after_fees_add=0.0,
            fee_rate_for_drag=0.05,
            gate_enabled=True,
            soft_match_enabled=False,
            soft_band=0.04,
            soft_edge_factor=0.75,
            disagree_extra_edge=0.0,
            disagree_gap=0.08,
            implied_soft_floor=0.30,
            implied_soft_boost_mult=1.0,
        )
        self.assertLessEqual(d.implied_yes, 0.30 + 1e-9)
        self.assertAlmostEqual(d.edge_soft_boost, 0.0, places=6)
        self.assertAlmostEqual(d.edge, d.edge_raw, places=6)


if __name__ == "__main__":
    unittest.main()
