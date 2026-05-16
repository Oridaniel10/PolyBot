from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
STATE_FILE = PROJECT_ROOT / "state.json"
ENV_FILE = PROJECT_ROOT / ".env"
RUNTIME_CONFIG_FILE = DATA_DIR / "runtime_config.json"
PNL_LEDGER_FILE = DATA_DIR / "pnl_ledger.jsonl"
BLACKLIST_FILE = DATA_DIR / "blacklist_day.json"
CITY_BUY_EARLIEST_HOURS_FILE = DATA_DIR / "city_buy_earliest_hour.json"
PRICE_SAMPLES_DIR = DATA_DIR / "price_samples"
FORECAST_CACHE_FILE = DATA_DIR / "forecast_cache.json"
# optional calibration JSON (offline tools + sigma_from_mae when sigma_c_setting is 0)
RESEARCH_CALIBRATION_LATEST_FILE = DATA_DIR / "research" / "calibration_latest.json"
# dashboard /api/forecast/preview: treat on-disk cache as fresh under this age
WEATHER_PREVIEW_CACHE_MAX_AGE_SEC = 360
UI_DIST_DIR = PROJECT_ROOT / "ui" / "dist"
# mandatory Telegram portfolio STATUS (full snapshot) at least this often
PORTFOLIO_STATUS_HEARTBEAT_SEC = 3600

TIMEZONE = "Asia/Jerusalem"
REPORT_HOURS = (10, 13, 16, 19)
SCAN_INTERVAL_SECONDS = 20
# short cooldown — we want to retry topup soon, not block exits for a full day.
# critical exits (stop-loss/take-profit/etc.) bypass via SELL_BYPASS_MIN_COOLDOWN_REASONS.
SELL_BELOW_MIN_COOLDOWN_SEC = 300
# repeat Telegram for same market stuck on below_min_order_size at most this often
SELL_BELOW_MIN_TELEGRAM_COOLDOWN_SEC = 1800

# ═══════════════════════════════════════════════════════════════════════
# BUY / SELL thresholds — grouped by entry type.
# UNIT KEY: values marked [PRICE] are absolute YES price points (0.0–1.0).
#           values marked [FRAC]  are fractional multipliers (e.g. 0.50 = 50%).
#           values marked [PCT]   are fractional price changes (1.0 = +100%).
# ═══════════════════════════════════════════════════════════════════════

# ─── NORMAL: buy band + sell stop-loss ────────────────────────────────
# [PRICE] buy band for normal (non-momentum) entries.
# Set both to 1.1 to effectively DISABLE normal entries (bot only trades momentum).
# To re-enable: e.g. BUY_MIN_THRESHOLD=0.65, BUY_MAX_THRESHOLD=0.84
BUY_MIN_THRESHOLD = 1.1
BUY_MAX_THRESHOLD = 1.1
BUY_DISABLE_PRICE_BAND = False

# [PRICE] hard stop-loss floor for normal entries.
# If live YES price falls below this absolute level → sell immediately.
# Example: 0.20 means sell at 0.20 no matter what entry price was.
STOP_LOSS_NORMAL = 0.20
# [FRAC] entry-relative stop for normal. Sell if price drops this fraction from entry.
# Example: entry 0.80, drop_pct=0.30 → relative stop = 0.80*(1-0.30) = 0.56.
# Effective stop = max(STOP_LOSS_NORMAL, entry*(1-drop_pct)).
STOP_LOSS_NORMAL_ENTRY_DROP_PCT = 0.30

