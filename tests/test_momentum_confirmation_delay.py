"""Tests for momentum / double_momentum confirmation delay gate."""

from __future__ import annotations

import time
from datetime import date
from unittest.mock import patch

import pytest

from config.settings import RuntimeSettings, default_runtime_dict
from forecast.parse_title import BracketKind, ParsedTempMarket
from strategy import redis_store
from strategy.decision_core import evaluate_entry
from strategy.momentum import append_price_sample


def make_settings(**overrides) -> RuntimeSettings:
    data = default_runtime_dict()
    data.update(overrides)
    return RuntimeSettings.from_dict(data)


def parsed_market() -> ParsedTempMarket:
    return ParsedTempMarket(
        city_key="london",
        tz_name="Europe/London",
        event_date=date(2026, 4, 26),
        bracket=BracketKind.EXACT,
        threshold_c=17.0,
        raw_title="Will the highest temperature in London be 17°C on April 26?",
    )


def add_samples(market_id: str, prices: list[float], *, step_sec: int = 60) -> None:
    now = time.time()
    if redis_store.is_connected():
        redis_store._client.delete(f"prices:{market_id}")
    for index, price in enumerate(prices):
        append_price_sample(
            market_id,
            price,
            ts=now - (len(prices) - index - 1) * step_sec,
        )


def momentum_settings(**overrides) -> RuntimeSettings:
    base = {
        "momentum_min_price": 0.40,
        "momentum_max_entry": 0.95,
        "enable_competition_filter": False,
    }
    base.update(overrides)
    return make_settings(**base)


class FakeEventClient:
    def __init__(self, siblings):
        self.siblings = siblings

    def get_markets_for_event_id(self, _event_id):
        return self.siblings


