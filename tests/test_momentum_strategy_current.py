import time
from datetime import date
from unittest.mock import patch

from config.settings import RuntimeSettings, default_runtime_dict
from forecast.parse_title import BracketKind, ParsedTempMarket
from strategy.decision_core import TradeDecision, evaluate_entry
from strategy.momentum import append_price_sample
from strategy.momentum_engine import momentum_entry_signal, peer_surge_detected
from strategy.sync_portfolio import sync_state_with_portfolio
from strategy.trades import close_position, place_buy, process_single_market, set_trade_lock


class DummyTelegram:
    def is_configured(self) -> bool:
        return False

    def send_html_chunks(self, *_args, **_kwargs) -> None:
        raise AssertionError("telegram should not be called")


class FakeEventClient:
    def __init__(self, siblings):
        self.siblings = siblings

    def get_markets_for_event_id(self, _event_id):
        return self.siblings


class FailIfCalledClient:
    def get_clob_yes_price(self, _market):
        raise AssertionError("buy lock should stop clob lookup")

    def get_yes_shares_on_exchange_for_market_id(self, _market_id):
        raise AssertionError("sell lock should stop exchange lookup")


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


def add_samples(market_id: str, prices: list[float]) -> None:
    now = time.time()
    for index, price in enumerate(prices):
        append_price_sample(market_id, price, ts=now - (len(prices) - index - 1) * 60)


def test_momentum_entry_uses_absolute_points(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    add_samples("relative_only", [0.40, 0.43, 0.46])
    add_samples("absolute_move", [0.40, 0.48, 0.55])

    signal, rise = momentum_entry_signal("relative_only", rise_threshold=0.15)
    assert signal is False
    assert round(rise, 3) == 0.06

    signal, rise = momentum_entry_signal("absolute_move", rise_threshold=0.15)
    assert signal is True
    assert round(rise, 3) == 0.15


def test_peer_surge_uses_absolute_points(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    add_samples("peer_a", [0.30, 0.33, 0.345])
    add_samples("peer_b", [0.30, 0.39, 0.45])

    surged, market_id, rise = peer_surge_detected(
        ["peer_a", "peer_b"],
        surge_threshold=0.15,
    )

    assert surged is True
    assert market_id == "peer_b"
    assert round(rise, 3) == 0.15


def test_momentum_entry_does_not_require_rank_one(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    add_samples("m2", [0.49, 0.56, 0.65])
    siblings = [
        {"id": "m1", "outcomePrices": "[0.80, 0.20]"},
        {"id": "m2", "outcomePrices": "[0.65, 0.35]"},
    ]
    market = {
        "id": "m2",
        "eventId": "e1",
        "outcomePrices": "[0.65, 0.35]",
        "question": parsed_market().raw_title,
    }

    decision = evaluate_entry(
        parsed_market(),
        17.0,
        0.65,
        market,
        FakeEventClient(siblings),
        make_settings(),
        {},
        {},
    )

    assert decision.decision == "BUY"
    assert decision.entry_type == "momentum"
    assert decision.event_yes_rank == 2


def test_normal_entry_high_band_not_blocked_by_market_prob(monkeypatch):
    market = {
        "id": "normal",
        "outcomePrices": "[0.80, 0.20]",
        "question": parsed_market().raw_title,
    }
    settings = make_settings(
        enable_competition_filter=False,
        research_edge_gate_buy=False,
        max_market_prob_for_buy=0.99,
    )

    with patch("strategy.decision_core.compute_model_prob", return_value=0.95):
        decision = evaluate_entry(
            parsed_market(),
            17.0,
            0.80,
            market,
            FakeEventClient([]),
            settings,
            {},
            {},
        )

    assert decision.decision == "BUY"
    assert decision.entry_type == "normal"


def test_double_momentum_low_band_reaches_place_buy(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    add_samples("double", [0.01, 0.16, 0.32])
    market = {
        "id": "double",
        "outcomePrices": "[0.32, 0.68]",
        "question": parsed_market().raw_title,
    }
    decision = TradeDecision(
        city="london",
        date_iso="2026-04-26",
        chosen_bucket=parsed_market().raw_title,
        model_prob=0.1,
        market_prob=0.32,
        edge=0.0,
        consensus_c=17.0,
        sigma_used=1.0,
        decision="BUY",
        reason="momentum_entry_ride_the_wave",
        momentum_relaxed_gates=True,
        event_yes_rank=3,
        entry_type="double_momentum",
    )

    with (
        patch("strategy.trades.market_can_post_clob_orders", return_value=True),
        patch("strategy.trades.entry_time_allowed", return_value=(True, 15, "")),
        patch("strategy.trades.parse_highest_temp_title", return_value=parsed_market()),
        patch("strategy.trades.get_forecast_max_for_city_day", return_value=(None, None, 17.0)),
        patch("strategy.trades.evaluate_entry", return_value=decision),
        patch("strategy.trades.place_buy") as buy_mock,
    ):
        process_single_market(
            FakeEventClient([]),
            DummyTelegram(),
            {"active_trades": {}},
            market,
            make_settings(),
            {},
            allow_new_buys=True,
        )

    assert buy_mock.called


def test_synced_manual_position_has_sync_origin():
    class Client:
        def get_open_positions(self):
            return [
                {
                    "market_id": "manual",
                    "condition_id": "cond",
                    "size": 5.0,
                    "avg_price": 0.42,
                    "cur_price": 0.45,
                    "title": "Manual position",
                }
            ]

    state = sync_state_with_portfolio(Client(), {})

    trade = state["active_trades"]["manual"]
    assert trade["last_action"] == "sync"
    assert trade["entry_price"] == 0.42


def test_buy_and_sell_locks_prevent_duplicate_orders():
    market = {"id": "locked", "outcomePrices": "[0.70, 0.30]"}
    state = {
        "active_trades": {
            "locked": {
                "market_id": "locked",
                "shares": 5.0,
                "entry_price": 0.70,
                "position_title": "Locked",
            }
        }
    }
    set_trade_lock(state, "buy_market", "locked", 60)
    place_buy(
        FailIfCalledClient(),
        market,
        state,
        DummyTelegram(),
        0.70,
        make_settings(),
        {},
    )

    set_trade_lock(state, "sell_market", "locked", 60)
    close_position(
        FailIfCalledClient(),
        market,
        state,
        DummyTelegram(),
        0.60,
        "stop-loss",
        make_settings(),
    )
