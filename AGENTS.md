# Agent notes (Cursor / automation)

## CLOB v2 (current)

Polymarket migrated the exchange to **CLOB v2** on **2026-04-28**. The bot uses **`py-clob-client-v2`** (≥1.0.0). All order placement goes through `clob.create_and_post_market_order(...)` / `clob.create_and_post_order(...)` which internally retries on `order_version_mismatch` (the v1 SDK errored hard). `clob_retry_transient` in `polymarket_client.py` also catches `order_version_mismatch` defensively. Do NOT reintroduce `from py_clob_client …` — only `from py_clob_client_v2 …`.

## Strategy stance

Decisions are **price-driven** (band, momentum, competition, SL, TP, time-decay). The forecast/research/calibration model still runs and is shown for context, but the runtime gates `research_edge_gate_buy`, `min_model_prob_for_buy`, and `decision_min_model_peak_prob` default to **off / 0.0** in `data/runtime_config.json`. A market with no forecast still trades through price-based gates.

Momentum now supports **absolute OR percentage rise** checks **across two windows** with start/current price guards. The dual-window check (`strategy/decision_core.py::momentum_dual_window_check`) qualifies if **either** the standard 15-minute window or the new 5-minute fast window passes the rise gate **and** the live price sits inside the entry-type band.
- normal momentum: `momentum_entry_rise=0.20` OR `momentum_pct_rise=6.0` (+600%); price band `momentum_min_price..momentum_max_entry`
- double momentum: `double_momentum_entry_rise=0.40` OR `double_momentum_pct_rise=10.0` (+1000%); price band `double_momentum_min_price..double_momentum_max_price`
- normal entries: `buy_max_threshold=0.84` (lowered from 0.91); a re-fetched live CLOB best ask is enforced before submit, otherwise skip with `live_price_above_entry_type_max`

Stop-loss now uses an **effective stop**:
- per-type hard floor (`stop_loss_*`)
- plus entry-relative drop (`stop_loss_*_entry_drop_pct`)
- plus **trailing stop** when `highest_seen_price ≥ entry + trailing_stop_activation_gain` (lock at `entry + trailing_stop_lock_gain`)
- effective stop = `max(hard_floor, entry_price * (1 − drop_pct), trailing_stop_level_if_active)`

Stop-loss breaches are **categorized** (`SL_ABSOLUTE` / `SL_RELATIVE` / `SL_TRAILING` / `SL_MOMENTUM`) by `decision_core.classify_stop_loss_breach`. The fast-exit watcher and the main loop both populate `trade["sl_category"]` / `trade["sl_level"]` so the trade ledger and Telegram show the exact breach reason.

**Order execution is limit-first**:
- `place_buy` re-fetches CLOB best ask, snaps to tick, posts a GTC limit; cancels and skips if not filled inside `buy_limit_order_timeout_sec`.
- Non-emergency sells try a limit first; emergency reasons (stop-loss, fast-exit, bucket switch, time-decay) may fall back to market when `emergency_exit_allow_market_order=true`.
- Slippage and fill metadata land in both `state.json` and the trade CSV.

**Bucket switching is now atomic** (`strategy/trades.py::_execute_bucket_switch_sell`): sell first, confirm fill, only then buy the new bucket. Each step emits structured logs (`switch_candidate_detected`, `switch_sell_started`, `switch_sell_failed_skip_buy`, `switch_sell_success_buy_new`, `switch_buy_failed_after_sell`, `switch_completed`) and matching Telegram updates.

**Telegram failed-exit dedup** (`strategy/telegram_dedup.py`) replaces the old SHA-256 fingerprint with a `(market_id, exit_reason, error_category)` cooldown driven by `telegram_failed_exit_cooldown_sec`. Successful close clears the dedup state.

Event churn is stricter:
- tiered loss cooldowns (`churn_event_loss_1_cooldown_sec`, `churn_event_loss_2_cooldown_sec`)
- short event block right after stop-loss to prevent sibling re-entry churn
- unstable event detector from rapid leader switches (`leader_switch_*`, `unstable_event_cooldown_sec`)

## Architecture

- **Anchor:** trading flow stays `run_bot` → `sync_state_with_portfolio` → `run_once` → `process_single_market` → `place_buy` / `close_position` / `claim_position` in `strategy/trades.py` and `strategy/loop.py`.
- **Decision layer:** `strategy/decision_core.py` is the central orchestrator. It calls `probability_engine`, `momentum_engine`, `competition_filter`, and `time_filter` to produce BUY/SKIP decisions. All entry decisions flow through `evaluate_entry()`, all exit checks through `check_exits()`.
- **Config:** defaults in `config/constants.py` (grouped by entry type); live overrides in `data/runtime_config.json` (merged each tick via `get_effective_settings()` in `config/settings.py`).
- **State:** `state.json` at repo root (paths in `config/constants.py`); churn counters in `state["churn_by_market"]` and `state["churn_by_event"]`. Active trades include `entry_time_utc` (time-decay), `entry_type` (`normal`/`momentum`/`double_momentum`) for per-type stop-loss, `highest_seen_price` (trailing stop), and `execution_*` metadata (limit/market mode, fill price, slippage).
- **Trade CSV:** `data/trade_log_YYYY-MM-DD.csv` via `state/pnl_ledger.py` — bot report clock `local_hhmm` plus `city_local_hhmm`, `entry_type`, `decision_reason` (BUY), `reason` (exit / claim), and the new rich-logging columns: `trigger_window`, `trigger_abs_rise`, `trigger_pct_rise`, `decision_price`, `live_clob_price_before_order`, `execution_mode`, `execution_limit_price`, `execution_fill_price`, `execution_slippage`, `sl_category`, `highest_seen_price`, `full_reason` (toggled by `trade_log_full_reason_enabled`).
- **No database:** JSON / JSONL only under `data/` (`runtime_config.json`, `blacklist_day.json`, `pnl_ledger.jsonl`, `price_samples/*.jsonl`).
- **API + UI:** FastAPI routes in `app/dashboard.py` (prefix `/api`). Static React build served at `/dashboard/` when `ui/dist` exists (`main_bot.py`).

