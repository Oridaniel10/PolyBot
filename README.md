# Polymarket weather bot

Automated Polymarket helper focused on **highest-temperature** style markets: scans Gamma, syncs open positions from the Data API, applies buy / stop-loss / take-profit rules, sends Telegram updates, and optional **momentum** + **competition (event sibling)** filters. **No database** — state lives in JSON / JSONL under `data/` and `state.json`.

## Docs and limits

- **Buy / sell rules (Hebrew):** [STRATEGY_LOGIC.md](STRATEGY_LOGIC.md)
- **Rate limits (read before adding API calls):** [POLY_RATE_LIMITS.MD](POLY_RATE_LIMITS.MD)
- **Polymarket API reference:** [https://docs.polymarket.com/](https://docs.polymarket.com/)
- **Agent / architecture cheat sheet:** [AGENTS.md](AGENTS.md)

## Layout

| Path | Role |
|------|------|
| `main_bot.py` | Cloud Run / Docker: FastAPI health + `/api/*` + `/dashboard/` static + bot thread |
| `main.py` | CLI entry → `strategy.bot_runner.run_bot` |
| `strategy/trades.py` | `place_buy`, `close_position`, `claim_position`, `process_single_market` |
| `strategy/loop.py` | `run_once` (scan + exit pass + price samples) |
| `config/` | Constants + `runtime_config.json` merge |
| `state/` | `state.json` I/O, `pnl_ledger` append |
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

## Run (dashboard + API only — no trading bot)

From repo root:

```bash
cd ui && npm install && npm run build && cd ..
python app/dashboard.py
# open http://127.0.0.1:8080/dashboard/
```

`app/dashboard.py` is the **router** when imported; running it as `__main__` starts uvicorn with the same `/api/*` routes and static files under `/dashboard/`, **without** spawning the bot thread.

- `GET /` — plain health for Cloud Run
- `GET /api/health` — JSON health
- `GET /api/pnl/summary` — day / week aggregates from `data/pnl_ledger.jsonl`
- `GET /api/portfolio/positions` — live open positions + cash (Data API; does not wait on the slow weather scan)
- `GET /api/markets/weather-today` — today’s highest-temperature scan (same logic as the bot)
- `GET|POST /api/runtime-config` — read/write `data/runtime_config.json`
- `POST /api/blacklist/toggle` — body `{"market_id":"…","enabled":true}` → `data/blacklist_day.json` (calendar day)
- `GET /dashboard/` — React UI (after `ui` build)
- Changing config/blacklist from the UI triggers a **Telegram** notification when `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` are set (same `.env` as the bot).

## Runtime config (`data/runtime_config.json`)

Merged **each tick** with env-backed sizing. Keys include:

- `buy_min_threshold`, `buy_max_threshold`, `stop_loss_threshold`, `take_profit_threshold`
- `min_lead_over_runner_up`, `enable_competition_filter` — require YES lead vs next-best sibling market in the same Gamma **event**
- `enable_momentum`, `momentum_window_min`, `momentum_rise`, `momentum_min_price`, `momentum_max_entry`, `momentum_peer_drop`
- `churn_max_stop_cycles`, `churn_cooldown_sec` — after N **stop-loss** exits, buy cooldown (default 2 cycles, 15 minutes); **take-profit resets** the counter
- `blacklist_market_ids` — extra IDs blocked for buys (merged with daily blacklist file)
- `cash_reserve_usd` — dollars of free cash **excluded** from buy sizing; default **10** (`config/constants.py` `CASH_RESERVE_USD`). `max_trade_fraction_of_cash` applies to **tradable** cash only (`cash − reserve`).

## Anti-churn policy

Per `market_id`: count **bot stop-loss** exits; at **≥ churn_max_stop_cycles** set `cooldown_until` for **churn_cooldown_sec**. **Take-profit** clears the counter. When cooldown expires, counter resets so trading can resume.

## Momentum & samples

Append-only `data/price_samples/YYYY-MM-DD.jsonl` (one line per market per tick for scanned + held markets). Files older than **7 days** are deleted on bot startup. Momentum **entry** can relax the buy ceiling toward `momentum_max_entry` when the window rise rule fires; **peer-drop sell** closes YES when another market in the same event drops enough in the window.

## Docker

Multi-stage image builds the React app then installs Python deps. `ui/dist` is copied into the runtime image.

```bash
docker build -t poly-bot .
docker run --env-file .env -p 8080:8080 poly-bot
```

## Cursor

See [.cursor/rules/poly-bot.mdc](.cursor/rules/poly-bot.mdc) and [AGENTS.md](AGENTS.md) for conventions.
