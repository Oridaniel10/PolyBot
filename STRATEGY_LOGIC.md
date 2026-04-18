# Smart Trading Engine — Strategy Logic

Full entry/exit strategy for the Polymarket weather bot. All thresholds are in `config/constants.py` with live overrides via `data/runtime_config.json` (merged each tick in `config/settings.py`).

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
```

**Decision engine**: `strategy/decision_core.py` — single source of truth for entry/exit decisions.

---

## General Rules

1. We invest **only** in maximum temperature markets — cities and locations.
2. Event date must be **today or yesterday** (Israel time) — never future dates.
3. **City local calendar day** must equal the market **event_date** (the day the max is for), and local clock **14:00–23:59** — no buys on the prior calendar day even if the hour is past 14:00.
4. Market price must be within price band: `BUY_MIN_THRESHOLD` ≤ price ≤ `BUY_MAX_THRESHOLD`.
5. Max **1 position per event** (gamma event).
6. Max **7 total open positions**.
7. After a stop-loss exit → market is **blacklisted for 20 minutes** (churn cooldown).

---

## ENTRY CONDITIONS (BUY)

A buy is executed only when **ALL** conditions pass, checked in order:

| # | Condition | Rule | Variable | Defined in |
|---|-----------|------|----------|------------|
| 1 | **Date gate** | event_date ≤ today (Israel TZ) | `BUY_BLOCK_EVENT_DATE_AFTER_ISRAEL_TODAY` | `config/constants.py:55` |
| 2 | **Time + event day** | city_local `date == event_date` **and** 14:00 ≤ hour ≤ 23:59 | `BUY_EARLIEST_HOUR = 14`, `buy_earliest_local_hour` / `buy_latest_local_hour` in `runtime_config.json`, `BUY_LATEST_LOCAL_HOUR = 24`; `entry_time_allowed(..., event_date=…)` | `config/constants.py`, `config/settings.py`, `strategy/time_filter.py`, `strategy/trades.py` |
| 3 | **Price band** | normal: `buy_min ≤ gamma_yes ≤ buy_max`; **or** momentum surge path: `momentum_min_price ≤ gamma ≤ momentum_max_entry` **and** ≥15% rise in 15m | `BUY_MIN_THRESHOLD` / `BUY_MAX_THRESHOLD`, `MOMENTUM_MIN_PRICE`, `MOMENTUM_MAX_ENTRY` | `config/constants.py`, `strategy/trades.py` |
| 4 | **Position limit** | total open < 7 | `MAX_CONCURRENT_POSITIONS = 7` | `config/constants.py:57` |
| 5 | **Forecast available** | consensus °C from Open-Meteo exists | — | `forecast/forecast_service.py` |
| 6 | **Event cooldown** | `event_buy_cooldown` in state (rare; competitor-surge no longer arms a 20m event-wide buy block) | — | `strategy/decision_core.py` |
| 7 | **No duplicate** | not already holding this market_id | — | `strategy/decision_core.py` |
| 8 | **Max per event** | max 1 position per gamma event — **unless** `momentum_switch` sells the held sibling first (`momentum-switch-out`) | `MAX_POSITIONS_PER_EVENT = 1` | `config/constants.py`, `strategy/trades.py` |
| 9 | **Market prob ceiling** | market_yes ≤ max (skipped when momentum path qualifies) | `MAX_MARKET_PROB_FOR_BUY = 0.75` | `config/constants.py:148` |
| 10 | **Model prob floor** | model_prob ≥ min (skipped when momentum path qualifies) | `MIN_MODEL_PROB_FOR_BUY = 0.10` | `config/constants.py:149` |
| 11 | **Not flat distribution** | model peak gate (skipped when momentum path qualifies) | `DECISION_MIN_MODEL_PEAK_PROB = 0.12` | `config/constants.py:152` |
| 12 | **Momentum entry** ⚡ | when **not** holding this event: YES up ≥15% in 15m **and** momentum price band **and** market-YES **rank = 1** **and** competition passes using **`min_lead_momentum_over_runner_up`** (not row 13’s normal gap) → still bypass model rows 9–11, edge, neg-mom; **forecast contradict** at `place_buy` uses momentum CLOB band | `MOMENTUM_ENTRY_MAX_RANK = 1`, `MOMENTUM_ENTRY_RISE`, `momentum_min_price` / `momentum_max_entry`, `MIN_LEAD_MOMENTUM_OVER_RUNNER_UP` / runtime `min_lead_momentum_over_runner_up` | `config/constants.py`, `strategy/decision_core.py`, `strategy/trades.py` |
| 12b | **Momentum switch** 🔁 | hold A; bucket B is rank **`MOMENTUM_SWITCH_LEADER_YES_RANK`** by market YES, momentum rise + band, and `B_yes ≥ A_yes + MOMENTUM_SWITCH_ABOVE_HELD_GAP` → sell A (`momentum-switch-out`), then BUY B | `MOMENTUM_SWITCH_LEADER_YES_RANK`, `MOMENTUM_SWITCH_ABOVE_HELD_GAP`, `MOMENTUM_ENTRY_RISE`, `MOMENTUM_WINDOW_SECONDS`, `MOMENTUM_MIN_PRICE` / `MOMENTUM_MAX_ENTRY` | `strategy/decision_core.py` (`detect_momentum_switch`), `strategy/trades.py` |
| 12c | **Dominant competitor exit** | while holding A: if **#1** sibling (not A) has momentum + band and `leader_yes ≥ mark_A + gap` → SELL A (`momentum-competitor-dominant`) even before the leader market is scanned for a switch-in | same gap constant | `strategy/decision_core.py` (`check_exits`) |
| 13 | **Competition** | when `enable_competition_filter`: this bucket must lead #2 by ≥ **`min_lead_over_runner_up`** on **normal** buys (default **0.20**). **Cold momentum** (row 12) uses **`min_lead_momentum_over_runner_up`** (default **0.10**) instead. | `MIN_LEAD_OVER_RUNNER_UP`, `MIN_LEAD_MOMENTUM_OVER_RUNNER_UP`, `ENABLE_COMPETITION_FILTER`, runtime keys | `config/constants.py`, `data/runtime_config.json`, `strategy/competition_filter.py`, `strategy/decision_core.py` |
| 14 | **No negative momentum** | 15-min change > -10% (skipped only for momentum entry) | — | `strategy/decision_core.py` |
| 15 | **Edge gate** | `edge ≥ required_edge` … (skipped only for momentum entry) | `RESEARCH_MIN_EDGE`, … | `config/constants.py`, `strategy/research_signal.py` |
| 16 | **Forecast gate** | EXACT bracket contradict (skipped for momentum BUY at execution) | `FORECAST_CONTRADICT_MARGIN_C = 2.5` | `config/constants.py`, `strategy/trades.py` |
| 16b | **Forecast “supports YES” (weak sizing)** | EXACT slack (still applies for sizing when not momentum-relaxed path) | `FORECAST_EXACT_BUCKET_SUPPORT_SLACK_C = 2.5` | `config/constants.py` |
| 17 | **CLOB price verify** | normal buy band **or** momentum band when `TradeDecision.momentum_relaxed_gates` | — | `strategy/trades.py` |
| 17b | **Visible book before USD market buy** | `place_buy` uses the **live CLOB best ask** (not only Gamma `bestAsk`). Before `place_market_buy_yes`, it **walks visible asks** for the planned notional: skip if any level **>** band `hi`, or **VWAP > hi**, or **unfilled** notional exceeds `MARKET_BUY_VISIBLE_UNFILLED_*` (rest could hit unseen prices). After fill, **avg from positions API** updates ledger/Telegram. | `MARKET_BUY_ENFORCE_VISIBLE_BOOK`, `MARKET_BUY_VISIBLE_UNFILLED_MAX_FRAC`, `MARKET_BUY_VISIBLE_UNFILLED_MIN_USD` | `config/constants.py`, `polymarket_client.py`, `strategy/trades.py` |
| 18 | **Churn / blacklist** | no cooldown from repeated stop-losses, not blacklisted | `CHURN_COOLDOWN_SEC = 1200` (20 min) | `config/constants.py:103` |
| 19 | **Debug** | optional `[momentum_eval] {json}` per `evaluate_entry` when `MOMENTUM_DECISION_DEBUG_LOG = True` | `MOMENTUM_DECISION_DEBUG_LOG` | `config/constants.py`, `strategy/decision_core.py` |

### Momentum Entry (Ride the Wave) 🌊

If a bucket’s YES price rose **≥20%** in the last **15 minutes** (`MOMENTUM_ENTRY_RISE`, `MOMENTUM_WINDOW_SECONDS`) **and** the current YES is in **`momentum_min_price` … `momentum_max_entry`** (see `config/constants.py` + runtime `momentum_min_price` / `momentum_max_entry`) **and** this bucket is **#1 by market YES** among siblings (`MOMENTUM_ENTRY_MAX_RANK = 1`) **and** `evaluate_competition` passes when `enable_competition_filter` using **`min_lead_momentum_over_runner_up`** (separate from normal-buy **`min_lead_over_runner_up`**):

- **Model / flat / market ceiling rows** in `evaluate_entry` are **skipped** (momentum is the signal).
- **Research edge** and **negative 15m momentum** are still **bypassed** for momentum-only path.
- **Forecast “contradicts bracket”** at `place_buy` and **CLOB band** use the **momentum** bounds when `TradeDecision.momentum_relaxed_gates` is set.
- **Still required:** time + event day, max positions, forecast **exists**, churn, blacklist, `max_positions_per_event` unless **momentum switch** (row 12b) sold the sibling first.

**Why fast stop-loss sometimes missed a −99% mark:** `momentum-stop-loss` uses **peak-to-trough drawdown over samples in the 15m window**. Sparse samples or a cliff with no high inside the window can keep measured drawdown **below 20%** even when the UI looks catastrophic — see `strategy/momentum_engine.py` (`should_fast_exit`).

### Where 15m momentum is recorded

Price samples (for 15m windows) are appended for **every market returned in the bot’s temperature scan** for the target day(s), plus **held positions** fetched on the extra exit pass — see `strategy/loop.py` (`sample_markets` → `record_samples_for_market_dicts`). That is **all scanned city-day markets in that run**, not every Polymarket market globally.

---

## EXIT CONDITIONS (SELL)

Exits are checked in this priority order (first match wins):

| # | Condition | Rule | Variable | Defined in |
|---|-----------|------|----------|------------|
| 1 | **Market resolved** | status = closed/claimable/resolved → CLAIM | `STATUS_CLOSED` | `config/constants.py:175` |
| 2 | **Fast stop-loss** 🚨 (`momentum-stop-loss`) | In the last **15 min** (local `price_samples`), YES had a **≥20%** peak-to-trough drawdown **and** current mark **< entry** → EXIT immediately | `MOMENTUM_FAST_EXIT_DROP = 0.20`, `MOMENTUM_WINDOW_SECONDS = 900` | `strategy/momentum_engine.py` (`max_drawdown_in_window`), `strategy/decision_core.py` (`check_exits`), `config/constants.py` |
| 3 | **Dominant competitor** (`momentum-competitor-dominant`) | sibling that is **`MOMENTUM_SWITCH_LEADER_YES_RANK`** by market YES has momentum rise + momentum band **and** `leader_yes ≥ our_mark + MOMENTUM_SWITCH_ABOVE_HELD_GAP` → EXIT | `MOMENTUM_SWITCH_LEADER_YES_RANK`, `MOMENTUM_SWITCH_ABOVE_HELD_GAP`, `MOMENTUM_ENTRY_RISE`, `MOMENTUM_WINDOW_SECONDS`, `MOMENTUM_MIN_PRICE` / `MOMENTUM_MAX_ENTRY` | `strategy/decision_core.py` (`momentum_competitor_dominates_held_exit`, `check_exits`) |
| 4 | **Competitor surge** 🔥 | any sibling rose ≥**15%** in 15 min → EXIT (broader than row 3) | `MOMENTUM_COMPETITOR_SURGE = 0.15` | `config/constants.py:84` |
| 5 | **Time-decay** ⏰ | held >2 hours AND gain **< 5%** vs entry AND mark <0.85 → EXIT (stale position) | `TIME_DECAY_HOURS = 2.0`, `TIME_DECAY_MIN_GAIN = 0.05`, `TIME_DECAY_MAX_PRICE = 0.85`, runtime `time_decay_*` | `config/constants.py`, `data/runtime_config.json`, `strategy/time_filter.py` |
| 6 | **Research model flip** | same contradict rule as buy gate (optional, default off) | `RESEARCH_EXIT_ON_MODEL_FLIP`, `forecast_contradict_margin_c` | `config/constants.py`, `data/runtime_config.json` |
| 7 | **Regular stop-loss** | tiered: if entry < 0.60 → exit when mark < 0.30; if entry ≥ 0.60 → exit when mark < 0.40 | `STOP_LOSS_USE_ENTRY_TIERS = True`, `STOP_LOSS_TIER_ENTRY_SPLIT = 0.60`, `STOP_LOSS_TIER_MARK_LOW = 0.30`, `STOP_LOSS_TIER_MARK_HIGH = 0.40` | `config/constants.py:47-50` |
| 8 | **Take-profit** | mark ≥ 0.96 → EXIT | `TAKE_PROFIT_THRESHOLD = 0.96` | `config/constants.py:51` |

### Important: Competition does NOT trigger exits

If after we buy, a competitor gets closer (gap shrinks below 15%), we do **NOT** sell. The competition filter applies **only to new buys**, never to existing positions.

### After Exit: Churn Blacklist

After any stop-loss-type exit (`stop-loss`, `momentum-stop-loss`, `competitor-surge`, `momentum-competitor-dominant`, `time-decay`), the specific market is blacklisted for **20 minutes** (`CHURN_COOLDOWN_SEC = 1200`). This prevents re-entering a losing position immediately.

---

## Probability Model

- **Input**: `calibrated_forecast_max_c` (bias-adjusted by city) + city-specific MAE → sigma
- **Model**: Gaussian `N(mean=forecast, sigma=f(MAE))`
- **Calibration**: per-city bias correction from `data/research/calibration_latest.json`
- **Output**: `model_prob` = P(YES) for each temperature bucket
- **Edge gate (research)**: same P(YES) idea as `model_prob` but from `implied_yes` vs CLOB; compare **`edge`** to **`required_edge`** (includes **fee_drag** in the hurdle, not subtracted from `edge`). High-implied path adds a **soft boost** to `edge` when `P_implied` is above `RESEARCH_EDGE_IMPLIED_SOFT_FLOOR` — see row 15 in the entry table.
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
| `CASH_RESERVE_USD` | 5.0 | `config/constants.py:76` |
| `MAX_BUY_NOTIONAL_USD` | 4.0 | `config/constants.py:73` |
| `MIN_ORDER_NOTIONAL_USD` | 2.0 | `config/constants.py:74` |
| `MAX_TRADE_FRACTION_OF_CASH` | 0.90 | `config/constants.py:72` |

Optional: edge-scaled sizing when `RESEARCH_EDGE_SCALE_SIZE = true` (default off).

---

## Telegram Portfolio Message

On **bot startup**, Telegram sends the **STATUS** HTML (portfolio + per-row context), then **one follow-up**: the strategy digest as a **single plain-text message** if it fits under Telegram’s 4096 limit; if longer, as **one `.txt` document`** with a short caption (avoids splitting HTML into multiple bubbles). The same digest text is appended to the **terminal echo** of that startup send.

