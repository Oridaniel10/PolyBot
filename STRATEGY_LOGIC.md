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
   │  checks: per-type SL + optional window momentum exit │
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

### Momentum Entry (absolute OR percent rise, multi-window) ⚡

All values below are **live runtime values** from `data/runtime_config.json`. Units: [PRICE] = absolute YES points (0–1), [PCT] = fractional multiplier (1.0 = +100%).

| Variable | **Live Value** | Unit | Where |
|----------|---------------|------|-------|
| `momentum_entry_rise` | **0.20** | [PRICE] absolute rise | `data/runtime_config.json` |
| `momentum_pct_rise` | **1.0** (+100%) | [PCT] fractional rise | `data/runtime_config.json` |
| `momentum_min_start_price` | **0.0001** | [PRICE] min window-start YES for pct gate | `data/runtime_config.json` |
| `momentum_min_price` | **0.55** | [PRICE] min live YES at entry | `data/runtime_config.json` |
| `momentum_max_entry` | **0.85** | [PRICE] max live YES at entry | `data/runtime_config.json` |
| `momentum_entry_max_window_seconds` | **3600** (60 min cap) | [SECONDS] | `data/runtime_config.json` |
| `momentum_window_seconds` | **3600** | [SECONDS] | `data/runtime_config.json` |
| `stop_loss_momentum` | **0.35** | [PRICE] hard floor | `data/runtime_config.json` |
| `stop_loss_momentum_entry_drop_pct` | **0.50** (50% from entry) | [FRAC] relative drop | `data/runtime_config.json` |

Entry when YES rose by **absolute** rise OR **fractional percent** rise **and** leader-yield passes on the **same** lookback length `W`, where `W` runs over the entry grid **1m, 2m, …, 15m** (60s steps up to `momentum_entry_max_window_seconds`, default **900s**) via `momentum_entry_candidate_windows()` + `momentum_leader_same_window_check()` in `decision_core.py`. The bot picks the **smallest** such `W` where **both** legs pass. All windows share the same guardrails: `momentum_min_start_price`, `momentum_min_price`, `momentum_max_entry`.

**Mandatory extra gate — leader yield (same `W`):** same **Gamma event**. Target must be **rising** on this `W`. Then **either**:

- **(A)** Any sibling (excluding the target) with **old YES** above `leader_yield_min_leader_old_price` **fell** by **`leader_yield_fall_min_abs_pts`** OR by **`leader_yield_fall_min_frac`** of **its own** old YES; **or**
- **(B)** The qualifying siblings **collectively** dropped by at least **`collective_fall_min_abs_pts`** in summed points **or** by **`collective_fall_min_frac`** of their **summed** old YES (a **weighted** drop fraction: total drop points ÷ sum of old YES).

The target may itself have been the highest-priced bucket at window start — what matters is that the bot can see probability flowing **out** of other buckets while the target rises. Implemented in `strategy/leader_yield_momentum.py::leader_yield_drop_qualifies` (called from `momentum_leader_same_window_check`). Events with **one** YES bucket cannot satisfy this → momentum-class entries are skipped with `leader_yield_blocked…`.

So a setup that only lines up on a **15m** horizon waits until `W=900` qualifies both rise and sibling bleed (conditions **A** or **B**); a setup that lines up in **1m** can enter on `W=60` when both pass there first.

Bypasses model/edge/competition gates when the full momentum path (rise + leader yield + band) passes. **No separate “anti‑FOMO” price gate** — the only YES band limits for momentum buys are `momentum_min_price` / `momentum_max_entry` (and the double band when applicable), from constants + `runtime_config.json`.

Rank and runner-up gap are logged for context but are **not** substitutes for leader yield.

### Double Momentum Entry (absolute OR percent rise, wider band) 🚀

| Variable | **Live Value** | Unit | Where |
|----------|---------------|------|-------|
| `double_momentum_entry_rise` | **0.40** | [PRICE] absolute rise | `data/runtime_config.json` |
| `double_momentum_pct_rise` | **9.0** (+900%) | [PCT] fractional rise | `data/runtime_config.json` |
| `double_momentum_min_start_price` | **0.0001** | [PRICE] min window-start for pct gate | `data/runtime_config.json` |
| `double_momentum_min_price` | **0.10** | [PRICE] min live YES at entry | `data/runtime_config.json` |
| `double_momentum_max_price` | **0.91** | [PRICE] max live YES at entry | `data/runtime_config.json` |
| `stop_loss_double_momentum` | **0.05** | [PRICE] hard floor | `data/runtime_config.json` |
| `stop_loss_double_momentum_entry_drop_pct` | **0.50** | [FRAC] relative drop from entry | `data/runtime_config.json` |