# ─── NORMAL_WINNER: end-of-day high-conviction "collect winners" entry ───────
# Buy only when YES has been stably above a floor for ≥ min window AND is now at
# or above the entry price.  Take profit is much higher than normal (near resolve).
#
# Entry gate:
#   1. current YES in [NORMAL_WINNER_MIN_ENTRY, NORMAL_WINNER_MAX_ENTRY] (0.92–0.96)
#   2. the *contiguous price tail* ending at now has stayed >= NORMAL_WINNER_STABILITY_FLOOR
#      (0.75) for at least NORMAL_WINNER_STABILITY_MIN_SEC (30 min), with no gap > 2 min
#      between samples (ring buffer; up to NORMAL_WINNER_STABILITY_MAX_SEC lookback)
#      and at least MOMENTUM_MIN_SAMPLE_POINTS samples exist in the buffer.
#
# Exit: take-profit when YES >= NORMAL_WINNER_TAKE_PROFIT (0.9997) — market is
#       about to resolve YES.  SL is tight: stop if price drops from entry.
NORMAL_WINNER_MIN_ENTRY = 0.92  # buy band floor
NORMAL_WINNER_MAX_ENTRY = 0.96  # buy band ceiling (avoid buying right at resolve)
NORMAL_WINNER_TAKE_PROFIT = 0.9987  # sell (market resolving YES)
NORMAL_WINNER_STABILITY_FLOOR = 0.75  # price must have been above this throughout
NORMAL_WINNER_STABILITY_MIN_SEC = 900  # minimum contiguous tail above floor (15 min)
NORMAL_WINNER_STABILITY_MAX_SEC = 7200  # maximum lookback considered (2h)
STOP_LOSS_NORMAL_WINNER = 0.75  # hard floor — if it dumps hard, exit
STOP_LOSS_NORMAL_WINNER_ENTRY_DROP_PCT = 0.20  # also exit if drops >20% from entry
NORMAL_WINNER_ENABLED = True

# ─── MANUAL / UI: custom stop loss for user trades ───────────────────
# [PRICE] hard stop floor for manual/UI trades. Higher than momentum because
# manually entered positions may be at lower probability.
STOP_LOSS_MANUAL = 0.20
# [FRAC] entry-relative drop before manual stop triggers. Same logic as normal.
STOP_LOSS_MANUAL_ENTRY_DROP_PCT = 0.30


# ─── MOMENTUM: buy on surge in any window (1m–15m) + sell stop-loss ───
# [PRICE] minimum ABSOLUTE YES price rise to qualify for momentum entry.
# This is the price in [0,1] range — NOT a percent.
# Example: 0.20 means YES must rise by 0.20 points (e.g. 0.30 → 0.50 qualifies).
# To make it EASIER to enter: lower this value (e.g. 0.10).
# To make it HARDER to enter: raise it (e.g. 0.20).
MOMENTUM_ENTRY_RISE = 0.20
# [PCT] alternative rise trigger — fractional multiplier (1.0 = price doubled = +100%).
# EITHER absolute OR percent must pass (whichever is easier).
# Example: 1.0 means price must have doubled (e.g. 0.05 → 0.10 passes on pct gate).
# To require bigger % move: raise (e.g. 2.0 = price tripled).
MOMENTUM_PCT_RISE = 1.0
# [PRICE] minimum window-start YES price for the percent gate to count.
# Filters out noise (e.g. 0.001 → 0.003 looks like +200% but is meaningless).
# 0.0 = allow all; 0.05 = ignore entries starting below 0.05.
MOMENTUM_MIN_START_PRICE = 0.05
# [PRICE] live YES band for momentum entry at decision time.
# Bot won't buy if current price is below MOMENTUM_MIN_PRICE or above MOMENTUM_MAX_ENTRY.
# Example: 0.20 min means don't buy if YES is < 0.20 (too cheap = too risky).
MOMENTUM_MIN_PRICE = 0.55
MOMENTUM_MAX_ENTRY = 0.85
# [PRICE] hard stop-loss floor for momentum entries.
# Momentum entries are more aggressive, so stop floor can be lower.
STOP_LOSS_MOMENTUM = 0.35
# [FRAC] entry-relative drop before momentum stop triggers.
# Example: entry 0.40, drop_pct=0.50 → stop at 0.20. Effective = max(0.10, 0.20).
STOP_LOSS_MOMENTUM_ENTRY_DROP_PCT = 0.50

# ─── DOUBLE MOMENTUM: stronger surge + wider band + sell stop-loss ────
# [PRICE] minimum ABSOLUTE YES price rise for double-momentum entry.
# Higher bar than standard momentum — rewards bigger, faster moves.
# Example: 0.40 means price must jump 0.40 points (e.g. 0.20 → 0.60).
DOUBLE_MOMENTUM_ENTRY_RISE = 0.40
# [PCT] fractional alternative for double momentum (9.0 = ninefold = +900%).
DOUBLE_MOMENTUM_PCT_RISE = 9.0
# [PRICE] minimum window-start price for double momentum pct gate.
DOUBLE_MOMENTUM_MIN_START_PRICE = 0.05
# [PRICE] live YES band for double momentum entry (same band as standard momentum here).
DOUBLE_MOMENTUM_MIN_PRICE = 0.40
DOUBLE_MOMENTUM_MAX_PRICE = 0.85
# [PRICE] stop-loss floor for double momentum entries.
STOP_LOSS_DOUBLE_MOMENTUM = 0.20
# [FRAC] entry-relative drop for double momentum stop.
STOP_LOSS_DOUBLE_MOMENTUM_ENTRY_DROP_PCT = 0.50

