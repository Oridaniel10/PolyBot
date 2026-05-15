# Polymarket Weather Bot

---

## For Friends — Quick Start Guide

This is a trading bot that automatically bets on weather outcomes on [Polymarket](https://polymarket.com). Every day, Polymarket lists markets like "Will the highest temperature in Paris be above 22°C on May 14?" — the bot finds those markets, decides whether to bet YES, and manages the trade automatically until it closes.

No need to watch charts all day. The bot runs on your computer (or a server) and sends you updates on Telegram.

---

### How the strategy works (plain English)

Each city/date has multiple "temperature buckets" listed as separate YES/NO markets — for example:
- Paris below 18°C (YES price: 0.05)
- Paris 18–20°C (YES price: 0.12)
- Paris 20–22°C (YES price: **0.78**)  ← bot might buy this one
- Paris above 22°C (YES price: 0.04)

The bot looks for the bucket with the **highest YES price** (the crowd's favorite). It only buys if:

1. **Price is in range** — between ~0.10 and 0.84. Not too cheap (uncertain) and not too expensive (no upside left).
2. **Momentum** — the price is rising AND a competing bucket's price is falling. This means the crowd is shifting toward this outcome.
3. **Time window** — not too early in the day (European cities from 16:00 local time; Asian/American cities from 14:00). Early-morning prices are noisy.
4. **No recent loss** — if the bot just stopped out of this market in the last 30 minutes, it won't re-enter.
5. **City not blacklisted** — if a city has been losing consistently, you can block it permanently in the config.

**Exit rules:**
- **Take profit** at 0.94 (close to 1.0 = "YES resolved")
- **Stop loss** — if the price drops too far from entry, cut the loss
- **Trailing stop** — once a trade is up +0.20, the stop moves up to lock in at least +0.10 gain
- **Time decay** — if the event date is close and the trade hasn't moved, exit to free up capital

---

### Prerequisites

You need:
- **Python 3.11 or 3.12** — [python.org](https://www.python.org/downloads/)
- **Redis** — an in-memory cache the bot uses to store price history
- A **Polymarket account** with a funded wallet
- A **Telegram bot** for notifications (optional but strongly recommended)

---

### Step 1 — Install Redis

Redis stores 2 hours of price history used for momentum detection.

**macOS (with Homebrew):**
```bash
brew install redis
brew services start redis
```

**Ubuntu / Debian:**
```bash
sudo apt update && sudo apt install redis-server -y
sudo systemctl enable redis-server
sudo systemctl start redis-server
```

**Windows:**
Use [Redis for Windows](https://github.com/microsoftarchive/redis/releases) or run via WSL2 with the Ubuntu instructions above.

**Verify Redis is running:**
```bash
redis-cli ping
# should print: PONG
```

---

### Step 2 — Clone and install Python dependencies

```bash
git clone <this-repo-url>
cd poly
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

---

### Step 3 — Create your `.env` file

Copy the template and fill in your keys:

```bash
cp .env.example .env   # or just create .env manually
```

Open `.env` and set the following:

```dotenv
# ── Polymarket wallet ────────────────────────────────────────────────────────
# Your wallet's private key (starts with 0x...)
POLY_PRIVATE_KEY=0xYOUR_PRIVATE_KEY_HERE

# The proxy address shown in Polymarket's "API Keys" page
POLY_PROXY_ADDRESS=0xYOUR_PROXY_ADDRESS_HERE

# ── Polymarket API credentials ───────────────────────────────────────────────
# Generate these at https://polymarket.com → Profile → API Keys
API_KEY=your-api-key
API_SECRET=your-api-secret
API_PASSPHRASE=your-api-passphrase

# ── Telegram bot (optional but recommended) ──────────────────────────────────
# 1. Message @BotFather on Telegram → /newbot → copy the token
TELEGRAM_BOT_TOKEN=123456:ABCdef...

# 2. Get your chat ID: message @userinfobot on Telegram
TELEGRAM_CHAT_ID=123456789

# ── Optional: OpenWeather forecast (extra accuracy) ──────────────────────────
# Free key from https://openweathermap.org/api
OPENWEATHER_API_KEY=

# ── Optional: AI-powered market research (not required for trading) ──────────
OPENROUTER_API_KEY=
```

**Where to find your Polymarket keys:**
1. Go to [polymarket.com](https://polymarket.com) and log in
2. Click your profile → "API Keys"
3. Click "Create API Key" — save the key, secret, and passphrase (shown once!)
4. The "proxy address" is the contract address shown on that same page

---

### Step 4 — Configure the bot

The main config file is `data/runtime_config.json`. Open it and adjust:

```jsonc
{
  "buy_min_threshold": 0.10,    // don't buy below this price (too uncertain)
  "buy_max_threshold": 0.84,    // don't buy above this price (too expensive)
  "take_profit_threshold": 0.94,// sell when price reaches this
  "stop_loss_momentum": 0.30,   // stop-loss level for momentum entries
  "buy_earliest_local_hour": 15,// global fallback: don't buy before this local hour
  "cash_reserve_usd": 3,        // keep this much cash untouched
  "permanent_blacklist_cities": ["Madrid"]  // cities to never trade
}
```

**Per-city buy hours** are in `data/city_buy_earliest_hour.json`. European cities default to 16, Asian/American cities to 14. If a city isn't listed, the global `buy_earliest_local_hour` applies.

---

### Step 5 — Run the bot

```bash
source venv/bin/activate
python main.py
```

You'll see output in the terminal every ~20 seconds as the bot scans markets. If you set up Telegram, you'll get a message whenever the bot buys or sells.

**To run with the web dashboard as well:**
```bash
python main_bot.py
# open http://localhost:8080/dashboard/
```

---

### Telegram commands

Once the bot is running, message your bot on Telegram:

| Command | What it does |
|---------|-------------|
| `/status` | Show all open positions with current P&L + charts |
| `/forecast` | Show today's temperature forecasts vs current YES prices |
| `/report` | Full portfolio summary |
| `/ask <question>` | Ask AI about any market |
| `/help` | List all commands |

---

### Key files at a glance

| File | Purpose |
|------|---------|
| `.env` | Your private keys — never share this |
| `data/runtime_config.json` | All trading parameters (edit live, reloads each tick) |
| `data/city_buy_earliest_hour.json` | Per-city earliest buy hour |
| `state.json` | Current open positions and bot memory |
| `data/trade_log_YYYY-MM-DD.csv` | Daily trade history |
| `data/price_samples/` | Rolling 2h price history for momentum detection |

---

### Common issues

**Bot doesn't buy anything:**
- Check that `buy_earliest_local_hour` isn't too high for your timezone
- Check `data/runtime_config.json` for `permanent_blacklist_cities`
- The bot only buys when the YES price is between `buy_min_threshold` (0.10) and `buy_max_threshold` (0.84)

**Redis connection error:**
```bash
redis-cli ping  # should return PONG; if not, start Redis first
```

**Import errors / missing packages:**
```bash
source venv/bin/activate  # make sure venv is active
pip install -r requirements.txt
```

**"No markets found":** The bot only scans highest-temperature markets. If Polymarket hasn't listed tomorrow's markets yet, it'll find nothing — this is normal before ~10:00 UTC.

---

*For technical documentation, architecture details, and strategy configuration, continue reading below.*

---

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
