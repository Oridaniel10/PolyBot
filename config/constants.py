from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
STATE_FILE = PROJECT_ROOT / "state.json"
ENV_FILE = PROJECT_ROOT / ".env"
RUNTIME_CONFIG_FILE = DATA_DIR / "runtime_config.json"
PNL_LEDGER_FILE = DATA_DIR / "pnl_ledger.jsonl"
BLACKLIST_FILE = DATA_DIR / "blacklist_day.json"
PRICE_SAMPLES_DIR = DATA_DIR / "price_samples"
FLOW_SAMPLES_FILE = DATA_DIR / "flow_samples.jsonl"
FORECAST_CACHE_FILE = DATA_DIR / "forecast_cache.json"
# UI + on-demand portfolio: treat cache as fresh under this age (aligns with digest interval)
# treat dashboard cache as fresh if younger than ~3× digest refresh
FORECAST_CACHE_MAX_AGE_SEC = 360
UI_DIST_DIR = PROJECT_ROOT / "ui" / "dist"

TIMEZONE = "Asia/Jerusalem"
REPORT_HOURS = (10, 13, 16, 19)
SCAN_INTERVAL_SECONDS = 30
SELL_BELOW_MIN_COOLDOWN_SEC = 86400

BUY_MIN_THRESHOLD = 0.60
BUY_MAX_THRESHOLD = 0.70
STOP_LOSS_THRESHOLD = 0.50
TAKE_PROFIT_THRESHOLD = 0.96
# earliest local hour to place new buys (weather forecasts stabilise late morning)
BUY_EARLIEST_HOUR = 10
# max open positions at once — limits total portfolio risk
MAX_CONCURRENT_POSITIONS = 7
# float slack vs gamma/mark rounding; also helps when take_profit is 0.99 and mark is 0.988
TAKE_PROFIT_COMPARE_SLACK = 0.002
# after a "below CLOB min" sell skip we set a long cooldown — must not block risk exits
SELL_BYPASS_MIN_COOLDOWN_REASONS = frozenset(
    {"take-profit", "stop-loss", "momentum-peer-drop", "flow-peer-surge"}
)
DEFAULT_ORDER_SIZE = 10.0
MAX_TRADE_FRACTION_OF_CASH = 0.90
MAX_BUY_NOTIONAL_USD = 4.0
MIN_ORDER_NOTIONAL_USD = 2.0
# never allocate buys from this portion of free cash (runtime + UI override)
CASH_RESERVE_USD = 2.0

MIN_LEAD_OVER_RUNNER_UP = 0.10
ENABLE_COMPETITION_FILTER = True

ENABLE_MOMENTUM = False
MOMENTUM_WINDOW_MIN = 10
MOMENTUM_RISE = 0.25
MOMENTUM_MIN_PRICE = 0.40
MOMENTUM_MAX_ENTRY = 0.65
MOMENTUM_PEER_DROP = 0.10
PRICE_SAMPLE_RETENTION_DAYS = 7
PRICE_SAMPLE_MAX_ENTRIES_PER_MARKET = 30

CHURN_MAX_STOP_CYCLES = 1
CHURN_COOLDOWN_SEC = 1200

FORECAST_DIGEST_ENABLED = False
# how often to rescan Gamma + Open-Meteo and rewrite data/forecast_cache.json
FORECAST_DIGEST_REFRESH_INTERVAL_SEC = 600
# how often to push the HTML digest to Telegram (0 = never auto; use /digest)
FORECAST_DIGEST_TELEGRAM_INTERVAL_SEC = 0
# legacy single knob; used only if refresh/telegram keys missing in runtime_config.json
FORECAST_DIGEST_INTERVAL_SEC = 600
FORECAST_DIGEST_SCOPE = "scan"
# cap Open-Meteo calls per digest (many city-days exist when Gamma returns full search results)
FORECAST_DIGEST_MAX_LOCATION_GROUPS = 80
# upper bound for runtime_config / UI; on-demand /digest ignores this and includes all parsable groups
FORECAST_DIGEST_MAX_GROUPS_CAP = 500

ENABLE_OPENWEATHER_FORECAST = True
ENABLE_FLOW_SAMPLING = True
FLOW_MAX_EVENTS_PER_TICK = 8
ENABLE_FLOW_PEER_EXIT = True
FLOW_PEER_WINDOW_SEC = 600
FLOW_PEER_SURGE_DROP = 0.12
FLOW_PEER_SURGE_RISE = 0.10

FORECAST_GATE_BUY = True
FORECAST_CONTRADICT_MARGIN_C = 1.5
FORECAST_REDUCE_USD_IF_WEAK = True
FORECAST_WEAK_SIZE_FACTOR = 0.75

YES_LABEL = "YES"
STATUS_CLOSED = frozenset({"closed", "claimable", "resolved"})
DUST_SHARES_EPS = 1e-6
# when YES on exchange is just under CLOB min (e.g. 4.85 vs 5), market-buy a sliver then sell all
CLOB_SELL_TOPUP_BUFFER_SHARES = 0.12
CLOB_SELL_TOPUP_SLIPPAGE_MULT = 1.25
CLOB_SELL_TOPUP_MIN_USD = 0.35
CLOB_SELL_TOPUP_MAX_USD = 4.0

TERM_RESET = "\033[0m"
TERM_BOLD = "\033[1m"
TERM_DIM = "\033[2m"
TERM_GREEN = "\033[32m"
TERM_RED = "\033[31m"
TERM_YELLOW = "\033[33m"
TERM_CYAN = "\033[36m"
