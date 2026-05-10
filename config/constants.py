from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
STATE_FILE = PROJECT_ROOT / "state.json"
ENV_FILE = PROJECT_ROOT / ".env"
RUNTIME_CONFIG_FILE = DATA_DIR / "runtime_config.json"
PNL_LEDGER_FILE = DATA_DIR / "pnl_ledger.jsonl"
BLACKLIST_FILE = DATA_DIR / "blacklist_day.json"
PRICE_SAMPLES_DIR = DATA_DIR / "price_samples"
FORECAST_CACHE_FILE = DATA_DIR / "forecast_cache.json"
RESEARCH_DIR = DATA_DIR / "research"
RESEARCH_CITIES_FILE = RESEARCH_DIR / "cities.json"
RESEARCH_RESOLUTION_REGISTRY_FILE = RESEARCH_DIR / "resolution_registry.json"
RESEARCH_RESOLUTION_OVERRIDES_FILE = RESEARCH_DIR / "resolution_overrides.json"
RESEARCH_DAILY_RUNS_FILE = RESEARCH_DIR / "daily_runs.jsonl"
RESEARCH_FORECASTS_HISTORY_FILE = RESEARCH_DIR / "forecasts_history.jsonl"
RESEARCH_TRUTH_DAILY_FILE = RESEARCH_DIR / "truth_daily.jsonl"
RESEARCH_MARKET_OUTCOMES_FILE = RESEARCH_DIR / "market_outcomes.jsonl"
RESEARCH_CALIBRATION_LATEST_FILE = RESEARCH_DIR / "calibration_latest.json"
RESEARCH_EVENT_SUMMARY_FILE = RESEARCH_DIR / "event_summary_latest.json"
RESEARCH_CITY_TABLES_DIR = RESEARCH_DIR / "city_tables"
RESEARCH_EXPORTS_DIR = RESEARCH_DIR / "exports"
# compact JSON output (new rows only; old jsonl lines unchanged)
RESEARCH_REGISTRY_RSRC_MAX_LEN = 360
RESEARCH_OUTCOME_QUESTION_MAX_LEN = 120
# mandatory Telegram portfolio STATUS (full snapshot) at least this often
PORTFOLIO_STATUS_HEARTBEAT_SEC = 3600

# UI + on-demand portfolio: treat cache as fresh under this age (aligns with digest interval)
# treat dashboard cache as fresh if younger than ~3× digest refresh
FORECAST_CACHE_MAX_AGE_SEC = 360
UI_DIST_DIR = PROJECT_ROOT / "ui" / "dist"

TIMEZONE = "Asia/Jerusalem"
REPORT_HOURS = (10, 13, 16, 19)
SCAN_INTERVAL_SECONDS = 30
# short cooldown — we want to retry topup soon, not block exits for a full day.
# critical exits (stop-loss/take-profit/etc.) bypass via SELL_BYPASS_MIN_COOLDOWN_REASONS.
SELL_BELOW_MIN_COOLDOWN_SEC = 300
# repeat Telegram for same market stuck on below_min_order_size at most this often
SELL_BELOW_MIN_TELEGRAM_COOLDOWN_SEC = 1800

# ═══════════════════════════════════════════════════════════════════════
# BUY / SELL thresholds — grouped by entry type.
# each block keeps the full entry + exit config together.
# ═══════════════════════════════════════════════════════════════════════

# ─── NORMAL: buy band + sell stop-loss ────────────────────────────────
# buy only if YES is inside this band.
# example: 0.82 is inside 0.80..0.84 so normal entry can pass this gate.
# upper bound lowered from 0.91 to 0.84 — at 0.88-0.92 upside is tiny while
# downside risk on a missed bracket is large.
BUY_MIN_THRESHOLD = 1.1
BUY_MAX_THRESHOLD = 1.1
BUY_DISABLE_PRICE_BAND = False

# hard stop for normal.
# example: if entry was 0.82, any live price below 0.50 is always a sell.
STOP_LOSS_NORMAL = 0.20
# relative stop for normal.
# example: entry 0.80 with 30% drop => relative stop is 0.56.
# effective stop = max(0.50, 0.56) = 0.56.
STOP_LOSS_NORMAL_ENTRY_DROP_PCT = 0.30