Every trade (buy/sell/claim) and every scheduled report sends a rich portfolio message including:

1. **Portfolio summary**: cash, positions MTM, total value
2. **Per position**:
   - Title, shares, entry price, current mark, PnL ($, %)
   - 🌡 Live Open-Meteo forecast + calibrated temperature
   - 📊 Research edge (model P(YES), edge, required edge, fee drag)
   - ⚡ Momentum: **15-minute** and **rolling 2-hour** YES change from `price_samples` (display)
   - ⏰ **Time decay preview**: same gates as exit (`held`, `gain vs entry`, `mark`) — uses `active_trades` + `should_time_decay_exit`
   - 📌 **YES Δ (since entry, ≤2h)**: first→last sample in `[max(entry, now−2h), now]` (path context; exit rule is still the time-decay row above)
   - 🏆 Competition — all sibling buckets with their probabilities

**Optional advisor (OpenRouter):** same Telegram chat; `/ask …` or messages containing **`bot`** / **`BOT`** spawn a **separate OS process** that bundles `STRATEGY_LOGIC.md`, **`MODEL_PROBABILITY_AND_CALIBRATION.md`** (forecast → calibrated μ → σ → P(YES), with worked numbers), `config/constants.py`, `config/settings.py`, `data/runtime_config.json`, `state.json`, recent `pnl_ledger.jsonl`, and today’s trade CSV into a prompt; **free models** are tried in order until one returns a reply (override with `OPENROUTER_MODEL` for a single id). See `OPENROUTER_FREE_MODELS` in `notifications/openrouter_advisor.py`. Requires `OPENROUTER_API_KEY` (or `OPENROUTER` / `openrouter` in env). **Before relying on Telegram**, run `python -m notifications.openrouter_advisor ping` (one tiny API call, no repo context) or `test` (ping + file report) and `context` (merged size). OpenRouter **429** is not retried (single attempt); free models can still rate-limit—wait or switch model. In-process decision history is **not** in files; paste logs into the chat. See `notifications/openrouter_advisor.py`.

---

## Module Map

| Area | File |
|------|------|
| Decision engine | `strategy/decision_core.py` |
| Probability model | `strategy/probability_engine.py`, `research/probability_from_forecast.py`, `MODEL_PROBABILITY_AND_CALIBRATION.md` (human doc) |
| Momentum engine | `strategy/momentum_engine.py` |
| Competition filter | `strategy/competition_filter.py` |
| Time filter | `strategy/time_filter.py` |
| Trade execution | `strategy/trades.py` |
| Main loop | `strategy/loop.py` |
| Price samples (infra) | `strategy/momentum.py` |
| Churn / blacklist | `strategy/churn.py` |
| Portfolio Telegram | `notifications/portfolio.py` |
| Forecast cache format | `notifications/forecast_cache_fmt.py` |
| Dashboard API | `app/dashboard.py` |
| Dashboard UI | `ui/src/App.tsx` |
| Constants | `config/constants.py` |
| Runtime settings | `config/settings.py`, `data/runtime_config.json` |
| Calibration data | `data/research/calibration_latest.json` |