Same **aligned-window** rule as standard momentum: smallest grid `W` where double rise **and** leader-yield both pass, with thresholds/band from `double_momentum_*`.

Entry when YES rose at least the configured absolute OR fractional percent threshold **on that same `W`** **and** the live YES is inside `[double_momentum_min_price, double_momentum_max_price]`.

**Precedence:** try **double** aligned pass first → `entry_type = double_momentum`. Else if **standard** aligned pass → `entry_type = momentum`.

### How entry_type is determined

At buy time, `evaluate_entry()` sets `TradeDecision.entry_type` using **`momentum_leader_same_window_check`** (double path first, then standard): smallest `W` in the 1m…15m grid (capped by `momentum_entry_max_window_seconds`) where **both** target rise and leader-yield pass.

- `"double_momentum"` → double thresholds + leader-yield on the **same** winning `W`, and current YES in the double band.
- `"momentum"` → standard thresholds + leader-yield on the **same** winning `W`, and current YES in the momentum band — **only if** double aligned path did not fire first.
- `"normal_winner"` → stability-based: YES ≥ 0.945 and price stably above 0.75 for ≥ 30 min. Bypasses rise/leader-yield/competition/model gates.
- `"normal"` → passes the normal price band + competition (+ optional model gates). No leader-yield requirement.

If **rise-alone** would pass on some window (`momentum_multi_window_check`) but **no** `W` has both rise + leader-yield (or the event has &lt;2 buckets), the bot returns **`SKIP`** `leader_yield_blocked…`.

The `TradeDecision` carries `trigger_window` (e.g. `7m_win`), `trigger_*` for the **bought** bucket on that `W`, plus **`leader_fallen_*`** from the **same** `W`; `leader_yield_window_sec` equals `W`.

### Leader-yield parameters (runtime + constants) — INDEPENDENT WINDOWS 🆕

> **Key change**: rise and fall windows are now **independent**. The bot finds the smallest window where our target rises AND separately finds the smallest window where any sibling falls. These can be different windows — e.g. a sibling that fell 3 minutes ago qualifies even if our rise happened over the last 10 minutes.

| Key / constant | **Live Value** | Unit | Meaning |
|----------------|---------------|------|---------|
| `momentum_entry_max_window_seconds` | **900** | [SECONDS] | Max window on the 1m…15m grid for rise check |
| `leader_yield_min_leader_old_price` | **0.03** | [PRICE] | Ignore siblings whose window-start YES ≤ this (noise filter) |
| `leader_yield_fall_min_abs_pts` | **0.40** | [PRICE] absolute drop | Cond **(A)**: sibling must drop ≥ this many YES **points** |
| `leader_yield_fall_min_frac` | **0.41** | [FRAC] fraction of sibling’s own old YES | Cond **(A)** alternative: sibling dropped ≥ this fraction of its own start price |
| `collective_fall_min_abs_pts` | **0.40** | [PRICE] sum of drops | Cond **(B)**: total drop across all qualifying siblings ≥ this many points |
| `collective_fall_min_frac` | **0.41** | [FRAC] weighted collective | Cond **(B)** alternative: total drop ÷ sum(siblings’ old YES) ≥ this |

### Normal Winner Entry (End-of-Day High-Conviction) 🏆

A **stability-based entry** for markets that are near-resolved: YES has been **stably above a floor** for at least 30 minutes (up to 2h) and is now trading in the **≥ 0.945** zone. The goal is to collect small end-of-day gains when a market is virtually certain to resolve YES.

> **Does NOT require a momentum rise** — purely based on price stability and current level.

| Variable | Default | Where |
|----------|---------|-------|
| `normal_winner_enabled` | **true** | `config/constants.py` / `data/runtime_config.json` |
| `normal_winner_min_entry` | **0.945** | minimum YES to buy |
| `normal_winner_max_entry` | **0.97** | maximum YES to buy (avoid buying at market-resolve) |
| `normal_winner_take_profit` | **0.9987** | exit (take-profit) when YES reaches this |
| `normal_winner_stability_floor` | **0.75** | price must have been above this throughout the window |
| `normal_winner_stability_min_sec` | **1800** (30 min) | minimum contiguous run above floor |
| `normal_winner_stability_max_sec` | **7200** (2h) | lookback window for checking stability |
| `stop_loss_normal_winner` | **0.88** | hard stop floor |
| `stop_loss_normal_winner_entry_drop_pct` | **0.05** (5%) | also exit if drops >5% from entry |

