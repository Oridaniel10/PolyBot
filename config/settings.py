import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Set

from config import constants as C

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[assignment]


def _ensure_data_dir() -> None:
    C.DATA_DIR.mkdir(parents=True, exist_ok=True)


def default_runtime_dict() -> Dict[str, Any]:
    return {
        # normal: buy band + sell stop-loss
        "buy_min_threshold": C.BUY_MIN_THRESHOLD,
        "buy_max_threshold": C.BUY_MAX_THRESHOLD,
        "buy_disable_price_band": C.BUY_DISABLE_PRICE_BAND,
        "stop_loss_normal": C.STOP_LOSS_NORMAL,
        "stop_loss_normal_entry_drop_pct": C.STOP_LOSS_NORMAL_ENTRY_DROP_PCT,

        # momentum: 15m entry + sell stop-loss
        # abs example: 0.30 -> 0.50 = +0.20
        # pct example: 0.03 -> 0.33 = +1000%
        "momentum_entry_rise": C.MOMENTUM_ENTRY_RISE,
        "momentum_pct_rise": C.MOMENTUM_PCT_RISE,
        "momentum_min_start_price": C.MOMENTUM_MIN_START_PRICE,
        "momentum_min_price": C.MOMENTUM_MIN_PRICE,
        "momentum_max_entry": C.MOMENTUM_MAX_ENTRY,
        "stop_loss_momentum": C.STOP_LOSS_MOMENTUM,
        "stop_loss_momentum_entry_drop_pct": C.STOP_LOSS_MOMENTUM_ENTRY_DROP_PCT,

        # double momentum: stronger 15m entry + sell stop-loss
        # abs example: 0.30 -> 0.70 = +0.40
        # pct example: 0.02 -> 0.22 = +1000%
        "double_momentum_entry_rise": C.DOUBLE_MOMENTUM_ENTRY_RISE,
        "double_momentum_pct_rise": C.DOUBLE_MOMENTUM_PCT_RISE,
        "double_momentum_min_start_price": C.DOUBLE_MOMENTUM_MIN_START_PRICE,
        "double_momentum_min_price": C.DOUBLE_MOMENTUM_MIN_PRICE,
        "double_momentum_max_price": C.DOUBLE_MOMENTUM_MAX_PRICE,
        "stop_loss_double_momentum": C.STOP_LOSS_DOUBLE_MOMENTUM,
        "stop_loss_double_momentum_entry_drop_pct": C.STOP_LOSS_DOUBLE_MOMENTUM_ENTRY_DROP_PCT,

        "take_profit_threshold": C.TAKE_PROFIT_THRESHOLD,
        "min_lead_over_runner_up": C.MIN_LEAD_OVER_RUNNER_UP,
        "enable_competition_filter": C.ENABLE_COMPETITION_FILTER,
        "enable_momentum": C.ENABLE_MOMENTUM,
        "momentum_window_min": C.MOMENTUM_WINDOW_MIN,
        "momentum_rise": C.MOMENTUM_RISE,
        "momentum_peer_drop": C.MOMENTUM_PEER_DROP,
        "churn_max_stop_cycles": C.CHURN_MAX_STOP_CYCLES,
        "churn_cooldown_sec": C.CHURN_COOLDOWN_SEC,
        "blacklist_market_ids": [],
        "scan_interval_seconds": C.SCAN_INTERVAL_SECONDS,
        "min_order_notional_usd": C.MIN_ORDER_NOTIONAL_USD,
        "max_buy_notional_usd": C.MAX_BUY_NOTIONAL_USD,
        "max_trade_fraction_of_cash": C.MAX_TRADE_FRACTION_OF_CASH,
        "dashboard_weather_max_pages": 6,
        "cash_reserve_usd": C.CASH_RESERVE_USD,
        "forecast_digest_enabled": C.FORECAST_DIGEST_ENABLED,
        "forecast_digest_refresh_interval_sec": C.FORECAST_DIGEST_REFRESH_INTERVAL_SEC,
        "forecast_digest_telegram_interval_sec": C.FORECAST_DIGEST_TELEGRAM_INTERVAL_SEC,
        "forecast_digest_interval_sec": C.FORECAST_DIGEST_INTERVAL_SEC,
        "forecast_digest_scope": C.FORECAST_DIGEST_SCOPE,
        "forecast_digest_max_location_groups": C.FORECAST_DIGEST_MAX_LOCATION_GROUPS,
        "enable_openweather_forecast": C.ENABLE_OPENWEATHER_FORECAST,
        "enable_flow_sampling": C.ENABLE_FLOW_SAMPLING,
        "flow_max_events_per_tick": C.FLOW_MAX_EVENTS_PER_TICK,
        "enable_flow_peer_exit": C.ENABLE_FLOW_PEER_EXIT,
        "flow_peer_window_sec": C.FLOW_PEER_WINDOW_SEC,
        "flow_peer_surge_drop": C.FLOW_PEER_SURGE_DROP,
        "flow_peer_surge_rise": C.FLOW_PEER_SURGE_RISE,
        "forecast_gate_buy": C.FORECAST_GATE_BUY,
        "forecast_contradict_margin_c": C.FORECAST_CONTRADICT_MARGIN_C,
        "forecast_exact_bucket_support_slack_c": C.FORECAST_EXACT_BUCKET_SUPPORT_SLACK_C,
        "forecast_reduce_usd_if_weak": C.FORECAST_REDUCE_USD_IF_WEAK,
        "forecast_weak_size_factor": C.FORECAST_WEAK_SIZE_FACTOR,
        "research_exit_on_model_flip": C.RESEARCH_EXIT_ON_MODEL_FLIP,
        "research_edge_gate_buy": C.RESEARCH_EDGE_GATE_BUY,
        "research_min_edge": C.RESEARCH_MIN_EDGE,
        "research_min_edge_after_fees_add": C.RESEARCH_MIN_EDGE_AFTER_FEES_ADD,
        "research_sigma_c": C.RESEARCH_SIGMA_C,
        "research_weather_taker_fee_rate": C.RESEARCH_WEATHER_TAKER_FEE_RATE,
        "research_edge_implied_soft_floor": C.RESEARCH_EDGE_IMPLIED_SOFT_FLOOR,
        "research_edge_implied_soft_boost": C.RESEARCH_EDGE_IMPLIED_SOFT_BOOST,
        "buy_earliest_local_hour": C.BUY_EARLIEST_HOUR,
        "buy_latest_local_hour": C.BUY_LATEST_LOCAL_HOUR,
        "research_edge_scale_size": C.RESEARCH_EDGE_SCALE_SIZE,
        "research_edge_size_slope": C.RESEARCH_EDGE_SIZE_SLOPE,
        "research_edge_size_cap_mult": C.RESEARCH_EDGE_SIZE_CAP_MULT,
        "research_crowd_soft_match": C.RESEARCH_CROWD_SOFT_MATCH,
        "research_crowd_soft_band": C.RESEARCH_CROWD_SOFT_BAND,
        "research_crowd_soft_edge_factor": C.RESEARCH_CROWD_SOFT_EDGE_FACTOR,
        "research_crowd_disagree_gap": C.RESEARCH_CROWD_DISAGREE_GAP,
        "research_crowd_disagree_extra_edge": C.RESEARCH_CROWD_DISAGREE_EXTRA_EDGE,
        "research_skip_telegram_cooldown_sec": C.RESEARCH_SKIP_TELEGRAM_COOLDOWN_SEC,
        "decision_skip_telegram_notify": C.DECISION_SKIP_TELEGRAM_NOTIFY,
        "max_market_prob_for_buy": C.MAX_MARKET_PROB_FOR_BUY,
        "min_model_prob_for_buy": C.MIN_MODEL_PROB_FOR_BUY,
        "max_positions_per_event": C.MAX_POSITIONS_PER_EVENT,
        "decision_min_model_peak_prob": C.DECISION_MIN_MODEL_PEAK_PROB,
        "enable_peer_surge_exit": C.ENABLE_PEER_SURGE_EXIT,
        "peer_surge_window_min": C.PEER_SURGE_WINDOW_MIN,
        "peer_surge_rise_threshold": C.PEER_SURGE_RISE_THRESHOLD,
        "peer_surge_event_cooldown_sec": C.PEER_SURGE_EVENT_COOLDOWN_SEC,
        "peer_surge_skip_buy_enabled": C.PEER_SURGE_SKIP_BUY_ENABLED,
        "peer_surge_skip_buy_rise_threshold": C.PEER_SURGE_SKIP_BUY_RISE_THRESHOLD,
        "momentum_window_seconds": C.MOMENTUM_WINDOW_SECONDS,
        "momentum_fast_window_seconds": C.MOMENTUM_FAST_WINDOW_SECONDS,
        "double_momentum_fast_window_seconds": C.DOUBLE_MOMENTUM_FAST_WINDOW_SECONDS,
        "momentum_fast_exit_drop": C.MOMENTUM_FAST_EXIT_DROP,
        "momentum_competitor_surge": C.MOMENTUM_COMPETITOR_SURGE,
        "time_decay_hours": C.TIME_DECAY_HOURS,
        "time_decay_min_gain": C.TIME_DECAY_MIN_GAIN,
        "time_decay_max_price": C.TIME_DECAY_MAX_PRICE,
        # event-level churn
        "churn_event_max_losses": C.CHURN_EVENT_MAX_LOSSES,
        "churn_event_cooldown_sec": C.CHURN_EVENT_COOLDOWN_SEC,
        "churn_event_loss_1_cooldown_sec": C.CHURN_EVENT_LOSS_1_COOLDOWN_SEC,
        "churn_event_loss_2_cooldown_sec": C.CHURN_EVENT_LOSS_2_COOLDOWN_SEC,
        "leader_switch_window_sec": C.LEADER_SWITCH_WINDOW_SEC,
        "leader_switch_max_count": C.LEADER_SWITCH_MAX_COUNT,
        "unstable_event_cooldown_sec": C.UNSTABLE_EVENT_COOLDOWN_SEC,
        # fast exit watcher
        "fast_exit_watcher_interval_sec": C.FAST_EXIT_WATCHER_INTERVAL_SEC,
        # trailing stop
        "trailing_stop_enabled": C.TRAILING_STOP_ENABLED,
        "trailing_stop_activation_gain": C.TRAILING_STOP_ACTIVATION_GAIN,
        "trailing_stop_lock_gain": C.TRAILING_STOP_LOCK_GAIN,
        # limit-order-first execution
        "limit_orders_enabled": C.LIMIT_ORDERS_ENABLED,
        "buy_limit_order_timeout_sec": C.BUY_LIMIT_ORDER_TIMEOUT_SEC,
        "sell_limit_order_timeout_sec": C.SELL_LIMIT_ORDER_TIMEOUT_SEC,
        "emergency_exit_allow_market_order": C.EMERGENCY_EXIT_ALLOW_MARKET_ORDER,
        "buy_limit_price_offset": C.BUY_LIMIT_PRICE_OFFSET,
        "sell_limit_price_offset": C.SELL_LIMIT_PRICE_OFFSET,
        "require_fill_before_state_buy": C.REQUIRE_FILL_BEFORE_STATE_BUY,
        # telegram dedupe + verbosity
        "telegram_failed_exit_dedupe_enabled": C.TELEGRAM_FAILED_EXIT_DEDUPE_ENABLED,
        "telegram_failed_exit_cooldown_sec": C.TELEGRAM_FAILED_EXIT_COOLDOWN_SEC,
        "trade_log_full_reason_enabled": C.TRADE_LOG_FULL_REASON_ENABLED,
        "telegram_verbose_trade_reason": C.TELEGRAM_VERBOSE_TRADE_REASON,
        "crash_drop_pct_from_peak": C.CRASH_DROP_PCT_FROM_PEAK,
        "crash_from_peak_grace_sec_after_entry": C.CRASH_FROM_PEAK_GRACE_SEC_AFTER_ENTRY,
        "buy_escalate_notional_on_submit_fail": C.BUY_ESCALATE_NOTIONAL_ON_SUBMIT_FAIL,
        "buy_escalate_notional_step_usd": C.BUY_ESCALATE_NOTIONAL_STEP_USD,
        "momentum_antifomo_min_mkt": C.MOMENTUM_ANTIFOMO_MIN_MKT,
        "momentum_antifomo_min_prior_rise": C.MOMENTUM_ANTIFOMO_MIN_PRIOR_RISE,
        "momentum_entry_max_window_seconds": C.MOMENTUM_ENTRY_MAX_WINDOW_SEC,
        "leader_yield_min_leader_old_price": C.LEADER_YIELD_MIN_LEADER_OLD_PRICE,
        "leader_yield_fall_min_abs_pts": C.LEADER_YIELD_FALL_MIN_ABS_PTS,
        "leader_yield_fall_min_frac": C.LEADER_YIELD_FALL_MIN_FRAC,
    }


