import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, useLocalStorage } from "../api";
import type { BacktestRequest, BacktestResponse } from "../api";

type FormState = {
  symbolsInput: string;
  tf: string;
  days: number;
  fvb_length: number;
  bxt_l1: number;
  bxt_l2: number;
  bxt_l3: number;
  adx_max: number;
  rsi2_oversold: number;
  rsi2_overbought: number;
  confirmation_bars: number;
  tp_atr_mult: number;
  sl_atr_mult: number;
  breakeven_bars: number;
  trail_after_be: number;
  max_bars: number;
  leverage: number;
  notional: number;
  maker_pct: number;
  taker_pct: number;
  slippage_pct: number;
};

const LS_KEY = "backtester:form:v2";

const TF_OPTIONS = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;

const DEFAULT_FORM: FormState = {
  symbolsInput: "BTCUSDT, ETHUSDT, SOLUSDT",
  tf: "15m",
  days: 90,
  fvb_length: 8,
  bxt_l1: 5,
  bxt_l2: 30,
  bxt_l3: 5,
  adx_max: 30,
  rsi2_oversold: 10,
  rsi2_overbought: 90,
  confirmation_bars: 6,
  tp_atr_mult: 2.0,
  sl_atr_mult: 1.5,
  breakeven_bars: 8,
  trail_after_be: 1.0,
  max_bars: 200,
  leverage: 15,
  notional: 100,
  maker_pct: 0.02,
  taker_pct: 0.06,
  slippage_pct: 0.05,
};

const LINE_COLORS = [
  "#58a6ff",
  "#3fb950",
  "#d29922",
  "#f85149",
  "#a371f7",
  "#db6d28",
  "#56d4dd",
  "#f778ba",
  "#7ee787",
  "#ffa657",
];

function parseSymbols(input: string): { ok: string[]; err?: string } {
  const parts = input
    .split(",")
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
  if (parts.length === 0) return { ok: [], err: "At least one symbol required" };
  if (parts.length > 10) return { ok: [], err: "Max 10 symbols" };
  for (const p of parts) {
    if (!/^[A-Z0-9]+USDT$/.test(p)) {
      return {
        ok: [],
        err: `Symbol "${p}" must end in USDT (A-Z/0-9 only)`,
      };
    }
  }
  return { ok: Array.from(new Set(parts)) };
}

function NumField({
  label,
  value,
  step,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  step?: number;
  min?: number;
  max?: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <label>{label}</label>
      <input
        type="number"
        value={Number.isFinite(value) ? value : 0}
        step={step ?? "any"}
        min={min}
        max={max}
        onChange={(e) => {
          const n = Number(e.target.value);
          if (Number.isFinite(n)) onChange(n);
        }}
      />
    </div>
  );
}

function formToRequest(f: FormState): BacktestRequest {
  const overrides: Record<string, unknown> = {
    strategy: {
      fvb_length: f.fvb_length,
      bxt_l1: f.bxt_l1,
      bxt_l2: f.bxt_l2,
      bxt_l3: f.bxt_l3,
      adx_max: f.adx_max,
      rsi2_oversold: f.rsi2_oversold,
      rsi2_overbought: f.rsi2_overbought,
      confirmation_bars: f.confirmation_bars,
    },
    exits: {
      tp_atr_mult: f.tp_atr_mult,
      sl_atr_mult: f.sl_atr_mult,
      breakeven_bars: f.breakeven_bars,
      trail_after_be: f.trail_after_be,
      max_bars: f.max_bars,
    },
    fees: {
      maker_pct: f.maker_pct,
      taker_pct: f.taker_pct,
      slippage_pct: f.slippage_pct,
    },
    execution: {
      leverage: f.leverage,
      notional: f.notional,
    },
  };
  // Flatten overrides into the request: the backend may accept either a flat
  // strategy/exits/fees payload or a nested overrides object.
  return {
    symbols: parseSymbols(f.symbolsInput).ok,
    tf: f.tf,
    days: f.days,
    strategy: overrides.strategy as Record<string, number>,
    exits: overrides.exits as Record<string, number>,
    fees: overrides.fees as Record<string, number>,
    leverage: f.leverage,
    notional: f.notional,
    overrides,
  };
}

