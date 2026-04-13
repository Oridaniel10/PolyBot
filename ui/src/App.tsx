import { useCallback, useEffect, useState } from "react";

const api = (path: string, init?: RequestInit) =>
  fetch(path, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });

type PnlSummary = {
  day: { buy_notional_usd: number; sell_notional_usd: number; est_pnl_usd: number; trade_count: number };
  week: { buy_notional_usd: number; sell_notional_usd: number; est_pnl_usd: number; trade_count: number };
  as_of: string;
  error?: string | null;
};

type PortfolioResp = {
  cash_usd: number;
  positions_mtm_usd: number;
  total_usd: number;
  rows: {
    market_id: string | null;
    condition_id: string | null;
    title: string;
    outcome: string;
    shares: number;
    avg_price: number;
    cur_price: number;
    cash_pnl: number;
    value_usd: number;
    bot_tracked: boolean;
  }[];
  error: string | null;
};

type TradeRow = {
  timestamp: string;
  local_hhmm?: string;
  action: string;
  market_id: string;
  market_title?: string;
  city: string;
  price: string;
  shares: string;
  usd: string;
  reason: string;
  entry_price: string;
  pnl_usd: string;
  cash_after: string;
  positions_mtm: string;
  total_value: string;
};

type WeatherResp = {
  target_day_label: string;
  secondary_target_day_label?: string | null;
  markets: { id: string; question: string; lastTradePrice?: unknown; probability?: unknown }[];
  error?: string | null;
  total_matched?: number;
  showing?: number;
  max_pages_used?: number;
};

type ForecastPreviewGroup = {
  city_key: string;
  date_iso: string;
  timezone_label: string;
  open_meteo_c: number | null;
  openweather_c: number | null;
  consensus_c: number | null;
  member_market_ids?: string[];
};

type ForecastPreviewResp = {
  error?: string | null;
  generated_at_local?: string;
  scope?: string;
  openweather_enabled?: boolean;
  openweather_key_configured?: boolean;
  group_count?: number;
  groups?: ForecastPreviewGroup[];
  target_day_label?: string;
  secondary_target_day_label?: string | null;
  max_pages_used?: number;
  markets_scanned?: number;
  from_cache?: boolean;
  cache_age_sec?: number;
  stale_after_error?: boolean;
  digest_meta?: {
    markets_in_scope?: number;
    markets_parsable_temp_title?: number;
    city_day_groups_total?: number;
    city_day_groups_included?: number;
    digest_max_groups_applied?: number;
  };
};

type RuntimeResp = {
  runtime: Record<string, unknown>;
  blacklist_day_ids: string[];
  effective_blacklist: string[];
};

type FormState = {
  buy_min_threshold: number;
  buy_max_threshold: number;
  stop_loss_threshold: number;
  take_profit_threshold: number;
  min_lead_over_runner_up: number;
  enable_competition_filter: boolean;
  enable_momentum: boolean;
  momentum_window_min: number;
  momentum_rise: number;
  momentum_min_price: number;
  momentum_max_entry: number;
  momentum_peer_drop: number;
  churn_max_stop_cycles: number;
  churn_cooldown_sec: number;
  scan_interval_seconds: number;
  min_order_notional_usd: number;
  max_buy_notional_usd: number;
  max_trade_fraction_of_cash: number;
  dashboard_weather_max_pages: number;
  cash_reserve_usd: number;
  forecast_digest_enabled: boolean;
  forecast_digest_refresh_interval_sec: number;
  forecast_digest_telegram_interval_sec: number;
  forecast_digest_scope: string;
  forecast_digest_max_location_groups: number;
  enable_openweather_forecast: boolean;
  enable_flow_sampling: boolean;
  flow_max_events_per_tick: number;
  enable_flow_peer_exit: boolean;
  flow_peer_window_sec: number;
  flow_peer_surge_drop: number;
  flow_peer_surge_rise: number;
  forecast_gate_buy: boolean;
  forecast_contradict_margin_c: number;
  forecast_reduce_usd_if_weak: boolean;
  forecast_weak_size_factor: number;
};

