# Smart Trading Engine — Strategy Logic

Full entry/exit strategy for the Polymarket weather bot. All thresholds are in `config/constants.py` with live overrides via `data/runtime_config.json` (merged each tick in `config/settings.py`).

> **Strategy stance (current).** Decisions are driven by **price action** — momentum, price band, competition, stop-loss, take-profit, time-decay. The forecast/research/calibration model still computes `model_prob` for display and optional gates, but all research-edge gates default to **OFF** (`research_edge_gate_buy=false`) and the model-prob floors are **0.0** so they don't block entries. A market with no forecast still trades through the price-based gates.

> **Exchange.** Polymarket switched to **CLOB v2** on 2026-04-28; the bot uses `py-clob-client-v2` (>=1.0.0). The `order_version_mismatch` errors in v1 are gone — v2 has built-in retry on schema drift via `create_and_post_market_order` / `create_and_post_order`.

---

## Architecture

```
run_bot → sync_state_with_portfolio → run_once → process_single_market
                                                        │
                                          ┌─────────────┼─────────────┐
                                          ▼             ▼             ▼
                                     EXIT CHECKS    ENTRY LOGIC    CLAIM
                                    (check_exits)  (evaluate_entry)
                                          │             │
                                    ┌─────┴────┐  ┌─────┴────┐
                                    │momentum  │  │probability│
                                    │time_decay│  │competition│
                                    │SL / TP   │  │momentum   │
                                    └──────────┘  │time_filter│
                                                  │edge gate  │
                                                  └───────────┘

   ┌────────────────────────────────────────────┐
   │  FAST EXIT WATCHER (daemon thread)         │
   │  polls CLOB /price every 2 seconds         │
   │  checks: per-type SL + momentum fast exit  │
   │  strategy/fast_exit_watcher.py             │
   └────────────────────────────────────────────┘
```

**Decision engine**: `strategy/decision_core.py` — single source of truth for entry/exit decisions.
**Fast exit watcher**: `strategy/fast_exit_watcher.py` — daemon thread, 2-second polling for emergency exits.

---

## General Rules

1. We invest **only** in maximum temperature markets — cities and locations.
2. Event date must be **today or yesterday** (Israel time) — never future dates.
3. **City local calendar day** must equal the market **event_date** (the day the max is for), and local clock **14:00–23:59** — no buys on the prior calendar day even if the hour is past 14:00.
4. Market price must be within price band (depends on entry type — see below).
5. Max **1 position per event** (gamma event).
6. Max **7 total open positions**.
7. After a stop-loss exit → market is **blacklisted for 20 minutes** (churn cooldown).
8. After **2 losses on the same event** → entire event is **blocked for 30 minutes** (event-level churn).

---

## ENTRY TYPES — Price Bands & Stop-Loss (grouped)

Each entry type defines its own price band and stop-loss threshold. Constants are grouped in `config/constants.py` under **ENTRY BANDS & STOP-LOSS**.

### Normal Entry (price band + competition)

| Variable | Default | Where |
|----------|---------|-------|
| `BUY_MIN_THRESHOLD` | **0.65** | `config/constants.py` |
| `BUY_MAX_THRESHOLD` | **0.84** | `config/constants.py` (runtime) |
| `STOP_LOSS_NORMAL` | **0.50** | `config/constants.py` |
| `MAX_MARKET_PROB_FOR_BUY` | **0.99** | `config/constants.py` |
| `MIN_MODEL_PROB_FOR_BUY` | **0.0** (runtime) | `data/runtime_config.json` |
| `RESEARCH_EDGE_GATE_BUY` | **false** (runtime) | `data/runtime_config.json` |

Entry when price is in the band (0.80–0.84 by default) and the candidate passes the competition filter (15% lead vs runner-up). The upper bound was tightened from 0.91 → **0.84** because buys at 0.85+ leave too little upside and large downside, and `place_buy` re-checks the **live CLOB best ask** before submitting; if it exceeds the entry-type max it logs `live_price_above_entry_type_max` and skips. With research and model-prob gates disabled by default, stable high-prob markets in 0.80–0.84 with no competing siblings flow through naturally.

### Momentum Entry (absolute OR percent rise, dual-window) ⚡

| Variable | Default | Where |
|----------|---------|-------|
| `MOMENTUM_ENTRY_RISE` | **0.20** | `config/constants.py` |
| `MOMENTUM_PCT_RISE` | **6.0** (i.e. +600%) | `config/constants.py` |
| `MOMENTUM_MIN_START_PRICE` | **0.10** | `config/constants.py` |
| `MOMENTUM_MIN_PRICE` | **0.61** | `config/constants.py` |
| `MOMENTUM_MAX_ENTRY` | **0.80** | `config/constants.py` |
| `MOMENTUM_WINDOW_SECONDS` | **900** (15 min) | `config/constants.py` |
| `MOMENTUM_FAST_WINDOW_SECONDS` | **300** (5 min) | `config/constants.py` |
| `STOP_LOSS_MOMENTUM` | **0.45** | `config/constants.py` |
| `STOP_LOSS_MOMENTUM_ENTRY_DROP_PCT` | **0.40** | `config/constants.py` |

