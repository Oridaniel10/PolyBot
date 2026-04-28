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
| `BUY_MAX_THRESHOLD` | **0.88** | `config/constants.py` (runtime) |
| `STOP_LOSS_NORMAL` | **0.55** | `config/constants.py` |
| `MAX_MARKET_PROB_FOR_BUY` | **0.99** | `config/constants.py` |
| `MIN_MODEL_PROB_FOR_BUY` | **0.0** (runtime) | `data/runtime_config.json` |
| `RESEARCH_EDGE_GATE_BUY` | **false** (runtime) | `data/runtime_config.json` |

Entry when price is in the band (0.65–0.88) and the candidate passes the competition filter (15% lead vs runner-up). With research and model-prob gates disabled by default, stable high-prob markets in 0.78–0.88 with no competing siblings flow through naturally. If market mark drops below **0.55** → exit.

### Momentum Entry (+0.15 price points in 15 min) ⚡

| Variable | Default | Where |
|----------|---------|-------|
| `MOMENTUM_ENTRY_RISE` | **0.15** | `config/constants.py` |
| `MOMENTUM_MIN_PRICE` | **0.61** | `config/constants.py` |
| `MOMENTUM_MAX_ENTRY` | **0.80** | `config/constants.py` |
| `STOP_LOSS_MOMENTUM` | **0.45** | `config/constants.py` |

Entry when YES rose at least **0.15 absolute price points** in 15 minutes and current price is in 0.61–0.80. Rank and runner-up gap are logged for context but are not required for momentum entry. Bypasses model/edge gates. If mark drops below **0.45** → exit.

### Double Momentum Entry (+0.30 price points in 15 min) 🚀

| Variable | Default | Where |
|----------|---------|-------|
| `DOUBLE_MOMENTUM_ENTRY_RISE` | **0.30** | `config/constants.py` |
| `DOUBLE_MOMENTUM_MIN_PRICE` | **0.20** | `config/constants.py` |
| `DOUBLE_MOMENTUM_MAX_PRICE` | **0.88** | `config/constants.py` |
| `STOP_LOSS_DOUBLE_MOMENTUM` | **0.30** | `config/constants.py` |

Entry when YES rose at least **0.30 absolute price points** in 15 minutes. Wider band than standard momentum: 0.20–0.88 — covers a market that jumped from 0.10 to 0.85. Lower stop-loss at 0.30 to accommodate the wider entry range.

### How entry_type is determined

At buy time, `evaluate_entry()` in `decision_core.py` sets `TradeDecision.entry_type`:
- `"double_momentum"` → rise ≥0.30 points, price in 0.20–0.88
- `"momentum"` → rise ≥0.15 points, price in 0.61–0.80
- `"normal"` → everything else (price band + competition)

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
| 13 | **Momentum entry** ⚡ | Standard (+0.15 points) or Double (+0.30 points) — see entry type tables above | `MOMENTUM_ENTRY_RISE`, `DOUBLE_MOMENTUM_ENTRY_RISE` | `strategy/decision_core.py` |
| 13b | **Momentum switch** 🔁 | hold A; B is #1 with momentum + gap → sell A, buy B | `MOMENTUM_SWITCH_ABOVE_HELD_GAP = 0.15` | `strategy/decision_core.py` |
| 14 | **Competition** | gap vs runner-up ≥ `min_lead` (skipped for momentum entry) | `MIN_LEAD_OVER_RUNNER_UP = 0.15` | `strategy/competition_filter.py` |
| 15 | **No negative momentum** | 15-min change > -0.10 points (skipped for momentum) | — | `strategy/decision_core.py` |
| 16 | **Edge gate** | `edge ≥ required_edge` (skipped for momentum) — **default OFF in runtime** (`research_edge_gate_buy=false`) | `RESEARCH_MIN_EDGE` | `strategy/research_signal.py` |
| 17 | **Forecast gate** | bracket contradict check | `FORECAST_CONTRADICT_MARGIN_C = 2.5` | `strategy/trades.py` |
| 18 | **CLOB price verify** | normal or momentum band at execution time | — | `strategy/trades.py` |
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
| 4 | **Competitor surge** 🔥 | any sibling rose ≥0.15 points in 15 min | 30s | `MOMENTUM_COMPETITOR_SURGE = 0.15` | `config/constants.py` |
| 5 | **Time-decay** ⏰ | held >2h AND **(mark − entry) < min_gain_points** AND mark < max_price | 30s | `time_decay_hours`, `time_decay_min_gain`, `time_decay_max_price` (defaults in `TIME_DECAY_*`) | `strategy/time_filter.py`, `data/runtime_config.json` |
| 6 | **Research model flip** | forecast contradict (optional, default off) | 30s | `RESEARCH_EXIT_ON_MODEL_FLIP` | `config/constants.py` |
| 7 | **Per-type stop-loss** 🆕 | mark < SL bar (depends on entry_type) | **2s** (watcher) + 30s (main) | `STOP_LOSS_NORMAL=0.55`, `STOP_LOSS_MOMENTUM=0.45`, `STOP_LOSS_DOUBLE_MOMENTUM=0.30` | `config/constants.py`, `strategy/decision_core.py` |
| 8 | **Take-profit** | mark ≥ 0.97 | 30s | `TAKE_PROFIT_THRESHOLD = 0.97` | `config/constants.py` |