const defaultForm = (): FormState => ({
  buy_min_threshold: 0.55,
  buy_max_threshold: 0.7,
  stop_loss_threshold: 0.44,
  take_profit_threshold: 0.98,
  min_lead_over_runner_up: 0.2,
  enable_competition_filter: true,
  enable_momentum: false,
  momentum_window_min: 10,
  momentum_rise: 0.25,
  momentum_min_price: 0.4,
  momentum_max_entry: 0.85,
  momentum_peer_drop: 0.1,
  churn_max_stop_cycles: 2,
  churn_cooldown_sec: 900,
  scan_interval_seconds: 20,
  min_order_notional_usd: 2,
  max_buy_notional_usd: 20,
  max_trade_fraction_of_cash: 0.2,
  dashboard_weather_max_pages: 6,
  cash_reserve_usd: 10,
  forecast_digest_enabled: true,
  forecast_digest_refresh_interval_sec: 120,
  forecast_digest_telegram_interval_sec: 0,
  forecast_digest_scope: "scan",
  forecast_digest_max_location_groups: 80,
  enable_openweather_forecast: false,
  enable_flow_sampling: true,
  flow_max_events_per_tick: 8,
  enable_flow_peer_exit: false,
  flow_peer_window_sec: 600,
  flow_peer_surge_drop: 0.12,
  flow_peer_surge_rise: 0.1,
  forecast_gate_buy: false,
  forecast_contradict_margin_c: 2.5,
  forecast_reduce_usd_if_weak: false,
  forecast_weak_size_factor: 0.45,
});

function runtimeToForm(rt: Record<string, unknown>, prev: FormState): FormState {
  const n = (k: string, d: number) => Number(rt[k] ?? d);
  const i = (k: string, d: number) => Math.round(Number(rt[k] ?? d));
  const b = (k: string, d: boolean) => Boolean(rt[k] ?? d);
  const sc = String(rt["forecast_digest_scope"] ?? prev.forecast_digest_scope ?? "scan").toLowerCase();
  const scope = sc === "positions" ? "positions" : "scan";
  return {
    buy_min_threshold: n("buy_min_threshold", prev.buy_min_threshold),
    buy_max_threshold: n("buy_max_threshold", prev.buy_max_threshold),
    stop_loss_threshold: n("stop_loss_threshold", prev.stop_loss_threshold),
    take_profit_threshold: n("take_profit_threshold", prev.take_profit_threshold),
    min_lead_over_runner_up: n("min_lead_over_runner_up", prev.min_lead_over_runner_up),
    enable_competition_filter: b("enable_competition_filter", prev.enable_competition_filter),
    enable_momentum: b("enable_momentum", prev.enable_momentum),
    momentum_window_min: i("momentum_window_min", prev.momentum_window_min),
    momentum_rise: n("momentum_rise", prev.momentum_rise),
    momentum_min_price: n("momentum_min_price", prev.momentum_min_price),
    momentum_max_entry: n("momentum_max_entry", prev.momentum_max_entry),
    momentum_peer_drop: n("momentum_peer_drop", prev.momentum_peer_drop),
    churn_max_stop_cycles: i("churn_max_stop_cycles", prev.churn_max_stop_cycles),
    churn_cooldown_sec: i("churn_cooldown_sec", prev.churn_cooldown_sec),
    scan_interval_seconds: i("scan_interval_seconds", prev.scan_interval_seconds),
    min_order_notional_usd: n("min_order_notional_usd", prev.min_order_notional_usd),
    max_buy_notional_usd: n("max_buy_notional_usd", prev.max_buy_notional_usd),
    max_trade_fraction_of_cash: n("max_trade_fraction_of_cash", prev.max_trade_fraction_of_cash),
    dashboard_weather_max_pages: i("dashboard_weather_max_pages", prev.dashboard_weather_max_pages),
    cash_reserve_usd: n("cash_reserve_usd", prev.cash_reserve_usd),
    forecast_digest_enabled: b("forecast_digest_enabled", prev.forecast_digest_enabled),
    forecast_digest_refresh_interval_sec: Math.round(
      Number(
        rt["forecast_digest_refresh_interval_sec"] ??
          rt["forecast_digest_interval_sec"] ??
          prev.forecast_digest_refresh_interval_sec,
      ),
    ),
    forecast_digest_telegram_interval_sec: Math.round(
      Number(
        rt["forecast_digest_telegram_interval_sec"] ??
          rt["forecast_digest_interval_sec"] ??
          prev.forecast_digest_telegram_interval_sec,
      ),
    ),
    forecast_digest_scope: scope,
    forecast_digest_max_location_groups: i(
      "forecast_digest_max_location_groups",
      prev.forecast_digest_max_location_groups,
    ),
    enable_openweather_forecast: b("enable_openweather_forecast", prev.enable_openweather_forecast),
    enable_flow_sampling: b("enable_flow_sampling", prev.enable_flow_sampling),
    flow_max_events_per_tick: i("flow_max_events_per_tick", prev.flow_max_events_per_tick),
    enable_flow_peer_exit: b("enable_flow_peer_exit", prev.enable_flow_peer_exit),
    flow_peer_window_sec: i("flow_peer_window_sec", prev.flow_peer_window_sec),
    flow_peer_surge_drop: n("flow_peer_surge_drop", prev.flow_peer_surge_drop),
    flow_peer_surge_rise: n("flow_peer_surge_rise", prev.flow_peer_surge_rise),
    forecast_gate_buy: b("forecast_gate_buy", prev.forecast_gate_buy),
    forecast_contradict_margin_c: n("forecast_contradict_margin_c", prev.forecast_contradict_margin_c),
    forecast_reduce_usd_if_weak: b("forecast_reduce_usd_if_weak", prev.forecast_reduce_usd_if_weak),
    forecast_weak_size_factor: n("forecast_weak_size_factor", prev.forecast_weak_size_factor),
  };
}