Entry when YES rose by **absolute** rise OR **percent** rise in **either** the standard 15-minute window **or** the new 5-minute fast window (`momentum_dual_window_check` in `decision_core.py`). Both windows use the same guardrails: min start price (no 0.001 → 0.01 noise), min current price, and max current price. Rank and runner-up gap are logged for context but are not required for momentum entry. Bypasses model/edge gates. **Both** the rise condition and the live-price band must hold at execution.

### Double Momentum Entry (absolute OR percent rise, wider band) 🚀

| Variable | Default | Where |
|----------|---------|-------|
| `DOUBLE_MOMENTUM_ENTRY_RISE` | **0.40** | `config/constants.py` |
| `DOUBLE_MOMENTUM_PCT_RISE` | **10.0** (i.e. +1000%) | `config/constants.py` |
| `DOUBLE_MOMENTUM_MIN_START_PRICE` | **0.05** | `config/constants.py` |
| `DOUBLE_MOMENTUM_MIN_PRICE` | **0.10** | `config/constants.py` |
| `DOUBLE_MOMENTUM_MAX_PRICE` | **0.80** | `config/constants.py` |
| `DOUBLE_MOMENTUM_FAST_WINDOW_SECONDS` | **300** (5 min) | `config/constants.py` |
| `STOP_LOSS_DOUBLE_MOMENTUM` | **0.20** | `config/constants.py` |
| `STOP_LOSS_DOUBLE_MOMENTUM_ENTRY_DROP_PCT` | **0.40** | `config/constants.py` |

Entry when YES rose at least the configured absolute OR percent threshold in **either** window. Same explicit guardrail: rise + live price band must both pass.

### How entry_type is determined

At buy time, `evaluate_entry()` in `decision_core.py` sets `TradeDecision.entry_type` based on the dual-window check:
- `"double_momentum"` → abs rise ≥0.40 OR pct rise ≥1000% in 15m **or** 5m, AND price in 0.10–0.80
- `"momentum"` → abs rise ≥0.20 OR pct rise ≥600% in 15m **or** 5m, AND price in 0.61–0.80
- `"normal"` → everything else (price in 0.65–0.84 + competition)

The `TradeDecision` also carries `trigger_window` (`15m_std` / `5m_fast` / `both`), `trigger_abs_rise`, `trigger_pct_rise`, and a `full_reason` string that downstream logs/Telegram render verbatim.

The `entry_type` is stored in `state.json → active_trades[market_id]["entry_type"]` and preserved through `sync_state_with_portfolio`. The per-type stop-loss is computed from `entry_type` via `stop_loss_bar_for_entry_type()` in `decision_core.py`.

### Legacy tiered SL (backward compat)

For positions opened before `entry_type` was introduced, the legacy tiered system is used as fallback:
- `STOP_LOSS_USE_ENTRY_TIERS`, `STOP_LOSS_TIER_ENTRY_SPLIT`, `STOP_LOSS_TIER_MARK_LOW`, `STOP_LOSS_TIER_MARK_HIGH`

All runtime-overridable via `runtime_config.json`.

---

## ENTRY CONDITIONS (BUY)

A buy is executed only when **ALL** conditions pass, checked in order:

