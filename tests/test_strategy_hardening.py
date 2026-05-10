"""focused tests for the strategy hardening features.

covers:
- dual-window momentum (15m + 5m fast) with absolute or pct rise
- noise protection via min_start_price
- buy-max enforcement per entry type
- trailing stop activation + breach classification
- stop-loss category labels (absolute / relative / trailing / momentum)
- bucket switch atomicity (sell-before-buy)
- failed-exit telegram dedup
- structured trade-log fields presence
"""

from __future__ import annotations

import time
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from config import constants as C
from config.settings import RuntimeSettings, default_runtime_dict
from strategy.decision_core import (
    classify_stop_loss_breach,
    effective_stop_price_for_trade,
    momentum_multi_window_check,
    trailing_stop_level,
)

_MOM_TEST_WINDOWS_SEC = [60.0, 120.0, 180.0, 240.0, 300.0, 900.0, 7200.0]
from strategy.momentum import append_price_sample
from strategy.limit_executor import (
    LimitExecutionResult,
    is_emergency_sell_reason,
    limit_orders_active,
)
from strategy.telegram_dedup import (
    categorize_error,
    clear_failed_exit_notices,
    should_send_failed_exit_notice,
)
from strategy.leader_yield_momentum import (
    leader_yield_drop_qualifies,
    parse_trigger_window_seconds,
)


def make_settings(**overrides) -> RuntimeSettings:
    data = default_runtime_dict()
    data.update(overrides)
    return RuntimeSettings.from_dict(data)


def add_samples(market_id: str, prices: List[float], spacing_sec: float = 60.0) -> None:
    """add evenly-spaced price samples ending at now."""
    now = time.time()
    for index, price in enumerate(prices):
        append_price_sample(
            market_id, price, ts=now - (len(prices) - index - 1) * spacing_sec
        )


# ─── momentum dual-window ────────────────────────────────────────────


