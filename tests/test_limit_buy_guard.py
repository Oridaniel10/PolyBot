"""pending limit buy guard — no duplicate GTC rests."""

from strategy.limit_buy_guard import (
    cancel_resting_limit_buys_for_market,
    migrate_legacy_orphan_limit_buys,
    record_pending_limit_buy,
    resting_limit_buy_blocks_entry,
)


class _FakeClient:
    def __init__(self, states: dict) -> None:
        self.states = states
        self.cancelled: list[str] = []

    def cancel_order(self, order_id: str) -> bool:
        self.cancelled.append(order_id)
        return True

    def get_order_state(self, order_id: str) -> dict:
        return dict(self.states.get(order_id) or {})


def test_migrate_legacy_orphan_into_pending_list():
    state = {"orphan_limit_buy_orders": {"99": "oid-legacy"}}
    migrate_legacy_orphan_limit_buys(state)
    assert "orphan_limit_buy_orders" not in state
    assert state["pending_limit_buy_orders"]["99"][0]["order_id"] == "oid-legacy"


def test_blocks_entry_when_resting_after_cancel():
    client = _FakeClient({"oid1": {"status": "live", "size_matched": 0}})
    state: dict = {}
    record_pending_limit_buy(
        state,
        market_id="42",
        order_id="oid1",
        limit_price=0.9,
        usd=5.0,
        title="Paris",
    )
    safe = cancel_resting_limit_buys_for_market(client, "42", state)
    assert safe is False
    blocked, reason = resting_limit_buy_blocks_entry(client, "42", state)
    assert blocked is True
    assert "open_limit_buy" in reason


def test_allows_entry_when_order_terminal():
    client = _FakeClient({"oid1": {"status": "cancelled", "size_matched": 0}})
    state: dict = {}
    record_pending_limit_buy(
        state,
        market_id="42",
        order_id="oid1",
        limit_price=0.9,
        usd=5.0,
        title="Paris",
    )
    safe = cancel_resting_limit_buys_for_market(client, "42", state)
    assert safe is True
    blocked, _ = resting_limit_buy_blocks_entry(client, "42", state)
    assert blocked is False