| # | Condition | Rule | Variable | Defined in |
|---|-----------|------|----------|------------|
| 1 | **Date gate** | event_date ≤ today (Israel TZ) | `BUY_BLOCK_EVENT_DATE_AFTER_ISRAEL_TODAY` | `config/constants.py` |
| 2 | **Time + event day** | city_local `date == event_date` **and** 14:00 ≤ hour ≤ 23:59 | `BUY_EARLIEST_HOUR`, `BUY_LATEST_LOCAL_HOUR` | `config/constants.py`, `strategy/time_filter.py` |
| 3 | **Price band** | depends on entry type (see table above) | see entry type tables | `config/constants.py` |
| 4 | **Position limit** | total open < 7 | `MAX_CONCURRENT_POSITIONS = 7` | `config/constants.py` |
| 5 | **Forecast (optional)** | consensus °C from Open-Meteo — when missing, bot continues on price-based gates | — | `forecast/forecast_service.py` |
| 6 | **Event churn** 🆕 | no event-level cooldown from 2 losses on same event | `CHURN_EVENT_MAX_LOSSES = 2`, `CHURN_EVENT_COOLDOWN_SEC = 1800` | `config/constants.py`, `strategy/churn.py` |
| 7 | **Event cooldown** | `event_buy_cooldown` in state | — | `strategy/decision_core.py` |
| 8 | **No duplicate** | not already holding this market_id | — | `strategy/decision_core.py` |
| 9 | **Max per event** | max 1 position per gamma event | `MAX_POSITIONS_PER_EVENT = 1` | `config/constants.py` |
| 10 | **Market prob ceiling** | market_yes ≤ max (skipped for momentum) | `MAX_MARKET_PROB_FOR_BUY = 0.99` | `config/constants.py` |
| 11 | **Model prob floor** | model_prob ≥ min (skipped for momentum) — **default 0.0 in runtime, effectively off** | `MIN_MODEL_PROB_FOR_BUY = 0.10` (constant) / `0.0` (runtime) | `config/constants.py`, `data/runtime_config.json` |
| 12 | **Not flat distribution** | model peak gate (skipped for momentum) — **default 0.0 in runtime, effectively off** | `DECISION_MIN_MODEL_PEAK_PROB = 0.12` (constant) / `0.0` (runtime) | `config/constants.py`, `data/runtime_config.json` |
| 13 | **Momentum entry** ⚡ | Standard (+0.20 abs OR +600%) or Double (+0.40 abs OR +1000%) — checked in **15m std + 5m fast** windows | `MOMENTUM_ENTRY_RISE`, `MOMENTUM_PCT_RISE`, `DOUBLE_MOMENTUM_ENTRY_RISE`, `DOUBLE_MOMENTUM_PCT_RISE`, `MOMENTUM_FAST_WINDOW_SECONDS`, `DOUBLE_MOMENTUM_FAST_WINDOW_SECONDS` | `strategy/decision_core.py::momentum_dual_window_check` |
| 13b | **Momentum switch** 🔁 | hold A; B is #1 with momentum + gap → sell A, **wait for fill**, then buy B atomically (`_execute_bucket_switch_sell`) | `MOMENTUM_SWITCH_ABOVE_HELD_GAP = 0.15` | `strategy/decision_core.py`, `strategy/trades.py` |
| 14 | **Competition** | gap vs runner-up ≥ `min_lead` (skipped for momentum entry) | `MIN_LEAD_OVER_RUNNER_UP = 0.15` | `strategy/competition_filter.py` |
| 15 | **No negative momentum** | 15-min change > -0.10 points (skipped for momentum) | — | `strategy/decision_core.py` |
| 16 | **Edge gate** | `edge ≥ required_edge` (skipped for momentum) — **default OFF in runtime** (`research_edge_gate_buy=false`) | `RESEARCH_MIN_EDGE` | `strategy/research_signal.py` |
| 17 | **Forecast gate** | bracket contradict check (relaxed for momentum/double-momentum) | `FORECAST_CONTRADICT_MARGIN_C = 2.5` | `strategy/trades.py` |
| 18 | **CLOB price verify** | re-fetch live best ask, enforce entry-type max (`buy_max_threshold` / `momentum_max_entry` / `double_momentum_max_price`); skip with `live_price_above_entry_type_max` if breached | — | `strategy/trades.py::place_buy` |
| 19 | **Market churn** | no cooldown from prior stop-loss on this market | `CHURN_COOLDOWN_SEC = 1200` | `strategy/churn.py` |

---

## EXIT CONDITIONS (SELL)

Exits are checked in priority order (first match wins). There are **two loops** checking exits:

1. **Fast exit watcher** (daemon thread, every **2 seconds**) — checks per-type SL + momentum fast exit only
2. **Main loop** (every **30 seconds**) — checks all exit conditions

### Exit Priority Table