# ─── MANUAL / UI: custom stop loss for user trades ───────────────────
# hard stop for manual trades.
STOP_LOSS_MANUAL = 0.40
# relative stop for manual trades.
STOP_LOSS_MANUAL_ENTRY_DROP_PCT = 0.30


# ─── MOMENTUM: buy on 15m surge + sell stop-loss ──────────────────────
# absolute rise trigger (any window).
# example: 0.30 -> 0.45 is +0.15 so this trigger passes.
MOMENTUM_ENTRY_RISE = 0.15
# percent rise trigger (fractional, not percent points).
# example: 3.0 means +300%; 0.05 -> 0.15 passes on pct gate.
MOMENTUM_PCT_RISE = 1.0
# floor on *old* YES in window below which we reject the pct gate (noise).
# 0.0 = allow any positive baseline; merged runtime still clamps ≥0 via settings.
MOMENTUM_MIN_START_PRICE = 0.0001
# live price band for momentum entry (current YES must fall in band at decision time).
MOMENTUM_MIN_PRICE = 0.20
MOMENTUM_MAX_ENTRY = 0.90
# hard floor for momentum SL (effective = max(floor, entry*(1-drop_pct), trailing…)).
STOP_LOSS_MOMENTUM = 0.10
# entry-relative leg: e.g. entry 0.56 → mark must stay above 0.28 before exit.
STOP_LOSS_MOMENTUM_ENTRY_DROP_PCT = 0.50

# ─── DOUBLE MOMENTUM: stronger 15m surge + wider band + sell stop-loss ─
# absolute 15m rise trigger.
# example: 0.30 -> 0.70 is +0.40 so this trigger passes.
DOUBLE_MOMENTUM_ENTRY_RISE = 0.30
# percent rise trigger (fractional). 4.0 = +400%.
DOUBLE_MOMENTUM_PCT_RISE = 2.0
# same pct noise floor as standard momentum — 0 allows low-start pct moves.
DOUBLE_MOMENTUM_MIN_START_PRICE = 0.0001
# wider entry band for larger surges only.
DOUBLE_MOMENTUM_MIN_PRICE = 0.20
DOUBLE_MOMENTUM_MAX_PRICE = 0.90
STOP_LOSS_DOUBLE_MOMENTUM = 0.10
STOP_LOSS_DOUBLE_MOMENTUM_ENTRY_DROP_PCT = 0.50

# ═══════════════════════════════════════════════════════════════════════
# EXIT THRESHOLDS
# ═══════════════════════════════════════════════════════════════════════

TAKE_PROFIT_THRESHOLD = 0.96
# float slack vs gamma/mark rounding; also helps when take_profit is 0.99 and mark is 0.988
TAKE_PROFIT_COMPARE_SLACK = 0.002

# ─── Momentum fast exit: ABSOLUTE price drop from peak in rolling window ──
# absolute YES points peak→now (see momentum_engine). not percent.
MOMENTUM_FAST_EXIT_DROP = 0.30
MOMENTUM_WINDOW_SECONDS = 900
MOMENTUM_COMPETITOR_SURGE = 0.25
# kept low so a fresh market that just jumped 0.1 → 0.7 in 2 ticks still
# qualifies for double-momentum entry (was 3 → blocked late entries on big moves).
MOMENTUM_MIN_SAMPLE_POINTS = 2

# bot momentum entry: only sample windows ≤ this length (seconds). 900 = 15m.
MOMENTUM_ENTRY_MAX_WINDOW_SEC = 900.0
# --- leader yield (אותו חלון W כמו מומנטום) ---
# מינימום "old_yes" לבucket אח בכדי שייחשב ל-sibling לבדיקת נפילה (מסנן 0/רעש).
# מקביל ב-runtime: leader_yield_min_leader_old_price (data/runtime_config.json)
LEADER_YIELD_MIN_LEADER_OLD_PRICE = 0.05
# תנאי (A): לפחות אחד מהאחים יורד בנקודות או באחוז מ-yes הישן שלו.
# מקביל: leader_yield_fall_min_abs_pts, leader_yield_fall_min_frac
LEADER_YIELD_FALL_MIN_ABS_PTS = 0.25
LEADER_YIELD_FALL_MIN_FRAC = 0.45
# תנאי (B) קולקטיבי: סכום נפילות (בנקודות) או שבר משוקלל מסכום old_yes של כל האחים המתאימים.
# מקביל: collective_fall_min_abs_pts, collective_fall_min_frac
COLLECTIVE_FALL_MIN_ABS_PTS = 0.25
COLLECTIVE_FALL_MIN_FRAC = 0.45

