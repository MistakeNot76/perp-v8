import { api, usePoll, fmtNum } from "../api";
import type { AppState, Position } from "../types";

export default function Positions() {
  const { data, error, loading, refetch } = usePoll(() => api.state() as Promise<AppState | null>, 3000);

  // AppState fields per backend may include: equity, available, upnl, margin, running,
  // killswitch, daily_pnl, total_pnl, kill_switch, mode, positions, ...
  const s = (data ?? {}) as any;
  const positions: Position[] = s.positions ?? [];
  const equity = num(s.equity);
  const available = num(s.available);
  const upnl = num(s.upnl);
  const margin = num(s.margin);
  const dailyPnl = num(s.daily_pnl);
  const totalPnl = num(s.total_pnl);

  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <button className="secondary" onClick={refetch}>Refresh</button>
        {loading && <span className="muted">loading…</span>}
        {error && <span className="error">{error}</span>}
      </div>

      <div className="grid">
        <Stat label="Equity" value={fmtNum(equity)} cls={(upnl ?? 0) >= 0 ? "pos" : "neg"} />
        <Stat label="Available" value={fmtNum(available)} />
        <Stat label="Unrealized PnL" value={fmtNum(upnl)} cls={(upnl ?? 0) >= 0 ? "pos" : "neg"} />
        <Stat label="Margin" value={fmtNum(margin)} />
        <Stat label="Daily PnL" value={fmtNum(dailyPnl)} cls={(dailyPnl ?? 0) >= 0 ? "pos" : "neg"} />
        <Stat label="Total PnL" value={fmtNum(totalPnl)} cls={(totalPnl ?? 0) >= 0 ? "pos" : "neg"} />
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        {positions.length === 0 ? (
          <div className="muted">No open positions.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th className="num">Qty</th>
                  <th className="num">Entry</th>
                  <th className="num">Mark</th>
                  <th className="num">uPnL</th>
                  <th className="num">Lev</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => {
                  const pp = p as any;
                  const q = num(pp.size ?? pp.qty, 4) ?? 0;
                  const ep = num(pp.entry_price ?? pp.entry, 4) ?? 0;
                  const mp = num(pp.mark_price ?? pp.mark, 4) ?? 0;
                  const up = num(pp.unrealized_pnl ?? pp.upnl) ?? 0;
                  const lv = num(pp.leverage, 0) ?? 0;
                  return (
                    <tr key={pp.symbol ?? i}>
                      <td>{pp.symbol}</td>
                      <td className={pp.side === "long" ? "pos" : "neg"}>{pp.side}</td>
                      <td className="num">{fmtNum(q, 4)}</td>
                      <td className="num">{fmtNum(ep, 4)}</td>
                      <td className="num">{fmtNum(mp, 4)}</td>
                      <td className={"num " + (up >= 0 ? "pos" : "neg")}>{fmtNum(up, 4)}</td>
                      <td className="num">{fmtNum(lv, 0)}x</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function num(v: unknown, _dp?: number): number | null {
  if (v === undefined || v === null) return null;
  const n = Number(v);
  if (!Number.isFinite(n)) return null;
  return n;
}

function Stat({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className={"value " + (cls ?? "")}>{value}</div>
    </div>
  );
}