| # | Condition | Rule | Speed | Variable | Defined in |
|---|-----------|------|-------|----------|------------|
| 1 | **Market resolved** | status = closed/claimable/resolved → CLAIM | 30s | `STATUS_CLOSED` | `config/constants.py` |
| 2 | **Momentum fast exit** 🚨 | ABSOLUTE price drop ≥ **0.15** from peak in 15-min window **and** mark < entry | **2s** (watcher) + 30s (main) | `MOMENTUM_FAST_EXIT_DROP = 0.15` | `config/constants.py`, `strategy/momentum_engine.py` |
| 3 | **Dominant competitor** | sibling #1 by YES has momentum + gap ≥ held + 0.15 → EXIT | 30s | `MOMENTUM_SWITCH_ABOVE_HELD_GAP` | `strategy/decision_core.py` |
| 4 | **Competitor surge** 🔥 | any sibling rose ≥ `momentum_competitor_surge` **absolute** points in `momentum_window_seconds` | 30s | `MOMENTUM_COMPETITOR_SURGE` (default **0.25**) | `config/constants.py` + `data/runtime_config.json` → `strategy/decision_core.py::check_exits` → `peer_surge_detected` |
| 5 | **Time-decay** ⏰ | held >2h AND **(mark − entry) < min_gain_points** AND mark < max_price | 30s | `time_decay_hours`, `time_decay_min_gain`, `time_decay_max_price` (defaults in `TIME_DECAY_*`) | `strategy/time_filter.py`, `data/runtime_config.json` |
| 6 | **Research model flip** | forecast contradict (optional, default off) | 30s | `RESEARCH_EXIT_ON_MODEL_FLIP` | `config/constants.py` |
| 7 | **Per-type stop-loss** 🆕 | mark < effective stop (hard floor + entry-relative) | **2s** (watcher) + 30s (main) | `STOP_LOSS_NORMAL=0.50`, `STOP_LOSS_MOMENTUM=0.45`, `STOP_LOSS_DOUBLE_MOMENTUM=0.30` | `config/constants.py`, `strategy/decision_core.py` |
| 8 | **Take-profit** | mark ≥ 0.97 | 30s | `TAKE_PROFIT_THRESHOLD = 0.97` | `config/constants.py` |

### Momentum Fast Exit — Absolute Drop 🆕

Momentum entry, competitor surge, and fast exit use **absolute price points**, not percentage:
- Entry: 0.40 → 0.46 is **not** +0.15 momentum; 0.40 → 0.55 is.
- Surge: peer 0.30 → 0.45 qualifies; 0.30 → 0.345 does not.
- Fast exit: peak-to-trough ≥ **0.15 absolute** exits (e.g., 0.75 → 0.59 = 0.16 > 0.15).

#### Where to change competitor surge (not the same as price stop-loss)

This exit is **`competitor-surge`**: a **sibling bucket** in the same event gained at least **`momentum_competitor_surge`** YES points inside **`momentum_window_seconds`**. It is **not** `stop_loss_normal` / trailing — those compare **our** market’s mark to floors.

| What to edit | Key / constant |
|--------------|----------------|
| Static default | `MOMENTUM_COMPETITOR_SURGE` in `config/constants.py` |
| Live override (preferred) | `momentum_competitor_surge` in `data/runtime_config.json` |
| Code path | `momentum_competitor_surge_thr(settings)` in `strategy/decision_core.py` → passed to `peer_surge_detected(..., surge_threshold=...)` from `check_exits` |

**Tuning direction:** the value is a **minimum rise** on the peer. **Higher number** (e.g. 0.25) → peer must jump **more** before we exit → **fewer** surge exits. **Lower number** (e.g. 0.08) → exit **sooner** when any sibling climbs a little.

Example: entered at 0.60, price spiked to 0.75, then dropped to 0.59.
- Peak = 0.75, current = 0.59, drop = 0.16 > 0.15 → **exit**
- Also: 0.59 < 0.60 entry → mark < entry check passes → **momentum-stop-loss** triggered

### Per-Type Stop-Loss 🆕

The stop-loss bar depends on how the position was entered:

| Entry Type | SL Bar | Example |
|------------|--------|---------|
| `normal` | **0.50** | hard floor for normal entries |
| `momentum` | **0.45** | Entered at 0.70, exits if mark < 0.45 |
| `double_momentum` | **0.20** | Entered at 0.30, exits if mark < 0.20 |

Effective stop also includes entry-relative drop **and** the trailing stop level:

`effective_stop = max(stop_loss_by_type, entry_price * (1 - entry_drop_pct_by_type), trailing_stop_level_if_active)`

The `entry_type` is stored in `state.json` at buy time and used by both the main loop and the fast exit watcher.

### Trailing Stop 🆕

A profit-protection layer that piggy-backs on the per-type stop. Logic in `strategy/decision_core.py::trailing_stop_level`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `TRAILING_STOP_ENABLED` | `true` | master toggle |
| `TRAILING_STOP_ACTIVATION_GAIN` | **0.20** | unlock once `highest_seen_price ≥ entry_price + 0.20` |
| `TRAILING_STOP_LOCK_GAIN` | **0.10** | once unlocked, lock stop at `entry_price + 0.10` |

Both the main loop and the fast exit watcher call `update_highest_seen_price` so `trade["highest_seen_price"]` always reflects the running peak from live CLOB. The trailing level is folded into `effective_stop_price_for_trade` and applies to **all** entry types unless disabled in runtime config.

### Stop-Loss Categories 🆕

