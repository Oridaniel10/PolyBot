import { useCallback, useEffect, useState } from "react";

const api = (path: string, init?: RequestInit) =>
  fetch(path, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });

type PnlBucket = { buy_notional_usd: number; sell_notional_usd: number; est_pnl_usd: number; trade_count: number };
type PnlSummary = { day: PnlBucket; week: PnlBucket; as_of: string; error?: string | null };

type PositionRow = {
  market_id: string | null; condition_id: string | null; title: string; outcome: string;
  shares: number; avg_price: number; cur_price: number; cash_pnl: number; value_usd: number; bot_tracked: boolean;
};
type PortfolioResp = { cash_usd: number; positions_mtm_usd: number; total_usd: number; rows: PositionRow[]; error: string | null };

type TradeRow = {
  timestamp: string; local_hhmm?: string; action: string; market_id: string; market_title?: string;
  city: string; price: string; shares: string; usd: string; reason: string;
  entry_price: string; pnl_usd: string; cash_after: string; positions_mtm: string; total_value: string;
  consensus_c?: string; implied_yes?: string; edge?: string; required_edge?: string;
};

type DecisionRow = {
  ts: number; city: string; date_iso: string; bucket: string;
  model_prob: number; market_prob: number; edge: number; momentum_15m: number;
  decision: string; reason: string;
};

type DayStat = {
  date: string; total_trades: number; buys: number; sells: number; wins: number; losses: number;
  win_rate: number; total_pnl: number; biggest_win: number; biggest_loss: number;
  total_buy_usd: number; total_sell_usd: number; error?: string;
};

type RuntimeResp = { runtime: Record<string, unknown>; blacklist_day_ids: string[]; effective_blacklist: string[] };

type Tab = "positions" | "history" | "decisions" | "settings";