# fast (short) momentum window — runs alongside the 15m window.
# either window passing = momentum signal qualifies.
# motivation: 15m can be too diluted on rapid moves; 5m catches fresh surges.
MOMENTUM_FAST_WINDOW_SECONDS = 300
DOUBLE_MOMENTUM_FAST_WINDOW_SECONDS = 300

# ─── Trailing stop ────────────────────────────────────────────────────
# protects unrealized profit when price has moved up significantly.
# example: entry 0.50 → price reaches 0.70 (>=entry+0.20 activation) →
# trailing level becomes entry+0.10 = 0.60 (locks 0.10 of gain).
TRAILING_STOP_ENABLED = True
TRAILING_STOP_ACTIVATION_GAIN = 0.20
TRAILING_STOP_LOCK_GAIN = 0.10

# ─── Time-decay exit ─────────────────────────────────────────────────
TIME_DECAY_HOURS = 2.0
# min gain vs entry as absolute YES price points (not % of entry)
TIME_DECAY_MIN_GAIN = 0.02
TIME_DECAY_MAX_PRICE = 0.85

# ═══════════════════════════════════════════════════════════════════════
# ANTI-CHURN
# ═══════════════════════════════════════════════════════════════════════

# per-market churn control
CHURN_MAX_STOP_CYCLES = 1
CHURN_COOLDOWN_SEC = 1200

# event-level churn: block entire event after repeated losses
CHURN_EVENT_MAX_LOSSES = 2  # losses on same gamma event before blocking
CHURN_EVENT_COOLDOWN_SEC = 1800  # block event for 30 minutes
CHURN_EVENT_LOSS_1_COOLDOWN_SEC = 900
CHURN_EVENT_LOSS_2_COOLDOWN_SEC = 3600
LEADER_SWITCH_WINDOW_SEC = 600
LEADER_SWITCH_MAX_COUNT = 3
UNSTABLE_EVENT_COOLDOWN_SEC = 1800

# ═══════════════════════════════════════════════════════════════════════
# FAST EXIT WATCHER — background thread polling CLOB every N seconds
# ═══════════════════════════════════════════════════════════════════════
FAST_EXIT_WATCHER_INTERVAL_SEC = 2

# ═══════════════════════════════════════════════════════════════════════
# ENTRY GATES & FILTERS
# ═══════════════════════════════════════════════════════════════════════

# earliest local hour to place new buys (city local TZ from title; 0–23)
BUY_EARLIEST_HOUR = 11
# skip new buys when title event date is after today's date in Asia/Jerusalem (report TZ)
BUY_BLOCK_EVENT_DATE_AFTER_ISRAEL_TODAY = True
# max open positions at once — limits total portfolio risk
MAX_CONCURRENT_POSITIONS = 7

MIN_LEAD_OVER_RUNNER_UP = 0.15
# normal BUY only (when enable_competition_filter): min (candidate YES − runner-up YES).
# momentum / double_momentum skip this gate entirely.
MIN_MARKET_YES_LEAD_GAP_NORMAL = 0.50
ENABLE_COMPETITION_FILTER = True

# cold momentum: rank 1 by market YES + lead vs runner-up ≥ min_lead_over_runner_up
MOMENTUM_ENTRY_MAX_RANK = 1
# momentum switch / defensive exit: leader YES >= held_mark + this gap (0.15 = +15pp)
MOMENTUM_SWITCH_ABOVE_HELD_GAP = 0.15
# print [momentum_eval] json per evaluate_entry (noisy; turn on when tuning)
MOMENTUM_DECISION_DEBUG_LOG = False

# portfolio Telegram only: second momentum line vs same price_samples (not used for exits)
PORTFOLIO_TELEGRAM_MOMENTUM_LONG_SEC = 7200