export default function Backtester() {
  const [stored, setStored] = useLocalStorage<FormState | null>(LS_KEY, null);
  const [form, setForm] = useState<FormState>(stored ?? DEFAULT_FORM);
  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string>("");

  const update = (patch: Partial<FormState>) => {
    const next = { ...form, ...patch };
    setForm(next);
    setStored(next);
  };

  const sym = useMemo(
    () => parseSymbols(form.symbolsInput),
    [form.symbolsInput]
  );

  const onSubmit = async () => {
    if (!sym.ok.length) {
      setErr(sym.err ?? "Invalid symbols");
      return;
    }
    setErr("");
    setRunning(true);
    setResult(null);
    try {
      const r = await api.backtest(formToRequest(form));
      setResult(r);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const onReset = () => {
    setForm(DEFAULT_FORM);
    setStored(DEFAULT_FORM);
    setResult(null);
    setErr("");
  };

  const results = result?.symbols ?? [];

  // Build chart data: one row per index, one column per symbol.
  const chartData = useMemo(() => {
    if (!result) return [];
    const series = results.map((s) => ({
      name: s.symbol,
      points: s.equity_curve ?? [],
    }));
    const len = Math.max(0, ...series.map((s) => s.points.length));
    const out: Array<Record<string, number>> = [];
    for (let i = 0; i < len; i++) {
      const row: Record<string, number> = { idx: i };
      for (const s of series) {
        const p = s.points[i];
        if (p) row[s.name] = Number(p.equity) || 0;
      }
      out.push(row);
    }
    return out;
  }, [result, results]);

  return (
    <div className="panel">
      <h2>Backtester</h2>
      <p className="muted">
        Symbols must end in USDT (e.g. BTCUSDT). Max 10. Strategy params
        preloaded from config defaults; tweak and run.
      </p>

      <div className="card">
        <h3>Symbols & timeframe</h3>
        <div className="form-row">
          <div style={{ flex: 2 }}>
            <label>comma-separated symbols (max 10, must end in USDT)</label>
            <input
              type="text"
              value={form.symbolsInput}
              onChange={(e) => update({ symbolsInput: e.target.value })}
              placeholder="BTCUSDT, ETHUSDT, SOLUSDT"
              style={{ width: "100%" }}
            />
            {sym.err && <p className="error">{sym.err}</p>}
            {!sym.err && sym.ok.length > 0 && (
              <p className="muted">parsed: {sym.ok.join(", ")}</p>
            )}
          </div>
          <div style={{ flex: 1 }}>
            <label>timeframe</label>
            <select
              value={form.tf}
              onChange={(e) => update({ tf: e.target.value })}
            >
              {TF_OPTIONS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div style={{ flex: 1 }}>
            <label>days</label>
            <input
              type="number"
              min={1}
              max={3650}
              value={form.days}
              onChange={(e) => {
                const n = Number(e.target.value);
                if (Number.isFinite(n) && n >= 1) update({ days: n });
              }}
            />
          </div>
        </div>
      </div>

      <div className="card">
        <h3>Strategy</h3>
        <div className="form-grid">
          <NumField
            label="fvb_length"
            value={form.fvb_length}
            min={1}
            onChange={(v) => update({ fvb_length: v })}
          />
          <NumField
            label="bxt_l1"
            value={form.bxt_l1}
            min={1}
            onChange={(v) => update({ bxt_l1: v })}
          />
          <NumField
            label="bxt_l2"
            value={form.bxt_l2}
            min={1}
            onChange={(v) => update({ bxt_l2: v })}
          />
          <NumField
            label="bxt_l3"
            value={form.bxt_l3}
            min={1}
            onChange={(v) => update({ bxt_l3: v })}
          />
          <NumField
            label="adx_max"
            value={form.adx_max}
            min={0}
            onChange={(v) => update({ adx_max: v })}
          />
          <NumField
            label="rsi2_oversold"
            value={form.rsi2_oversold}
            min={0}
            max={100}
            onChange={(v) => update({ rsi2_oversold: v })}
          />
          <NumField
            label="rsi2_overbought"
            value={form.rsi2_overbought}
            min={0}
            max={100}
            onChange={(v) => update({ rsi2_overbought: v })}
          />
          <NumField
            label="confirmation_bars"
            value={form.confirmation_bars}
            min={1}
            onChange={(v) => update({ confirmation_bars: v })}
          />
        </div>
      </div>

      <div className="card">
        <h3>Exits</h3>
        <div className="form-grid">
          <NumField
            label="tp_atr_mult"
            value={form.tp_atr_mult}
            step={0.1}
            onChange={(v) => update({ tp_atr_mult: v })}
          />
          <NumField
            label="sl_atr_mult"
            value={form.sl_atr_mult}
            step={0.1}
            onChange={(v) => update({ sl_atr_mult: v })}
          />
          <NumField
            label="breakeven_bars"
            value={form.breakeven_bars}
            min={0}
            onChange={(v) => update({ breakeven_bars: v })}
          />
          <NumField
            label="trail_after_be"
            value={form.trail_after_be}
            step={0.1}
            onChange={(v) => update({ trail_after_be: v })}
          />
          <NumField
            label="max_bars"
            value={form.max_bars}
            min={1}
            onChange={(v) => update({ max_bars: v })}
          />
        </div>
      </div>

      <div className="card">
        <h3>Execution</h3>
        <div className="form-grid">
          <NumField
            label="leverage (x)"
            value={form.leverage}
            min={1}
            onChange={(v) => update({ leverage: v })}
          />
          <NumField
            label="notional (USD)"
            value={form.notional}
            min={1}
            onChange={(v) => update({ notional: v })}
          />
          <NumField
            label="maker_pct"
            value={form.maker_pct}
            step={0.001}
            onChange={(v) => update({ maker_pct: v })}
          />
          <NumField
            label="taker_pct"
            value={form.taker_pct}
            step={0.001}
            onChange={(v) => update({ taker_pct: v })}
          />
          <NumField
            label="slippage_pct"
            value={form.slippage_pct}
            step={0.001}
            onChange={(v) => update({ slippage_pct: v })}
          />
        </div>
      </div>

      <div className="row" style={{ marginBottom: 16 }}>
        <button
          className="primary"
          onClick={onSubmit}
          disabled={running || !sym.ok.length}
        >
          {running ? "running…" : "run backtest"}
        </button>
        <button onClick={onReset}>reset form</button>
      </div>

      {err && <p className="error">error: {err}</p>}

      {result && (
        <>
          {result.totals && (
            <div className="card">
              <h3>Totals</h3>
              <div className="kv-inline">
                <span className="muted">trades:</span>
                <span className="mono">{result.totals.trades}</span>
                <span className="muted">·</span>
                <span className="muted">win rate:</span>
                <span className="mono">
                  {(result.totals.win_rate * 100).toFixed(1)}%
                </span>
                <span className="muted">·</span>
                <span className="muted">profit factor:</span>
                <span className="mono">
                  {Number.isFinite(result.totals.profit_factor)
                    ? result.totals.profit_factor.toFixed(2)
                    : "—"}
                </span>
                <span className="muted">·</span>
                <span className="muted">net pnl:</span>
                <span
                  className={`mono ${
                    result.totals.pnl >= 0 ? "pnl-pos" : "pnl-neg"
                  }`}
                >
                  {result.totals.pnl.toFixed(2)}
                </span>
                {result.duration_s !== undefined && (
                  <>
                    <span className="muted">·</span>
                    <span className="muted">duration:</span>
                    <span className="mono">
                      {result.duration_s.toFixed(1)}s
                    </span>
                  </>
                )}
              </div>
            </div>
          )}

          {chartData.length > 0 && (
            <div className="card">
              <h3>Equity curve</h3>
              <div className="chart-container">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData}>
                    <CartesianGrid stroke="#30363d" strokeDasharray="3 3" />
                    <XAxis dataKey="idx" stroke="#8b949e" />
                    <YAxis stroke="#8b949e" />
                    <Tooltip
                      contentStyle={{
                        background: "#161b22",
                        border: "1px solid #30363d",
                        color: "#e6edf3",
                      }}
                    />
                    <Legend />
                    {results.map((s, i) => (
                      <Line
                        key={s.symbol}
                        type="monotone"
                        dataKey={s.symbol}
                        stroke={LINE_COLORS[i % LINE_COLORS.length]}
                        dot={false}
                        strokeWidth={1.5}
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          <div className="card">
            <h3>Per-symbol</h3>
            <table>
              <thead>
                <tr>
                  <th>symbol</th>
                  <th className="right">trades</th>
                  <th className="right">win rate</th>
                  <th className="right">net pnl</th>
                  <th className="right">pnl %</th>
                  <th className="right">profit factor</th>
                  <th className="right">max DD %</th>
                </tr>
              </thead>
              <tbody>
                {results.map((s) => {
                  const dd = s.max_dd_pct ?? s.max_drawdown_pct ?? 0;
                  return (
                    <tr key={s.symbol}>
                      <td className="mono">{s.symbol}</td>
                      <td className="right mono">{s.trades}</td>
                      <td className="right mono">
                        {(s.win_rate * 100).toFixed(1)}%
                      </td>
                      <td
                        className={`right mono ${
                          s.pnl >= 0 ? "pnl-pos" : "pnl-neg"
                        }`}
                      >
                        {s.pnl.toFixed(2)}
                      </td>
                      <td
                        className={`right mono ${
                          (s.pnl_pct ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"
                        }`}
                      >
                        {(s.pnl_pct ?? 0).toFixed(2)}%
                      </td>
                      <td className="right mono">
                        {Number.isFinite(s.profit_factor ?? 0)
                          ? (s.profit_factor ?? 0).toFixed(2)
                          : "—"}
                      </td>
                      <td className="right mono">{dd.toFixed(2)}%</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="card">
            <h3>Trades</h3>
            {results.flatMap((s) => s.trade_list ?? []).length === 0 ? (
              <p className="muted">No trades generated.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>time</th>
                    <th>symbol</th>
                    <th>side</th>
                    <th className="right">entry</th>
                    <th className="right">exit</th>
                    <th className="right">pnl</th>
                    <th>reason</th>
                  </tr>
                </thead>
                <tbody>
                  {results
                    .flatMap((s) =>
                      (s.trade_list ?? []).map((t) => ({ ...t, symbol: t.symbol ?? s.symbol }))
                    )
                    .sort((a, b) => {
                      const ta = typeof a.ts === "number" ? a.ts : new Date(a.closed_at ?? a.ts ?? 0).getTime();
                      const tb = typeof b.ts === "number" ? b.ts : new Date(b.closed_at ?? b.ts ?? 0).getTime();
                      return tb - ta;
                    })
                    .map((t, i) => {
                      const ep = t.entry ?? t.entry_price ?? 0;
                      const xp = t.exit ?? t.exit_price ?? t.price ?? 0;
                      return (
                        <tr key={`${t.symbol}-${i}`}>
                          <td className="muted mono">
                            {t.closed_at
                              ? String(t.closed_at).slice(0, 19).replace("T", " ")
                              : typeof t.ts === "number"
                              ? new Date(t.ts * (t.ts < 1e12 ? 1000 : 1))
                                  .toISOString()
                                  .slice(0, 19)
                                  .replace("T", " ")
                              : "—"}
                          </td>
                          <td className="mono">{t.symbol}</td>
                          <td
                            className={
                              String(t.side).toLowerCase() === "long" ||
                              t.side === "buy"
                                ? "long"
                                : "short"
                            }
                          >
                            {String(t.side).toUpperCase()}
                          </td>
                          <td className="right mono">{Number(ep).toFixed(4)}</td>
                          <td className="right mono">{Number(xp).toFixed(4)}</td>
                          <td
                            className={`right mono ${
                              t.pnl >= 0 ? "pnl-pos" : "pnl-neg"
                            }`}
                          >
                            {t.pnl.toFixed(4)}
                          </td>
                          <td>{t.reason ?? "—"}</td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