export default function App() {
  const [pnl, setPnl] = useState<PnlSummary | null>(null);
  const [positions, setPositions] = useState<PortfolioResp | null>(null);
  const [weather, setWeather] = useState<WeatherResp | null>(null);
  const [forecastPreview, setForecastPreview] = useState<ForecastPreviewResp | null>(null);
  const [forecastLoading, setForecastLoading] = useState(true);
  const [cfg, setCfg] = useState<RuntimeResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [coreLoading, setCoreLoading] = useState(true);
  const [weatherLoading, setWeatherLoading] = useState(true);

  const [trades, setTrades] = useState<TradeRow[]>([]);

  const [blMid, setBlMid] = useState("");
  const [form, setForm] = useState<FormState>(defaultForm);

  const loadCore = useCallback(async () => {
    setErr(null);
    setCoreLoading(true);
    try {
      const [pnlRes, posRes, cfgRes, tradesRes] = await Promise.all([
        api("/api/pnl/summary"),
        api("/api/portfolio/positions"),
        api("/api/runtime-config"),
        api("/api/trades/today"),
      ]);
      if (!pnlRes.ok) throw new Error(`PnL: ${await pnlRes.text()}`);
      if (!posRes.ok) throw new Error(`Positions: ${await posRes.text()}`);
      if (!cfgRes.ok) throw new Error(`Config: ${await cfgRes.text()}`);
      const pnlJ = (await pnlRes.json()) as PnlSummary;
      const posJ = (await posRes.json()) as PortfolioResp;
      const cfgJ = (await cfgRes.json()) as RuntimeResp;
      const tradesJ = tradesRes.ok ? ((await tradesRes.json()) as TradeRow[]) : [];
      setPnl(pnlJ);
      setPositions(posJ);
      setCfg(cfgJ);
      setTrades(tradesJ);
      const rt = cfgJ.runtime as Record<string, unknown>;
      setForm((f) => runtimeToForm(rt, f));
    } catch (e) {
      setErr(String(e));
    } finally {
      setCoreLoading(false);
    }
  }, []);

  const loadWeather = useCallback(async () => {
    setWeatherLoading(true);
    try {
      const r = await api("/api/markets/weather-today?limit=200");
      const j = (await r.json()) as WeatherResp & { detail?: unknown };
      if (!r.ok) {
        const msg =
          typeof j.detail === "string" ? j.detail : j.error ? String(j.error) : `HTTP ${r.status}`;
        setWeather({ target_day_label: j.target_day_label ?? "", markets: [], error: msg });
        return;
      }
      setWeather(j);
    } catch (e) {
      setWeather({ target_day_label: "", markets: [], error: String(e) });
    } finally {
      setWeatherLoading(false);
    }
  }, []);

  const loadForecastPreview = useCallback(async (refresh = false) => {
    setForecastLoading(true);
    try {
      const q = refresh ? "?refresh=1" : "";
      const r = await api(`/api/forecast/preview${q}`);
      const j = (await r.json()) as ForecastPreviewResp;
      setForecastPreview(j);
    } catch (e) {
      setForecastPreview({ error: String(e) });
    } finally {
      setForecastLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCore();
  }, [loadCore]);

  useEffect(() => {
    void loadWeather();
  }, [loadWeather]);

  useEffect(() => {
    void loadForecastPreview();
  }, [loadForecastPreview]);

  async function refreshAll() {
    await loadCore();
    await loadWeather();
    await loadForecastPreview(true);
  }

  async function resetToDefaults() {
    if (!window.confirm("Reset ALL settings to constants.py defaults?")) return;
    setBusy(true);
    setErr(null);
    try {
      const res = await api("/api/runtime-config/reset", { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      await loadCore();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveThresholds() {
    setBusy(true);
    setErr(null);
    try {
      const res = await api("/api/runtime-config", {
        method: "POST",
        body: JSON.stringify(form),
      });
      if (!res.ok) throw new Error(await res.text());
      await loadCore();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggleBlacklist(enabled: boolean) {
    if (!blMid.trim()) {
      setErr("enter market id");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const res = await api("/api/blacklist/toggle", {
        method: "POST",
        body: JSON.stringify({ market_id: blMid.trim(), enabled }),
      });
      if (!res.ok) throw new Error(await res.text());
      setBlMid("");
      await loadCore();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Polymarket bot</h1>
      <p className="muted">
        Ledger + live positions (Data API) · config in <code>data/runtime_config.json</code> ·{" "}
        <code>/dashboard/</code>
      </p>
      {err ? <p className="err">{err}</p> : null}

      <h2>Open positions (live)</h2>
      <section>
        {coreLoading ? (
          <p className="muted">loading positions…</p>
        ) : positions?.error ? (
          <p className="err">{positions.error}</p>
        ) : positions && positions.rows.length === 0 ? (
          <p className="muted">No open positions in Data API (or below live filters).</p>
        ) : (
          <>
            <p className="portfolio-totals">
              <span>
                Cash <strong>${positions?.cash_usd.toFixed(2)}</strong>
              </span>
              <span>
                Positions MTM <strong>${positions?.positions_mtm_usd.toFixed(2)}</strong>
              </span>
              <span>
                Total <strong>${positions?.total_usd.toFixed(2)}</strong>
              </span>
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Outcome</th>
                    <th>Shares</th>
                    <th>Entry</th>
                    <th>Mark</th>
                    <th>Value</th>
                    <th>PnL</th>
                    <th>Bot</th>
                  </tr>
                </thead>
                <tbody>
                  {positions?.rows.map((row, i) => (
                    <tr key={`${row.market_id ?? row.condition_id ?? i}`}>
                      <td className="td-title">{row.title.slice(0, 72)}{row.title.length > 72 ? "…" : ""}</td>
                      <td>{row.outcome || "—"}</td>
                      <td>{row.shares.toFixed(4)}</td>
                      <td>{row.avg_price.toFixed(4)}</td>
                      <td>{row.cur_price.toFixed(4)}</td>
                      <td>${row.value_usd.toFixed(2)}</td>
                      <td className={row.cash_pnl >= 0 ? "pnl-pos" : "pnl-neg"}>
                        {row.cash_pnl >= 0 ? "+" : ""}${row.cash_pnl.toFixed(2)}
                      </td>
                      <td>{row.bot_tracked ? "✓" : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <h2>PnL (ledger)</h2>
      <section>
        {coreLoading ? (
          <p className="muted">loading ledger…</p>
        ) : pnl?.error ? (
          <p className="err">Ledger: {pnl.error}</p>
        ) : pnl ? (
          <>
            <p>
              <strong>Today</strong> — buys ${pnl.day.buy_notional_usd} · sells ${pnl.day.sell_notional_usd} · est
              PnL ${pnl.day.est_pnl_usd} ({pnl.day.trade_count} rows)
            </p>
            <p>
              <strong>Last 7 days</strong> — buys ${pnl.week.buy_notional_usd} · sells ${pnl.week.sell_notional_usd}{" "}
              · est PnL ${pnl.week.est_pnl_usd} ({pnl.week.trade_count} rows)
            </p>
            <p className="muted">as of {pnl.as_of}</p>
          </>
        ) : null}
        <button type="button" className="secondary" onClick={() => void refreshAll()} disabled={busy}>
          Refresh all
        </button>
      </section>

      <h2>Position bars</h2>
      <section>
        {positions?.rows.map((row, i) => {
          const entry = row.avg_price;
          const mark = row.cur_price;
          const profit = mark >= entry;
          const lo = Math.min(entry, mark);
          const hi = Math.max(entry, mark);
          const barLeft = Math.round(lo * 100);
          const barWidth = Math.max(1, Math.round((hi - lo) * 100));
          const cityMatch = (row.title || "").match(/in (.+?) (?:be|on)/i);
          const city = cityMatch ? cityMatch[1] : "";
          const rowTrades = trades
            .filter((t) => t.market_id === row.market_id)
            .slice()
            .sort((a, b) => a.timestamp.localeCompare(b.timestamp));
          return (
            <div key={`bar-${row.market_id ?? i}`} style={{ marginBottom: "0.75rem" }}>
              <div style={{ fontSize: "0.85rem", marginBottom: 2 }}>
                <strong>{city || row.title.slice(0, 40)}</strong>
                <span className="muted" style={{ marginLeft: 8 }}>
                  entry {(entry * 100).toFixed(0)}% → mark {(mark * 100).toFixed(0)}%
                  {" · "}
                  <span className={row.cash_pnl >= 0 ? "pnl-pos" : "pnl-neg"}>
                    {row.cash_pnl >= 0 ? "+" : ""}${row.cash_pnl.toFixed(2)}
                  </span>
                </span>
              </div>
              <div
                style={{
                  position: "relative",
                  height: 22,
                  background: "#1a1a2e",
                  borderRadius: 4,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    position: "absolute",
                    left: `${barLeft}%`,
                    width: `${barWidth}%`,
                    height: "100%",
                    background: profit ? "#22c55e44" : "#ef444444",
                    borderRadius: 2,
                  }}
                />
                <div
                  style={{
                    position: "absolute",
                    left: `${Math.round(entry * 100)}%`,
                    width: 2,
                    height: "100%",
                    background: "#3b82f6",
                  }}
                  title={`Entry ${(entry * 100).toFixed(1)}%`}
                />
                <div
                  style={{
                    position: "absolute",
                    left: `${Math.round(mark * 100)}%`,
                    width: 2,
                    height: "100%",
                    background: profit ? "#22c55e" : "#ef4444",
                  }}
                  title={`Mark ${(mark * 100).toFixed(1)}%`}
                />
                {rowTrades.map((t, ti) => {
                  const px = Math.round(Number(t.price) * 100);
                  const n = ti + 1;
                  return (
                    <div
                      key={`${t.timestamp}-${t.action}-${n}`}
                      style={{
                        position: "absolute",
                        left: `${px}%`,
                        top: 0,
                        fontSize: 10,
                        fontWeight: "bold",
                        color: t.action === "BUY" ? "#3b82f6" : "#f59e0b",
                        transform: "translateX(-50%)",
                        lineHeight: "22px",
                      }}
                      title={`#${n} ${t.action} @ ${t.price} ${t.reason || ""}`}
                    >
                      {n}
                    </div>
                  );
                })}
              </div>
              <div style={{ fontSize: "0.7rem", display: "flex", justifyContent: "space-between" }}>
                <span>0%</span>
                <span>50%</span>
                <span>100%</span>
              </div>
            </div>
          );
        })}
      </section>

      <h2>Today&apos;s trades</h2>
      <section>
        {trades.length === 0 ? (
          <p className="muted">No trades today yet.</p>
        ) : (
          <>
            <div style={{ height: 80, display: "flex", alignItems: "flex-end", gap: 1, marginBottom: "0.75rem" }}>
              {trades.map((t, i) => {
                const val = Number(t.total_value) || 0;
                const allVals = trades.map((r) => Number(r.total_value) || 0);
                const mn = Math.min(...allVals) * 0.95;
                const mx = Math.max(...allVals) * 1.02;
                const pct = mx > mn ? ((val - mn) / (mx - mn)) * 100 : 50;
                return (
                  <div
                    key={i}
                    style={{
                      flex: 1,
                      height: `${Math.max(4, pct)}%`,
                      background: t.action === "BUY" ? "#3b82f6" : Number(t.pnl_usd) >= 0 ? "#22c55e" : "#ef4444",
                      borderRadius: 2,
                      minWidth: 4,
                    }}
                    title={`${t.action} ${t.city} $${t.total_value}`}
                  />
                );
              })}
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Local</th>
                    <th>Action</th>
                    <th>City</th>
                    <th>Price</th>
                    <th>Shares</th>
                    <th>USD</th>
                    <th>Reason</th>
                    <th>PnL</th>
                    <th>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t, i) => {
                    const time = t.timestamp.split("T")[1]?.slice(0, 5) || t.timestamp;
                    const pnl = Number(t.pnl_usd) || 0;
                    return (
                      <tr key={i}>
                        <td className="td-mono">{time}</td>
                        <td className="td-mono">{t.local_hhmm ?? "—"}</td>
                        <td style={{ color: t.action === "BUY" ? "#3b82f6" : "#f59e0b", fontWeight: "bold" }}>
                          {t.action}
                        </td>
                        <td>{t.city}</td>
                        <td>{Number(t.price).toFixed(2)}</td>
                        <td>{Number(t.shares).toFixed(2)}</td>
                        <td>${Number(t.usd).toFixed(2)}</td>
                        <td>{t.reason || "—"}</td>
                        <td className={pnl >= 0 ? "pnl-pos" : "pnl-neg"}>
                          {pnl !== 0 ? `${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}` : "—"}
                        </td>
                        <td>${Number(t.total_value).toFixed(2)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <h2>Today&apos;s weather markets (sample)</h2>
      <section>
        <p className="muted">
          Fast scan (pages from config). Full bot scan may use more pages.
        </p>
        {weatherLoading ? (
          <p className="muted">loading weather list…</p>
        ) : weather?.error ? (
          <p className="err">{weather.error}</p>
        ) : weather ? (
          <>
            <p className="muted">
              Days: {weather.target_day_label}
              {weather.secondary_target_day_label
                ? ` + ${weather.secondary_target_day_label}`
                : ""}{" "}
              · matched {weather.total_matched ?? weather.markets.length} · showing{" "}
              {weather.showing ?? weather.markets.length} · Gamma pages {weather.max_pages_used ?? "—"}
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>id</th>
                    <th>question</th>
                    <th>yes ~</th>
                  </tr>
                </thead>
                <tbody>
                  {weather.markets.map((m) => (
                    <tr key={m.id}>
                      <td className="td-mono">{m.id}</td>
                      <td>{m.question.slice(0, 64)}{m.question.length > 64 ? "…" : ""}</td>
                      <td>{String(m.lastTradePrice ?? m.probability ?? "—")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
      </section>

      <h2>Forecast preview (models vs markets)</h2>
      <section>
        <p className="muted">
          Open-Meteo (+ optional OpenWeather). Uses <code>data/forecast_cache.json</code> when fresh (&lt;10m);
          run <code>python -m forecast</code> or click refresh below to rebuild. Same grouping as the Telegram digest.
        </p>
        {forecastLoading ? (
          <p className="muted">loading forecast preview…</p>
        ) : forecastPreview?.error ? (
          <p className="err">{forecastPreview.error}</p>
        ) : forecastPreview?.groups?.length ? (
          <>
            <p className="muted">
              local {forecastPreview.generated_at_local ?? "—"} · scope <code>{forecastPreview.scope}</code> ·
              groups {forecastPreview.group_count} · days {forecastPreview.target_day_label}
              {forecastPreview.secondary_target_day_label
                ? ` + ${forecastPreview.secondary_target_day_label}`
                : ""}{" "}
              · Gamma pages {forecastPreview.max_pages_used ?? "—"} · scanned {forecastPreview.markets_scanned ?? "—"}{" "}
              · OW enabled {forecastPreview.openweather_enabled ? "yes" : "no"} · key{" "}
              {forecastPreview.openweather_key_configured ? "present" : "missing"}
              {forecastPreview.from_cache
                ? ` · cache ${forecastPreview.cache_age_sec != null ? `${forecastPreview.cache_age_sec}s old` : ""}`
                : ""}
              {forecastPreview.stale_after_error ? " · stale (live fetch failed)" : ""}
            </p>
            {forecastPreview.digest_meta ? (
              <p className="muted" style={{ marginTop: 4 }}>
                digest: parsable titles {forecastPreview.digest_meta.markets_parsable_temp_title ?? "—"} /{" "}
                {forecastPreview.digest_meta.markets_in_scope ?? "—"} scanned · distinct city-days{" "}
                {forecastPreview.digest_meta.city_day_groups_total ?? "—"} · included{" "}
                {forecastPreview.digest_meta.city_day_groups_included ?? "—"} · cap{" "}
                {forecastPreview.digest_meta.digest_max_groups_applied ?? "—"}
              </p>
            ) : null}
            {forecastPreview.groups.map((g) => (
              <div key={`${g.city_key}-${g.date_iso}`} style={{ marginBottom: "1.25rem" }}>
                <div style={{ fontWeight: "bold", marginBottom: 4 }}>
                  {g.city_key} · {g.date_iso} · {g.timezone_label} local
                </div>
                <p className="muted" style={{ margin: "0 0 0.5rem" }}>
                  Daily max forecast: OM {g.open_meteo_c != null ? `${g.open_meteo_c.toFixed(1)}°C` : "—"} · OW{" "}
                  {g.openweather_c != null ? `${g.openweather_c.toFixed(1)}°C` : "—"} · consensus{" "}
                  {g.consensus_c != null ? `${g.consensus_c.toFixed(1)}°C` : "—"}
                </p>
              </div>
            ))}
          </>
        ) : (
          <p className="muted">No parsable temperature markets in this scan (or empty batch).</p>
        )}
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => void loadForecastPreview(true)}
        >
          Refresh forecast preview (ignore cache)
        </button>
      </section>

      <h2>Daily blacklist</h2>
      <section>
        <p className="muted">merged with runtime blacklist · per calendar day file · Telegram on change</p>
        <div className="row">
          <div>
            <label htmlFor="bl">market id</label>
            <input id="bl" value={blMid} onChange={(e) => setBlMid(e.target.value)} type="text" placeholder="gamma market id" />
          </div>
          <button type="button" disabled={busy} onClick={() => void toggleBlacklist(true)}>
            Block today
          </button>
          <button type="button" className="secondary" disabled={busy} onClick={() => void toggleBlacklist(false)}>
            Unblock
          </button>
        </div>
        {cfg ? (
          <p className="muted" style={{ marginTop: "0.75rem" }}>
            today file: {cfg.blacklist_day_ids.join(", ") || "(empty)"} · effective:{" "}
            {cfg.effective_blacklist.join(", ") || "(empty)"}
          </p>
        ) : null}
      </section>

      <h2>Thresholds &amp; sizing</h2>
      <section>
        <p className="muted">Saving sends a Telegram message (if bot token/chat configured).</p>
        <div className="form-grid">
          {(
            [
              ["scan_interval_seconds", "bot loop sleep (sec)", true],
              ["dashboard_weather_max_pages", "dashboard weather Gamma pages (1–80)", true],
              ["cash_reserve_usd", "cash reserve ($) — never use for buys", false],
              ["min_order_notional_usd", "min order ($) per buy", false],
              ["max_buy_notional_usd", "max notional ($) per buy", false],
              ["max_trade_fraction_of_cash", "max fraction of tradable cash (0–1)", false],
              ["buy_min_threshold", "buy min yes", false],
              ["buy_max_threshold", "buy max yes", false],
              ["stop_loss_threshold", "stop loss yes", false],
              ["take_profit_threshold", "take profit yes", false],
              ["min_lead_over_runner_up", "min lead vs runner-up", false],
              ["momentum_window_min", "momentum window (min)", true],
              ["momentum_rise", "momentum rise ratio", false],
              ["momentum_min_price", "momentum min yes", false],
              ["momentum_max_entry", "momentum max entry", false],
              ["momentum_peer_drop", "peer drop sell ratio", false],
              ["churn_max_stop_cycles", "churn max stop cycles", true],
              ["churn_cooldown_sec", "churn cooldown sec", true],
            ] as const
          ).map(([k, label, asInt]) => (
            <div key={k}>
              <label htmlFor={k}>{label}</label>
              <input
                id={k}
                type="number"
                step="any"
                value={String(form[k as keyof FormState])}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    [k]: e.target.value === "" ? 0 : asInt ? Math.round(Number(e.target.value)) : Number(e.target.value),
                  }))
                }
              />
            </div>
          ))}
          <label className="check">
            <input
              type="checkbox"
              checked={form.enable_competition_filter}
              onChange={(e) => setForm((f) => ({ ...f, enable_competition_filter: e.target.checked }))}
            />
            competition filter
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={form.enable_momentum}
              onChange={(e) => setForm((f) => ({ ...f, enable_momentum: e.target.checked }))}
            />
            momentum
          </label>
        </div>

        <h3 style={{ marginTop: "1.25rem" }}>Forecast &amp; flow</h3>
        <p className="muted">
          <b>Refresh</b> = Gamma + Open-Meteo + <code>forecast_cache.json</code>. <b>Telegram</b> = optional digest
          message cadence (0 = never auto; use <code>/digest</code> in chat). Standalone{" "}
          <code>python -m forecast</code> uses the same two intervals.
        </p>
        <div className="form-grid">
          {(
            [
              ["forecast_digest_refresh_interval_sec", "forecast refresh / cache (sec)", true],
              ["forecast_digest_telegram_interval_sec", "Telegram digest (sec, 0=off)", true],
              ["forecast_digest_max_location_groups", "forecast max city-day groups (4–500, not /digest)", true],
              ["flow_max_events_per_tick", "flow max events per bot tick", true],
              ["flow_peer_window_sec", "flow peer window (sec)", true],
              ["flow_peer_surge_drop", "flow peer surge drop ratio", false],
              ["flow_peer_surge_rise", "flow peer surge rise ratio", false],
              ["forecast_contradict_margin_c", "forecast gate margin (°C)", false],
              ["forecast_weak_size_factor", "weak forecast size factor (0–1)", false],
            ] as const
          ).map(([k, label, asInt]) => (
            <div key={k}>
              <label htmlFor={k}>{label}</label>
              <input
                id={k}
                type="number"
                step="any"
                value={String(form[k as keyof FormState])}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    [k]: e.target.value === "" ? 0 : asInt ? Math.round(Number(e.target.value)) : Number(e.target.value),
                  }))
                }
              />
            </div>
          ))}
          <div>
            <label htmlFor="forecast_digest_scope">forecast digest scope</label>
            <select
              id="forecast_digest_scope"
              value={form.forecast_digest_scope}
              onChange={(e) => setForm((f) => ({ ...f, forecast_digest_scope: e.target.value }))}
            >
              <option value="scan">scan (weather batch)</option>
              <option value="positions">positions only</option>
            </select>
          </div>
        </div>
        <div className="form-grid" style={{ marginTop: "0.5rem" }}>
          <label className="check">
            <input
              type="checkbox"
              checked={form.forecast_digest_enabled}
              onChange={(e) => setForm((f) => ({ ...f, forecast_digest_enabled: e.target.checked }))}
            />
            forecast digest (Telegram)
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={form.enable_openweather_forecast}
              onChange={(e) => setForm((f) => ({ ...f, enable_openweather_forecast: e.target.checked }))}
            />
            OpenWeather second source
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={form.enable_flow_sampling}
              onChange={(e) => setForm((f) => ({ ...f, enable_flow_sampling: e.target.checked }))}
            />
            flow sampling (jsonl)
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={form.enable_flow_peer_exit}
              onChange={(e) => setForm((f) => ({ ...f, enable_flow_peer_exit: e.target.checked }))}
            />
            flow peer surge exit
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={form.forecast_gate_buy}
              onChange={(e) => setForm((f) => ({ ...f, forecast_gate_buy: e.target.checked }))}
            />
            forecast gate buy (blocks weak YES)
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={form.forecast_reduce_usd_if_weak}
              onChange={(e) => setForm((f) => ({ ...f, forecast_reduce_usd_if_weak: e.target.checked }))}
            />
            reduce USD if forecast weak
          </label>
        </div>

        <div className="row" style={{ gap: "0.75rem", marginTop: "0.5rem" }}>
          <button type="button" disabled={busy} onClick={() => void saveThresholds()}>
            Save (Telegram notify)
          </button>
          <button type="button" className="secondary" disabled={busy} onClick={() => void resetToDefaults()}>
            Reset to defaults
          </button>
        </div>
      </section>
    </>
  );
}
