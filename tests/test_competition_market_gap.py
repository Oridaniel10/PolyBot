"""market YES gap vs runner-up (momentum cold entry gate)."""

from strategy.competition_filter import market_yes_lead_gap_vs_runner_up


def test_gap_vs_runner_up():
    sibs = [
        {"id": "m1", "outcomePrices": "[0.55, 0.45]"},
        {"id": "m2", "outcomePrices": "[0.35, 0.65]"},
    ]
    gap, ru = market_yes_lead_gap_vs_runner_up("m1", 0.55, sibs)
    assert abs(gap - 0.20) < 1e-9
    assert abs(ru - 0.35) < 1e-9


def test_gap_when_tied_runner():
    sibs = [
        {"id": "a", "outcomePrices": "[0.40, 0.60]"},
        {"id": "b", "outcomePrices": "[0.40, 0.60]"},
    ]
    gap, ru = market_yes_lead_gap_vs_runner_up("a", 0.40, sibs)
    assert abs(gap - 0.0) < 1e-9
    assert abs(ru - 0.40) < 1e-9