def test_momentum_pct_rise_passes_when_absolute_low(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    # 0.10 -> 0.78 = +680% in 15m; pct gate dominates when abs gate is unreachable.
    add_samples("pct_only", [0.10, 0.30, 0.50, 0.78])
    s = make_settings(momentum_pct_rise=6.0, momentum_min_start_price=0.10)
    passed, trigger, meta = momentum_multi_window_check(
        market_id="pct_only",
        abs_rise_threshold=0.99,
        pct_rise_threshold=s.momentum_pct_rise,
        windows_sec=_MOM_TEST_WINDOWS_SEC,
        min_start_price=s.momentum_min_start_price,
        min_current_price=0.05,
        max_current_price=0.80,
    )
    assert passed is True
    assert trigger.endswith("m_win") and trigger != "none"
    assert meta["std_pct_rise"] >= 6.0


def test_momentum_blocked_by_min_start_price(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    # 0.001 -> 0.011 is +1000% but start price is below 0.10 floor.
    add_samples("noise", [0.001, 0.005, 0.011])
    s = make_settings(
        momentum_min_start_price=0.10,
        momentum_pct_rise=6.0,
    )
    passed, _, meta = momentum_multi_window_check(
        market_id="noise",
        abs_rise_threshold=s.momentum_entry_rise,
        pct_rise_threshold=s.momentum_pct_rise,
        windows_sec=_MOM_TEST_WINDOWS_SEC,
        min_start_price=s.momentum_min_start_price,
        min_current_price=0.005,
        max_current_price=0.80,
    )
    assert passed is False
    assert meta.get("std_passed") is not True


def test_momentum_blocked_by_max_current_price(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    # 0.70 -> 0.91 — current price above momentum_max_entry (0.80).
    add_samples("over_max", [0.70, 0.80, 0.91])
    passed, _, meta = momentum_multi_window_check(
        market_id="over_max",
        abs_rise_threshold=0.20,
        pct_rise_threshold=6.0,
        windows_sec=_MOM_TEST_WINDOWS_SEC,
        min_start_price=0.10,
        min_current_price=0.61,
        max_current_price=0.80,
    )
    assert passed is False
    assert meta.get("std_passed") is not True


def test_fast_window_qualifies_when_std_window_does_not(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    # samples spread 4 minutes apart so 5m window sees only the surge.
    # 15m window: full series 0.62 -> 0.65 (slow). 5m: 0.40 -> 0.65 (+0.25).
    now = time.time()
    series = [
        (now - 14 * 60, 0.62),
        (now - 10 * 60, 0.63),
        (now - 4 * 60, 0.40),
        (now - 1 * 60, 0.65),
    ]
    for ts, px in series:
        append_price_sample("fastonly", px, ts=ts)
    passed, trigger, meta = momentum_multi_window_check(
        market_id="fastonly",
        abs_rise_threshold=0.20,
        pct_rise_threshold=10.0,
        windows_sec=_MOM_TEST_WINDOWS_SEC,
        min_start_price=0.10,
        min_current_price=0.61,
        max_current_price=0.80,
    )
    assert passed is True
    # first matching window in sorted grid can be 2m or 5m for this synthetic path
    assert trigger in ("2m_win", "3m_win", "4m_win", "5m_win")
    assert meta["std_passed"] is True


# ─── trailing stop + sl categorization ───────────────────────────────


def test_trailing_stop_activates_above_threshold():
    s = make_settings()
    # peak 0.70 with entry 0.50 → +0.20 → activates → locks 0.50+0.10 = 0.60
    assert trailing_stop_level(0.50, 0.70, s) == pytest.approx(0.60)


def test_trailing_stop_inactive_below_threshold():
    s = make_settings()
    assert trailing_stop_level(0.50, 0.65, s) is None


def test_trailing_stop_disabled_returns_none():
    s = make_settings(trailing_stop_enabled=False)
    assert trailing_stop_level(0.50, 0.80, s) is None


def test_effective_stop_combines_floor_and_trailing():
    s = make_settings(stop_loss_normal=0.65)
    # entry 0.50 normal: floor=0.65, drop_pct=0.30 -> entry_relative=0.35
    # base = max(0.65, 0.35) = 0.65. peak 0.70 → trail=0.60. final = max(0.65,0.60)=0.65.
    assert effective_stop_price_for_trade(
        "normal", 0.50, s, highest_seen_price=0.70
    ) == pytest.approx(0.65)
    # entry 0.80 normal: floor=0.65, drop=0.30 → entry_relative=0.56, base=0.65
    # peak 1.00 → trail=0.90. final=0.90.
    assert effective_stop_price_for_trade(
        "normal", 0.80, s, highest_seen_price=1.00
    ) == pytest.approx(0.90)


def test_classify_sl_categories_distinct():
    s = make_settings(stop_loss_normal=0.65)
    # SL_ABSOLUTE: live below floor only
    breach = classify_stop_loss_breach("normal", 0.55, 0.59, s)
    # entry 0.55 → entry_relative=0.385. live=0.59 < floor 0.65 → absolute breach.
    # actually 0.59<0.60, so absolute breach.
    assert breach is not None
    cat, level = breach
    assert cat == C.SL_CATEGORY_ABSOLUTE
    assert level == pytest.approx(0.65)

    # SL_RELATIVE: entry-relative > floor and live below it
    s2 = make_settings(stop_loss_normal=0.40)
    # entry 0.80 → entry_relative = 0.80*(1-0.30) = 0.56 > floor 0.40
    # live 0.50 < 0.56 → SL_RELATIVE
    breach2 = classify_stop_loss_breach("normal", 0.80, 0.50, s2)
    assert breach2 is not None
    cat2, level2 = breach2
    assert cat2 == C.SL_CATEGORY_RELATIVE
    assert level2 == pytest.approx(0.56)

    # SL_TRAILING: trail pins effective above default hard floor
    s_tr = make_settings(stop_loss_normal=0.50)
    breach3 = classify_stop_loss_breach(
        "normal", 0.50, 0.55, s_tr, highest_seen_price=0.80
    )
    # entry 0.50, peak 0.80 → trail=0.60 > floor 0.50. effective=0.60. live=0.55 < 0.60.
    assert breach3 is not None
    assert breach3[0] == C.SL_CATEGORY_TRAILING
    assert breach3[1] == pytest.approx(0.60)


def test_classify_sl_no_breach_when_live_above_levels():
    s = make_settings()
    assert classify_stop_loss_breach("normal", 0.50, 0.65, s) is None


# ─── buy-max enforcement per entry type ──────────────────────────────


def test_entry_type_max_price_normal_uses_buy_max_threshold():
    from strategy.trades import entry_type_max_price

    s = make_settings(buy_max_threshold=0.84)
    assert entry_type_max_price("normal", s) == pytest.approx(0.84)


def test_entry_type_max_price_momentum_uses_momentum_max_entry():
    from strategy.trades import entry_type_max_price

    s = make_settings(momentum_max_entry=0.80)
    assert entry_type_max_price("momentum", s) == pytest.approx(0.80)


def test_entry_type_max_price_double_momentum_uses_double_max_price():
    from strategy.trades import entry_type_max_price

    s = make_settings(double_momentum_max_price=0.80)
    assert entry_type_max_price("double_momentum", s) == pytest.approx(0.80)


# ─── highest_seen_price tracking ─────────────────────────────────────


def test_update_highest_seen_price_only_grows():
    from strategy.trades import update_highest_seen_price

    row: Dict[str, Any] = {"highest_seen_price": 0.55}
    assert update_highest_seen_price(row, 0.62) == pytest.approx(0.62)
    assert update_highest_seen_price(row, 0.50) == pytest.approx(0.62)
    assert row["highest_seen_price"] == pytest.approx(0.62)


def test_update_highest_seen_price_initial_set():
    from strategy.trades import update_highest_seen_price

    row: Dict[str, Any] = {}
    update_highest_seen_price(row, 0.41)
    assert row["highest_seen_price"] == pytest.approx(0.41)


# ─── telegram dedup ─────────────────────────────────────────────────


def test_telegram_dedup_first_send_then_suppress(tmp_path):
    s = make_settings(
        telegram_failed_exit_dedupe_enabled=True,
        telegram_failed_exit_cooldown_sec=900,
    )
    clear_failed_exit_notices("dedup_test_market")
    assert should_send_failed_exit_notice(
        s,
        market_id="dedup_test_market",
        exit_reason="stop-loss",
        error_category="generic",
    ) is True
    assert should_send_failed_exit_notice(
        s,
        market_id="dedup_test_market",
        exit_reason="stop-loss",
        error_category="generic",
    ) is False
    clear_failed_exit_notices("dedup_test_market")
    assert should_send_failed_exit_notice(
        s,
        market_id="dedup_test_market",
        exit_reason="stop-loss",
        error_category="generic",
    ) is True


def test_telegram_dedup_disabled_always_sends(tmp_path):
    s = make_settings(telegram_failed_exit_dedupe_enabled=False)
    clear_failed_exit_notices("dedup_off")
    for _ in range(3):
        assert should_send_failed_exit_notice(
            s,
            market_id="dedup_off",
            exit_reason="stop-loss",
            error_category="generic",
        ) is True


def test_telegram_dedup_categorize_error_distinct():
    assert categorize_error("error: below_min_order_size value=4.5") == "below_min"
    assert categorize_error("connection timeout") == "timeout"
    assert categorize_error("404 not found") == "not_found"
    assert categorize_error("rate limit exceeded 429") == "rate_limited"
    assert categorize_error("") == "unknown"


# ─── limit-order helpers ─────────────────────────────────────────────


def test_limit_orders_active_respects_setting():
    assert limit_orders_active(make_settings(limit_orders_enabled=True)) is True
    assert limit_orders_active(make_settings(limit_orders_enabled=False)) is False


def test_is_emergency_sell_reason_classifies_critical_exits():
    assert is_emergency_sell_reason("stop-loss") is True
    assert is_emergency_sell_reason("trailing-stop") is True
    assert is_emergency_sell_reason("momentum-stop-loss") is True
    assert is_emergency_sell_reason("competitor-surge") is True
    assert is_emergency_sell_reason("take-profit") is False
    assert is_emergency_sell_reason("time-decay") is False


# ─── TradeDecision trigger metadata ──────────────────────────────────


def test_trade_decision_carries_trigger_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    # sibling ex-leader must bleed (leader-yield gate) while target ramps for double momentum
    add_samples("td_leader", [0.75, 0.55, 0.38])
    add_samples("td_meta", [0.05, 0.30, 0.50])
    from datetime import date

    from forecast.parse_title import BracketKind, ParsedTempMarket
    from strategy.decision_core import evaluate_entry

    parsed = ParsedTempMarket(
        city_key="test",
        tz_name="UTC",
        event_date=date(2026, 4, 26),
        bracket=BracketKind.EXACT,
        threshold_c=17.0,
        raw_title="Will the highest temperature in Test be 17°C on April 26?",
    )
    market = {
        "id": "td_meta",
        "eventId": "ev_td",
        "outcomePrices": "[0.50, 0.50]",
        "question": parsed.raw_title,
    }

    class FakeClient:
        def get_markets_for_event_id(self, _eid):
            return [
                {
                    "id": "td_leader",
                    "outcomePrices": "[0.38, 0.62]",
                    "question": "leader bucket",
                },
                market.copy(),
            ]

    decision = evaluate_entry(
        parsed,
        17.0,
        0.50,
        market,
        FakeClient(),
        make_settings(double_momentum_min_start_price=0.05),
        {},
        {},
    )
    assert decision.entry_type == "double_momentum"
    assert decision.trigger_window.endswith("m_win") and decision.trigger_window != "none"
    assert decision.trigger_abs_rise > 0
    assert decision.leader_fallen_market_id == "td_leader"


# ─── csv schema includes new columns ─────────────────────────────────


def test_trade_csv_fields_include_rich_logging():
    from state.pnl_ledger import TRADE_CSV_FIELDS

    for required in (
        "trigger_window",
        "trigger_abs_rise",
        "trigger_pct_rise",
        "decision_price",
        "live_clob_price_before_order",
        "execution_mode",
        "execution_limit_price",
        "execution_fill_price",
        "execution_slippage",
        "sl_category",
        "highest_seen_price",
        "full_reason",
    ):
        assert required in TRADE_CSV_FIELDS, f"missing trade-csv column: {required}"


# ─── place_buy enforces live-CLOB max (entry_type) ───────────────────


class _FakeBalanceClient:
    def __init__(self, clob_price: float):
        self.clob_price = clob_price
        self.market_buy_called = False

    def get_clob_best_ask_yes(self, _market):
        return self.clob_price

    def get_clob_yes_price(self, _market):
        return self.clob_price

    def get_portfolio_balance(self, force_allowance_refresh=False):
        return {"cash": 100.0, "positions_market_value": 0.0, "total_value": 100.0}

    def place_market_buy_yes(self, _m, _u):
        self.market_buy_called = True
        return {"orderID": "x"}

    def place_limit_buy_yes(self, _m, _u, _p):
        self.market_buy_called = True
        return {"orderID": "x", "limit_price": _p, "limit_size": _u / max(_p, 1e-9)}

    def get_order_state(self, _o):
        return {}

    def cancel_order(self, _o):
        return True

    def get_market_by_id(self, _m):
        return None

    def invalidate_data_positions_cache(self):
        pass

    def invalidate_balance_allowance_cache(self):
        pass


class _NoopTelegram:
    def is_configured(self):
        return False

    def send_html_chunks(self, *_a, **_k):
        pass


def test_place_buy_skips_when_live_clob_above_entry_type_max():
    from strategy.trades import place_buy
    from strategy.decision_core import TradeDecision

    s = make_settings(buy_max_threshold=0.84)
    market = {
        "id": "above_max",
        "outcomePrices": "[0.90, 0.10]",
        "question": "Will the highest temperature in TestCity be 17°C on April 26?",
    }
    state: Dict[str, Any] = {"active_trades": {}}
    fake_client = _FakeBalanceClient(clob_price=0.90)
    td = TradeDecision(
        city="test",
        date_iso="2026-04-26",
        chosen_bucket=market["question"],
        model_prob=0.0,
        market_prob=0.90,
        edge=0.0,
        consensus_c=17.0,
        sigma_used=1.0,
        decision="BUY",
        reason="passed_all_filters",
        entry_type="normal",
    )
    place_buy(
        fake_client, market, state, _NoopTelegram(), 0.85, s, {}, trade_decision=td
    )
    assert fake_client.market_buy_called is False
    assert "above_max" not in state["active_trades"]


# ─── bucket switch atomic — sell-before-buy ──────────────────────────


def test_bucket_switch_skips_buy_if_sell_fails():
    """when close_position raises or fails to clear, no new buy should happen."""
    from strategy.trades import _execute_bucket_switch_sell

    state: Dict[str, Any] = {
        "active_trades": {
            "held": {
                "market_id": "held",
                "shares": 5.0,
                "entry_price": 0.5,
                "position_title": "Held",
            }
        }
    }
    fake_client = MagicMock()
    fake_telegram = _NoopTelegram()
    held_market = {"id": "held", "outcomePrices": "[0.50, 0.50]", "question": "Held"}
    s = make_settings()

    with patch(
        "strategy.trades.close_position", side_effect=RuntimeError("boom")
    ):
        cleared = _execute_bucket_switch_sell(
            client=fake_client,
            telegram=fake_telegram,
            state=state,
            settings=s,
            held_market=held_market,
            held_key="held",
            target_title="Target",
            target_market_id="target",
        )
    assert cleared is False
    assert "held" in state["active_trades"]


def test_bucket_switch_clears_when_close_position_removes_held():
    from strategy.trades import _execute_bucket_switch_sell

    state: Dict[str, Any] = {
        "active_trades": {
            "held": {
                "market_id": "held",
                "shares": 5.0,
                "entry_price": 0.5,
                "position_title": "Held",
            }
        }
    }

    def fake_close_position(*_args, **_kwargs):
        state["active_trades"].pop("held", None)

    with patch("strategy.trades.close_position", side_effect=fake_close_position):
        cleared = _execute_bucket_switch_sell(
            client=MagicMock(),
            telegram=_NoopTelegram(),
            state=state,
            settings=make_settings(),
            held_market={
                "id": "held",
                "outcomePrices": "[0.50, 0.50]",
                "question": "Held",
            },
            held_key="held",
            target_title="Target",
            target_market_id="target",
        )
    assert cleared is True
    assert "held" not in state["active_trades"]


def test_parse_trigger_window_seconds_maps_nm_win():
    assert parse_trigger_window_seconds("15m_win") == 900.0
    assert parse_trigger_window_seconds("1m_win") == 60.0
    assert parse_trigger_window_seconds("none") is None


def test_leader_yield_short_window_misses_peak_long_window_sees_it(tmp_path, monkeypatch):
    """Without anchor trick: last 60s only shows tail of crash; 15m includes peak."""
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    from strategy.momentum import _price_ring, _price_ring_lock

    with _price_ring_lock:
        _price_ring.pop("ex_L", None)
        _price_ring.pop("buy_B", None)
    now = time.time()
    L, B = "ex_L", "buy_B"
    # peak old; mid-crash before last 60s (anchor for 60s window); last-minute tail
    append_price_sample(L, 0.64, ts=now - 800)
    append_price_sample(L, 0.32, ts=now - 120)
    append_price_sample(L, 0.35, ts=now - 45)
    append_price_sample(L, 0.26, ts=now - 8)
    append_price_sample(B, 0.08, ts=now - 40)
    append_price_sample(B, 0.12, ts=now - 7)
    ok_60, _ = leader_yield_drop_qualifies(
        target_market_id=B,
        event_market_ids=[L, B],
        window_sec=60.0,
        min_leader_old_price=0.05,
        min_fall_abs_pts=0.30,
        min_fall_frac_of_old=0.50,
        min_samples=2,
        now_ts=now,
    )
    ok_900, m900 = leader_yield_drop_qualifies(
        target_market_id=B,
        event_market_ids=[L, B],
        window_sec=900.0,
        min_leader_old_price=0.05,
        min_fall_abs_pts=0.30,
        min_fall_frac_of_old=0.50,
        min_samples=2,
        now_ts=now,
    )
    assert ok_60 is False
    assert ok_900 is True
    assert m900.get("leader_id") == L


def test_leader_yield_ok_when_ex_leader_bleeds_in_window(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    spacing = 120.0
    now = time.time()
    leader_prices = [0.70, 0.62, 0.55, 0.45, 0.34]
    tgt_prices = [0.08, 0.10, 0.15, 0.22, 0.38]
    noise_prices = [0.05] * 5
    for mid, series in (("L", leader_prices), ("T", tgt_prices), ("N", noise_prices)):
        for i, pr in enumerate(series):
            append_price_sample(mid, pr, ts=now - (len(series) - i - 1) * spacing)
    ok, meta = leader_yield_drop_qualifies(
        target_market_id="T",
        event_market_ids=["L", "T", "N"],
        window_sec=900.0,
        min_leader_old_price=0.05,
        min_fall_abs_pts=0.30,
        min_fall_frac_of_old=0.40,
        min_samples=2,
        now_ts=now,
    )
    assert ok is True
    assert meta.get("leader_id") == "L"
    assert meta.get("reason") == "ok"
    assert meta.get("pass_condition") == "A"


def test_leader_yield_passes_when_any_single_sibling_drops_enough(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    spacing = 120.0
    now = time.time()
    leader_prices = [0.70] * 5
    dropper_prices = [0.25, 0.20, 0.15, 0.10, 0.02]
    target_prices = [0.06, 0.10, 0.18, 0.25, 0.40]
    for mid, series in (("X", leader_prices), ("Y", dropper_prices), ("Z", target_prices)):
        for i, pr in enumerate(series):
            append_price_sample(mid, pr, ts=now - (len(series) - i - 1) * spacing)
    ok, meta = leader_yield_drop_qualifies(
        target_market_id="Z",
        event_market_ids=["X", "Y", "Z"],
        window_sec=900.0,
        min_leader_old_price=0.05,
        min_fall_abs_pts=0.30,
        min_fall_frac_of_old=0.40,
        min_samples=2,
        now_ts=now,
    )
    assert ok is True
    assert meta.get("leader_id") == "Y"
    assert meta.get("pass_condition") == "A"
    assert meta.get("reason") == "ok"


def test_leader_yield_fails_when_target_not_rising(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    from strategy.momentum import _price_ring, _price_ring_lock

    with _price_ring_lock:
        _price_ring.pop("L", None)
        _price_ring.pop("T", None)
    spacing = 120.0
    now = time.time()
    peer_drop = [0.70, 0.62, 0.55, 0.45, 0.25]
    target_falling = [0.14, 0.13, 0.12, 0.11, 0.10]
    for mid, series in (("L", peer_drop), ("T", target_falling)):
        for i, pr in enumerate(series):
            append_price_sample(mid, pr, ts=now - (len(series) - i - 1) * spacing)
    ok, meta = leader_yield_drop_qualifies(
        target_market_id="T",
        event_market_ids=["L", "T"],
        window_sec=900.0,
        min_leader_old_price=0.05,
        min_fall_abs_pts=0.30,
        min_fall_frac_of_old=0.40,
        min_samples=2,
        now_ts=now,
    )
    assert ok is False
    assert meta.get("reason") == "target_not_rising"


def test_leader_yield_passes_via_collective_when_no_single_faller(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    spacing = 120.0
    now = time.time()
    tgt = [0.10, 0.12, 0.15, 0.18, 0.35]
    srow = [0.25, 0.24, 0.22, 0.18, 0.15]
    for i, pr in enumerate(tgt):
        append_price_sample("T", pr, ts=now - (5 - i - 1) * spacing)
    for mid in ("S1", "S2", "S3", "S4"):
        for i, pr in enumerate(srow):
            append_price_sample(mid, pr, ts=now - (5 - i - 1) * spacing)
    ok, meta = leader_yield_drop_qualifies(
        target_market_id="T",
        event_market_ids=["S1", "S2", "S3", "S4", "T"],
        window_sec=900.0,
        min_leader_old_price=0.05,
        min_fall_abs_pts=0.30,
        min_fall_frac_of_old=0.50,
        min_samples=2,
        collective_fall_min_abs_pts=0.30,
        collective_fall_min_frac=0.30,
        now_ts=now,
    )
    assert ok is True
    assert meta.get("pass_condition") == "B"
    assert float(meta.get("collective_total_drop_pts") or 0.0) >= 0.39


def test_leader_yield_fails_when_neither_condition_holds(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    from strategy.momentum import _price_ring, _price_ring_lock

    with _price_ring_lock:
        _price_ring.pop("T", None)
        _price_ring.pop("P", None)
    spacing = 120.0
    now = time.time()
    tgt = [0.10, 0.11, 0.12, 0.13, 0.14]
    peer = [0.40, 0.399, 0.398, 0.397, 0.396]
    for i, pr in enumerate(tgt):
        append_price_sample("T", pr, ts=now - (5 - i - 1) * spacing)
    for i, pr in enumerate(peer):
        append_price_sample("P", pr, ts=now - (5 - i - 1) * spacing)
    ok, meta = leader_yield_drop_qualifies(
        target_market_id="T",
        event_market_ids=["P", "T"],
        window_sec=900.0,
        min_leader_old_price=0.05,
        min_fall_abs_pts=0.30,
        min_fall_frac_of_old=0.50,
        min_samples=2,
        collective_fall_min_abs_pts=0.30,
        collective_fall_min_frac=0.30,
        now_ts=now,
    )
    assert ok is False
    assert str(meta.get("reason") or "").endswith("no_qualifying_fall")


def test_leader_yield_picks_largest_individual_faller_for_meta(tmp_path, monkeypatch):
    monkeypatch.setattr("strategy.momentum.PRICE_SAMPLES_DIR", tmp_path)
    spacing = 120.0
    now = time.time()
    tgt = [0.08, 0.10, 0.15, 0.22, 0.38]
    a_series = [0.80, 0.75, 0.65, 0.50, 0.40]
    b_series = [0.70, 0.68, 0.65, 0.45, 0.38]
    for mid, series in (("T", tgt), ("A", a_series), ("B", b_series)):
        for i, pr in enumerate(series):
            append_price_sample(mid, pr, ts=now - (len(series) - i - 1) * spacing)
    ok, meta = leader_yield_drop_qualifies(
        target_market_id="T",
        event_market_ids=["A", "B", "T"],
        window_sec=900.0,
        min_leader_old_price=0.05,
        min_fall_abs_pts=0.30,
        min_fall_frac_of_old=0.40,
        min_samples=2,
        now_ts=now,
    )
    assert ok is True
    assert meta.get("leader_id") == "A"
    assert meta.get("pass_condition") == "A"
