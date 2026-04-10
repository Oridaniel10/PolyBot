# Agent notes (Cursor / automation)

## Architecture

- **Anchor:** trading flow stays `run_bot` → `sync_state_with_portfolio` → `run_once` → `process_single_market` → `place_buy` / `close_position` / `claim_position` in `strategy/trades.py` and `strategy/loop.py`.
- **Config:** defaults in `config/constants.py`; live overrides in `data/runtime_config.json` (merged each tick via `get_effective_settings()` in `config/settings.py`).
- **State:** `state.json` at repo root (paths in `config/constants.py`); churn counters in `state["churn_by_market"]`.
- **No database:** JSON / JSONL only under `data/` (`runtime_config.json`, `blacklist_day.json`, `pnl_ledger.jsonl`, `price_samples/*.jsonl`).
- **API + UI:** FastAPI routes in `app/dashboard.py` (prefix `/api`). Static React build served at `/dashboard/` when `ui/dist` exists (`main_bot.py`).

## Rate limits

Respect Polymarket limits; see **[POLY_RATE_LIMITS.MD](POLY_RATE_LIMITS.MD)** and [Polymarket docs](https://docs.polymarket.com/). Prefer reusing tick data for momentum samples; competition uses targeted Gamma `markets` / `events` calls per event, not full market rescans.

## Modules

| Area        | Location |
|------------|----------|
| Thresholds | `config/settings.py`, `data/runtime_config.json` (incl. `cash_reserve_usd`, sizing) |
| Gates / CLOB | `strategy/gates.py` |
| Probabilities | `strategy/probability.py` |
| Competition | `strategy/competition.py` + `polymarket_client.get_markets_for_event_id` |
| Momentum / samples | `strategy/momentum.py`, `data/price_samples/` |
| Anti-churn | `strategy/churn.py` |
| Telegram HTML | `notifications/telegram_fmt.py`, portfolio shell in `notifications/portfolio.py` |
| Gamma helpers | `polymarket_client.gamma_event_ids_for_market` |
