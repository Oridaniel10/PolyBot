"""Late-fill reclaim in sync_portfolio: only when avg_price matches intended limit."""

from __future__ import annotations

import time
from typing import Any, Dict, List

import pytest

from strategy.sync_portfolio import sync_state_with_portfolio


def test_reclaim_skipped_when_site_avg_diverges_from_bot_intended():
    """Manual site buy after unfilled bot order must not inherit bot entry_type."""

    class Client:
        def get_open_positions(self) -> List[Dict[str, Any]]:
            return [
                {
                    "market_id": "2257634",
                    "condition_id": "c1",
                    "size": 3.409,
                    "avg_price": 0.8799,
                    "cur_price": 0.91,
                    "title": "Helsinki 18C",
                }
            ]

    state: Dict[str, Any] = {
        "active_trades": {},
        "recent_buy_attempts": {
            "2257634": {
                "ts": time.time(),
                "intended_price": 0.69,
                "intended_usd": 5.0,
                "entry_type": "double_momentum",
                "title": "Helsinki 18C",
            }
        },
    }
    out = sync_state_with_portfolio(Client(), state)
    row = out["active_trades"]["2257634"]
    assert row["entry_type"] == "manual"
    assert row["last_action"] == "manual_sync_open"
    assert row["entry_price"] == pytest.approx(0.8799)


def test_reclaim_succeeds_when_avg_matches_intended():
    """True late fill: portfolio avg aligns with the bot's limit/decision price."""

    class Client:
        def get_open_positions(self) -> List[Dict[str, Any]]:
            return [
                {
                    "market_id": "m1",
                    "condition_id": "c2",
                    "size": 7.0,
                    "avg_price": 0.692,
                    "cur_price": 0.70,
                    "title": "Test",
                }
            ]

    state: Dict[str, Any] = {
        "active_trades": {},
        "recent_buy_attempts": {
            "m1": {
                "ts": time.time(),
                "intended_price": 0.69,
                "intended_usd": 5.0,
                "entry_type": "momentum",
                "title": "Test",
            }
        },
    }
    out = sync_state_with_portfolio(Client(), state)
    row = out["active_trades"]["m1"]
    assert row["entry_type"] == "momentum"
    assert row["last_action"] == "buy_late_fill_reclaimed"
    assert row["entry_price"] == pytest.approx(0.69)