# after a "below CLOB min" sell skip we set a long cooldown — must not block risk exits
SELL_BYPASS_MIN_COOLDOWN_REASONS = frozenset(
    {
        "take-profit",
        "stop-loss",
        "trailing-stop",
        "momentum-stop-loss",
        "competitor-surge",
        "time-decay",
        "momentum-peer-drop",
        "flow-peer-surge",
        "research-model-flip",
        "peer-yes-surge",
        "momentum-switch-out",
        "momentum-competitor-dominant",
    }
)

# stop-loss category labels — emitted by exit detection so logs/telegram/reports
# can distinguish hard-floor vs entry-relative vs trailing vs momentum SL.
SL_CATEGORY_ABSOLUTE = "SL_ABSOLUTE"
SL_CATEGORY_RELATIVE = "SL_RELATIVE"
SL_CATEGORY_TRAILING = "SL_TRAILING"
SL_CATEGORY_MOMENTUM = "SL_MOMENTUM"
# crash-from-running-peak vs entry (fraction of peak lost); orthogonal to SL bar.
SL_CATEGORY_CRASH_PEAK = "SL_CRASH_PEAK"

# drop from highest_seen_price vs peak; 0 disables. catches cliff when window DD thin.
CRASH_DROP_PCT_FROM_PEAK = 0.50
# skip crash-from-peak for this long after entry_ts (API avg can jump; thin book whipsaws).
CRASH_FROM_PEAK_GRACE_SEC_AFTER_ENTRY = 180.0

# on limit submit/network failure before a fillable order id exists, bump notional (+step) up to max.
# unfilled-timeout (cancel) does not escalate — only submit-side failure.
BUY_ESCALATE_NOTIONAL_ON_SUBMIT_FAIL = True
BUY_ESCALATE_NOTIONAL_STEP_USD = 1.0

# ═══════════════════════════════════════════════════════════════════════
# SIZING
# ═══════════════════════════════════════════════════════════════════════

DEFAULT_ORDER_SIZE = 1.0
MAX_TRADE_FRACTION_OF_CASH = 0.90
MAX_BUY_NOTIONAL_USD = 5.0
MIN_ORDER_NOTIONAL_USD = 1.0
# never allocate buys from this portion of free cash (runtime + UI override)
CASH_RESERVE_USD = 0.0

# ═══════════════════════════════════════════════════════════════════════
# LEGACY MOMENTUM (kept for backward compat with price sample infra)
# ═══════════════════════════════════════════════════════════════════════

ENABLE_MOMENTUM = True
MOMENTUM_WINDOW_MIN = 10
MOMENTUM_RISE = 0.15
MOMENTUM_PEER_DROP = 0.10
PRICE_SAMPLE_RETENTION_DAYS = 7
PRICE_SAMPLE_MAX_ENTRIES_PER_MARKET = 240
TRADE_BUY_LOCK_TTL_SEC = 120
TRADE_SELL_LOCK_TTL_SEC = 120
TRADE_RECENT_SELL_TTL_SEC = 180

# ═══════════════════════════════════════════════════════════════════════
# FORECAST & RESEARCH
# ═══════════════════════════════════════════════════════════════════════

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

ENABLE_OPENWEATHER_FORECAST = False
ENABLE_FLOW_SAMPLING = True
FLOW_MAX_EVENTS_PER_TICK = 8
ENABLE_FLOW_PEER_EXIT = True
FLOW_PEER_WINDOW_SEC = 600
FLOW_PEER_SURGE_DROP = 0.12
FLOW_PEER_SURGE_RISE = 0.10

FORECAST_GATE_BUY = True
# °C slack for forecast_gate_buy: larger = fewer "contradicts bracket" skips.
# exact buckets use margin + 0.5 in forecast_contradicts_strongly (see forecast/parse_title.py).
FORECAST_CONTRADICT_MARGIN_C = 2.5
# for forecast_supports_yes on EXACT buckets: |forecast − threshold| within this counts as "support"
FORECAST_EXACT_BUCKET_SUPPORT_SLACK_C = 2.5
FORECAST_REDUCE_USD_IF_WEAK = True
FORECAST_WEAK_SIZE_FACTOR = 0.75

