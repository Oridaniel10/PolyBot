from __future__ import annotations

from config.settings import RuntimeSettings


def tradable_cash_usd(cash_usd: float, settings: RuntimeSettings) -> float:
    """free cash minus reserve — all buy sizing uses this, not raw cash."""
    reserve = max(0.0, float(settings.cash_reserve_usd))
    return max(0.0, float(cash_usd) - reserve)


def compute_buy_usd_amount(
    cash_usd: float, yes_price: float, settings: RuntimeSettings
) -> float:
    usable = tradable_cash_usd(cash_usd, settings)
    if usable <= 0 or yes_price <= 0 or yes_price >= 1.0:
        return 0.0
    frac = settings.max_trade_fraction_of_cash
    max_usd = min(usable * frac, settings.max_buy_notional_usd, usable)
    if max_usd < settings.min_order_notional_usd:
        return 0.0
    return max(0.0, round(max_usd, 6))


def planned_buy_cap_lines(
    cash_usd: float, settings: RuntimeSettings
) -> tuple[float, float, float, float, float]:
    """
    returns (fraction, per_trade_cap_usd, planned_max_usd, reserve_usd, tradable_cash_usd).
    planned uses tradable pool only.
    """
    reserve = max(0.0, float(settings.cash_reserve_usd))
    usable = tradable_cash_usd(cash_usd, settings)
    frac = settings.max_trade_fraction_of_cash
    planned = min(usable * frac, settings.max_buy_notional_usd, usable)
    return frac, settings.max_buy_notional_usd, planned, reserve, usable
