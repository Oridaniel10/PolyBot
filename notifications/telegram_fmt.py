from telegram_bot import tg_escape


def format_est_pnl_line_html(shares: float, entry: float, mark: float) -> str:
    est = (mark - entry) * shares
    if est > 1e-6:
        return f"💰 <b>Est PnL</b>  <code>+${est:.2f}</code>"
    if est < -1e-6:
        return f"💰 <b>Est PnL</b>  <code>-${abs(est):.2f}</code>"
    return f"💰 <b>Est PnL</b>  <code>$0.00</code>"


def format_buy_max_risk_line_html(usd: float) -> str:
    return f"⚠️ <i>max trade notional (this order)</i>  <code>${usd:.2f}</code>"