# ═══════════════════════════════════════════════════════════════════════
# EXIT THRESHOLDS
# ═══════════════════════════════════════════════════════════════════════

TAKE_PROFIT_THRESHOLD = 0.94
# float slack vs gamma/mark rounding; also helps when take_profit is 0.99 and mark is 0.988
TAKE_PROFIT_COMPARE_SLACK = 0.002

# ─── Momentum fast exit: ABSOLUTE price drop from peak in rolling window ──
# [PRICE] if price drops this many absolute YES points from its peak inside
# MOMENTUM_WINDOW_SECONDS → trigger emergency exit.
# Example: 0.30 means peak was 0.80 and now it's 0.50 → exit.
# To exit SOONER on smaller drops: lower (e.g. 0.15).
# To tolerate bigger swings: raise (e.g. 0.40).
MOMENTUM_FAST_EXIT_DROP = 0.35
MOMENTUM_FAST_EXIT_ENABLED = False
# [SECONDS] rolling window for fast exit drawdown + competitor surge checks.
MOMENTUM_WINDOW_SECONDS = 3600
# [PRICE] if any SIBLING bucket in the same event rises this many YES points
# inside MOMENTUM_WINDOW_SECONDS → our position exits (money flowing to sibling).
# Example: 0.35 means a sibling jumping from 0.30 to 0.65 triggers our exit.
MOMENTUM_COMPETITOR_SURGE = 0.30
# minimum price samples needed before momentum signal counts.
# 3 = requires signal to appear in at least 3 windows (~30s, 60s, 90s) — filters single-tick spikes.
MOMENTUM_MIN_SAMPLE_POINTS = 3

# [SECONDS] max window for the 1m–60m candidate grid.
# The bot checks 1-15m (every 1m), 20-60m (every 5m), up to this cap.
MOMENTUM_ENTRY_MAX_WINDOW_SEC = 3600.0

# ─── Leader-yield gate: sibling drop required for momentum entry ──────
# At least one sibling must fall (condition A) OR siblings must fall collectively
# (condition B) — independently of the window used for our rise check.
#
# [PRICE] ignore siblings whose window-start YES was below this (noise filter).
LEADER_YIELD_MIN_LEADER_OLD_PRICE = 0.03
# [PRICE] condition (A) individual sibling: must drop at least this many YES points.
# Example: 0.40 means sibling must fall from 0.60 to ≤0.20 to qualify.
# To require BIGGER sibling drops: raise (e.g. 0.35).
# To allow SMALLER sibling drops: lower (e.g. 0.15).
LEADER_YIELD_FALL_MIN_ABS_PTS = 0.40  # ירידה מוחלטת של 0.40
# [FRAC] condition (A) alternative — sibling must drop this fraction of its own old YES.
# Example: 0.60 means sibling at 0.60 must fall to ≤0.36 (dropped 60% of 0.60).
LEADER_YIELD_FALL_MIN_FRAC = 0.41
# [PRICE] condition (B) collective: SUM of all sibling drops must be at least this.
COLLECTIVE_FALL_MIN_ABS_PTS = 0.40  # ירידה מוחלטת של 0.40
# [FRAC] condition (B) alternative — total drop / sum(sibling old YES) ≥ this.
COLLECTIVE_FALL_MIN_FRAC = 0.41  # ירידה חלקית של 0.60 משמע באחוזים משמע באחוזים של 0.60

# [SECONDS] fast momentum window — checked alongside the 15m window.
# Either window passing qualifies. Good for catching rapid surges.
MOMENTUM_FAST_WINDOW_SECONDS = 300
DOUBLE_MOMENTUM_FAST_WINDOW_SECONDS = 300

# ─── Trailing stop ────────────────────────────────────────────────────
# [PRICE] once price rises this much above entry, trailing stop activates.
# Example: 0.20 means entry 0.50 → trailing unlocks when price reaches 0.70.
TRAILING_STOP_ENABLED = True
TRAILING_STOP_ACTIVATION_GAIN = 0.30
# [PRICE] once trailing stop is active, lock stop at entry + this amount.
# Example: 0.10 means stop locks at 0.50+0.10 = 0.60 (protecting 0.10 gain).
TRAILING_STOP_LOCK_GAIN = 0.05

