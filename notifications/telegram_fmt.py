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


def format_buy_exit_plan_html(
    entry: float, shares: float, tp_bar: float, sl_bar: float
) -> str:
    """rough USD est at exit bars / YES=$1; ignores fees and slippage."""
    e = float(entry)
    sh = float(shares)
    tpb = float(tp_bar)
    slb = float(sl_bar)
    pnl_tp = (tpb - e) * sh
    pnl_sl = (slb - e) * sh
    pnl_yes = (1.0 - e) * sh
    return (
        f"🎯 <b>TP</b> bar≈<code>{tpb:.2f}</code> → est vs entry <code>{pnl_tp:+.2f}</code> USD\n"
        f"🛑 <b>SL</b> bar≈<code>{slb:.2f}</code> → est vs entry <code>{pnl_sl:+.2f}</code> USD\n"
        f"✅ <b>if YES→$1</b> gross vs entry <code>{pnl_yes:+.2f}</code> USD"
    )