# research calibration file under data/research/; optional model exit
RESEARCH_EXIT_ON_MODEL_FLIP = False

# model-implied P(YES) vs CLOB gate (decision engine)
RESEARCH_EDGE_GATE_BUY = False
RESEARCH_MIN_EDGE = 0.08
# added on top of min_edge plus taker fee drag (probability-scale cushion)
RESEARCH_MIN_EDGE_AFTER_FEES_ADD = 0.0
# 0 = derive sigma from calibration MAE; else fixed Gaussian sigma (°C), clamped 0.5..8
RESEARCH_SIGMA_C = 0.0
# Polymarket weather taker fee rate (see POLY_FEES.MD) for drag estimate
RESEARCH_WEATHER_TAKER_FEE_RATE = 0.05
# when implied P(YES) is strictly above this floor, add boost to edge vs raw (implied−clob).
# set floor <= 0 in runtime to disable. boost = (implied − floor) × mult added on top of raw edge.
RESEARCH_EDGE_IMPLIED_SOFT_FLOOR = 0.30
RESEARCH_EDGE_IMPLIED_SOFT_BOOST = 1.0
# 0 = disabled; else no new buys after this local hour (city TZ)
# 23 = last local hour 23:xx; 24 = no upper bound (only BUY_EARLIEST applies)
BUY_LATEST_LOCAL_HOUR = 24

# decision engine: do not buy dominant-crowd buckets or ultra-weak model buckets
MAX_MARKET_PROB_FOR_BUY = 0.99
MIN_MODEL_PROB_FOR_BUY = 0.0
MAX_POSITIONS_PER_EVENT = 1
# skip BUY if implied P(YES) for this strike is below this (flat tail / low conviction)
DECISION_MIN_MODEL_PEAK_PROB = 0.0

# peer YES surge — sibling bucket momentum (same gamma event)
ENABLE_PEER_SURGE_EXIT = True
PEER_SURGE_WINDOW_MIN = 10
PEER_SURGE_RISE_THRESHOLD = 0.20
PEER_SURGE_EVENT_COOLDOWN_SEC = 1200
PEER_SURGE_SKIP_BUY_ENABLED = False
PEER_SURGE_SKIP_BUY_RISE_THRESHOLD = 0.25
RESEARCH_EDGE_SCALE_SIZE = False
RESEARCH_EDGE_SIZE_SLOPE = 2.0
RESEARCH_EDGE_SIZE_CAP_MULT = 1.35
RESEARCH_CROWD_SOFT_MATCH = False
RESEARCH_CROWD_SOFT_BAND = 0.04
RESEARCH_CROWD_SOFT_EDGE_FACTOR = 0.75
RESEARCH_CROWD_DISAGREE_GAP = 0.08
RESEARCH_CROWD_DISAGREE_EXTRA_EDGE = 0.03
# min seconds between repeated Telegram "research skip" for same market+reason
RESEARCH_SKIP_TELEGRAM_COOLDOWN_SEC = 600
# when false, no Telegram for decision-engine BUY skips (still logged to console)
DECISION_SKIP_TELEGRAM_NOTIFY = False

# ═══════════════════════════════════════════════════════════════════════
# LIMIT-ORDER-FIRST EXECUTION
# ═══════════════════════════════════════════════════════════════════════

# when true, BUY orders are submitted as GTC limit at controlled price.
# market orders pay taker fees + slippage; limit at best ask captures the
# same fill probability without crossing more than necessary.
LIMIT_ORDERS_ENABLED = True
# how long to wait for a limit BUY to fill before cancelling.
# kept short so we don't chase price endlessly on missed fills.
# 8s was too tight in practice: thin books + scan interval meant many GTC cancels.
BUY_LIMIT_ORDER_TIMEOUT_SEC = 15
# limit SELL timeout — used for non-emergency exits (take-profit, time-decay).
SELL_LIMIT_ORDER_TIMEOUT_SEC = 5
# emergency exits (stop-loss, momentum-stop-loss, etc.) prioritize fill speed
# over maker rebate. when true, these go straight to market.
EMERGENCY_EXIT_ALLOW_MARKET_ORDER = True
# offset added to best ask for limit BUY (positive = chase up; 0 = at best ask).
BUY_LIMIT_PRICE_OFFSET = 0.0
# offset subtracted from best bid for limit SELL (positive = below bid; faster fill).
SELL_LIMIT_PRICE_OFFSET = 0.005
# require a real fill before we record the position as held.
# protects against state.json drift when limit BUY times out unfilled.
REQUIRE_FILL_BEFORE_STATE_BUY = True
# emergency SELL reasons — these can use market order even when limit-orders enabled.
EMERGENCY_SELL_REASONS = frozenset(
    {
        "stop-loss",
        "momentum-stop-loss",
        "trailing-stop",
        "competitor-surge",
        "momentum-competitor-dominant",
        "peer-yes-surge",
    }
)