**Entry conditions (all must pass):**
1. `normal_winner_min_entry ≤ current_yes ≤ normal_winner_max_entry`
2. The **contiguous tail** of price samples (walking backward from now) stays above `normal_winner_stability_floor` for ≥ `normal_winner_stability_min_sec` seconds with no gap > 2 minutes

**Exit:**
- **Take-profit** when YES ≥ `normal_winner_take_profit` (0.9997) — market resolving YES
- **Stop-loss** effective floor = max(`stop_loss_normal_winner=0.88`, `entry * (1 - 0.05)`)

**Bypasses:** model/edge gates, competition filter, negative momentum gate (all irrelevant at this price level).

**Interaction with other strategies:** evaluated only if no momentum/double-momentum/persistent-leader signal fired first. Normal BUY is unaffected.

### Persistent Leader Entry (2h Dominance) 🏆

A **dedicated entry path** for markets that have held **#1 rank** among siblings for a large fraction of the last 2 hours. Because the setup already provides strong conviction, only a **small momentum nudge** is required — no sibling fall (leader-yield) needed.

| Variable | Default | Where |
|----------|---------|-------|
| `persistent_leader_enabled` | **true** | `data/runtime_config.json` |
| `persistent_leader_lookback_sec` | **7200** (2h) | `data/runtime_config.json` |
| `persistent_leader_min_fraction` | **0.80** | must be #1 in ≥ 80% of samples in the lookback |
| `persistent_leader_entry_rise` | **0.10** | min absolute rise (+0.10 pts) to trigger buy |
| `persistent_leader_pct_rise` | **0.25** | OR min fractional rise (+25%) to trigger buy |
| `persistent_leader_min_price` | **0.55** | buy band floor (only buy if YES ≥ 0.55) |
| `persistent_leader_max_price` | **0.85** | buy band ceiling |

**Flow (Section 6 in `evaluate_entry`):**
1. Standard and double-momentum paths run first; if either fires, this path is skipped.
2. `is_persistent_leader(market_id, siblings, lookback_sec, min_fraction)` checks the in-memory ring buffer.
3. If the market qualifies, `momentum_multi_window_check` runs with the **lower PL thresholds** and the `[pl_min_price, pl_max_price]` band.
4. If the rise check passes → `entry_type = momentum`, `decision_reason = persistent_leader_2h`. No sibling fall required.

This targets scenarios like: YES has been at 0.60 for 2 hours, competitors are flat, and then it nudges to 0.72 — the standard path would require a sibling to fall first, but the historical dominance makes it a low-risk buy.

### Legacy: `pair_reversal.py`

The older **pair-reversal** path (`strategy/pair_reversal.py`) is **no longer wired** into `evaluate_entry()`. Momentum-class entries use **leader yield** on the **same** window `W` as the rise (`momentum_leader_same_window_check`).

### Buy notional escalation (submit failures only)

If `buy_escalate_notional_on_submit_fail` is **true**, `place_buy` retries with **+$`buy_escalate_notional_step_usd`** up to **`max_buy_notional_usd`** when `execute_buy` returns **`limit_failed`** (e.g. CLOB rejects the post) or **`limit_unsupported`** without an order id. **Unfilled-after-timeout (`limit_cancelled`) does not escalate** — that is “price walked away”, not minimum-size rejection.

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
| 13 | **Momentum entry** ⚡ | Smallest grid window **W** (≤ `momentum_entry_max_window_seconds`) where **both** standard or double rise **and** leader-yield pass on **that same W** | `momentum_*`, `double_momentum_*`, `leader_yield_*` | `decision_core.py::momentum_leader_same_window_check` |
| 13a | **Leader yield block** | If a raw momentum/double rise shows but leader yield fails (or single-bucket event) → **SKIP** `leader_yield_blocked…` | same | `decision_core.py::evaluate_entry` |
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

1. **Fast exit watcher** (daemon thread, every **2 seconds**) — checks per-type SL + optional hour-window momentum fast exit
2. **Main loop** (every **30 seconds**) — checks all exit conditions