When a stop-loss fires, the bot tags the breach so reports and Telegram messages can distinguish a slow bleed from a sudden crash. `strategy/decision_core.py::classify_stop_loss_breach` returns one of:

| Category | When |
|----------|------|
| `SL_ABSOLUTE` | mark below per-type hard floor |
| `SL_RELATIVE` | mark below `entry_price × (1 − entry_drop_pct)` |
| `SL_TRAILING` | mark below `entry_price + lock_gain` after activation |
| `SL_MOMENTUM` | momentum drawdown fast exit (`momentum_fast_exit_drop`) |

The category is stored on the trade row (`trade["sl_category"]` / `trade["sl_level"]` / `trade["sl_drawdown_points"]`) so it survives the close path, ledger write, and the Telegram template (`🔴 SELL: [STOP LOSS] - Reason: Trailing Stop (Trigger: 0.60, Max Seen: 0.72)`).

### Fast Exit Watcher 🆕

A **daemon thread** running every **2 seconds** that checks ONLY open positions for emergency exits:

- Uses **live CLOB orderbook** via `get_clob_yes_price_live_by_id` (best ask / midpoint) — **bypasses Gamma `bestAsk` cache** which can be stale by tens of seconds and miss real price drops. This is the fix for the "bought 0.75 → dropped to 0.30 without SL" bug.
- Writes the fresh CLOB price back into `trade_row["last_price"]` so the slow main-loop SL also sees current data on the next tick.
- Checks per-type stop-loss and momentum fast exit
- Uses `threading.Lock` to prevent double-sells with the main loop
- Does NOT scan for new buys or call Gamma list APIs
- Module: `strategy/fast_exit_watcher.py`
- Started in `strategy/bot_runner.py` at startup
- Interval configurable: `FAST_EXIT_WATCHER_INTERVAL_SEC = 2` (runtime override via `runtime_config.json`)

---

## Order Execution — Limit-First 🆕

By default the bot now **never** places blind market orders for buys. The flow is in `strategy/limit_executor.py::execute_buy` and on the sell side in `strategy/trades.py::close_position`.

### Buy path

1. `place_buy` re-fetches the **live CLOB best ask** (`get_clob_best_ask_yes`) and snaps it to the market tick (`align_price_to_tick_buy`).
2. If best ask exceeds the entry-type max (see entry-type tables) the buy is skipped with reason `live_price_above_entry_type_max` and structured log fields `entry_type / decision_price / live_clob_price_before_order / max_allowed_price / buy_allowed_true_false`.
3. Otherwise a **GTC limit buy** is posted at `best_ask + buy_limit_price_offset`.
4. Up to `buy_limit_order_timeout_sec` seconds the executor polls `get_order_state`. Filled → state is written; not filled → `cancel_order` is called and the candidate is dropped (no chasing).
5. Slippage and fill price are recorded in `state.json → active_trades[market_id]["execution_*"]` and in the trade ledger for post-mortems.
6. If `limit_orders_enabled` is `false`, the same wrapper falls back to the legacy market order so the toggle is reversible without a redeploy.

### Sell path

* Normal take-profit and non-emergency exits attempt a limit sell first (best bid − `sell_limit_price_offset`) with `sell_limit_order_timeout_sec`.
* Stop-loss / momentum-fast-exit / bucket-switch / time-decay are tagged as **emergency** (see `EMERGENCY_SELL_REASONS` in `config/constants.py`). When `emergency_exit_allow_market_order` is `true`, the bot is allowed to fall back to market orders so safety wins over fee savings.

### Config keys

| Key | Default |
|-----|---------|
| `limit_orders_enabled` | `true` |
| `buy_limit_order_timeout_sec` | `8` |
| `sell_limit_order_timeout_sec` | `5` |
| `emergency_exit_allow_market_order` | `true` |
| `buy_limit_price_offset` | `0.0` |
| `sell_limit_price_offset` | `0.005` |
| `require_fill_before_state_buy` | `true` |

---

## Safe Bucket Switching 🆕

`strategy/trades.py::_execute_bucket_switch_sell` enforces the **sell first → confirm → buy** ordering so the bot never holds two buckets in the same gamma event. Each step emits a structured log line via `_bucket_switch_log` and a Telegram update via `_bucket_switch_telegram`:

| Log label | Trigger |
|-----------|---------|
| `switch_candidate_detected` | `decision_core` flagged a stronger sibling |
| `switch_sell_started` | the held position is being closed |
| `switch_sell_failed_skip_buy` | the sell attempt did not complete — buy is **aborted** |
| `switch_sell_success_buy_new` | sell confirmed, new bucket buy is initiated |
| `switch_buy_failed_after_sell` | sell succeeded, but buy on the new bucket failed |
| `switch_completed` | both legs succeeded |