## Rate limits

Respect Polymarket limits; see **[POLY_RATE_LIMITS.MD](POLY_RATE_LIMITS.MD)** and [Polymarket docs](https://docs.polymarket.com/). Prefer reusing tick data for momentum samples; competition uses targeted Gamma `markets` / `events` calls per event, not full market rescans.

## Modules

| Area | Location |
|------------|----------|
| **Decision engine** | `strategy/decision_core.py` — central entry/exit orchestrator, `momentum_dual_window_check`, `trailing_stop_level`, `classify_stop_loss_breach`, `stop_loss_bar_for_entry_type` |
| **Probability model** | `strategy/probability_engine.py` — Gaussian P(YES) per bucket |
| **Momentum engine** | `strategy/momentum_engine.py` — absolute 0.15 fast exit, competitor surge, entry signal |
| **Competition filter** | `strategy/competition_filter.py` — 15% lead gap requirement |
| **Time filter** | `strategy/time_filter.py` — entry window (14:00-24:00) + time-decay exit |
| **Fast exit watcher** | `strategy/fast_exit_watcher.py` — daemon thread polling **live CLOB orderbook** (`get_clob_yes_price_live_by_id`) every 2s for per-type SL + momentum fast exit + trailing stop; updates `highest_seen_price` and `sl_category`; bypasses Gamma `bestAsk` cache |
| **Limit-order executor** | `strategy/limit_executor.py` — `execute_buy` posts GTC limit, polls fill, cancels on timeout; emergency reasons (`EMERGENCY_SELL_REASONS`) may fall back to market |
| **Telegram dedup** | `strategy/telegram_dedup.py` — `(market_id, exit_reason, error_category)` cooldown for failed exits; cleared on success |
| Thresholds | `config/settings.py`, `data/runtime_config.json` (incl. `cash_reserve_usd`, sizing, limit-order toggles, trailing stop, telegram dedup) |
| Gates / CLOB | `strategy/gates.py`, `polymarket_client` (`get_clob_best_ask_yes`, `get_clob_best_bid_yes`, `place_limit_buy_yes`, `cancel_order`, `get_order_state`, `align_price_to_tick_buy`) |
| Probabilities (parse/TP/SL) | `strategy/probability.py` |
| Price samples infra | `strategy/momentum.py`, `data/price_samples/` |
| Anti-churn | `strategy/churn.py` — per-market + **per-event** churn (2 losses → 30 min block) |
| Telegram HTML | `notifications/telegram_fmt.py`, portfolio shell in `notifications/portfolio.py`; rich BUY/SELL templates in `strategy/trades.py` (entry-type header, trigger window/metric, SL category) |
| Gamma helpers | `polymarket_client.gamma_event_ids_for_market` |
| Research edge | `strategy/research_signal.py`, `notifications/research_trade_fmt.py` |
| Dashboard API | `app/dashboard.py` — includes `/api/trades/history`, `/api/decisions/recent`, `/api/stats/summary` |
| Dashboard UI | `ui/src/App.tsx` — tabs: Positions, Trade History, Decisions, Settings |
| **Telegram OpenRouter advisor** | `notifications/openrouter_advisor.py` — optional Q&A in a **separate `multiprocessing` process**; context includes `STRATEGY_LOGIC.md` + **`MODEL_PROBABILITY_AND_CALIBRATION.md`** (forecast→calibrated μ→σ→P(YES)). Set `OPENROUTER_API_KEY` (or `OPENROUTER` / `openrouter`), trigger with `/ask …` or messages containing `bot` / `BOT`; optional `ADVISOR_CATCHALL_NON_COMMANDS=1`. Tries `OPENROUTER_FREE_MODELS` in order until one succeeds; `OPENROUTER_MODEL` forces a single model. CLI: `ping`, `test`, `context`, `ask "…"` |

## Deprecated (kept for reference / tests)

- `strategy/decision_engine.py` — replaced by `decision_core.py`
- `strategy/competition.py` — replaced by `competition_filter.py`
- `strategy/flow_sampling.py`, `strategy/flow_signals.py` — removed (flow signals were not effective)