export default function App() {
  const [pnl, setPnl] = useState<PnlSummary | null>(null);
  const [positions, setPositions] = useState<PortfolioResp | null>(null);
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [decisions, setDecisions] = useState<DecisionRow[]>([]);
  const [cfg, setCfg] = useState<RuntimeResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("positions");

  // history tab state
  const [availDates, setAvailDates] = useState<string[]>([]);
  const [histDate, setHistDate] = useState("");
  const [histTrades, setHistTrades] = useState<TradeRow[]>([]);
  const [histStats, setHistStats] = useState<DayStat | null>(null);
  const [histLoading, setHistLoading] = useState(false);

  const [blMid, setBlMid] = useState("");

  const loadCore = useCallback(async () => {
    setErr(null);
    setLoading(true);
    try {
      const [pnlR, posR, cfgR, trR] = await Promise.all([
        api("/api/pnl/summary"), api("/api/portfolio/positions"),
        api("/api/runtime-config"), api("/api/trades/today"),
      ]);
      const pnlJ = pnlR.ok ? (await pnlR.json()) as PnlSummary : null;
      const posJ = posR.ok ? (await posR.json()) as PortfolioResp : null;
      const cfgJ = cfgR.ok ? (await cfgR.json()) as RuntimeResp : null;
      const trJ = trR.ok ? (await trR.json()) as TradeRow[] : [];
      if (pnlJ) setPnl(pnlJ);
      if (posJ) setPositions(posJ);
      if (cfgJ) setCfg(cfgJ);
      setTrades(trJ);
    } catch (e) { setErr(String(e)); } finally { setLoading(false); }
  }, []);

  const loadDecisions = useCallback(async () => {
    try {
      const r = await api("/api/decisions/recent?limit=100");
      if (r.ok) setDecisions((await r.json()) as DecisionRow[]);
    } catch {}
  }, []);

  const loadDates = useCallback(async () => {
    try {
      const r = await api("/api/trades/dates");
      if (r.ok) {
        const d = (await r.json()) as string[];
        setAvailDates(d);
        if (d.length > 0 && !histDate) setHistDate(d[d.length - 1]);
      }
    } catch {}
  }, [histDate]);

  useEffect(() => { void loadCore(); void loadDates(); }, [loadCore, loadDates]);
  useEffect(() => { if (tab === "decisions") void loadDecisions(); }, [tab, loadDecisions]);

  const loadHistory = useCallback(async (dt: string) => {
    if (!dt) return;
    setHistLoading(true);
    try {
      const [tR, sR] = await Promise.all([
        api(`/api/trades/history?date=${dt}`),
        api(`/api/stats/summary?date=${dt}`),
      ]);
      setHistTrades(tR.ok ? (await tR.json()) as TradeRow[] : []);
      setHistStats(sR.ok ? (await sR.json()) as DayStat : null);
    } catch {} finally { setHistLoading(false); }
  }, []);

  useEffect(() => { if (tab === "history" && histDate) void loadHistory(histDate); }, [tab, histDate, loadHistory]);

  async function toggleBlacklist(enabled: boolean) {
    if (!blMid.trim()) return;
    setBusy(true);
    try {
      await api("/api/blacklist/toggle", { method: "POST", body: JSON.stringify({ market_id: blMid.trim(), enabled }) });
      setBlMid("");
      await loadCore();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }

  async function saveConfig() {
    if (!cfg) return;
    setBusy(true);
    try {
      const res = await api("/api/runtime-config", { method: "POST", body: JSON.stringify(cfg.runtime) });
      if (!res.ok) throw new Error(await res.text());
      await loadCore();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }

  const todayPnl = pnl?.day.est_pnl_usd ?? 0;
  const totalVal = positions?.total_usd ?? 0;

  return (
    <div className="app">
      {/* ─── Header ─── */}
      <div className="header">
        <div className="header-title">Polymarket Trading</div>
        <div className="header-stat">
          <span>Equity</span>
          <strong>${totalVal.toFixed(2)}</strong>
        </div>
        <div className="header-stat">
          <span>Cash</span>
          <strong>${(positions?.cash_usd ?? 0).toFixed(2)}</strong>
        </div>
        <div className="header-stat">
          <span>Positions</span>
          <strong>${(positions?.positions_mtm_usd ?? 0).toFixed(2)}</strong>
        </div>
        <div className="header-stat">
          <span>Today PnL</span>
          <strong className={todayPnl >= 0 ? "pnl-pos" : "pnl-neg"}>
            {todayPnl >= 0 ? "+" : ""}${todayPnl.toFixed(2)}
          </strong>
        </div>
        <div className="header-actions">
          <button onClick={() => { void loadCore(); void loadDecisions(); }} disabled={busy}>Refresh</button>
        </div>
      </div>

      {/* ─── Tabs ─── */}
      <div className="tabs">
        {(["positions", "history", "decisions", "settings"] as Tab[]).map(t => (
          <button key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t === "positions" ? "Positions" : t === "history" ? "Trade History" : t === "decisions" ? "Decisions" : "Settings"}
          </button>
        ))}
      </div>

      {/* ─── Content ─── */}
      <div className="main">
        {err && <p className="err">{err}</p>}

        {/* ═══ Positions Tab ═══ */}
        {tab === "positions" && (
          <>
            <h2>Open Positions</h2>
            {loading ? <p className="muted">Loading...</p> :
              positions?.error ? <p className="err">{positions.error}</p> :
              !positions?.rows.length ? <p className="muted">No open positions.</p> : (
              <>
                {positions.rows.map((row, i) => {
                  const entry = row.avg_price;
                  const mark = row.cur_price;
                  const profit = mark >= entry;
                  const lo = Math.min(entry, mark);
                  const hi = Math.max(entry, mark);
                  const cityMatch = (row.title || "").match(/in (.+?) (?:be|on)/i);
                  const city = cityMatch ? cityMatch[1] : row.title.slice(0, 30);
                  return (
                    <div key={`pos-${row.market_id ?? i}`} className="pos-card">
                      <div className="pos-header">
                        <div>
                          <strong>{city}</strong>
                          <span className="muted" style={{ marginLeft: 8 }}>{row.outcome || "YES"}</span>
                        </div>
                        <div>
                          <span className={row.cash_pnl >= 0 ? "pnl-pos" : "pnl-neg"} style={{ fontSize: "1rem" }}>
                            {row.cash_pnl >= 0 ? "+" : ""}${row.cash_pnl.toFixed(2)}
                          </span>
                          <span className="muted" style={{ marginLeft: 8 }}>${row.value_usd.toFixed(2)}</span>
                        </div>
                      </div>
                      <div style={{ display: "flex", gap: "1.5rem", fontSize: "0.78rem", color: "#9aa0be", marginBottom: 6 }}>
                        <span>Entry <strong className="mono">{(entry * 100).toFixed(0)}%</strong></span>
                        <span>Mark <strong className="mono">{(mark * 100).toFixed(0)}%</strong></span>
                        <span>Shares <strong className="mono">{row.shares.toFixed(2)}</strong></span>
                        {row.bot_tracked && <span style={{ color: "var(--accent)" }}>BOT</span>}
                      </div>
                      <div className="pos-bar">
                        <div className="pos-bar-fill" style={{
                          left: `${Math.round(lo * 100)}%`,
                          width: `${Math.max(1, Math.round((hi - lo) * 100))}%`,
                          background: profit ? "var(--green-dim)" : "var(--red-dim)",
                        }} />
                        <div className="pos-bar-marker" style={{ left: `${Math.round(entry * 100)}%`, background: "var(--accent)" }} title={`Entry ${(entry * 100).toFixed(1)}%`} />
                        <div className="pos-bar-marker" style={{ left: `${Math.round(mark * 100)}%`, background: profit ? "var(--green)" : "var(--red)" }} title={`Mark ${(mark * 100).toFixed(1)}%`} />
                      </div>
                    </div>
                  );
                })}
              </>
            )}

            <h2>Today's Trades</h2>
            <section>
              {!trades.length ? <p className="muted">No trades today.</p> : (
                <>
                  <div className="equity-chart">
                    {trades.map((t, i) => {
                      const val = Number(t.total_value) || 0;
                      const allVals = trades.map(r => Number(r.total_value) || 0);
                      const mn = Math.min(...allVals) * 0.95;
                      const mx = Math.max(...allVals) * 1.02;
                      const pct = mx > mn ? ((val - mn) / (mx - mn)) * 100 : 50;
                      const color = t.action === "BUY" ? "var(--accent)" : Number(t.pnl_usd) >= 0 ? "var(--green)" : "var(--red)";
                      return <div key={i} className="equity-bar" style={{ height: `${Math.max(4, pct)}%`, background: color }} title={`${t.action} ${t.city} $${t.total_value}`} />;
                    })}
                  </div>
                  <div className="table-wrap">
                    <table>
                      <thead><tr>
                        <th>Time</th><th>Action</th><th>City</th><th>Price</th><th>USD</th><th>Reason</th><th>PnL</th><th>Total</th>
                      </tr></thead>
                      <tbody>
                        {trades.map((t, i) => {
                          const time = t.timestamp.split("T")[1]?.slice(0, 5) || t.timestamp;
                          const pnlV = Number(t.pnl_usd) || 0;
                          return (
                            <tr key={i}>
                              <td className="mono">{time}</td>
                              <td><span className={`badge badge-${t.action.toLowerCase() === "buy" ? "buy" : "sell"}`}>{t.action}</span></td>
                              <td>{t.city}</td>
                              <td className="mono">{Number(t.price).toFixed(2)}</td>
                              <td className="mono">${Number(t.usd).toFixed(2)}</td>
                              <td className="muted">{t.reason || "-"}</td>
                              <td className={pnlV >= 0 ? "pnl-pos" : "pnl-neg"}>{pnlV !== 0 ? `${pnlV >= 0 ? "+" : ""}$${pnlV.toFixed(2)}` : "-"}</td>
                              <td className="mono">${Number(t.total_value).toFixed(2)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </section>

            <h2>PnL Summary</h2>
            <section>
              {pnl?.error ? <p className="err">{pnl.error}</p> : pnl ? (
                <div className="stat-row">
                  <div className="stat-card">
                    <div className="label">Today PnL</div>
                    <div className={`value ${pnl.day.est_pnl_usd >= 0 ? "pnl-pos" : "pnl-neg"}`}>${pnl.day.est_pnl_usd}</div>
                  </div>
                  <div className="stat-card">
                    <div className="label">Today Trades</div>
                    <div className="value">{pnl.day.trade_count}</div>
                  </div>
                  <div className="stat-card">
                    <div className="label">Week PnL</div>
                    <div className={`value ${pnl.week.est_pnl_usd >= 0 ? "pnl-pos" : "pnl-neg"}`}>${pnl.week.est_pnl_usd}</div>
                  </div>
                  <div className="stat-card">
                    <div className="label">Week Trades</div>
                    <div className="value">{pnl.week.trade_count}</div>
                  </div>
                </div>
              ) : null}
            </section>
          </>
        )}

        {/* ═══ Trade History Tab ═══ */}
        {tab === "history" && (
          <>
            <h2>Trade History</h2>
            <div className="date-picker">
              <label style={{ margin: 0 }}>Date</label>
              <select value={histDate} onChange={e => setHistDate(e.target.value)} style={{ maxWidth: 180 }}>
                {availDates.map(d => <option key={d} value={d}>{d}</option>)}
              </select>
              <button onClick={() => { void loadHistory(histDate); void loadDates(); }}>Load</button>
            </div>

            {histStats && !histStats.error && (
              <div className="stat-row">
                <div className="stat-card">
                  <div className="label">Total PnL</div>
                  <div className={`value ${histStats.total_pnl >= 0 ? "pnl-pos" : "pnl-neg"}`}>${histStats.total_pnl}</div>
                </div>
                <div className="stat-card">
                  <div className="label">Win Rate</div>
                  <div className="value">{(histStats.win_rate * 100).toFixed(0)}%</div>
                </div>
                <div className="stat-card">
                  <div className="label">Trades</div>
                  <div className="value">{histStats.total_trades}</div>
                </div>
                <div className="stat-card">
                  <div className="label">Buys / Sells</div>
                  <div className="value">{histStats.buys} / {histStats.sells}</div>
                </div>
                <div className="stat-card">
                  <div className="label">Best Win</div>
                  <div className="value pnl-pos">${histStats.biggest_win}</div>
                </div>
                <div className="stat-card">
                  <div className="label">Worst Loss</div>
                  <div className="value pnl-neg">${histStats.biggest_loss}</div>
                </div>
              </div>
            )}

            <section>
              {histLoading ? <p className="muted">Loading...</p> :
                !histTrades.length ? <p className="muted">No trades for {histDate}.</p> : (
                <>
                  <div className="equity-chart">
                    {histTrades.map((t, i) => {
                      const val = Number(t.total_value) || 0;
                      const allVals = histTrades.map(r => Number(r.total_value) || 0);
                      const mn = Math.min(...allVals) * 0.95;
                      const mx = Math.max(...allVals) * 1.02;
                      const pct = mx > mn ? ((val - mn) / (mx - mn)) * 100 : 50;
                      const color = t.action === "BUY" ? "var(--accent)" : Number(t.pnl_usd) >= 0 ? "var(--green)" : "var(--red)";
                      return <div key={i} className="equity-bar" style={{ height: `${Math.max(4, pct)}%`, background: color }} title={`${t.action} ${t.city}`} />;
                    })}
                  </div>
                  <div className="table-wrap">
                    <table>
                      <thead><tr>
                        <th>Time</th><th>Local</th><th>Action</th><th>City</th><th>Price</th><th>Shares</th><th>USD</th><th>Reason</th><th>Entry</th><th>PnL</th><th>Total</th>
                      </tr></thead>
                      <tbody>
                        {histTrades.map((t, i) => {
                          const time = t.timestamp.split("T")[1]?.slice(0, 8) || t.timestamp;
                          const pnlV = Number(t.pnl_usd) || 0;
                          return (
                            <tr key={i}>
                              <td className="mono">{time}</td>
                              <td className="mono">{t.local_hhmm ?? "-"}</td>
                              <td><span className={`badge badge-${t.action.toLowerCase() === "buy" ? "buy" : "sell"}`}>{t.action}</span></td>
                              <td>{t.city}</td>
                              <td className="mono">{Number(t.price).toFixed(3)}</td>
                              <td className="mono">{Number(t.shares).toFixed(2)}</td>
                              <td className="mono">${Number(t.usd).toFixed(2)}</td>
                              <td className="muted" style={{ maxWidth: 120, fontSize: "0.75rem" }}>{t.reason || "-"}</td>
                              <td className="mono">{Number(t.entry_price).toFixed(3)}</td>
                              <td className={pnlV > 0 ? "pnl-pos" : pnlV < 0 ? "pnl-neg" : "muted"}>
                                {pnlV !== 0 ? `${pnlV >= 0 ? "+" : ""}$${pnlV.toFixed(2)}` : "-"}
                              </td>
                              <td className="mono">${Number(t.total_value).toFixed(2)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </section>
          </>
        )}

        {/* ═══ Decisions Tab ═══ */}
        {tab === "decisions" && (
          <>
            <h2>Decision Engine Log</h2>
            <p className="muted">Last {decisions.length} decisions from the engine (in-memory, resets on restart).</p>
            <section>
              {!decisions.length ? <p className="muted">No decisions logged yet.</p> : (
                <div className="table-wrap">
                  <table>
                    <thead><tr>
                      <th>Time</th><th>Decision</th><th>City</th><th>Bucket</th><th>Model P</th><th>Market P</th><th>Edge</th><th>Mom 15m</th><th>Reason</th>
                    </tr></thead>
                    <tbody>
                      {decisions.map((d, i) => {
                        const dt = new Date(d.ts * 1000);
                        const time = dt.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
                        const cls = d.decision === "BUY" ? "buy" : d.decision === "SKIP" ? "skip" : "hold";
                        return (
                          <tr key={i}>
                            <td className="mono">{time}</td>
                            <td><span className={`badge badge-${cls}`}>{d.decision}</span></td>
                            <td>{d.city}</td>
                            <td className="td-title">{d.bucket}</td>
                            <td className="mono">{(d.model_prob * 100).toFixed(1)}%</td>
                            <td className="mono">{(d.market_prob * 100).toFixed(1)}%</td>
                            <td className={`mono ${d.edge > 0 ? "pnl-pos" : d.edge < 0 ? "pnl-neg" : ""}`}>
                              {d.edge >= 0 ? "+" : ""}{(d.edge * 100).toFixed(1)}%
                            </td>
                            <td className={`mono ${d.momentum_15m > 0.05 ? "pnl-pos" : d.momentum_15m < -0.05 ? "pnl-neg" : ""}`}>
                              {d.momentum_15m >= 0 ? "+" : ""}{(d.momentum_15m * 100).toFixed(1)}%
                            </td>
                            <td className="muted" style={{ maxWidth: 200, fontSize: "0.72rem" }}>{d.reason}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              <button style={{ marginTop: 8 }} onClick={() => void loadDecisions()}>Refresh Decisions</button>
            </section>
          </>
        )}

        {/* ═══ Settings Tab ═══ */}
        {tab === "settings" && cfg && (
          <>
            <h2>Runtime Configuration</h2>
            <section>
              <p className="muted">Changes are saved to <code>data/runtime_config.json</code> and applied next tick.</p>
              <div className="form-grid">
                {([
                  ["scan_interval_seconds", "Scan interval (sec)", true],
                  ["cash_reserve_usd", "Cash reserve ($)", false],
                  ["min_order_notional_usd", "Min order ($)", false],
                  ["max_buy_notional_usd", "Max buy ($)", false],
                  ["max_trade_fraction_of_cash", "Max fraction of cash", false],
                  ["buy_min_threshold", "Buy min YES", false],
                  ["buy_max_threshold", "Buy max YES", false],
                  ["take_profit_threshold", "Take profit", false],
                  ["stop_loss_normal", "SL (normal entry)", false],
                  ["stop_loss_momentum", "SL (momentum)", false],
                  ["stop_loss_double_momentum", "SL (double mom)", false],
                  ["double_momentum_entry_rise", "Dbl mom min rise", false],
                  ["double_momentum_min_price", "Dbl mom min price", false],
                  ["double_momentum_max_price", "Dbl mom max price", false],
                  ["min_lead_over_runner_up", "Competition lead gap", false],
                  ["research_min_edge", "Min edge (prob)", false],
                  ["research_edge_implied_soft_floor", "Edge soft floor P(implied)", false],
                  ["research_edge_implied_soft_boost", "Edge soft boost mult", false],
                  ["research_sigma_c", "Sigma C (0=auto)", false],
                  ["research_weather_taker_fee_rate", "Fee rate (weather)", false],
                  ["max_market_prob_for_buy", "Max market prob", false],
                  ["min_model_prob_for_buy", "Min model prob", false],
                  ["decision_min_model_peak_prob", "Min model peak", false],
                  ["max_positions_per_event", "Max pos/event", true],
                  ["buy_earliest_local_hour", "Earliest buy hour", true],
                  ["buy_latest_local_hour", "Latest buy hour", true],
                  ["peer_surge_rise_threshold", "Peer surge threshold", false],
                  ["peer_surge_event_cooldown_sec", "Peer cooldown (sec)", true],
                  ["momentum_window_seconds", "Momentum window (sec)", true],
                  ["momentum_fast_exit_drop", "Fast exit drop", false],
                  ["momentum_competitor_surge", "Competitor surge", false],
                  ["time_decay_hours", "Time decay (hours)", false],
                  ["time_decay_min_gain", "Decay min gain", false],
                  ["time_decay_max_price", "Decay max price", false],
                ] as const).map(([k, label, asInt]) => (
                  <div key={k}>
                    <label htmlFor={k}>{label}</label>
                    <input
                      id={k} type="number" step="any"
                      value={String(cfg.runtime[k] ?? "")}
                      onChange={e => setCfg(prev => prev ? {
                        ...prev, runtime: { ...prev.runtime, [k]: e.target.value === "" ? 0 : asInt ? Math.round(Number(e.target.value)) : Number(e.target.value) }
                      } : prev)}
                    />
                  </div>
                ))}
              </div>
              <div className="form-grid" style={{ marginTop: "0.5rem" }}>
                {([
                  ["enable_competition_filter", "Competition filter"],
                  ["research_edge_gate_buy", "Research edge gate"],
                  ["forecast_gate_buy", "Forecast gate buy"],
                  ["enable_peer_surge_exit", "Peer surge exit"],
                  ["buy_disable_price_band", "Disable price band"],
                  ["research_exit_on_model_flip", "Exit on model flip"],
                  ["decision_skip_telegram_notify", "Telegram skip notify"],
                ] as const).map(([k, label]) => (
                  <label className="check" key={k}>
                    <input type="checkbox" checked={Boolean(cfg.runtime[k])}
                      onChange={e => setCfg(prev => prev ? { ...prev, runtime: { ...prev.runtime, [k]: e.target.checked } } : prev)} />
                    {label}
                  </label>
                ))}
              </div>
              <div className="row" style={{ gap: "0.75rem", marginTop: "0.75rem" }}>
                <button className="primary" disabled={busy} onClick={() => void saveConfig()}>Save</button>
                <button disabled={busy} onClick={async () => {
                  if (!confirm("Reset ALL settings to defaults?")) return;
                  setBusy(true);
                  try { await api("/api/runtime-config/reset", { method: "POST" }); await loadCore(); } catch (e) { setErr(String(e)); } finally { setBusy(false); }
                }}>Reset to defaults</button>
              </div>
            </section>

            <h2>Daily Blacklist</h2>
            <section>
              <div className="row">
                <div>
                  <label>Market ID</label>
                  <input value={blMid} onChange={e => setBlMid(e.target.value)} type="text" placeholder="gamma market id" />
                </div>
                <button disabled={busy} onClick={() => void toggleBlacklist(true)}>Block today</button>
                <button disabled={busy} onClick={() => void toggleBlacklist(false)}>Unblock</button>
              </div>
              {cfg && (
                <p className="muted" style={{ marginTop: "0.5rem" }}>
                  Today: {cfg.blacklist_day_ids.join(", ") || "(empty)"} | Effective: {cfg.effective_blacklist.join(", ") || "(empty)"}
                </p>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  );
}