If the sell fails for any reason the new buy is unconditionally skipped. The sell uses the emergency path so it can fall back to a market order when needed.

---

## Telegram Failed-Exit Dedup 🆕

`strategy/telegram_dedup.py` replaces the old SHA-256 fingerprint with a `(market_id, exit_reason, error_category)` cooldown. `categorize_error` strips IDs/whitespace from the underlying error string so a sequence of similar failures (e.g. repeated `not enough liquidity`) does **not** spam Telegram.

| Key | Default | Purpose |
|-----|---------|---------|
| `telegram_failed_exit_dedupe_enabled` | `true` | master toggle |
| `telegram_failed_exit_cooldown_sec` | `900` | min seconds between identical failure notices |

Behaviour:

* First failure for a `(market_id, reason, category)` triple → Telegram sends one message and logs `telegram_failed_exit_first_notice`.
* Subsequent identical failures inside the cooldown → suppressed; logs `telegram_failed_exit_suppressed_duplicate`.
* On the next successful close, `clear_failed_exit_notices(market_id)` runs and the normal SELL message is sent. Logs `telegram_failed_exit_success_cleared`.
* A different category or a new exit_reason (e.g. switching from `stop-loss` to `take-profit`) produces a new message immediately.

The dedup applies to: stop-loss failures, take-profit failures, emergency exits, and bucket-switch sell failures. Successful BUY/SELL/CLAIM/STOP-LOSS messages are unaffected.

---

## Rich Trade Logging 🆕

Every BUY/SELL row in `data/trade_log_YYYY-MM-DD.csv` (and the JSONL ledger) now carries the full traceable context. Schema lives in `state/pnl_ledger.py::TRADE_CSV_FIELDS`:

| Field | Description |
|-------|-------------|
| `entry_type` | `normal` / `momentum` / `double_momentum` |
| `trigger_window` | `15m_std` / `5m_fast` / `both` (`-` for normal entries) |
| `trigger_abs_rise`, `trigger_pct_rise` | exact values that satisfied the rise gate |
| `decision_price` | Gamma price at the moment of decision |
| `live_clob_price_before_order` | best-ask read just before submitting |
| `execution_mode` | `limit` / `market` / `emergency_market` / `cancelled` |
| `execution_limit_price`, `execution_fill_price`, `execution_slippage` | from the limit executor |
| `sl_category` | `SL_ABSOLUTE` / `SL_RELATIVE` / `SL_TRAILING` / `SL_MOMENTUM` (sell rows only) |
| `highest_seen_price` | trailing-stop reference at exit time |
| `full_reason` | human-readable concatenation; toggled by `trade_log_full_reason_enabled` |

Telegram BUY/SELL HTML mirrors the same data when `telegram_verbose_trade_reason=true` so the chat tells you exactly which window/metric/band fired the trade.

---

## Anti-Churn System

### Per-Market Churn

After a stop-loss exit, the specific `market_id` is blocked for **20 minutes**:

| Variable | Default | Where |
|----------|---------|-------|
| `CHURN_MAX_STOP_CYCLES` | 1 | `config/constants.py` |
| `CHURN_COOLDOWN_SEC` | 1200 (20 min) | `config/constants.py` |

### Event-Level Churn 🆕

After **2 losses** on ANY market in the same gamma event, the **entire event** is blocked for **30 minutes**. This prevents the bot from losing on "Paris 22°C", switching to "Paris 23°C", and losing again.

| Variable | Default | Where |
|----------|---------|-------|
| `CHURN_EVENT_MAX_LOSSES` | 2 | `config/constants.py` |
| `CHURN_EVENT_COOLDOWN_SEC` | 1800 (30 min) | `config/constants.py` |

State tracked in `state["churn_by_event"][event_id]`. Module: `strategy/churn.py`.

### After Exit: What Gets Tracked

On any loss exit (`stop-loss`, `momentum-stop-loss`, `competitor-surge`, `momentum-competitor-dominant`, `time-decay`):
1. **Market-level** churn counter incremented → may block re-buy of same market
2. **Event-level** churn counter incremented → may block ALL buys in that gamma event
3. Take-profit resets the market-level counter

---

## Price Samples

Price samples are stored per market under `data/price_samples/YYYY-MM-DD.jsonl`.

- The main loop records one sample per scanned or held market per tick.
- Retention is trimmed to roughly the last **2 hours** only (`strategy/momentum.py`), then capped by `PRICE_SAMPLE_MAX_ENTRIES_PER_MARKET = 240`.
- Momentum decisions require at least `MOMENTUM_MIN_SAMPLE_POINTS = 2` samples in the window — low enough that a fresh market that just jumped from 0.10 to 0.70 in two ticks still qualifies for double-momentum entry.
- Core momentum logic is per `market_id`; event-level views compose sibling market IDs from the same Gamma event.