### Exit Priority Table

| # | Condition | Rule | Speed | Variable | Defined in |
|---|-----------|------|-------|----------|------------|
| 1 | **Market resolved** | status = closed/claimable/resolved → CLAIM | 30s | `STATUS_CLOSED` | `config/constants.py` |
| 2 | **Momentum fast exit** 🚨 *(optional)* | When **`momentum_fast_exit_enabled`** is `true`: ABSOLUTE peak-to-trough ≥ `momentum_fast_exit_drop` inside `momentum_window_seconds`; slice starts at **`entry_time_utc`**. Requires mark **< entry** → reason **`momentum-stop-loss`**. When **`false`**, this row is skipped — only per-type SL + trailing + crash + TP (and other rows) apply. | **2s** (watcher) + 30s (main) | `momentum_fast_exit_enabled`, `momentum_fast_exit_drop`, `momentum_window_seconds` | `strategy/momentum_engine.py`, `strategy/decision_core.py::check_exits`, `strategy/fast_exit_watcher.py` |
| 2b | **Crash from peak** 🧨 | loss from `highest_seen_price` ≥ `crash_drop_pct_from_peak × peak` **and** mark below entry → **`stop-loss`**, `sl_category=SL_CRASH_PEAK`. Set `crash_drop_pct_from_peak` to **0** to disable. | **2s** + 30s | `crash_drop_pct_from_peak` | `strategy/decision_core.py`, `strategy/fast_exit_watcher.py` |
| 3 | **Dominant competitor** | sibling #1 by YES has momentum + gap ≥ held + 0.15 → EXIT | 30s | `MOMENTUM_SWITCH_ABOVE_HELD_GAP` | `strategy/decision_core.py` |
| 4 | **Competitor surge** 🔥 | any sibling rose ≥ `momentum_competitor_surge` **absolute** points in `momentum_window_seconds` | 30s | `MOMENTUM_COMPETITOR_SURGE` (default **0.25**) | `config/constants.py` + `data/runtime_config.json` → `strategy/decision_core.py::check_exits` → `peer_surge_detected` |
| 5 | **Time-decay** ⏰ | held >2h AND **(mark − entry) < min_gain_points** AND mark < max_price | 30s | `time_decay_hours`, `time_decay_min_gain`, `time_decay_max_price` (defaults in `TIME_DECAY_*`) | `strategy/time_filter.py`, `data/runtime_config.json` |
| 6 | **Research model flip** | forecast contradict (optional, default off) | 30s | `RESEARCH_EXIT_ON_MODEL_FLIP` | `config/constants.py` |
| 7 | **Per-type stop-loss** 🆕 | mark < effective stop (hard floor + entry-relative + trailing) | **2s** (watcher) + 30s (main) | see per-type constants / `runtime_config.json` (defaults align with `config/constants.py`) | `strategy/decision_core.py` |
| 8 | **Take-profit** | mark ≥ 0.94 | 30s | `TAKE_PROFIT_THRESHOLD = 0.94` | `config/constants.py` |
| 9 | **2h stagnation** 🆕 | mark < entry AND max rise in 2h < 5% → exit dead position | **2s** (watcher) | `stagnation_sl_enabled`, `stagnation_sl_min_rise_pct` | `strategy/fast_exit_watcher.py` |

### Momentum fast exit — hour-window peak→trough *(optional)*

**Toggle:** `momentum_fast_exit_enabled` in `data/runtime_config.json` (default **`true`** in `config/constants.py` as `MOMENTUM_FAST_EXIT_ENABLED`). Set to **`false`** to **disable** exits with reason **`momentum-stop-loss`** (no more “look back one hour for a deep dip vs peak” exit). **Per-type** stop-loss (`stop_loss_momentum`, `stop_loss_double_momentum`, `stop_loss_normal`, `stop_loss_normal_winner`, …), **entry-relative %**, and **trailing stop** are unchanged — they still use `effective_stop_price_for_trade` / `classify_stop_loss_breach`.

Momentum entry signals and **competitor surge** still use **absolute price points** in windows (separate from this toggle for surge — only the **`momentum-stop-loss`** path is gated).