def load_runtime_config_file() -> Dict[str, Any]:
    _ensure_data_dir()
    base = default_runtime_dict()
    if not C.RUNTIME_CONFIG_FILE.is_file():
        return base
    try:
        raw = json.loads(C.RUNTIME_CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            base.update(
                {
                    k: v
                    for k, v in raw.items()
                    if k in base or k == "blacklist_market_ids"
                }
            )
    except (OSError, json.JSONDecodeError):
        pass
    return base


def save_runtime_config_file(data: Dict[str, Any]) -> None:
    _ensure_data_dir()
    allowed = default_runtime_dict()
    merged = {**allowed, **{k: data[k] for k in data if k in allowed}}
    C.RUNTIME_CONFIG_FILE.write_text(
        json.dumps(merged, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )


@dataclass
class RuntimeSettings:
    buy_min: float
    buy_max: float
    buy_disable_price_band: bool
    # per-type stop-loss
    stop_loss_normal: float
    stop_loss_momentum: float
    stop_loss_double_momentum: float
    stop_loss_normal_entry_drop_pct: float
    stop_loss_momentum_entry_drop_pct: float
    stop_loss_double_momentum_entry_drop_pct: float
    take_profit: float
    min_lead_over_runner_up: float
    enable_competition_filter: bool
    enable_momentum: bool
    momentum_window_min: int
    momentum_rise: float
    momentum_min_price: float
    momentum_max_entry: float
    momentum_peer_drop: float
    # double momentum entry
    double_momentum_entry_rise: float
    double_momentum_pct_rise: float
    double_momentum_min_start_price: float
    double_momentum_min_price: float
    double_momentum_max_price: float
    churn_max_stop_cycles: int
    churn_cooldown_sec: int
    # event-level churn
    churn_event_max_losses: int
    churn_event_cooldown_sec: int
    churn_event_loss_1_cooldown_sec: int
    churn_event_loss_2_cooldown_sec: int
    leader_switch_window_sec: int
    leader_switch_max_count: int
    unstable_event_cooldown_sec: int
    scan_interval_seconds: int
    min_order_notional_usd: float
    max_buy_notional_usd: float
    # fast exit watcher
    fast_exit_watcher_interval_sec: int
    max_trade_fraction_of_cash: float
    dashboard_weather_max_pages: int
    cash_reserve_usd: float
    forecast_digest_enabled: bool
    forecast_digest_refresh_interval_sec: int
    forecast_digest_telegram_interval_sec: int
    forecast_digest_scope: str
    forecast_digest_max_location_groups: int
    enable_openweather_forecast: bool
    enable_flow_sampling: bool
    flow_max_events_per_tick: int
    enable_flow_peer_exit: bool
    flow_peer_window_sec: int
    flow_peer_surge_drop: float
    flow_peer_surge_rise: float
    forecast_gate_buy: bool
    forecast_contradict_margin_c: float
    forecast_exact_bucket_support_slack_c: float
    forecast_reduce_usd_if_weak: bool
    forecast_weak_size_factor: float
    research_exit_on_model_flip: bool
    research_edge_gate_buy: bool
    research_min_edge: float
    research_min_edge_after_fees_add: float
    research_sigma_c: float
    research_weather_taker_fee_rate: float
    research_edge_implied_soft_floor: float
    research_edge_implied_soft_boost: float
    buy_earliest_local_hour: int
    buy_latest_local_hour: int
    research_edge_scale_size: bool
    research_edge_size_slope: float
    research_edge_size_cap_mult: float
    research_crowd_soft_match: bool
    research_crowd_soft_band: float
    research_crowd_soft_edge_factor: float
    research_crowd_disagree_gap: float
    research_crowd_disagree_extra_edge: float
    research_skip_telegram_cooldown_sec: int
    decision_skip_telegram_notify: bool
    max_market_prob_for_buy: float
    min_model_prob_for_buy: float
    max_positions_per_event: int
    decision_min_model_peak_prob: float
    enable_peer_surge_exit: bool
    peer_surge_window_min: int
    peer_surge_rise_threshold: float
    peer_surge_event_cooldown_sec: int
    peer_surge_skip_buy_enabled: bool
    peer_surge_skip_buy_rise_threshold: float
    momentum_window_seconds: float
    momentum_fast_window_seconds: float
    double_momentum_fast_window_seconds: float
    momentum_fast_exit_drop: float
    momentum_competitor_surge: float
    momentum_entry_rise: float
    momentum_pct_rise: float
    momentum_min_start_price: float
    time_decay_hours: float
    time_decay_min_gain: float
    time_decay_max_price: float
    # trailing stop
    trailing_stop_enabled: bool
    trailing_stop_activation_gain: float
    trailing_stop_lock_gain: float
    # limit-order-first execution
    limit_orders_enabled: bool
    buy_limit_order_timeout_sec: int
    sell_limit_order_timeout_sec: int
    emergency_exit_allow_market_order: bool
    buy_limit_price_offset: float
    sell_limit_price_offset: float
    require_fill_before_state_buy: bool
    # telegram dedupe + verbosity
    telegram_failed_exit_dedupe_enabled: bool
    telegram_failed_exit_cooldown_sec: int
    trade_log_full_reason_enabled: bool
    telegram_verbose_trade_reason: bool
    crash_drop_pct_from_peak: float
    crash_from_peak_grace_sec_after_entry: float
    buy_escalate_notional_on_submit_fail: bool
    buy_escalate_notional_step_usd: float
    momentum_antifomo_min_mkt: float
    momentum_antifomo_min_prior_rise: float
    momentum_entry_max_window_seconds: float
    leader_yield_min_leader_old_price: float
    leader_yield_fall_min_abs_pts: float
    leader_yield_fall_min_frac: float
    blacklist_market_ids: Set[str] = field(default_factory=set)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RuntimeSettings":
        bl = d.get("blacklist_market_ids") or []
        ids = {str(x).strip() for x in bl if str(x).strip()}
        scan = int(d.get("scan_interval_seconds", C.SCAN_INTERVAL_SECONDS))
        scan = max(5, min(600, scan))
        frac = float(d.get("max_trade_fraction_of_cash", C.MAX_TRADE_FRACTION_OF_CASH))
        frac = min(1.0, max(0.0, frac))
        min_n = max(
            0.0, float(d.get("min_order_notional_usd", C.MIN_ORDER_NOTIONAL_USD))
        )
        max_buy = max(0.0, float(d.get("max_buy_notional_usd", C.MAX_BUY_NOTIONAL_USD)))
        if max_buy > 0 and min_n > max_buy:
            min_n = min(min_n, max_buy)
        dwp = int(d.get("dashboard_weather_max_pages", 6))
        dwp = max(1, min(80, dwp))
        reserve = max(0.0, float(d.get("cash_reserve_usd", C.CASH_RESERVE_USD)))
        reserve = min(reserve, 1_000_000.0)
        legacy_fd = d.get("forecast_digest_interval_sec")
        refresh_raw = d.get("forecast_digest_refresh_interval_sec")
        if refresh_raw is not None:
            refresh_iv = int(refresh_raw)
        elif legacy_fd is not None:
            refresh_iv = int(legacy_fd)
        else:
            refresh_iv = int(C.FORECAST_DIGEST_REFRESH_INTERVAL_SEC)
        refresh_iv = max(60, min(3600, refresh_iv))
        tg_raw = d.get("forecast_digest_telegram_interval_sec")
        if tg_raw is not None:
            tg_iv = int(tg_raw)
        elif legacy_fd is not None:
            tg_iv = int(legacy_fd)
        else:
            tg_iv = int(C.FORECAST_DIGEST_TELEGRAM_INTERVAL_SEC)
        tg_iv = max(0, min(86400, tg_iv))
        fscope = str(d.get("forecast_digest_scope", C.FORECAST_DIGEST_SCOPE) or "scan")
        if fscope not in ("scan", "positions"):
            fscope = "scan"
        fd_cap = int(
            d.get(
                "forecast_digest_max_location_groups",
                C.FORECAST_DIGEST_MAX_LOCATION_GROUPS,
            )
        )
        fd_cap = max(4, min(int(C.FORECAST_DIGEST_MAX_GROUPS_CAP), fd_cap))
        fme = int(d.get("flow_max_events_per_tick", C.FLOW_MAX_EVENTS_PER_TICK))
        fme = max(1, min(40, fme))
        fpw = int(d.get("flow_peer_window_sec", C.FLOW_PEER_WINDOW_SEC))
        fpw = max(120, min(3600, fpw))
        beh = int(d.get("buy_earliest_local_hour", C.BUY_EARLIEST_HOUR))
        beh = max(0, min(23, beh))
        blh = int(d.get("buy_latest_local_hour", C.BUY_LATEST_LOCAL_HOUR))
        blh = max(0, min(24, blh))
        sig = float(d.get("research_sigma_c", C.RESEARCH_SIGMA_C))
        if sig <= 1e-12:
            sig = 0.0
        else:
            sig = max(0.5, min(8.0, sig))
        rsec = int(
            d.get(
                "research_skip_telegram_cooldown_sec",
                C.RESEARCH_SKIP_TELEGRAM_COOLDOWN_SEC,
            )
        )
        rsec = max(0, min(86400, rsec))
        d_skip_tg = bool(
            d.get("decision_skip_telegram_notify", C.DECISION_SKIP_TELEGRAM_NOTIFY)
        )
        max_mkt = float(d.get("max_market_prob_for_buy", C.MAX_MARKET_PROB_FOR_BUY))
        max_mkt = max(0.05, min(0.99, max_mkt))
        min_mod = float(d.get("min_model_prob_for_buy", C.MIN_MODEL_PROB_FOR_BUY))
        min_mod = max(0.0, min(0.95, min_mod))
        max_pe = int(d.get("max_positions_per_event", C.MAX_POSITIONS_PER_EVENT))
        max_pe = max(1, min(20, max_pe))
        d_peak = float(
            d.get("decision_min_model_peak_prob", C.DECISION_MIN_MODEL_PEAK_PROB)
        )
        # 0.0 disables the gate entirely (price-driven mode without forecast).
        d_peak = max(0.0, min(0.5, d_peak))
        psw = int(d.get("peer_surge_window_min", C.PEER_SURGE_WINDOW_MIN))
        psw = max(1, min(180, psw))
        psr = float(d.get("peer_surge_rise_threshold", C.PEER_SURGE_RISE_THRESHOLD))
        psr = max(0.01, min(2.0, psr))
        pscd = int(
            d.get("peer_surge_event_cooldown_sec", C.PEER_SURGE_EVENT_COOLDOWN_SEC)
        )
        pscd = max(0, min(86400, pscd))
        ps_skip_thr = float(
            d.get(
                "peer_surge_skip_buy_rise_threshold",
                C.PEER_SURGE_SKIP_BUY_RISE_THRESHOLD,
            )
        )
        ps_skip_thr = max(0.01, min(2.0, ps_skip_thr))
        mom_wsec = float(d.get("momentum_window_seconds", C.MOMENTUM_WINDOW_SECONDS))
        mom_wsec = max(120.0, min(7200.0, mom_wsec))
        mom_fast_wsec = float(
            d.get("momentum_fast_window_seconds", C.MOMENTUM_FAST_WINDOW_SECONDS)
        )
        mom_fast_wsec = max(60.0, min(3600.0, mom_fast_wsec))
        dbl_fast_wsec = float(
            d.get(
                "double_momentum_fast_window_seconds",
                C.DOUBLE_MOMENTUM_FAST_WINDOW_SECONDS,
            )
        )
        dbl_fast_wsec = max(60.0, min(3600.0, dbl_fast_wsec))
        mom_drop = float(d.get("momentum_fast_exit_drop", C.MOMENTUM_FAST_EXIT_DROP))
        mom_drop = max(0.01, min(0.95, mom_drop))
        mom_surge = float(d.get("momentum_competitor_surge", C.MOMENTUM_COMPETITOR_SURGE))
        mom_surge = max(0.01, min(0.95, mom_surge))
        mom_entry_rise = float(d.get("momentum_entry_rise", C.MOMENTUM_ENTRY_RISE))
        mom_entry_rise = max(0.01, min(0.95, mom_entry_rise))
        mom_pct_rise = float(d.get("momentum_pct_rise", C.MOMENTUM_PCT_RISE))
        mom_pct_rise = max(0.01, min(10.0, mom_pct_rise))
        mom_min_start = float(
            d.get("momentum_min_start_price", C.MOMENTUM_MIN_START_PRICE)
        )
        mom_min_start = max(0.0, min(0.99, mom_min_start))
        td_hours = float(d.get("time_decay_hours", C.TIME_DECAY_HOURS))
        td_hours = max(0.25, min(48.0, td_hours))
        td_min_gain = float(d.get("time_decay_min_gain", C.TIME_DECAY_MIN_GAIN))
        td_min_gain = max(0.0, min(0.5, td_min_gain))
        td_max_px = float(d.get("time_decay_max_price", C.TIME_DECAY_MAX_PRICE))
        td_max_px = max(0.05, min(0.99, td_max_px))
        churn_l1 = int(
            d.get("churn_event_loss_1_cooldown_sec", C.CHURN_EVENT_LOSS_1_COOLDOWN_SEC)
        )
        churn_l1 = max(0, min(86400, churn_l1))
        churn_l2 = int(
            d.get("churn_event_loss_2_cooldown_sec", C.CHURN_EVENT_LOSS_2_COOLDOWN_SEC)
        )
        churn_l2 = max(0, min(86400, churn_l2))
        leader_switch_window = int(
            d.get("leader_switch_window_sec", C.LEADER_SWITCH_WINDOW_SEC)
        )
        leader_switch_window = max(60, min(86400, leader_switch_window))
        leader_switch_max = int(
            d.get("leader_switch_max_count", C.LEADER_SWITCH_MAX_COUNT)
        )
        leader_switch_max = max(1, min(100, leader_switch_max))
        unstable_cd = int(
            d.get("unstable_event_cooldown_sec", C.UNSTABLE_EVENT_COOLDOWN_SEC)
        )
        unstable_cd = max(0, min(86400, unstable_cd))
        # trailing stop
        ts_enabled = bool(d.get("trailing_stop_enabled", C.TRAILING_STOP_ENABLED))
        ts_act = float(
            d.get("trailing_stop_activation_gain", C.TRAILING_STOP_ACTIVATION_GAIN)
        )
        ts_act = max(0.01, min(0.95, ts_act))
        ts_lock = float(d.get("trailing_stop_lock_gain", C.TRAILING_STOP_LOCK_GAIN))
        ts_lock = max(0.0, min(0.95, ts_lock))
        if ts_lock + 1e-9 >= ts_act:
            # lock must stay strictly below activation; otherwise trailing never bites.
            ts_lock = max(0.0, ts_act - 0.01)
        # limit-order execution
        lo_enabled = bool(d.get("limit_orders_enabled", C.LIMIT_ORDERS_ENABLED))
        buy_to = int(d.get("buy_limit_order_timeout_sec", C.BUY_LIMIT_ORDER_TIMEOUT_SEC))
        buy_to = max(1, min(120, buy_to))
        sell_to = int(
            d.get("sell_limit_order_timeout_sec", C.SELL_LIMIT_ORDER_TIMEOUT_SEC)
        )
        sell_to = max(1, min(120, sell_to))
        em_market = bool(
            d.get(
                "emergency_exit_allow_market_order", C.EMERGENCY_EXIT_ALLOW_MARKET_ORDER
            )
        )
        buy_off = float(d.get("buy_limit_price_offset", C.BUY_LIMIT_PRICE_OFFSET))
        buy_off = max(-0.05, min(0.05, buy_off))
        sell_off = float(d.get("sell_limit_price_offset", C.SELL_LIMIT_PRICE_OFFSET))
        sell_off = max(-0.05, min(0.05, sell_off))
        req_fill = bool(
            d.get("require_fill_before_state_buy", C.REQUIRE_FILL_BEFORE_STATE_BUY)
        )
        # telegram dedupe + verbosity
        tg_dedup = bool(
            d.get(
                "telegram_failed_exit_dedupe_enabled",
                C.TELEGRAM_FAILED_EXIT_DEDUPE_ENABLED,
            )
        )
        tg_cd = int(
            d.get(
                "telegram_failed_exit_cooldown_sec",
                C.TELEGRAM_FAILED_EXIT_COOLDOWN_SEC,
            )
        )
        tg_cd = max(0, min(86400, tg_cd))
        trade_full_reason = bool(
            d.get("trade_log_full_reason_enabled", C.TRADE_LOG_FULL_REASON_ENABLED)
        )
        tg_verbose = bool(
            d.get("telegram_verbose_trade_reason", C.TELEGRAM_VERBOSE_TRADE_REASON)
        )
        return cls(
            buy_min=float(d.get("buy_min_threshold", C.BUY_MIN_THRESHOLD)),
            buy_max=float(d.get("buy_max_threshold", C.BUY_MAX_THRESHOLD)),
            buy_disable_price_band=bool(
                d.get("buy_disable_price_band", C.BUY_DISABLE_PRICE_BAND)
            ),
            take_profit=float(d.get("take_profit_threshold", C.TAKE_PROFIT_THRESHOLD)),
            min_lead_over_runner_up=float(
                d.get("min_lead_over_runner_up", C.MIN_LEAD_OVER_RUNNER_UP)
            ),
            enable_competition_filter=bool(
                d.get("enable_competition_filter", C.ENABLE_COMPETITION_FILTER)
            ),
            enable_momentum=bool(d.get("enable_momentum", C.ENABLE_MOMENTUM)),
            momentum_window_min=int(
                d.get("momentum_window_min", C.MOMENTUM_WINDOW_MIN)
            ),
            momentum_rise=float(d.get("momentum_rise", C.MOMENTUM_RISE)),
            momentum_min_price=float(d.get("momentum_min_price", C.MOMENTUM_MIN_PRICE)),
            momentum_max_entry=float(d.get("momentum_max_entry", C.MOMENTUM_MAX_ENTRY)),
            momentum_peer_drop=float(d.get("momentum_peer_drop", C.MOMENTUM_PEER_DROP)),
            churn_max_stop_cycles=int(
                d.get("churn_max_stop_cycles", C.CHURN_MAX_STOP_CYCLES)
            ),
            churn_cooldown_sec=int(d.get("churn_cooldown_sec", C.CHURN_COOLDOWN_SEC)),
            scan_interval_seconds=scan,
            min_order_notional_usd=min_n,
            max_buy_notional_usd=max_buy,
            max_trade_fraction_of_cash=frac,
            dashboard_weather_max_pages=dwp,
            cash_reserve_usd=reserve,
            forecast_digest_enabled=bool(
                d.get("forecast_digest_enabled", C.FORECAST_DIGEST_ENABLED)
            ),
            forecast_digest_refresh_interval_sec=refresh_iv,
            forecast_digest_telegram_interval_sec=tg_iv,
            forecast_digest_scope=fscope,
            forecast_digest_max_location_groups=fd_cap,
            enable_openweather_forecast=bool(
                d.get("enable_openweather_forecast", C.ENABLE_OPENWEATHER_FORECAST)
            ),
            enable_flow_sampling=bool(
                d.get("enable_flow_sampling", C.ENABLE_FLOW_SAMPLING)
            ),
            flow_max_events_per_tick=fme,
            enable_flow_peer_exit=bool(
                d.get("enable_flow_peer_exit", C.ENABLE_FLOW_PEER_EXIT)
            ),
            flow_peer_window_sec=fpw,
            flow_peer_surge_drop=float(
                d.get("flow_peer_surge_drop", C.FLOW_PEER_SURGE_DROP)
            ),
            flow_peer_surge_rise=float(
                d.get("flow_peer_surge_rise", C.FLOW_PEER_SURGE_RISE)
            ),
            forecast_gate_buy=bool(d.get("forecast_gate_buy", C.FORECAST_GATE_BUY)),
            forecast_contradict_margin_c=float(
                d.get("forecast_contradict_margin_c", C.FORECAST_CONTRADICT_MARGIN_C)
            ),
            forecast_exact_bucket_support_slack_c=float(
                d.get(
                    "forecast_exact_bucket_support_slack_c",
                    C.FORECAST_EXACT_BUCKET_SUPPORT_SLACK_C,
                )
            ),
            forecast_reduce_usd_if_weak=bool(
                d.get("forecast_reduce_usd_if_weak", C.FORECAST_REDUCE_USD_IF_WEAK)
            ),
            forecast_weak_size_factor=float(
                d.get("forecast_weak_size_factor", C.FORECAST_WEAK_SIZE_FACTOR)
            ),
            research_exit_on_model_flip=bool(
                d.get("research_exit_on_model_flip", C.RESEARCH_EXIT_ON_MODEL_FLIP)
            ),
            research_edge_gate_buy=bool(
                d.get("research_edge_gate_buy", C.RESEARCH_EDGE_GATE_BUY)
            ),
            research_min_edge=float(d.get("research_min_edge", C.RESEARCH_MIN_EDGE)),
            research_min_edge_after_fees_add=float(
                d.get(
                    "research_min_edge_after_fees_add",
                    C.RESEARCH_MIN_EDGE_AFTER_FEES_ADD,
                )
            ),
            research_sigma_c=sig,
            research_weather_taker_fee_rate=float(
                d.get(
                    "research_weather_taker_fee_rate",
                    C.RESEARCH_WEATHER_TAKER_FEE_RATE,
                )
            ),
            research_edge_implied_soft_floor=min(
                0.95,
                max(
                    0.0,
                    float(
                        d.get(
                            "research_edge_implied_soft_floor",
                            C.RESEARCH_EDGE_IMPLIED_SOFT_FLOOR,
                        )
                    ),
                ),
            ),
            research_edge_implied_soft_boost=min(
                3.0,
                max(
                    0.0,
                    float(
                        d.get(
                            "research_edge_implied_soft_boost",
                            C.RESEARCH_EDGE_IMPLIED_SOFT_BOOST,
                        )
                    ),
                ),
            ),
            buy_earliest_local_hour=beh,
            buy_latest_local_hour=blh,
            research_edge_scale_size=bool(
                d.get("research_edge_scale_size", C.RESEARCH_EDGE_SCALE_SIZE)
            ),
            research_edge_size_slope=float(
                d.get("research_edge_size_slope", C.RESEARCH_EDGE_SIZE_SLOPE)
            ),
            research_edge_size_cap_mult=float(
                d.get("research_edge_size_cap_mult", C.RESEARCH_EDGE_SIZE_CAP_MULT)
            ),
            research_crowd_soft_match=bool(
                d.get("research_crowd_soft_match", C.RESEARCH_CROWD_SOFT_MATCH)
            ),
            research_crowd_soft_band=float(
                d.get("research_crowd_soft_band", C.RESEARCH_CROWD_SOFT_BAND)
            ),
            research_crowd_soft_edge_factor=float(
                d.get(
                    "research_crowd_soft_edge_factor",
                    C.RESEARCH_CROWD_SOFT_EDGE_FACTOR,
                )
            ),
            research_crowd_disagree_gap=float(
                d.get("research_crowd_disagree_gap", C.RESEARCH_CROWD_DISAGREE_GAP)
            ),
            research_crowd_disagree_extra_edge=float(
                d.get(
                    "research_crowd_disagree_extra_edge",
                    C.RESEARCH_CROWD_DISAGREE_EXTRA_EDGE,
                )
            ),
            research_skip_telegram_cooldown_sec=rsec,
            decision_skip_telegram_notify=d_skip_tg,
            max_market_prob_for_buy=max_mkt,
            min_model_prob_for_buy=min_mod,
            max_positions_per_event=max_pe,
            decision_min_model_peak_prob=d_peak,
            enable_peer_surge_exit=bool(
                d.get("enable_peer_surge_exit", C.ENABLE_PEER_SURGE_EXIT)
            ),
            peer_surge_window_min=psw,
            peer_surge_rise_threshold=psr,
            peer_surge_event_cooldown_sec=pscd,
            peer_surge_skip_buy_enabled=bool(
                d.get("peer_surge_skip_buy_enabled", C.PEER_SURGE_SKIP_BUY_ENABLED)
            ),
            peer_surge_skip_buy_rise_threshold=ps_skip_thr,
            momentum_window_seconds=mom_wsec,
            momentum_fast_window_seconds=mom_fast_wsec,
            double_momentum_fast_window_seconds=dbl_fast_wsec,
            momentum_fast_exit_drop=mom_drop,
            momentum_competitor_surge=mom_surge,
            momentum_entry_rise=mom_entry_rise,
            momentum_pct_rise=mom_pct_rise,
            momentum_min_start_price=mom_min_start,
            time_decay_hours=td_hours,
            time_decay_min_gain=td_min_gain,
            time_decay_max_price=td_max_px,
            trailing_stop_enabled=ts_enabled,
            trailing_stop_activation_gain=ts_act,
            trailing_stop_lock_gain=ts_lock,
            limit_orders_enabled=lo_enabled,
            buy_limit_order_timeout_sec=buy_to,
            sell_limit_order_timeout_sec=sell_to,
            emergency_exit_allow_market_order=em_market,
            buy_limit_price_offset=buy_off,
            sell_limit_price_offset=sell_off,
            require_fill_before_state_buy=req_fill,
            telegram_failed_exit_dedupe_enabled=tg_dedup,
            telegram_failed_exit_cooldown_sec=tg_cd,
            trade_log_full_reason_enabled=trade_full_reason,
            telegram_verbose_trade_reason=tg_verbose,
            blacklist_market_ids=ids,
            # per-type stop-loss
            stop_loss_normal=max(0.01, min(0.99, float(
                d.get("stop_loss_normal", C.STOP_LOSS_NORMAL)
            ))),
            stop_loss_momentum=max(0.01, min(0.99, float(
                d.get("stop_loss_momentum", C.STOP_LOSS_MOMENTUM)
            ))),
            stop_loss_double_momentum=max(0.01, min(0.99, float(
                d.get("stop_loss_double_momentum", C.STOP_LOSS_DOUBLE_MOMENTUM)
            ))),
            stop_loss_normal_entry_drop_pct=max(0.01, min(0.95, float(
                d.get("stop_loss_normal_entry_drop_pct", C.STOP_LOSS_NORMAL_ENTRY_DROP_PCT)
            ))),
            stop_loss_momentum_entry_drop_pct=max(0.01, min(0.95, float(
                d.get("stop_loss_momentum_entry_drop_pct", C.STOP_LOSS_MOMENTUM_ENTRY_DROP_PCT)
            ))),
            stop_loss_double_momentum_entry_drop_pct=max(0.01, min(0.95, float(
                d.get("stop_loss_double_momentum_entry_drop_pct", C.STOP_LOSS_DOUBLE_MOMENTUM_ENTRY_DROP_PCT)
            ))),
            # double momentum entry
            double_momentum_entry_rise=max(0.05, min(1.0, float(
                d.get("double_momentum_entry_rise", C.DOUBLE_MOMENTUM_ENTRY_RISE)
            ))),
            double_momentum_pct_rise=max(0.01, min(10.0, float(
                d.get("double_momentum_pct_rise", C.DOUBLE_MOMENTUM_PCT_RISE)
            ))),
            double_momentum_min_start_price=max(0.0, min(0.99, float(
                d.get("double_momentum_min_start_price", C.DOUBLE_MOMENTUM_MIN_START_PRICE)
            ))),
            double_momentum_min_price=max(0.01, min(0.99, float(
                d.get("double_momentum_min_price", C.DOUBLE_MOMENTUM_MIN_PRICE)
            ))),
            double_momentum_max_price=max(0.01, min(0.99, float(
                d.get("double_momentum_max_price", C.DOUBLE_MOMENTUM_MAX_PRICE)
            ))),
            # event-level churn
            churn_event_max_losses=max(1, min(20, int(
                d.get("churn_event_max_losses", C.CHURN_EVENT_MAX_LOSSES)
            ))),
            churn_event_cooldown_sec=max(0, min(86400, int(
                d.get("churn_event_cooldown_sec", C.CHURN_EVENT_COOLDOWN_SEC)
            ))),
            churn_event_loss_1_cooldown_sec=churn_l1,
            churn_event_loss_2_cooldown_sec=churn_l2,
            leader_switch_window_sec=leader_switch_window,
            leader_switch_max_count=leader_switch_max,
            unstable_event_cooldown_sec=unstable_cd,
            # fast exit watcher
            fast_exit_watcher_interval_sec=max(2, min(60, int(
                d.get("fast_exit_watcher_interval_sec", C.FAST_EXIT_WATCHER_INTERVAL_SEC)
            ))),
            crash_drop_pct_from_peak=max(0.0, min(0.95, float(
                d.get("crash_drop_pct_from_peak", C.CRASH_DROP_PCT_FROM_PEAK)
            ))),
            crash_from_peak_grace_sec_after_entry=max(0.0, min(3600.0, float(
                d.get(
                    "crash_from_peak_grace_sec_after_entry",
                    C.CRASH_FROM_PEAK_GRACE_SEC_AFTER_ENTRY,
                )
            ))),
            buy_escalate_notional_on_submit_fail=bool(
                d.get(
                    "buy_escalate_notional_on_submit_fail",
                    C.BUY_ESCALATE_NOTIONAL_ON_SUBMIT_FAIL,
                )
            ),
            buy_escalate_notional_step_usd=max(0.1, min(25.0, float(
                d.get(
                    "buy_escalate_notional_step_usd",
                    C.BUY_ESCALATE_NOTIONAL_STEP_USD,
                )
            ))),
            momentum_antifomo_min_mkt=max(0.5, min(0.98, float(
                d.get("momentum_antifomo_min_mkt", C.MOMENTUM_ANTIFOMO_MIN_MKT)
            ))),
            momentum_antifomo_min_prior_rise=max(0.0, min(0.95, float(
                d.get(
                    "momentum_antifomo_min_prior_rise",
                    C.MOMENTUM_ANTIFOMO_MIN_PRIOR_RISE,
                )
            ))),
            momentum_entry_max_window_seconds=max(60.0, min(3600.0, float(
                d.get(
                    "momentum_entry_max_window_seconds",
                    C.MOMENTUM_ENTRY_MAX_WINDOW_SEC,
                )
            ))),
            leader_yield_min_leader_old_price=max(0.0, min(0.99, float(
                d.get(
                    "leader_yield_min_leader_old_price",
                    C.LEADER_YIELD_MIN_LEADER_OLD_PRICE,
                )
            ))),
            leader_yield_fall_min_abs_pts=max(0.0, min(0.95, float(
                d.get(
                    "leader_yield_fall_min_abs_pts",
                    C.LEADER_YIELD_FALL_MIN_ABS_PTS,
                )
            ))),
            leader_yield_fall_min_frac=max(0.0, min(0.98, float(
                d.get(
                    "leader_yield_fall_min_frac",
                    C.LEADER_YIELD_FALL_MIN_FRAC,
                )
            ))),
        )


def _today_key() -> str:
    if ZoneInfo is None:
        return datetime.utcnow().strftime("%Y-%m-%d")
    return datetime.now(ZoneInfo(C.TIMEZONE)).strftime("%Y-%m-%d")


def load_blacklist_day_ids() -> Set[str]:
    if not C.BLACKLIST_FILE.is_file():
        return set()
    try:
        raw = json.loads(C.BLACKLIST_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(raw, dict):
        return set()
    if str(raw.get("day") or "") != _today_key():
        return set()
    ids = raw.get("market_ids") or []
    return {str(x).strip() for x in ids if str(x).strip()}


def get_effective_settings() -> RuntimeSettings:
    d = load_runtime_config_file()
    rs = RuntimeSettings.from_dict(d)
    day_bl = load_blacklist_day_ids()
    rs.blacklist_market_ids = set(rs.blacklist_market_ids) | day_bl
    return rs


def save_blacklist_day_file(market_ids: List[str]) -> None:
    C.DATA_DIR.mkdir(parents=True, exist_ok=True)
    ids = sorted({str(x).strip() for x in market_ids if str(x).strip()})
    C.BLACKLIST_FILE.write_text(
        json.dumps({"day": _today_key(), "market_ids": ids}, indent=2) + "\n",
        encoding="utf-8",
    )