---

## Probability Model

- **Input**: `calibrated_forecast_max_c` (bias-adjusted by city) + city-specific MAE → sigma
- **Model**: Gaussian `N(mean=forecast, sigma=f(MAE))`
- **Calibration**: per-city bias correction from `data/research/calibration_latest.json`
- **Output**: `model_prob` = P(YES) for each temperature bucket
- **Edge gate (research)**: same P(YES) idea as `model_prob` but from `implied_yes` vs CLOB; compare **`edge`** to **`required_edge`** (includes **fee_drag** in the hurdle). High-implied path adds a **soft boost** to `edge` when `P_implied` is above `RESEARCH_EDGE_IMPLIED_SOFT_FLOOR`.
- **Module**: `strategy/probability_engine.py`, `research/calibration_apply.py`

---

## Sizing

```
tradable = max(0, cash - cash_reserve_usd)
planned = min(tradable × fraction, max_buy_notional_usd, tradable)
if planned < min_order_notional_usd → skip
```

| Variable | Default | Defined in |
|----------|---------|------------|
| `CASH_RESERVE_USD` | 3.0 | `config/constants.py` |
| `MAX_BUY_NOTIONAL_USD` | 3.0 | `config/constants.py` |
| `MIN_ORDER_NOTIONAL_USD` | 2.0 | `config/constants.py` |
| `MAX_TRADE_FRACTION_OF_CASH` | 0.90 | `config/constants.py` |

Current live values may differ via `data/runtime_config.json` (for example cash reserve and max buy size).

Optional: edge-scaled sizing when `RESEARCH_EDGE_SCALE_SIZE = true` (default off).

---

## Telegram Portfolio Message

On **bot startup**, Telegram sends the **STATUS** HTML (portfolio + per-row context), then **one follow-up**: the strategy digest as a **single plain-text message** if it fits under Telegram's 4096 limit; if longer, as **one `.txt` document`** with a short caption. The same digest text is appended to the **terminal echo** of that startup send.

Every trade (buy/sell/claim) and every scheduled report sends a rich portfolio message including:

1. **Portfolio summary**: cash, positions MTM, total value
2. **Per position**:
   - Title, shares, entry price, current mark, PnL ($, %)
   - 🌡 Live Open-Meteo forecast + calibrated temperature
   - 📊 Research edge (model P(YES), edge, required edge, fee drag)
   - ⚡ Momentum: **15-minute** and **rolling 2-hour** YES change from `price_samples` (display)
   - ⏰ **Time decay preview**: same gates as exit (`held`, `gain vs entry`, `mark`)
   - 📌 **YES Δ (since entry, ≤2h)**: first→last sample in `[max(entry, now−2h), now]`
   - 🏆 Competition — all sibling buckets with their probabilities

**Optional advisor (OpenRouter):** same Telegram chat; `/ask …` or messages containing **`bot`** / **`BOT`** spawn a **separate OS process**. See `notifications/openrouter_advisor.py`.

---

## Module Map

| Area | File |
|------|------|
| **Decision engine** | `strategy/decision_core.py` — entry/exit orchestrator, `stop_loss_bar_for_entry_type()` |
| **Probability model** | `strategy/probability_engine.py`, `research/probability_from_forecast.py` |
| **Momentum engine** | `strategy/momentum_engine.py` — absolute fast exit, competitor surge, entry signal |
| **Competition filter** | `strategy/competition_filter.py` |
| **Time filter** | `strategy/time_filter.py` |
| **Trade execution** | `strategy/trades.py` — stores `entry_type` at buy, uses per-type SL at exit |
| **Main loop** | `strategy/loop.py` |
| **Fast exit watcher** 🆕 | `strategy/fast_exit_watcher.py` — daemon thread, 2s CLOB polling |
| **Price samples (infra)** | `strategy/momentum.py` |
| **Anti-churn** | `strategy/churn.py` — per-market + per-event churn |
| **Portfolio sync** | `strategy/sync_portfolio.py` — preserves `entry_type` through sync |
| **Bot runner** | `strategy/bot_runner.py` — starts fast exit watcher at boot |
| **Portfolio Telegram** | `notifications/portfolio.py` |
| **Dashboard API** | `app/dashboard.py` |
| **Dashboard UI** | `ui/src/App.tsx` |
| **Constants** | `config/constants.py` — grouped by entry type |
| **Runtime settings** | `config/settings.py`, `data/runtime_config.json` |
| **Calibration data** | `data/research/calibration_latest.json` |

---

## All Runtime-Configurable Variables (via `runtime_config.json`)

