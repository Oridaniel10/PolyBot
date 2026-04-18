# Agent notes (Cursor / automation)

## Architecture

- **Anchor:** trading flow stays `run_bot` → `sync_state_with_portfolio` → `run_once` → `process_single_market` → `place_buy` / `close_position` / `claim_position` in `strategy/trades.py` and `strategy/loop.py`.
- **Decision layer:** `strategy/decision_core.py` is the central orchestrator. It calls `probability_engine`, `momentum_engine`, `competition_filter`, and `time_filter` to produce BUY/SKIP decisions. All entry decisions flow through `evaluate_entry()`, all exit checks through `check_exits()`.
- **Config:** defaults in `config/constants.py`; live overrides in `data/runtime_config.json` (merged each tick via `get_effective_settings()` in `config/settings.py`).
- **State:** `state.json` at repo root (paths in `config/constants.py`); churn counters in `state["churn_by_market"]`. Active trades include `entry_time_utc` for time-decay.
- **No database:** JSON / JSONL only under `data/` (`runtime_config.json`, `blacklist_day.json`, `pnl_ledger.jsonl`, `price_samples/*.jsonl`).
- **API + UI:** FastAPI routes in `app/dashboard.py` (prefix `/api`). Static React build served at `/dashboard/` when `ui/dist` exists (`main_bot.py`).

## Rate limits

Respect Polymarket limits; see **[POLY_RATE_LIMITS.MD](POLY_RATE_LIMITS.MD)** and [Polymarket docs](https://docs.polymarket.com/). Prefer reusing tick data for momentum samples; competition uses targeted Gamma `markets` / `events` calls per event, not full market rescans.

## Modules

| Area | Location |
|------------|----------|
| **Decision engine** | `strategy/decision_core.py` — central entry/exit orchestrator |
| **Probability model** | `strategy/probability_engine.py` — Gaussian P(YES) per bucket |
| **Momentum engine** | `strategy/momentum_engine.py` — 15-min fast exit, competitor surge, entry signal |
| **Competition filter** | `strategy/competition_filter.py` — 15% lead gap requirement |
| **Time filter** | `strategy/time_filter.py` — entry window (14:00-24:00) + time-decay exit |
| Thresholds | `config/settings.py`, `data/runtime_config.json` (incl. `cash_reserve_usd`, sizing) |
| Gates / CLOB | `strategy/gates.py` |
| Probabilities (parse/TP/SL) | `strategy/probability.py` |
| Price samples infra | `strategy/momentum.py`, `data/price_samples/` |
| Anti-churn | `strategy/churn.py` |
| Telegram HTML | `notifications/telegram_fmt.py`, portfolio shell in `notifications/portfolio.py` |
| Gamma helpers | `polymarket_client.gamma_event_ids_for_market` |
| Research edge | `strategy/research_signal.py`, `notifications/research_trade_fmt.py` |
| Dashboard API | `app/dashboard.py` — includes `/api/trades/history`, `/api/decisions/recent`, `/api/stats/summary` |
| Dashboard UI | `ui/src/App.tsx` — tabs: Positions, Trade History, Decisions, Settings |
| **Telegram OpenRouter advisor** | `notifications/openrouter_advisor.py` — optional Q&A in a **separate `multiprocessing` process**; context includes `STRATEGY_LOGIC.md` + **`MODEL_PROBABILITY_AND_CALIBRATION.md`** (forecast→calibrated μ→σ→P(YES)). Set `OPENROUTER_API_KEY` (or `OPENROUTER` / `openrouter`), trigger with `/ask …` or messages containing `bot` / `BOT`; optional `ADVISOR_CATCHALL_NON_COMMANDS=1`. Tries `OPENROUTER_FREE_MODELS` in order until one succeeds; `OPENROUTER_MODEL` forces a single model. CLI: `ping`, `test`, `context`, `ask "…"` |

## Deprecated (kept for reference / tests)

- `strategy/decision_engine.py` — replaced by `decision_core.py`
- `strategy/flow_sampling.py`, `strategy/flow_signals.py` — removed (flow signals were not effective)
