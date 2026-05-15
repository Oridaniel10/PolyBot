import time
from datetime import date
from unittest.mock import patch

from config.settings import RuntimeSettings, default_runtime_dict
from forecast.parse_title import BracketKind, ParsedTempMarket
from strategy.decision_core import TradeDecision, evaluate_entry
from strategy.momentum import append_price_sample, load_samples_for_market
from strategy import redis_store
from strategy.momentum_engine import momentum_entry_signal, peer_surge_detected, price_change_in_window
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
    # clear Redis data for this market so tests are isolated
    if redis_store.is_connected():
        redis_store._client.delete(f"prices:{market_id}")
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
    add_samples("m1", [0.78, 0.58, 0.42])
    add_samples("m2", [0.42, 0.55, 0.69])
    siblings = [
        {"id": "m1", "outcomePrices": "[0.80, 0.20]"},
        {"id": "m2", "outcomePrices": "[0.69, 0.31]"},
    ]
    market = {
        "id": "m2",
        "eventId": "e1",
        "outcomePrices": "[0.69, 0.31]",
        "question": parsed_market().raw_title,
    }

    decision = evaluate_entry(
        parsed_market(),
        17.0,
        0.69,
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


def test_normal_entry_skips_when_market_lead_gap_below_configured_half():
    market = {
        "id": "normal",
        "eventId": "e1",
        "outcomePrices": "[0.86, 0.14]",
        "question": parsed_market().raw_title,
    }
    siblings = [
        market,
        {"id": "other", "outcomePrices": "[0.45, 0.55]", "question": "other bucket"},
    ]
    settings = make_settings(
        enable_competition_filter=True,
        max_market_prob_for_buy=0.99,
        min_market_yes_lead_gap_normal=0.5,
    )
    with patch("strategy.decision_core.compute_model_prob", return_value=0.95):
        decision = evaluate_entry(
            parsed_market(),
            17.0,
            0.86,
            market,
            FakeEventClient(siblings),
            settings,
            {},
            {},
        )
    assert decision.decision == "SKIP"
    assert "competition_fail" in decision.reason
    assert "market_gap=" in decision.reason


def test_normal_entry_passes_when_market_lead_gap_meets_half():
    market = {
        "id": "normal",
        "eventId": "e1",
        "outcomePrices": "[0.86, 0.14]",
        "question": parsed_market().raw_title,
    }
    siblings = [
        market,
        {"id": "other", "outcomePrices": "[0.34, 0.66]", "question": "other bucket"},
    ]
    settings = make_settings(
        enable_competition_filter=True,
        max_market_prob_for_buy=0.99,
        min_market_yes_lead_gap_normal=0.5,
    )
    with patch("strategy.decision_core.compute_model_prob", return_value=0.95):
        decision = evaluate_entry(
            parsed_market(),
            17.0,
            0.86,
            market,
            FakeEventClient(siblings),
            settings,
            {},
            {},
        )
    assert decision.decision == "BUY"
    assert decision.entry_type == "normal"


def test_double_momentum_buy_skips_market_lead_gap_even_when_tight(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    add_samples("td_leader", [0.75, 0.55, 0.38])
    add_samples("td_meta", [0.05, 0.30, 0.50])
    market = {
        "id": "td_meta",
        "eventId": "ev_td",
        "outcomePrices": "[0.50, 0.50]",
        "question": parsed_market().raw_title,
    }
    siblings = [
        {"id": "td_leader", "outcomePrices": "[0.38, 0.62]", "question": "leader"},
        market.copy(),
    ]

    class FakeClient:
        def get_markets_for_event_id(self, _eid):
            return siblings

    settings = make_settings(
        double_momentum_min_start_price=0.05,
        enable_competition_filter=True,
        min_market_yes_lead_gap_normal=0.99,
    )
    decision = evaluate_entry(
        parsed_market(),
        17.0,
        0.50,
        market,
        FakeClient(),
        settings,
        {},
        {},
    )
    assert decision.decision == "BUY"
    assert decision.entry_type == "double_momentum"


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

    mcc_ret = (
        True,
        "15m_win",
        {
            "std_passed": True,
            "std_abs_rise": 0.45,
            "std_pct_rise": 4.0,
            "std_old_price": 0.05,
            "std_new_price": 0.55,
            "std_window_sec": 900.0,
        },
    )
    with (
        patch("strategy.trades.market_can_post_clob_orders", return_value=True),
        patch("strategy.trades.entry_time_allowed", return_value=(True, 15, "")),
        patch("strategy.trades.parse_highest_temp_title", return_value=parsed_market()),
        patch("strategy.trades.evaluate_entry", return_value=decision),
        patch("strategy.trades.momentum_multi_window_check", return_value=mcc_ret),
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
    assert trade["last_action"] == "manual_sync_open"
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


def test_ring_buffer_momentum_1m_5m_15m_2h(tmp_path, monkeypatch):
    """Verify all momentum windows return correct non-zero values from Redis."""
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    mid = "ring_test_1"
    now = time.time()

    # clear Redis for isolation
    if redis_store.is_connected():
        redis_store._client.delete(f"prices:{mid}")

    # add samples spanning 2h: price rising from 0.30 to 0.60
    for i in range(121):  # 0..120 minutes ago
        t = now - (120 - i) * 60
        price = 0.30 + (i / 120.0) * 0.30  # linear 0.30 → 0.60
        append_price_sample(mid, price, ts=t)

    # 1m window: should see ~0.0025 absolute change (small)
    _, _, m1 = price_change_in_window(mid, 60.0, now)
    assert m1 != 0.0, f"1m momentum should be non-zero, got {m1}"

    # 5m window
    _, _, m5 = price_change_in_window(mid, 300.0, now)
    assert m5 != 0.0, f"5m momentum should be non-zero, got {m5}"

    # 15m window
    _, _, m15 = price_change_in_window(mid, 900.0, now)
    assert m15 != 0.0, f"15m momentum should be non-zero, got {m15}"

    # 2h window: should see ~100% rise (0.30 → 0.60)
    _, _, m2h = price_change_in_window(mid, 7200.0, now)
    assert abs(m2h - 1.0) < 0.1, f"2h momentum should be ~100%, got {m2h}"

    if redis_store.is_connected():
        redis_store._client.delete(f"prices:{mid}")


def test_ring_buffer_load_samples_window_boundary(tmp_path, monkeypatch):
    """Verify load_samples_for_market returns correct window slices from Redis."""
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    mid = "ring_boundary"
    now = time.time()

    if redis_store.is_connected():
        redis_store._client.delete(f"prices:{mid}")

    # sample at 10m ago, 5m ago, 1m ago, now
    for offset_min, price in [(10, 0.50), (5, 0.55), (1, 0.58), (0, 0.60)]:
        append_price_sample(mid, price, ts=now - offset_min * 60)

    # 3m window should get the anchor (5m ago) + 1m ago + now = 3 points
    pts = load_samples_for_market(mid, 180.0, now)
    assert len(pts) >= 2, f"Expected ≥2 points in 3m window, got {len(pts)}"
    # oldest should be anchor from ~5m ago
    assert pts[0][1] == 0.55, f"Anchor should be 0.55, got {pts[0][1]}"
    assert pts[-1][1] == 0.60, f"Latest should be 0.60, got {pts[-1][1]}"

    if redis_store.is_connected():
        redis_store._client.delete(f"prices:{mid}")


def test_ring_buffer_cold_start_from_disk(tmp_path, monkeypatch):
    """Verify warm_ring_buffer_from_disk loads JSONL into Redis."""
    import json
    from strategy.momentum import warm_ring_buffer_from_disk
    from strategy import redis_store as rs

    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    monkeypatch.setattr("strategy.momentum._ring_warmed", False)

    # write a JSONL file to disk
    now = time.time()
    jsonl_path = tmp_path / "2026-05-03.jsonl"
    with jsonl_path.open("w") as f:
        for i in range(10):
            row = {"market_id": "cold_start", "ts": now - (10 - i) * 30, "yes": 0.50 + i * 0.02}
            f.write(json.dumps(row) + "\n")

    # clear Redis for this test market
    if rs.is_connected():
        rs._client.delete("prices:cold_start")

    loaded = warm_ring_buffer_from_disk()
    assert loaded >= 10, f"Expected ≥10 samples loaded, got {loaded}"

    pts = load_samples_for_market("cold_start", 7200.0)
    assert len(pts) >= 10, f"Expected ≥10 points in Redis, got {len(pts)}"

    if rs.is_connected():
        rs._client.delete("prices:cold_start")