# ─── Time-decay exit ─────────────────────────────────────────────────
# [HOURS] exit if held longer than this AND not profitable enough.
TIME_DECAY_HOURS = 2.0
# [PRICE] minimum absolute YES gain vs entry to AVOID the decay exit.
# Example: 0.02 means entry 0.50 → must be above 0.52 after 2h to not decay.
TIME_DECAY_MIN_GAIN = 0.02
# [PRICE] decay exit only applies when mark is below this (near take-profit = skip).
TIME_DECAY_MAX_PRICE = 0.85

# ─── momentum debug alert ────────────────────────────────────────────
# When enabled, sends a Telegram alert + event plot whenever ANY sibling market
# moves significantly in the configured window. Useful to see live momentum.
#
# MOMENTUM_ALERT_ENABLED   [BOOL]  master on/off switch
# MOMENTUM_ALERT_MIN_ABS   [PRICE] minimum absolute move to trigger
#   Example: 0.10 → alert if price moves ±0.10 points (e.g. 0.20→0.31 or 0.20→0.09)
# MOMENTUM_ALERT_MIN_PCT   [FRAC]  minimum fractional move to trigger
#   Example: 0.30 → alert if price moves ±30% (e.g. 0.40→0.52 qualifies)
#   Either abs OR pct can trigger — whichever is hit first.
# MOMENTUM_ALERT_WINDOW_SEC [SECONDS] lookback window for the check
#   Use 900 for 15-min momentum, up to 7200 for 2-hour momentum.
# MOMENTUM_ALERT_COOLDOWN_SEC [SECONDS] min gap before re-alerting same market
MOMENTUM_ALERT_ENABLED = False
MOMENTUM_ALERT_MIN_ABS = 0.2
MOMENTUM_ALERT_MIN_PCT = 0.3
MOMENTUM_ALERT_WINDOW_SEC = 900.0
MOMENTUM_ALERT_COOLDOWN_SEC = 300.0

# ─── 2-hour stagnation stop-loss ─────────────────────────────────────
# Exits a position if price has been stuck (no meaningful rise in 2 hours)
# AND price is below entry. Prevents holding dead positions.
# Enable/disable via stagnation_sl_enabled in runtime_config.json.
STAGNATION_SL_ENABLED = False
# [HOURS] lookback window to measure "has price risen at all?"
STAGNATION_SL_WINDOW_HOURS = 2.0
# [FRAC] minimum fractional rise in the window to KEEP the position.
# Example: 0.05 = price must have risen at least 5% from 2h ago (0.40 → 0.42+).
# If price hasn't risen this much in 2h AND is below entry → exit.
STAGNATION_SL_MIN_RISE_PCT = 0.05

# ═══════════════════════════════════════════════════════════════════════
# ANTI-CHURN
# ═══════════════════════════════════════════════════════════════════════

# per-market churn control
CHURN_MAX_STOP_CYCLES = 1
CHURN_COOLDOWN_SEC = 1800

# event-level churn: block entire event after repeated losses
CHURN_EVENT_MAX_LOSSES = 2  # losses on same gamma event before blocking
CHURN_EVENT_COOLDOWN_SEC = 1800  # block event for 30 minutes
CHURN_EVENT_LOSS_1_COOLDOWN_SEC = 900
CHURN_EVENT_LOSS_2_COOLDOWN_SEC = 3600
LEADER_SWITCH_WINDOW_SEC = 600
LEADER_SWITCH_MAX_COUNT = 1
UNSTABLE_EVENT_COOLDOWN_SEC = 1800

# ═══════════════════════════════════════════════════════════════════════
# FAST EXIT WATCHER — background thread polling CLOB every N seconds
# ═══════════════════════════════════════════════════════════════════════
FAST_EXIT_WATCHER_INTERVAL_SEC = 2

# ═══════════════════════════════════════════════════════════════════════
# ENTRY GATES & FILTERS
# ═══════════════════════════════════════════════════════════════════════

# earliest local hour to place new buys (city local TZ from title; 0–23)
BUY_EARLIEST_HOUR = 15
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