When enabled, fast exit uses:
- Entry: 0.40 → 0.46 is **not** +0.15 momentum; 0.40 → 0.55 is.
- Surge: peer 0.30 → 0.45 qualifies; 0.30 → 0.345 does not.
- Fast exit: peak-to-trough ≥ **`momentum_fast_exit_drop`** in points (runtime `momentum_fast_exit_drop`).

#### Where to change competitor surge (not the same as price stop-loss)

This exit is **`competitor-surge`**: a **sibling bucket** in the same event gained at least **`momentum_competitor_surge`** YES points inside **`momentum_window_seconds`**. It is **not** `stop_loss_normal` / trailing — those compare **our** market’s mark to floors.

| What to edit | Key / constant |
|--------------|----------------|
| Static default | `MOMENTUM_COMPETITOR_SURGE` in `config/constants.py` |
| Live override (preferred) | `momentum_competitor_surge` in `data/runtime_config.json` |
| Code path | `momentum_competitor_surge_thr(settings)` in `strategy/decision_core.py` → passed to `peer_surge_detected(..., surge_threshold=...)` from `check_exits` |

**Tuning direction:** the value is a **minimum rise** on the peer. **Higher number** (e.g. 0.25) → peer must jump **more** before we exit → **fewer** surge exits. **Lower number** (e.g. 0.08) → exit **sooner** when any sibling climbs a little.

Example: entered at 0.60, price spiked to 0.95, then dropped to 0.62 with default fast-exit drop 0.30.
- Peak = 0.95, current = 0.62, drop = 0.33 ≥ 0.30 → **exit**
- Also mark **< entry** where required → **momentum fast exit** / related path

### Per-Type Stop-Loss 🆕

The stop-loss bar depends on how the position was entered (defaults from `config/constants.py`, overridden via `runtime_config.json`):

| Entry Type | SL Bar (constants default) | Example |
|------------|---------------------------|---------|
| `normal` | **0.20** | Hard floor vs `STOP_LOSS_NORMAL` |
| `momentum` | **0.35** | Vs `STOP_LOSS_MOMENTUM` (+ entry-relative + trailing) |
| `double_momentum` | **0.05** | Vs `STOP_LOSS_DOUBLE_MOMENTUM` |
| `manual` / `ui` | **0.20** | `STOP_LOSS_MANUAL` |

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
| `SL_MOMENTUM` | only when **`momentum_fast_exit_enabled`**: hour-window fast exit hit `momentum_fast_exit_drop` + mark &lt; entry (`momentum-stop-loss`) |

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

## Price Samples — Redis Backend 🆕

Price samples are stored in **Redis sorted sets** (primary) + `data/price_samples/YYYY-MM-DD.jsonl` (archival/fallback).

- **Redis key**: `prices:{market_id}` — sorted set, score = Unix timestamp, member = `"{ts:.6f}:{price:.6f}"`
- **TTL**: entries older than **2 hours 5 minutes** are removed automatically by `cleanup_all_markets()` (runs every 2 minutes from the main loop)
- **Warm-up on restart**: `warm_ring_buffer_from_disk()` bulk-loads today's JSONL into Redis so momentum calculations work immediately
- **Window queries**: `ZRANGEBYSCORE prices:{mid} {cutoff} +inf` — sub-millisecond; includes one anchor point before the window for accurate rise calculations
- The bot records one sample per scanned/held market every 30 seconds across all BUCKETS/SIBLINGS in every active gamma event
- Momentum decisions require at least `MOMENTUM_MIN_SAMPLE_POINTS = 3` samples in the window (filters single-tick spikes — signal must appear across ≥ 3 windows, ~30s apart)

### 2-Hour Stagnation Stop-Loss 🆕

Because Redis now keeps **2 hours** of price history, the bot can detect **dead positions**:

| Key | **Live Value** | Unit | Meaning |
|-----|---------------|------|---------|
| `stagnation_sl_enabled` | **true** | bool | Master toggle |
| `stagnation_sl_window_hours` | **2.0** | [HOURS] | Lookback window |
| `stagnation_sl_min_rise_pct` | **0.05** | [FRAC] | Minimum fractional price rise in window to KEEP position |

**Logic**: checked every 2 seconds in the fast exit watcher.
- If `current_price < entry_price` AND the maximum price seen in the last 2 hours never exceeded `price_2h_ago × 1.05` (i.e. rose less than 5%) → exit with reason `stop-loss` / `sl_category = SL_STAGNATION`.
- Prevents holding a position that has been stuck or slowly bleeding for over 2 hours with no recovery attempt.
- **To make it LESS aggressive**: raise `stagnation_sl_min_rise_pct` (e.g. 0.10 = require 10% rise).
- **To disable**: set `stagnation_sl_enabled = false` in `data/runtime_config.json`.

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

