# Agent notes (Cursor / automation)

## CLOB v2 (current)

Polymarket migrated the exchange to **CLOB v2** on **2026-04-28**. The bot uses **`py-clob-client-v2`** (≥1.0.0). All order placement goes through `clob.create_and_post_market_order(...)` / `clob.create_and_post_order(...)` which internally retries on `order_version_mismatch` (the v1 SDK errored hard). `clob_retry_transient` in `polymarket_client.py` also catches `order_version_mismatch` defensively. Do NOT reintroduce `from py_clob_client …` — only `from py_clob_client_v2 …`.

## Strategy stance

Decisions are **price-driven** (band, momentum, competition, SL, TP, time-decay). The forecast/research/calibration model still runs and is shown for context, but the runtime gates `research_edge_gate_buy`, `min_model_prob_for_buy`, and `decision_min_model_peak_prob` default to **off / 0.0** in `data/runtime_config.json`. A market with no forecast still trades through price-based gates.

Momentum now supports **absolute OR percentage rise** checks with start/current price guards:
- `momentum_entry_rise` OR `momentum_pct_rise`
- `double_momentum_entry_rise` OR `double_momentum_pct_rise`

Stop-loss now uses an **effective stop**:
- per-type hard floor (`stop_loss_*`)
- plus entry-relative drop (`stop_loss_*_entry_drop_pct`)
- effective stop = `max(hard_floor, entry_price * (1 - drop_pct))`

Event churn is stricter:
- tiered loss cooldowns (`churn_event_loss_1_cooldown_sec`, `churn_event_loss_2_cooldown_sec`)
- short event block right after stop-loss to prevent sibling re-entry churn
- unstable event detector from rapid leader switches (`leader_switch_*`, `unstable_event_cooldown_sec`)

## Architecture

- **Anchor:** trading flow stays `run_bot` → `sync_state_with_portfolio` → `run_once` → `process_single_market` → `place_buy` / `close_position` / `claim_position` in `strategy/trades.py` and `strategy/loop.py`.
- **Decision layer:** `strategy/decision_core.py` is the central orchestrator. It calls `probability_engine`, `momentum_engine`, `competition_filter`, and `time_filter` to produce BUY/SKIP decisions. All entry decisions flow through `evaluate_entry()`, all exit checks through `check_exits()`.
- **Config:** defaults in `config/constants.py` (grouped by entry type); live overrides in `data/runtime_config.json` (merged each tick via `get_effective_settings()` in `config/settings.py`).
- **State:** `state.json` at repo root (paths in `config/constants.py`); churn counters in `state["churn_by_market"]` and `state["churn_by_event"]`. Active trades include `entry_time_utc` for time-decay and `entry_type` (`normal`/`momentum`/`double_momentum`) for per-type stop-loss.
- **Trade CSV:** `data/trade_log_YYYY-MM-DD.csv` via `state/pnl_ledger.py` — bot report clock `local_hhmm` plus `city_local_hhmm`, `entry_type`, `decision_reason` (BUY), `reason` (exit / claim).
- **No database:** JSON / JSONL only under `data/` (`runtime_config.json`, `blacklist_day.json`, `pnl_ledger.jsonl`, `price_samples/*.jsonl`).
- **API + UI:** FastAPI routes in `app/dashboard.py` (prefix `/api`). Static React build served at `/dashboard/` when `ui/dist` exists (`main_bot.py`).

## Rate limits

Respect Polymarket limits; see **[POLY_RATE_LIMITS.MD](POLY_RATE_LIMITS.MD)** and [Polymarket docs](https://docs.polymarket.com/). Prefer reusing tick data for momentum samples; competition uses targeted Gamma `markets` / `events` calls per event, not full market rescans.

## Modules

| Area | Location |
|------------|----------|
| **Decision engine** | `strategy/decision_core.py` — central entry/exit orchestrator, `stop_loss_bar_for_entry_type()` |
| **Probability model** | `strategy/probability_engine.py` — Gaussian P(YES) per bucket |
| **Momentum engine** | `strategy/momentum_engine.py` — absolute 0.15 fast exit, competitor surge, entry signal |
| **Competition filter** | `strategy/competition_filter.py` — 15% lead gap requirement |
| **Time filter** | `strategy/time_filter.py` — entry window (14:00-24:00) + time-decay exit |
| **Fast exit watcher** | `strategy/fast_exit_watcher.py` — daemon thread polling **live CLOB orderbook** (`get_clob_yes_price_live_by_id`) every 2s for per-type SL + momentum fast exit; bypasses Gamma `bestAsk` cache |
| Thresholds | `config/settings.py`, `data/runtime_config.json` (incl. `cash_reserve_usd`, sizing) |
| Gates / CLOB | `strategy/gates.py` |
| Probabilities (parse/TP/SL) | `strategy/probability.py` |
| Price samples infra | `strategy/momentum.py`, `data/price_samples/` |
| Anti-churn | `strategy/churn.py` — per-market + **per-event** churn (2 losses → 30 min block) |
| Telegram HTML | `notifications/telegram_fmt.py`, portfolio shell in `notifications/portfolio.py` |
| Gamma helpers | `polymarket_client.gamma_event_ids_for_market` |
| Research edge | `strategy/research_signal.py`, `notifications/research_trade_fmt.py` |
| Dashboard API | `app/dashboard.py` — includes `/api/trades/history`, `/api/decisions/recent`, `/api/stats/summary` |
| Dashboard UI | `ui/src/App.tsx` — tabs: Positions, Trade History, Decisions, Settings |
| **Telegram OpenRouter advisor** | `notifications/openrouter_advisor.py` — optional Q&A in a **separate `multiprocessing` process**; context includes `STRATEGY_LOGIC.md` + **`MODEL_PROBABILITY_AND_CALIBRATION.md`** (forecast→calibrated μ→σ→P(YES)). Set `OPENROUTER_API_KEY` (or `OPENROUTER` / `openrouter`), trigger with `/ask …` or messages containing `bot` / `BOT`; optional `ADVISOR_CATCHALL_NON_COMMANDS=1`. Tries `OPENROUTER_FREE_MODELS` in order until one succeeds; `OPENROUTER_MODEL` forces a single model. CLI: `ping`, `test`, `context`, `ask "…"` |

## Deprecated (kept for reference / tests)

- `strategy/decision_engine.py` — replaced by `decision_core.py`
- `strategy/competition.py` — replaced by `competition_filter.py`
- `strategy/flow_sampling.py`, `strategy/flow_signals.py` — removed (flow signals were not effective)
