"""Tests for decision_engine and calibration sigma resolution."""

import unittest
from datetime import date
from config.settings import RuntimeSettings
from forecast.parse_title import BracketKind, ParsedTempMarket
from research.calibration_apply import mae_global, resolved_research_sigma_c, sigma_from_mae
from strategy.decision_engine import evaluate_opportunity


def _minimal_settings(**overrides) -> RuntimeSettings:
    d = {
        "buy_min_threshold": 0.05,
        "buy_max_threshold": 0.75,
        "buy_disable_price_band": False,
        "stop_loss_threshold": 0.5,
        "stop_loss_use_entry_tiers": True,
        "stop_loss_tier_entry_split": 0.6,
        "stop_loss_tier_mark_low": 0.3,
        "stop_loss_tier_mark_high": 0.4,
        "take_profit_threshold": 0.96,
        "min_lead_over_runner_up": 0.1,
        "enable_competition_filter": True,
        "enable_momentum": False,
        "momentum_window_min": 10,
        "momentum_rise": 0.25,
        "momentum_min_price": 0.4,
        "momentum_max_entry": 0.65,
        "momentum_peer_drop": 0.1,
        "churn_max_stop_cycles": 1,
        "churn_cooldown_sec": 1200,
        "scan_interval_seconds": 30,
        "min_order_notional_usd": 2.0,
        "max_buy_notional_usd": 4.0,
        "max_trade_fraction_of_cash": 0.9,
        "dashboard_weather_max_pages": 6,
        "cash_reserve_usd": 2.0,
        "forecast_digest_enabled": False,
        "forecast_digest_refresh_interval_sec": 600,
        "forecast_digest_telegram_interval_sec": 0,
        "forecast_digest_scope": "scan",
        "forecast_digest_max_location_groups": 80,
        "enable_openweather_forecast": False,
        "enable_flow_sampling": True,
        "flow_max_events_per_tick": 8,
        "enable_flow_peer_exit": True,
        "flow_peer_window_sec": 600,
        "flow_peer_surge_drop": 0.12,
        "flow_peer_surge_rise": 0.1,
        "forecast_gate_buy": True,
        "forecast_contradict_margin_c": 1.5,
        "forecast_exact_bucket_support_slack_c": 2.5,
        "forecast_reduce_usd_if_weak": True,
        "forecast_weak_size_factor": 0.75,
        "research_exit_on_model_flip": False,
        "research_edge_gate_buy": True,
        "research_min_edge": 0.08,
        "research_min_edge_after_fees_add": 0.0,
        "research_sigma_c": 0.0,
        "research_weather_taker_fee_rate": 0.05,
        "buy_latest_local_hour": 20,
        "research_edge_scale_size": False,
        "research_edge_size_slope": 2.0,
        "research_edge_size_cap_mult": 1.35,
        "research_crowd_soft_match": False,
        "research_crowd_soft_band": 0.04,
        "research_crowd_soft_edge_factor": 0.75,
        "research_crowd_disagree_gap": 0.08,
        "research_crowd_disagree_extra_edge": 0.03,
        "research_skip_telegram_cooldown_sec": 600,
        "max_market_prob_for_buy": 0.75,
        "min_model_prob_for_buy": 0.10,
        "max_positions_per_event": 1,
        "decision_min_model_peak_prob": 0.12,
        "enable_peer_surge_exit": True,
        "peer_surge_window_min": 10,
        "peer_surge_rise_threshold": 0.2,
        "peer_surge_event_cooldown_sec": 1200,
        "peer_surge_skip_buy_enabled": False,
        "peer_surge_skip_buy_rise_threshold": 0.25,
    }
    d.update(overrides)
    return RuntimeSettings.from_dict(d)


class TestDecisionEngine(unittest.TestCase):
    def test_sigma_from_mae_floors(self) -> None:
        self.assertGreaterEqual(sigma_from_mae(0.001), 0.5)

    def test_resolved_sigma_explicit(self) -> None:
        s = resolved_research_sigma_c(1.8, "london")
        self.assertAlmostEqual(s, 1.8, places=3)

    def test_mae_global_positive(self) -> None:
        self.assertGreater(mae_global(), 0.5)

    def test_skip_high_market_prob(self) -> None:
        p = ParsedTempMarket(
            city_key="london",
            tz_name="Europe/London",
            event_date=date(2026, 4, 15),
            bracket=BracketKind.EXACT,
            threshold_c=17.0,
            raw_title="Will the highest temperature in London be 17°C on April 15?",
        )
        st = _minimal_settings()
        td = evaluate_opportunity(
            p,
            17.0,
            0.90,
            st,
            {},
            event_id="e1",
            event_market_ids={"1", "2"},
            peer_context=None,
        )
        self.assertEqual(td.decision, "SKIP")
        self.assertIn("max_market_prob", td.reason)


if __name__ == "__main__":
    unittest.main()