@pytest.fixture
def momentum_market_setup(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    add_samples("m1", [0.78, 0.58, 0.42])
    add_samples("m2", [0.42, 0.55, 0.69])
    market = {
        "id": "m2",
        "eventId": "e1",
        "outcomePrices": "[0.69, 0.31]",
        "question": parsed_market().raw_title,
    }
    siblings = [
        {"id": "m1", "outcomePrices": "[0.80, 0.20]"},
        {"id": "m2", "outcomePrices": "[0.69, 0.31]"},
    ]
    return market, siblings


def eval_momentum(
    market,
    siblings,
    state,
    settings,
    *,
    market_yes: float = 0.69,
):
    return evaluate_entry(
        parsed_market(),
        17.0,
        market_yes,
        market,
        FakeEventClient(siblings),
        settings,
        state,
        {},
    )


def test_first_trigger_creates_pending_and_skips(momentum_market_setup):
    market, siblings = momentum_market_setup
    state: dict = {}
    settings = momentum_settings(momentum_confirmation_delay_sec=60.0)

    decision = eval_momentum(market, siblings, state, settings)

    assert decision.decision == "SKIP"
    assert "momentum_pending_confirmation" in decision.reason
    pending = state["pending_momentum_confirms"]["m2"]
    assert pending["entry_type"] == "momentum"
    assert abs(float(pending["ts"]) - time.time()) < 5.0


def test_second_call_before_delay_still_skips(momentum_market_setup):
    market, siblings = momentum_market_setup
    state = {
        "pending_momentum_confirms": {
            "m2": {"ts": time.time() - 30, "entry_type": "momentum"},
        }
    }
    settings = momentum_settings(momentum_confirmation_delay_sec=60.0)

    decision = eval_momentum(market, siblings, state, settings)

    assert decision.decision == "SKIP"
    assert "remaining=" in decision.reason


def test_second_call_after_delay_buys_and_clears_pending(momentum_market_setup):
    market, siblings = momentum_market_setup
    state = {
        "pending_momentum_confirms": {
            "m2": {"ts": time.time() - 65, "entry_type": "momentum"},
        }
    }
    settings = momentum_settings(momentum_confirmation_delay_sec=60.0)

    decision = eval_momentum(market, siblings, state, settings)

    assert decision.decision == "BUY"
    assert decision.entry_type == "momentum"
    assert "m2" not in (state.get("pending_momentum_confirms") or {})


def test_disabled_delay_buys_immediately_without_pending(momentum_market_setup):
    market, siblings = momentum_market_setup
    state: dict = {}
    settings = momentum_settings(momentum_confirmation_delay_sec=0.0)

    decision = eval_momentum(market, siblings, state, settings)

    assert decision.decision == "BUY"
    assert decision.entry_type == "momentum"
    assert not state.get("pending_momentum_confirms")


def test_expired_pending_garbage_collected_and_recreated(momentum_market_setup):
    market, siblings = momentum_market_setup
    old_ts = time.time() - 700
    state = {
        "pending_momentum_confirms": {
            "m2": {"ts": old_ts, "entry_type": "momentum"},
        }
    }
    settings = momentum_settings(
        momentum_confirmation_delay_sec=60.0,
        momentum_confirmation_max_age_sec=600.0,
    )

    decision = eval_momentum(market, siblings, state, settings)

    assert decision.decision == "SKIP"
    assert "momentum_pending_confirmation" in decision.reason
    new_ts = float(state["pending_momentum_confirms"]["m2"]["ts"])
    assert new_ts > old_ts


def test_normal_winner_bypasses_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    market_id = "nw1"
    now = time.time()
    for offset in range(3600, 0, -120):
        append_price_sample(market_id, 0.92, ts=now - offset)
    append_price_sample(market_id, 0.94, ts=now - 5)
    market = {
        "id": market_id,
        "eventId": "e_nw",
        "outcomePrices": "[0.94, 0.06]",
        "question": parsed_market().raw_title,
    }
    state: dict = {}
    settings = make_settings(
        normal_winner_enabled=True,
        momentum_confirmation_delay_sec=60.0,
    )
    with patch("strategy.decision_core.compute_model_prob", return_value=0.95):
        decision = evaluate_entry(
            parsed_market(),
            17.0,
            0.94,
            market,
            FakeEventClient([market]),
            settings,
            state,
            {},
        )

    assert decision.decision == "BUY"
    assert decision.entry_type == "normal_winner"
    assert not state.get("pending_momentum_confirms")


def test_normal_entry_bypasses_confirmation():
    market = {
        "id": "normal1",
        "outcomePrices": "[0.80, 0.20]",
        "question": parsed_market().raw_title,
    }
    state: dict = {}
    settings = make_settings(
        enable_competition_filter=False,
        max_market_prob_for_buy=0.99,
        momentum_confirmation_delay_sec=60.0,
    )
    with patch("strategy.decision_core.compute_model_prob", return_value=0.95):
        decision = evaluate_entry(
            parsed_market(),
            17.0,
            0.80,
            market,
            FakeEventClient([]),
            settings,
            state,
            {},
        )

    assert decision.decision == "BUY"
    assert decision.entry_type == "normal"
    assert not state.get("pending_momentum_confirms")


def test_configurable_delay_120_sec(momentum_market_setup):
    market, siblings = momentum_market_setup
    settings = momentum_settings(momentum_confirmation_delay_sec=120.0)

    state_wait = {
        "pending_momentum_confirms": {
            "m2": {"ts": time.time() - 90, "entry_type": "momentum"},
        }
    }
    decision_wait = eval_momentum(market, siblings, state_wait, settings)
    assert decision_wait.decision == "SKIP"
    assert "remaining=" in decision_wait.reason

    state_buy = {
        "pending_momentum_confirms": {
            "m2": {"ts": time.time() - 130, "entry_type": "momentum"},
        }
    }
    decision_buy = eval_momentum(market, siblings, state_buy, settings)
    assert decision_buy.decision == "BUY"
    assert decision_buy.entry_type == "momentum"
