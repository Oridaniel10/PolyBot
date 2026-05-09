# Polymarket weather bot

Automated Polymarket helper focused on **highest-temperature** style markets: scans Gamma, syncs open positions from the Data API, applies **price-band + momentum** entries, **per-type stop-loss**, take-profit, **time-decay**, and **competition (event sibling)** filters; sends Telegram updates. **No database** — state lives in JSON / JSONL under `data/` and `state.json`.

> **CLOB v2 (since 2026-04-28).** Polymarket migrated the exchange to CLOB v2. The bot uses **`py-clob-client-v2`** (≥1.0.0), which has built-in retry on `order_version_mismatch` via `create_and_post_market_order` / `create_and_post_order`. The v1 SDK (`py-clob-client`) is no longer compatible.

> **Decisions are price-driven.** The forecast/research/calibration model still computes `model_prob` for display, but research-edge gates default to **OFF** in `data/runtime_config.json` (`research_edge_gate_buy=false`, `min_model_prob_for_buy=0.0`, `decision_min_model_peak_prob=0.0`). A market with no forecast still trades through the price-based gates. To re-enable model-based filtering, flip those keys in runtime config.

## Docs and limits

- **Buy / sell rules (Hebrew):** [STRATEGY_LOGIC.md](STRATEGY_LOGIC.md)
- **Rate limits (read before adding API calls):** [POLY_RATE_LIMITS.MD](POLY_RATE_LIMITS.MD)
- **Fees (weather taker ~5%):** [POLY_FEES.MD](POLY_FEES.MD)
- **Polymarket API reference:** [https://docs.polymarket.com/](https://docs.polymarket.com/)
- **Agent / architecture cheat sheet:** [AGENTS.md](AGENTS.md)

### Trading + research (optional, OFF by default)

- Offline pipeline writes `data/research/*.jsonl` and `calibration_latest.json` (see `research/WORKFLOW.md`).
- The bot’s Open-Meteo consensus path applies city bias from `calibration_latest.json` (`forecast/forecast_service.py` → `research/calibration_apply.py`).
- The **model vs CLOB edge gate** (`research_edge_gate_buy`) is disabled by default — entries are driven by price band, momentum, and competition. To enable it, set `research_edge_gate_buy: true` and tune `min_model_prob_for_buy` / `decision_min_model_peak_prob` in `data/runtime_config.json`. Regenerate calibration periodically (`python -m research analyze`) before tightening gates in production.

## Layout

| Path | Role |
|------|------|
| `main_bot.py` | Cloud Run / Docker: FastAPI health + `/api/*` + `/dashboard/` static + bot thread |
| `main.py` | CLI entry → `strategy.bot_runner.run_bot` |
| `strategy/trades.py` | `place_buy`, `close_position`, `claim_position`, `process_single_market` |
| `strategy/research_signal.py` | optional implied P(YES) vs CLOB edge + size multiplier |
| `strategy/loop.py` | `run_once` (scan + exit pass + price samples) |
| `config/` | Constants + `runtime_config.json` merge |
| `state/` | `state.json` I/O, `pnl_ledger` append |
| `data/trade_log_YYYY-MM-DD.csv` | daily CSV (`TRADE_CSV_FIELDS` in `state/pnl_ledger.py`) — includes bot-report `local_hhmm` plus **city-local** `city_local_hhmm`, `entry_type`, `decision_reason` on BUY rows |
| `notifications/` | Terminal + Telegram HTML (incl. est PnL lines) |
| `app/dashboard.py` | REST: PnL summary, weather list, runtime config, daily blacklist |
| `ui/` | Vite React dashboard (build → `ui/dist`) |

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env` with keys (see below). Ensure `data/` exists (created automatically).

## Environment variables

Core (trading + Telegram), same as before:

- `API_KEY`, `API_SECRET`, `API_PASSPHRASE`, `POLY_PRIVATE_KEY`, `POLY_PROXY_ADDRESS` / `POLY_ADDRESS`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- Optional: `POLY_GAMMA_BASE_URL`, `POLY_CLOB_BASE_URL`, `POLY_SIGNATURE_TYPE`, `MAX_TRADE_FRACTION_OF_CASH`, `MIN_ORDER_NOTIONAL_USD`, `BUY_MAX_NOTIONAL_USD`

## Run (bot only, local)

```bash
python main.py
```

## Run (HTTP + bot + API + dashboard)

```bash
# build UI once (needed for /dashboard/)
cd ui && npm install && npm run build && cd ..

export PORT=8080
python main_bot.py
# or: uvicorn main_bot:app --host 0.0.0.0 --port 8080
```

### Phone / another network (HTTPS)

`127.0.0.1` only works on your PC. For mobile data or another Wi‑Fi, use **`./start.sh`**: it runs [Cloudflare quick Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/do-more-with-tunnels/trycloudflare/) and prints a **`https://….trycloudflare.com`** URL — open that on your phone (same session as the bot; UI uses relative `/api` paths). Quick tunnels are ephemeral and not for production; for a stable hostname use a [named tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/) on your domain.

## Run (dashboard + API only — no trading bot)

From repo root:

```bash
cd ui && npm install && npm run build && cd ..
python app/dashboard.py
# open http://127.0.0.1:8080/dashboard/
```

`app/dashboard.py` is the **router** when imported; running it as `__main__` starts uvicorn with the same `/api/*` routes and static files under `/dashboard/`, **without** spawning the bot thread.

- `GET /` — redirects to `/dashboard/` when `ui/dist` exists; otherwise plain text (health)
- `GET /api/health` — JSON health
- `GET /api/pnl/summary` — day / week aggregates from `data/pnl_ledger.jsonl`
- `GET /api/portfolio/positions` — live open positions + cash (Data API; does not wait on the slow weather scan)
- `GET /api/markets/weather-today` — today’s highest-temperature scan (same logic as the bot)
- `GET /api/forecast/preview` — Open-Meteo (+ optional OpenWeather) vs YES prices (same grouping as the Telegram digest; **read-only**)
- `GET|POST /api/runtime-config` — read/write `data/runtime_config.json`
- `POST /api/blacklist/toggle` — body `{"market_id":"…","enabled":true}` → `data/blacklist_day.json` (calendar day)
- `GET /dashboard/` — React UI (after `ui` build)
- Changing config/blacklist from the UI triggers a **Telegram** notification when `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` are set (same `.env` as the bot).

## Runtime config (`data/runtime_config.json`)

Merged **each tick** with env-backed sizing. Keys include:

- `buy_min_threshold`, `buy_max_threshold` (default **0.84** — lowered from 0.91 so normal entries keep meaningful upside), `stop_loss_normal`, `stop_loss_momentum`, `stop_loss_double_momentum`, `take_profit_threshold`
- `stop_loss_normal_entry_drop_pct`, `stop_loss_momentum_entry_drop_pct`, `stop_loss_double_momentum_entry_drop_pct` (entry-relative SL extension)
- `trailing_stop_enabled`, `trailing_stop_activation_gain` (default **0.20**), `trailing_stop_lock_gain` (default **0.10**) — once `highest_seen_price ≥ entry + activation_gain`, the effective stop is raised to `entry + lock_gain`
- `min_lead_over_runner_up`, `enable_competition_filter` — require YES lead vs next-best sibling market in the same Gamma **event**
- `momentum_window_seconds`, `momentum_fast_exit_drop`, `momentum_competitor_surge`, `momentum_entry_rise` (**0.15**), `momentum_pct_rise` (**1.0** = +100%), `momentum_min_start_price` (default **0** = allow pct surges off low-start YES), `momentum_min_price` / `momentum_max_entry`, `crash_drop_pct_from_peak`, `buy_escalate_notional_on_submit_fail`, `buy_escalate_notional_step_usd`
- `double_momentum_fast_window_seconds`, `double_momentum_entry_rise` (**0.25**), `double_momentum_pct_rise` (**2.0** = +200%), `double_momentum_min_start_price`, `double_momentum_min_price`, `double_momentum_max_price`
- `limit_orders_enabled`, `buy_limit_order_timeout_sec`, `sell_limit_order_timeout_sec`, `emergency_exit_allow_market_order`, `buy_limit_price_offset`, `sell_limit_price_offset`, `require_fill_before_state_buy` — limit-first order execution; emergency reasons (stop-loss, momentum-fast-exit, bucket switch, time-decay) may fall back to market when allowed
- `telegram_failed_exit_dedupe_enabled`, `telegram_failed_exit_cooldown_sec` — suppress repeated failed-exit Telegram notices for the same `(market_id, exit_reason, error_category)`
- `trade_log_full_reason_enabled`, `telegram_verbose_trade_reason` — write/render the rich trade reason (entry type, trigger window, abs/pct rise, decision vs CLOB price, SL category)
- `time_decay_hours`, `time_decay_min_gain`, `time_decay_max_price` — time-decay uses **absolute YES points** gain vs entry, not percent of entry
- `churn_event_loss_1_cooldown_sec`, `churn_event_loss_2_cooldown_sec`, `leader_switch_window_sec`, `leader_switch_max_count`, `unstable_event_cooldown_sec`
- `churn_max_stop_cycles`, `churn_cooldown_sec` — after N **stop-loss** exits, buy cooldown (default 1 cycle, 20 minutes); **take-profit resets** the counter
- `blacklist_market_ids` — extra IDs blocked for buys (merged with daily blacklist file)
- `cash_reserve_usd` — dollars of free cash **excluded** from buy sizing; default **3** in `config/constants.py` and often overridden live in `data/runtime_config.json`. `max_trade_fraction_of_cash` applies to **tradable** cash only (`cash − reserve`).

## Anti-churn policy

Per `market_id`: count **bot stop-loss** exits; at **≥ churn_max_stop_cycles** set `cooldown_until` for **churn_cooldown_sec**. **Take-profit** clears the counter. When cooldown expires, counter resets so trading can resume.

## Forecast (see it + effect on the bot)

**Where the forecast appears (pick one or more):**

| Goal | Command / place |
|------|------------------|
| **Telegram digest** (HTML every N seconds) | From repo root: `python -m forecast` — **always** sends on the configured interval and **ignores** `forecast_digest_enabled` (so you can leave that checkbox off on the bot and use this process only). The bot / `main_bot.py` background thread respects `forecast_digest_enabled`. **Do not** run both digest paths to the same chat unless you want duplicate messages. |
| **Browser (no Telegram)** | Build `ui/` then run `python app/dashboard.py` or `main_bot.py` → open `/dashboard/` → section **Forecast preview** (calls `/api/forecast/preview`). |

Optional second model: set `OPENWEATHER_API_KEY` in `.env` and turn on `enable_openweather_forecast` in `data/runtime_config.json` (or the dashboard form).

**Does it change buy/sell?** Yes, when the runtime flags are enabled (merged each tick):

- **`forecast_gate_buy`** — can **block** a buy if the external model strongly contradicts the bracket.
- **`forecast_reduce_usd_if_weak`** — can **shrink** buy notional when the model does not support YES (`forecast_weak_size_factor`).
- **`enable_flow_peer_exit`** — can trigger a **sell** (`flow-peer-surge`) from sampled flow data, not from the digest HTML.

Current defaults are in `config/constants.py`, and live values can override them in `data/runtime_config.json`. Details: [STRATEGY_LOGIC.md](STRATEGY_LOGIC.md).

**Smoke test (digest + optional live Gamma/Open-Meteo):** `python tests/test_forecast_digest.py`

## Momentum & samples

Append-only `data/price_samples/YYYY-MM-DD.jsonl` (one line per market per tick for scanned + held markets). Files older than **7 days** are deleted on bot startup. Each market keeps the latest **240** samples per day, enough for roughly 2 hours at a 30-second scan. Momentum entry and competitor surge use **absolute YES-price points** (`+0.15`, not +15% relative), with at least **2** samples required in the window — low enough that a fresh market that just jumped from 0.10 to 0.70 in two ticks still qualifies for **double-momentum** entry (`+0.30` rise, price band 0.20–0.88).

## Stop-loss (per-type), trailing stop, and the fast-exit watcher

Stop-loss now uses an **effective stop** built from the entry type, entry price and the running peak:

`effective_stop = max(stop_loss_by_type, entry_price × (1 − entry_drop_pct_by_type), trailing_stop_level_if_active)`

* `trailing_stop_level` activates once `highest_seen_price ≥ entry + trailing_stop_activation_gain` (default +0.20) and locks the stop at `entry + trailing_stop_lock_gain` (default +0.10).
* Each breach is **categorized** as `SL_ABSOLUTE` / `SL_RELATIVE` / `SL_TRAILING` / `SL_MOMENTUM` and persisted on the trade row; Telegram and the trade ledger render that label so post-mortems are unambiguous.

Two paths check it:

1. **Main loop** (~30s): `decision_core.check_exits` calls `classify_stop_loss_breach` with the latest mark.
2. **Fast exit watcher** (`strategy/fast_exit_watcher.py`, default 2s): polls **live CLOB orderbook** via `get_clob_yes_price_live_by_id` (best ask / midpoint), updates `highest_seen_price`, bypasses the stale Gamma `bestAsk` cache, and writes the fresh price back into `trade_row["last_price"]` so the slow loop also sees current data.

## Order execution (limit-first)

`strategy/limit_executor.py` posts a **GTC limit buy** at the live best ask + `buy_limit_price_offset`, polls for `buy_limit_order_timeout_sec`, and cancels on timeout (no chasing). Slippage and fill price are persisted in `state.json` and the trade CSV. Non-emergency sells try a limit too; emergency reasons (stop-loss, momentum-fast-exit, bucket switch, time-decay) may fall back to a market order when `emergency_exit_allow_market_order` is `true`. Bucket switching is now atomic: sell first, confirm fill, only then buy the new bucket — see the `switch_*` log labels in `STRATEGY_LOGIC.md`.

## Docker

Multi-stage image builds the React app then installs Python deps. `ui/dist` is copied into the runtime image.

```bash
docker build -t poly-bot .
docker run --env-file .env -p 8080:8080 poly-bot
```

## Cursor

See [.cursor/rules/poly-bot.mdc](.cursor/rules/poly-bot.mdc) and [AGENTS.md](AGENTS.md) for conventions.