# ═══════════════════════════════════════════════════════════════════════
# TELEGRAM FAILED-EXIT DEDUPLICATION
# ═══════════════════════════════════════════════════════════════════════

# notify once per (market_id, exit_reason) and suppress repeats inside cooldown.
# avoids spam when a stop-loss / take-profit sell keeps failing every tick.
TELEGRAM_FAILED_EXIT_DEDUPE_ENABLED = True
TELEGRAM_FAILED_EXIT_COOLDOWN_SEC = 900

# ═══════════════════════════════════════════════════════════════════════
# RICH TRADE LOGGING / TELEGRAM VERBOSITY
# ═══════════════════════════════════════════════════════════════════════

# when true, log full reason/trigger metadata (window, abs/pct rise, prices).
# adds a few extra columns to trade CSV; safe to disable for compact logs.
TRADE_LOG_FULL_REASON_ENABLED = True
# when true, BUY/SELL telegram messages include explicit category + trigger
# context (e.g. "Triggered by 5m Fast Window (+0.25 / 700%)").
TELEGRAM_VERBOSE_TRADE_REASON = True

# לאחר כל BUY (בוט או זיהוי MANUAL בסנכרון): PNG של כל bracket באירוע ה-Gamma (כמו plot_recent_prices)
# ושליחה ל-Telegram ברקע (sendPhoto). כיבוי: post_buy_plot_enabled=false ב-runtime_config.json
POST_BUY_PLOT_ENABLED = True
# חלון זמן לגרף בדקות; מקביל: post_buy_plot_window_min
POST_BUY_PLOT_WINDOW_MIN = 15.0
# נושא (forum thread) בקבוצת טלגרם — מספר >0 כמו ב-Telegram UI; 0 = צ'אט ראשי / ללא message_thread_id
# מקביל: telegram_message_thread_id (אפשר גם TELEGRAM_MESSAGE_THREAD_ID ב-env אם נוסיף ב-bot_runner)
TELEGRAM_MESSAGE_THREAD_ID = 0

# ═══════════════════════════════════════════════════════════════════════
# EXCHANGE / CLOB
# ═══════════════════════════════════════════════════════════════════════

YES_LABEL = "YES"
STATUS_CLOSED = frozenset({"closed", "claimable", "resolved"})
DUST_SHARES_EPS = 1e-6
# when YES on exchange is just under CLOB min (e.g. 4.85 vs 5), market-buy a sliver then sell all
CLOB_SELL_TOPUP_BUFFER_SHARES = 0.25
CLOB_SELL_TOPUP_SLIPPAGE_MULT = 1.35
CLOB_SELL_TOPUP_MIN_USD = 0.35
CLOB_SELL_TOPUP_MAX_USD = 15.0
CLOB_SELL_TOPUP_HARD_USD_CAP = 28.0
# extra topup rounds in close_position when exchange size still under CLOB min
CLOB_SELL_TOPUP_MAX_ROUNDS = 6

# ═══════════════════════════════════════════════════════════════════════
# TERMINAL FORMATTING
# ═══════════════════════════════════════════════════════════════════════

TERM_RESET = "\033[0m"
TERM_BOLD = "\033[1m"
TERM_DIM = "\033[2m"
TERM_GREEN = "\033[32m"
TERM_RED = "\033[31m"
TERM_YELLOW = "\033[33m"
TERM_CYAN = "\033[36m"
