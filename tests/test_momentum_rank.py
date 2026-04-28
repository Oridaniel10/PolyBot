"""ranking + peer momentum helpers (market YES order)."""

from unittest.mock import patch

from strategy.momentum_engine import top_price_change_peers, yes_rank_by_market_prob


def test_yes_rank_by_market_prob_orders_descending():
    sibs = [
        {"id": "m2", "outcomePrices": "[0.6, 0.4]"},
        {"id": "m1", "outcomePrices": "[0.9, 0.1]"},
        {"id": "m3", "outcomePrices": "[0.2, 0.8]"},
    ]
    assert yes_rank_by_market_prob("m1", sibs) == 1
    assert yes_rank_by_market_prob("m2", sibs) == 2
    assert yes_rank_by_market_prob("m3", sibs) == 3


def test_top_price_change_peers_sorts_by_change():
    with patch("strategy.momentum_engine.absolute_price_change_in_window") as pcw:
        pcw.side_effect = [
            (0.0, 0.0, 0.05),
            (0.0, 0.0, 0.20),
            (0.0, 0.0, 0.10),
        ]
        out = top_price_change_peers(["a", "b", "c"], window_sec=900.0, top_n=3)
    assert out[0] == ("b", 0.20)
    assert out[1] == ("c", 0.10)
    assert out[2] == ("a", 0.05)