### Momentum Fast Exit — Absolute Drop 🆕

Momentum entry, competitor surge, and fast exit use **absolute price points**, not percentage:
- Entry: 0.40 → 0.46 is **not** +0.15 momentum; 0.40 → 0.55 is.
- Surge: peer 0.30 → 0.45 qualifies; 0.30 → 0.345 does not.
- Fast exit: peak-to-trough ≥ **0.15 absolute** exits (e.g., 0.75 → 0.59 = 0.16 > 0.15).

Example: entered at 0.60, price spiked to 0.75, then dropped to 0.59.
- Peak = 0.75, current = 0.59, drop = 0.16 > 0.15 → **exit**
- Also: 0.59 < 0.60 entry → mark < entry check passes → **momentum-stop-loss** triggered

### Per-Type Stop-Loss 🆕

The stop-loss bar depends on how the position was entered:

| Entry Type | SL Bar | Example |
|------------|--------|---------|
| `normal` | **0.55** | Entered at 0.80, exits if mark < 0.55 |
| `momentum` | **0.45** | Entered at 0.70, exits if mark < 0.45 |
| `double_momentum` | **0.30** | Entered at 0.50, exits if mark < 0.30 |

The `entry_type` is stored in `state.json` at buy time and used by both the main loop and the fast exit watcher.

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
| `stop_loss_normal` | 0.55 | SL for normal entries |
| `stop_loss_momentum` | 0.45 | SL for momentum entries |
| `stop_loss_double_momentum` | 0.30 | SL for double momentum entries |

### Double Momentum Entry 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `double_momentum_entry_rise` | 0.30 | Min absolute price-point rise to qualify as double momentum |
| `double_momentum_min_price` | 0.20 | Min YES price for double momentum entry |
| `double_momentum_max_price` | 0.88 | Max YES price for double momentum entry |

### Momentum window + exits (runtime) 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `momentum_window_seconds` | 900 | Rolling window for entry rise, peer surge, and fast-exit drawdown (seconds) |
| `momentum_entry_rise` | 0.15 | Min **absolute** YES rise inside the window to count as momentum entry |
| `momentum_fast_exit_drop` | 0.15 | Min **absolute** peak-to-trough drop inside the window for momentum fast exit |
| `momentum_competitor_surge` | 0.15 | Min **absolute** YES rise for sibling surge exit |

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

### Fast Exit Watcher 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `fast_exit_watcher_interval_sec` | 2 | Polling interval (seconds) |
