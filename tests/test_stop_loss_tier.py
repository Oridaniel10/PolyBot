from strategy.probability import (
    stop_loss_mark_bar_for_entry,
    stop_loss_reference_if_triggered,
)


def test_stop_loss_mark_bar_tiers() -> None:
    assert (
        stop_loss_mark_bar_for_entry(
            0.55,
            use_tiers=True,
            tier_split=0.60,
            mark_if_entry_below_split=0.30,
            mark_if_entry_at_or_above_split=0.40,
            legacy_flat=0.50,
        )
        == 0.30
    )
    assert (
        stop_loss_mark_bar_for_entry(
            0.65,
            use_tiers=True,
            tier_split=0.60,
            mark_if_entry_below_split=0.30,
            mark_if_entry_at_or_above_split=0.40,
            legacy_flat=0.50,
        )
        == 0.40
    )
    assert (
        stop_loss_mark_bar_for_entry(
            0.65,
            use_tiers=False,
            tier_split=0.60,
            mark_if_entry_below_split=0.30,
            mark_if_entry_at_or_above_split=0.40,
            legacy_flat=0.50,
        )
        == 0.50
    )


def test_stop_loss_triggers_when_mark_below_bar() -> None:
    market = {"outcomePrices": "[0.25, 0.75]"}
    trade = {"entry_price": 0.55, "last_price": 0.24}
    assert stop_loss_reference_if_triggered(market, trade, 0.30) is not None
