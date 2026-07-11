import { useMemo } from "react";
import { api, fmtNum, useLocalStorage, usePoll } from "../api";
import type { Trade } from "../types";

const REASON_HELP: Record<string, string> = {
  tp: "Fixed take-profit",
  sl: "Stop-loss",
  breakeven: "Breakeven stop",
  trail: "Trailing giveback stop",
  opposite_bx: "Same-TF BXT flip",
  opposite_bx_ltf: "Lower-TF BXT flip",
  fvb_revert: "FVB mean-revert target",
  max_bars: "Max bars held",
  partial_tp: "Partial take-profit @ R",
};

function formatTs(ts: number | string | undefined): string {
  if (ts === undefined || ts === null) return "—";
  if (typeof ts === "number") {
    const ms = ts < 1e12 ? ts * 1000 : ts;
    return new Date(ms).toISOString().replace("T", " ").slice(0, 19);
  }
  return String(ts);
}

function num(v: unknown): number {
  if (v === undefined || v === null) return 0;
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function sideOf(t: Trade): string {
  const any = t as any;
  return String(any.side || any.direction || "").toLowerCase();
}

function pnlOf(t: Trade): number {
  const any = t as any;
  return num(any.pnl ?? any.pnl_net ?? any.pnl_usd);
}

export default function Trades() {
  const [symbolFilter, setSymbolFilter] = useLocalStorage<string>("trades.symbolFilter", "");
  const [sideFilter, setSideFilter] = useLocalStorage<"all" | "long" | "short">("trades.sideFilter", "all");

  const { data, error, loading, refetch } = usePoll(() => api.trades() as Promise<Trade[]>, 5000);

  const symbols = useMemo(() => {
    if (!data) return [] as string[];
    const set = new Set<string>();
    for (const t of data) set.add(t.symbol);
    return Array.from(set).sort();
  }, [data]);

  const filtered = useMemo(() => {
    if (!data) return [] as Trade[];
    return data
      .filter((t) => (symbolFilter ? t.symbol === symbolFilter : true))
      .filter((t) => (sideFilter === "all" ? true : sideOf(t) === sideFilter))
      .sort((a, b) => {
        const at = (a as any).closed_at ?? (a as any).ts ?? "";
        const bt = (b as any).closed_at ?? (b as any).ts ?? "";
        return String(bt).localeCompare(String(at));
      });
  }, [data, symbolFilter, sideFilter]);

  const totalPnl = filtered.reduce((s, t) => s + pnlOf(t), 0);
  const wins = filtered.filter((t) => pnlOf(t) > 0).length;
  const winRate = filtered.length > 0 ? (wins / filtered.length) * 100 : 0;

  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <h2 style={{ margin: 0, flex: 1 }}>Trades</h2>
        <button className="secondary" onClick={refetch}>Refresh</button>
        {loading && <span className="muted">loading…</span>}
        {error && <span className="error">{error}</span>}
      </div>

      <div className="row" style={{ marginBottom: 12 }}>
        <label className="field" style={{ flex: "0 0 200px" }}>
          Symbol
          <select value={symbolFilter} onChange={(e) => setSymbolFilter(e.target.value)}>
            <option value="">All</option>
            {symbols.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <label className="field" style={{ flex: "0 0 160px" }}>
          Side
          <select
            value={sideFilter}
            onChange={(e) => setSideFilter(e.target.value as any)}
          >
            <option value="all">All</option>
            <option value="long">Long</option>
            <option value="short">Short</option>
          </select>
        </label>
        <span className="spacer" />
        <div className="stat">
          <div className="label">Filtered PnL</div>
          <div className={"value " + (totalPnl >= 0 ? "pos" : "neg")}>{fmtNum(totalPnl, 4)}</div>
        </div>
        <div className="stat">
          <div className="label">Win Rate</div>
          <div className="value">{winRate.toFixed(1)}%</div>
        </div>
        <div className="stat">
          <div className="label">Count</div>
          <div className="value">{filtered.length}</div>
        </div>
      </div>

      <div className="card">
        {filtered.length === 0 ? (
          <div className="muted">
            {data && data.length === 0
              ? "No closed trades yet. Run the live bot or a backtest."
              : "No trades match this filter."}
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th className="num">Qty</th>
                  <th className="num">Entry</th>
                  <th className="num">Exit</th>
                  <th className="num">PnL</th>
                  <th className="num">%</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {filtered.slice(0, 500).map((t, i) => {
                  const tt = t as any;
                  const ts = tt.closed_at ?? tt.ts;
                  const ep = num(tt.entry_price ?? tt.entry);
                  const xp = num(tt.exit_price ?? tt.exit ?? tt.price);
                  const q = num(tt.size ?? tt.qty);
                  const p = pnlOf(t);
                  const pp = num(tt.pnl_pct);
                  const side = sideOf(t);
                  const reason = String(tt.reason ?? "—");
                  const tip = REASON_HELP[reason] || reason;
                  return (
                    <tr key={tt.id ?? `${tt.symbol}-${ts}-${i}`}>
                      <td className="mono">{formatTs(ts)}</td>
                      <td>{tt.symbol}</td>
                      <td className={side === "long" ? "pos" : "neg"}>{side || "—"}</td>
                      <td className="num">{fmtNum(q, 4)}</td>
                      <td className="num">{fmtNum(ep, 4)}</td>
                      <td className="num">{fmtNum(xp, 4)}</td>
                      <td className={"num " + (p >= 0 ? "pos" : "neg")}>{fmtNum(p, 4)}</td>
                      <td className={"num " + (pp >= 0 ? "pos" : "neg")}>
                        {pp !== 0 ? (Math.abs(pp) <= 1 ? (pp * 100).toFixed(2) : pp.toFixed(2)) + "%" : "—"}
                      </td>
                      <td title={tip}>
                        <span className="mono">{reason}</span>
                        {REASON_HELP[reason] && (
                          <span className="muted" style={{ marginLeft: 6, fontSize: 11 }}>
                            {REASON_HELP[reason]}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {filtered.length > 500 && (
              <p className="muted" style={{ marginTop: 8 }}>
                Showing first 500 of {filtered.length} trades.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