| Variable | **Live Value** | Defined in |
|----------|--------------|------------|
| `cash_reserve_usd` | **0.0** | `data/runtime_config.json` |
| `max_buy_notional_usd` | **5.0** (hard cap in `config/constants.py`) | `data/runtime_config.json` |
| `min_order_notional_usd` | **1.0** | `data/runtime_config.json` |
| `max_trade_fraction_of_cash` | **0.90** | `data/runtime_config.json` |

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
| **Leader-yield momentum gate** | `strategy/leader_yield_momentum.py` + `decision_core.py::momentum_leader_same_window_check` — target rise + sibling bleed (**A** or **B**) on the **same** `W` |
| **Competition filter** | `strategy/competition_filter.py` |
| **Time filter** | `strategy/time_filter.py` |
| **Trade execution** | `strategy/trades.py` — stores `entry_type` at buy, uses per-type SL at exit |
| **Main loop** | `strategy/loop.py` |
| **Fast exit watcher** 🆕 | `strategy/fast_exit_watcher.py` — daemon thread, 2s CLOB polling |
| **Price samples (infra)** | `strategy/momentum.py` |
| **Anti-churn** | `strategy/churn.py` — per-market + per-event churn |
| **Portfolio sync** | `strategy/sync_portfolio.py` — merges Data API; late-fill reclaim only when `avg_price` ≈ bot `intended_price` (see “Late-fill reclaim” below) |
| **Bot runner** | `strategy/bot_runner.py` — starts fast exit watcher at boot |
| **Portfolio Telegram** | `notifications/portfolio.py` |
| **Dashboard API** | `app/dashboard.py` |
| **Dashboard UI** | `ui/src/App.tsx` |
| **Constants** | `config/constants.py` — grouped by entry type |
| **Runtime settings** | `config/settings.py`, `data/runtime_config.json` |
| **Calibration data** | `data/research/calibration_latest.json` |

### Late-fill reclaim vs manual site buy

After **`BUY UNFILLED` / `limit_cancelled`**, `place_buy` removes `recent_buy_attempts` for that market (the limit executor already re-polls after cancel — no hidden fill). The next scan can retry without `recent_buy_attempt_pending`, and a **manual** UI buy cannot inherit the bot’s old `entry_type` (e.g. `double_momentum`) or **momentum-stop-loss** rules.

If reclaim is considered, it runs **only** when Data-API `avg_price` is within **`LATE_FILL_RECLAIM_MAX_AVG_VS_INTENDED`** in `config/constants.py` (plus 5% of `intended_price`) of the stored `intended_price`. A manual buy near **0.88** after a bot attempt at **0.69** does **not** reclaim — sync opens **`manual_sync_open`** / `entry_type=manual`, logs **`MANUAL BUY DETECTED`** to CSV, and uses **manual** SL.

**Retry after unfilled:** each new scan runs `evaluate_entry` again; if it still returns **BUY**, `place_buy` runs again. Notional stays capped by **`max_buy_notional_usd`** / **`MAX_BUY_NOTIONAL_USD`**. If the CLOB still shows an **OPEN** GTC after timeout, `limit_executor` runs extra cancel+poll rounds; any stubborn order id is stored in **`state["orphan_limit_buy_orders"]`** and the **next** `place_buy` for that market calls cancel again **before** posting a new limit, so two live bids should not stack.

---

## All Runtime-Configurable Variables (via `runtime_config.json`)

### Per-Type Stop-Loss 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `stop_loss_normal` | 0.65 | hard-floor SL for normal entries |
| `stop_loss_momentum` | 0.20 | SL for momentum entries |
| `stop_loss_double_momentum` | 0.20 | SL for double momentum entries |
| `stop_loss_manual` | 0.40 | hard-floor SL for manual/UI entries |
| `stop_loss_normal_entry_drop_pct` | 0.30 | entry-relative SL drop for normal |
| `stop_loss_momentum_entry_drop_pct` | 0.50 | entry-relative SL drop for momentum |
| `stop_loss_double_momentum_entry_drop_pct` | 0.50 | entry-relative SL drop for double momentum |
| `stop_loss_manual_entry_drop_pct` | 0.30 | entry-relative SL drop for manual/UI trades |