### Per-Type Stop-Loss 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `stop_loss_normal` | 0.50 | hard-floor SL for normal entries |
| `stop_loss_momentum` | 0.45 | SL for momentum entries |
| `stop_loss_double_momentum` | 0.30 | SL for double momentum entries |
| `stop_loss_normal_entry_drop_pct` | 0.22 | entry-relative SL drop for normal |
| `stop_loss_momentum_entry_drop_pct` | 0.30 | entry-relative SL drop for momentum |
| `stop_loss_double_momentum_entry_drop_pct` | 0.45 | entry-relative SL drop for double momentum |

### Double Momentum Entry 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `double_momentum_entry_rise` | 0.30 | Min absolute price-point rise to qualify as double momentum |
| `double_momentum_pct_rise` | 0.80 | Min fractional rise alternative for double momentum |
| `double_momentum_min_start_price` | 0.05 | Ignore near-zero start prices for pct logic |
| `double_momentum_min_price` | 0.20 | Min YES price for double momentum entry |
| `double_momentum_max_price` | 0.88 | Max YES price for double momentum entry |

### Momentum window + exits (runtime) 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `momentum_window_seconds` | 900 | Rolling window for entry rise, peer surge, and fast-exit drawdown (seconds) |
| `momentum_entry_rise` | 0.15 | Min **absolute** YES rise inside the window to count as momentum entry |
| `momentum_pct_rise` | 0.35 | Min **fractional** YES rise alternative for momentum entry |
| `momentum_min_start_price` | 0.10 | Ignore near-zero start prices for pct logic |
| `momentum_fast_exit_drop` | 0.15 | Min **absolute** peak-to-trough drop inside the window for momentum fast exit |
| `momentum_competitor_surge` | 0.25 | Min **absolute** YES rise on **any sibling** in the event (within `momentum_window_seconds`) to fire exit **`competitor-surge`** — raise to require a bigger peer jump; lower to exit earlier on peer strength |

### Time decay (runtime) 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `time_decay_hours` | 2.0 | Hours held before decay logic applies |
| `time_decay_min_gain` | 0.02 | Min **absolute** YES gain vs entry required to avoid decay exit |
| `time_decay_max_price` | 0.85 | Decay exit only applies when mark is below this |

### Event Churn 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `churn_event_max_losses` | 2 | Losses on event before blocking |
| `churn_event_cooldown_sec` | 1800 | Block duration (30 min) |
| `churn_event_loss_1_cooldown_sec` | 900 | cooldown after first event loss |
| `churn_event_loss_2_cooldown_sec` | 3600 | stronger cooldown after second event loss |
| `leader_switch_window_sec` | 600 | time window for switch counting |
| `leader_switch_max_count` | 3 | switches allowed before event is unstable |
| `unstable_event_cooldown_sec` | 1800 | cooldown while event marked unstable |

### Fast Exit Watcher 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `fast_exit_watcher_interval_sec` | 2 | Polling interval (seconds) |

### Limit-Order Execution 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `limit_orders_enabled` | `true` | master toggle for limit-first buy/sell |
| `buy_limit_order_timeout_sec` | `8` | seconds to wait for a buy limit fill before cancel |
| `sell_limit_order_timeout_sec` | `5` | seconds to wait for a non-emergency sell limit fill |
| `emergency_exit_allow_market_order` | `true` | allow market fallback on stop-loss / fast-exit / switch |
| `buy_limit_price_offset` | `0.0` | added to best ask for buy limit price (use negative to be tighter) |
| `sell_limit_price_offset` | `0.005` | subtracted from best bid for non-emergency sells |
| `require_fill_before_state_buy` | `true` | only mark position open when the limit buy actually fills |

### Trailing Stop & SL Categories 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `trailing_stop_enabled` | `true` | enable trailing stop for all entry types |
| `trailing_stop_activation_gain` | `0.20` | unlock trailing stop when peak ≥ entry + this |
| `trailing_stop_lock_gain` | `0.10` | once unlocked, lock stop at entry + this |

### Fast Momentum Window 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `momentum_fast_window_seconds` | `300` | fast (5m) window for normal momentum check |
| `double_momentum_fast_window_seconds` | `300` | fast (5m) window for double momentum check |

### Telegram Failed-Exit Dedup 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `telegram_failed_exit_dedupe_enabled` | `true` | suppress repeated failed-exit notifications |
| `telegram_failed_exit_cooldown_sec` | `900` | seconds before the same failure may notify again |

### Rich Trade Logging & Telegram Verbosity 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `trade_log_full_reason_enabled` | `true` | write the full reason string to the trade ledger |
| `telegram_verbose_trade_reason` | `true` | render trigger window/metric/band lines in Telegram |