DEFAULT_ORDER_SIZE = 4.0
MAX_TRADE_FRACTION_OF_CASH = 0.90
# bot order cap (USD); runtime max_buy_notional_usd cannot exceed this value
MAX_BUY_NOTIONAL_USD = 5.0
MIN_ORDER_NOTIONAL_USD = 4.0
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
# FLOW SAMPLING / PEER EXITS
# ═══════════════════════════════════════════════════════════════════════

ENABLE_FLOW_SAMPLING = True
FLOW_MAX_EVENTS_PER_TICK = 8
ENABLE_FLOW_PEER_EXIT = True
FLOW_PEER_WINDOW_SEC = 600
FLOW_PEER_SURGE_DROP = 0.12
FLOW_PEER_SURGE_RISE = 0.10

# ═══════════════════════════════════════════════════════════════════════
# TIME / MODEL CEILING GATES (only when evaluate_entry receives consensus °C)
# ═══════════════════════════════════════════════════════════════════════

# 0 = disabled; else no new buys after this local hour (city TZ)
# 23 = last local hour 23:xx; 24 = no upper bound (only BUY_EARLIEST applies)
BUY_LATEST_LOCAL_HOUR = 24
MAX_MARKET_PROB_FOR_BUY = 0.99
MIN_MODEL_PROB_FOR_BUY = 0.0
MAX_POSITIONS_PER_EVENT = 1
DECISION_MIN_MODEL_PEAK_PROB = 0.0

# peer YES surge — sibling bucket momentum (same gamma event)
ENABLE_PEER_SURGE_EXIT = True
PEER_SURGE_WINDOW_MIN = 10
PEER_SURGE_RISE_THRESHOLD = 0.20
PEER_SURGE_EVENT_COOLDOWN_SEC = 1200
PEER_SURGE_SKIP_BUY_ENABLED = False
PEER_SURGE_SKIP_BUY_RISE_THRESHOLD = 0.25

# min seconds between repeated Telegram decision BUY skips for same market+reason
DECISION_SKIP_TELEGRAM_COOLDOWN_SEC = 600
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
POST_BUY_PLOT_WINDOW_MIN = 120.0
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

# ═══════════════════════════════════════════════════════════════════════
# SELL COOLDOWN — 30-min per-market blacklist after stop-loss or manual sell
# ═══════════════════════════════════════════════════════════════════════
# [SECONDS] how long to block re-buying a market after stop-loss or manual sell.
# Take-profit exits do NOT trigger this — only loss/manual exits do.
SELL_COOLDOWN_30M_SEC = 1800

# ═══════════════════════════════════════════════════════════════════════
# MANUAL SYNC — minimum entry price to register a synced position
# ═══════════════════════════════════════════════════════════════════════
# [PRICE] external positions with avg_price below this are treated as dust
# and not registered as manual trades (prevents phantom 0.01 CSV rows).
MANUAL_SYNC_MIN_ENTRY_PRICE = 0.10
# [PRICE] sync_portfolio reclaims `recent_buy_attempts` as a bot late-fill only
# if Data-API avg_price is within this many points of the attempt's intended_price.
# wider gaps mean a manual site buy — do not inherit bot entry_type / SL / fast-exit.
LATE_FILL_RECLAIM_MAX_AVG_VS_INTENDED = 0.04

# ═══════════════════════════════════════════════════════════════════════
# PERSISTENT LEADER — new buy trigger: first place for 2h + momentum jump
# ═══════════════════════════════════════════════════════════════════════
# [SECONDS] lookback window to check if market held #1 rank continuously.
PERSISTENT_LEADER_LOOKBACK_SEC = 7200.0
# [FRAC] minimum fraction of sample points where market was #1 to qualify.
# 0.80 = must have been in first place for 80% of the lookback window.
PERSISTENT_LEADER_MIN_FRACTION = 0.80
# master on/off switch for the persistent-leader entry path.
PERSISTENT_LEADER_ENABLED = True
# dedicated (lower) rise thresholds for the persistent-leader path.
# a market that dominated for 2h only needs a small nudge to qualify.
PERSISTENT_LEADER_ENTRY_RISE = 0.10  # +0.10 pts absolute
PERSISTENT_LEADER_PCT_RISE = 0.25  # OR +25% fractional
PERSISTENT_LEADER_MIN_PRICE = 0.55  # buy band floor for PL path
PERSISTENT_LEADER_MAX_PRICE = 0.85  # buy band ceiling for PL path
