"""order_resting_unfilled helper for CLOB cancel hardening."""

from polymarket_client import order_resting_unfilled


def test_empty_payload_not_resting():
    assert order_resting_unfilled({}) is False


def test_filled_not_resting():
    assert (
        order_resting_unfilled({"status": "filled", "original_size": 10, "size_matched": 10})
        is False
    )


def test_cancelled_not_resting():
    assert order_resting_unfilled({"status": "cancelled", "size_matched": 0}) is False


def test_live_is_resting():
    assert order_resting_unfilled({"status": "live", "size_matched": 0}) is True


def test_partial_fill_resting():
    assert (
        order_resting_unfilled(
            {"original_size": 10.0, "size_matched": 3.0, "status": "open"}
        )
        is True
    )