### Double Momentum Entry 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `double_momentum_entry_rise` | **0.40** | Min absolute price-point rise to qualify as double momentum |
| `double_momentum_pct_rise` | **9.0** | Min **fractional** rise alternative (+900%) |
| `double_momentum_min_start_price` | **0.0001** | Floor on *old* price in-window for pct leg |
| `double_momentum_min_price` | **0.10** | Min YES price for double momentum entry |
| `double_momentum_max_price` | **0.91** | Max YES price for double momentum entry |

### Momentum window + exits (runtime) 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `momentum_window_seconds` | 900 | Rolling window for peer surge, fast-exit drawdown, and other exit logic (seconds) |
| `momentum_entry_max_window_seconds` | 900 | Max aligned window **W** (seconds); grid is 60…900 in steps of 60 |
| `momentum_entry_rise` | **0.20** | Min **absolute** YES rise for standard momentum entry (any qualifying window) |
| `momentum_pct_rise` | **1.0** | Min **fractional** YES rise alternative (+100%) |
| `momentum_min_start_price` | 0.0 | Floor on *old* price for pct gate (0 = allow e.g. 0.05 → 0.15 on pct) |
| `momentum_min_price` / `momentum_max_entry` | **0.55 / 0.85** | Live-price band at decision time |
| `momentum_fast_exit_enabled` | **`true`** | **`false`** = never fire **`momentum-stop-loss`** (hour-window peak→trough); keep per-type SL + trailing only |
| `momentum_fast_exit_drop` | **0.35** | Min **absolute** peak-to-trough drop inside the window (only if `momentum_fast_exit_enabled`) |
| `crash_drop_pct_from_peak` | 0.50 | Fractional drop from `highest_seen_price` triggering crash exit (0 = off) |
| `buy_escalate_notional_on_submit_fail` | `true` | Ladder buys after submit-side limit failure |
| `buy_escalate_notional_step_usd` | 1.0 | USD increment per escalation step |
| `momentum_competitor_surge` | **0.35** | Min **absolute** YES rise on **any sibling** in the event (within `momentum_window_seconds`) to fire exit **`competitor-surge`** — raise to require a bigger peer jump; lower to exit earlier on peer strength |

### Leader-yield (runtime) 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `leader_yield_min_leader_old_price` | **0.03** | Ignore peers at/below this window-start YES when evaluating sibling drops |
| `leader_yield_fall_min_abs_pts` | **0.40** | Condition **(A)**: min absolute YES **points** dropped by **some** qualifying sibling (or frac leg vs its old YES) |
| `leader_yield_fall_min_frac` | **0.41** | Condition **(A)**: **or** fractional drop vs **that sibling’s** old YES |
| `collective_fall_min_abs_pts` | **0.40** | Condition **(B)**: min **sum** of sibling drops in YES **points** (qualifying siblings only) |
| `collective_fall_min_frac` | **0.41** | Condition **(B)**: **or** total drop points ÷ sum of siblings’ old YES (weighted fraction) |

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
| `trailing_stop_activation_gain` | **0.20** | unlock trailing stop when peak ≥ entry + this |
| `trailing_stop_lock_gain` | **0.03** | once unlocked, lock stop at entry + this |

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

### Persistent Leader Entry 🆕
| Key | Default | Description |
|-----|---------|-------------|
| `persistent_leader_enabled` | `true` | master toggle for 2h-dominance entry path |
| `persistent_leader_lookback_sec` | `7200` | lookback window in seconds |
| `persistent_leader_min_fraction` | `0.80` | min fraction of samples where market was #1 |
| `persistent_leader_entry_rise` | `0.10` | min absolute YES rise to trigger PL entry |
| `persistent_leader_pct_rise` | `0.25` | min fractional YES rise (OR with absolute) |
| `persistent_leader_min_price` | `0.55` | buy band floor for PL path |
| `persistent_leader_max_price` | `0.85` | buy band ceiling for PL path |

### Order Size Cap
| Key | Default | Description |
|-----|---------|-------------|
| `max_buy_notional_usd` | `5.0` | per-order USD cap; **hard ceiling** enforced by `config/constants.py::MAX_BUY_NOTIONAL_USD` — runtime cannot exceed this |
| `min_order_notional_usd` | `1.0` | skip buy if computed size is below this |
