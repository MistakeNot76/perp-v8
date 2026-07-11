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
  fvb_band_mult: number;
  bxt_l1: number;
  bxt_l2: number;
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
  // Exit stack (both FVB targets + both BXT styles available)
  use_fixed_tp: boolean;
  use_trail: boolean;
  fvb_exit_enabled: boolean;
  fvb_exit_target: "vwap" | "inner";
  bxt_exit_same_tf_enabled: boolean;
  bxt_exit_ltf_enabled: boolean;
  bxt_ltf: string;
  partial_tp_enabled: boolean;
};

type OptimizeResult = {
  results?: Array<{
    symbol: string;
    error?: string;
    best?: {
      params: Record<string, number>;
      train_stats?: Record<string, number>;
      test_stats?: Record<string, number> | null;
      score?: number;
    };
    ranked?: Array<{
      params: Record<string, number>;
      train_stats?: Record<string, number>;
      test_stats?: Record<string, number> | null;
      score?: number;
    }>;
  }>;
  persisted?: { written?: Array<{ symbol: string; path: string }> };
  duration_s?: number;
  error?: string;
};

const LS_KEY = "backtester:form:v4";

const TF_OPTIONS = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;

const DEFAULT_FORM: FormState = {
  symbolsInput: "BTCUSDT, ETHUSDT, SOLUSDT",
  tf: "15m",
  days: 90,
  fvb_length: 20,
  fvb_band_mult: 1.5,
  bxt_l1: 5,
  bxt_l2: 30,
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
  use_fixed_tp: true,
  use_trail: true,
  fvb_exit_enabled: true,
  fvb_exit_target: "vwap",
  bxt_exit_same_tf_enabled: true,
  bxt_exit_ltf_enabled: true,
  bxt_ltf: "5m",
  partial_tp_enabled: true,
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

/** Backend win_rate is 0–100. Guard against older 0–1 fractions. */
function fmtWinRate(wr: number | undefined | null): string {
  if (wr == null || !Number.isFinite(wr)) return "—";
  const pct = wr <= 1.0 ? wr * 100 : wr;
  return `${pct.toFixed(1)}%`;
}

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
  hint,
}: {
  label: string;
  value: number;
  step?: number;
  min?: number;
  max?: number;
  onChange: (v: number) => void;
  hint?: string;
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
      {hint && <p className="muted" style={{ margin: "4px 0 0", fontSize: 11 }}>{hint}</p>}
    </div>
  );
}

function formToRequest(f: FormState): BacktestRequest {
  const strategy = {
    fvb_length: f.fvb_length,
    fvb_band_mult: f.fvb_band_mult,
    bxt_l1: f.bxt_l1,
    bxt_l2: f.bxt_l2,
    adx_max: f.adx_max,
    rsi2_oversold: f.rsi2_oversold,
    rsi2_overbought: f.rsi2_overbought,
    confirmation_bars: f.confirmation_bars,
  };
  const exits = {
    tp_atr_mult: f.tp_atr_mult,
    sl_atr_mult: f.sl_atr_mult,
    breakeven_bars: f.breakeven_bars,
    trail_after_be: f.trail_after_be,
    max_bars: f.max_bars,
    use_fixed_tp: f.use_fixed_tp,
    use_trail: f.use_trail,
    fvb_exit: {
      enabled: f.fvb_exit_enabled,
      target: f.fvb_exit_target,
    },
    bxt_exit: {
      same_tf: { enabled: f.bxt_exit_same_tf_enabled },
      lower_tf: { enabled: f.bxt_exit_ltf_enabled, tf: f.bxt_ltf },
    },
    partial_tp: { enabled: f.partial_tp_enabled, pct: 0.5, r_multiple: 1.0 },
  };
  const fees = {
    maker_pct: f.maker_pct,
    taker_pct: f.taker_pct,
    slippage_pct: f.slippage_pct,
  };
  return {
    symbols: parseSymbols(f.symbolsInput).ok,
    tf: f.tf,
    days: f.days,
    strategy,
    exits,
    fees,
    leverage: f.leverage,
    notional: f.notional,
    overrides: {
      strategy,
      exits,
      fees,
      execution: {
        leverage: f.leverage,
        notional_per_trade: f.notional,
        partial_tp: { enabled: f.partial_tp_enabled, pct: 0.5, r_multiple: 1.0 },
      },
    },
  };
}

export default function Backtester() {
  const [stored, setStored] = useLocalStorage<FormState | null>(LS_KEY, null);
  const [form, setForm] = useState<FormState>(() => {
    const s = stored ?? DEFAULT_FORM;
    // migrate v2 forms missing fvb_band_mult
    return {
      ...DEFAULT_FORM,
      ...s,
      fvb_band_mult: (s as FormState).fvb_band_mult ?? DEFAULT_FORM.fvb_band_mult,
    };
  });
  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [optResult, setOptResult] = useState<OptimizeResult | null>(null);
  const [running, setRunning] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [applyBest, setApplyBest] = useState(true);
  const [err, setErr] = useState<string>("");
  const [mode, setMode] = useState<"backtest" | "optimize">("backtest");

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
      if ((r as { error?: string }).error) {
        setErr(String((r as { error?: string }).error));
      }
      setResult(r);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const onOptimize = async () => {
    if (!sym.ok.length) {
      setErr(sym.err ?? "Invalid symbols");
      return;
    }
    setErr("");
    setOptimizing(true);
    setOptResult(null);
    try {
      const r = await api.optimize({
        symbols: sym.ok,
        tf: form.tf,
        days: form.days,
        apply_config: applyBest,
        write_params: true,
      });
      setOptResult(r as OptimizeResult);
      // Apply best params into the form for the first symbol that has a best
      const first = (r as OptimizeResult).results?.find((x) => x.best?.params);
      if (first?.best?.params) {
        const p = first.best.params;
        update({
          fvb_length: Number(p.fvb_length),
          fvb_band_mult: Number(p.fvb_band_mult),
          bxt_l1: Number(p.bxt_l1),
          bxt_l2: Number(p.bxt_l2),
          confirmation_bars: Number(p.confirmation_bars),
        });
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setOptimizing(false);
    }
  };

  const onReset = () => {
    setForm(DEFAULT_FORM);
    setStored(DEFAULT_FORM);
    setResult(null);
    setOptResult(null);
    setErr("");
  };

  const results = result?.symbols ?? [];

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
        Entries: close outside outer FVB band (×2) + bullish/bearish BXT zero-cross.
        Optimize finds per-perp FVB/BXT settings via train/test grid search.
      </p>

      <div className="row" style={{ marginBottom: 12, gap: 8 }}>
        <button
          className={mode === "backtest" ? "primary" : ""}
          onClick={() => setMode("backtest")}
        >
          Backtest
        </button>
        <button
          className={mode === "optimize" ? "primary" : ""}
          onClick={() => setMode("optimize")}
        >
          Optimize
        </button>
      </div>

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

      {mode === "backtest" && (
        <>
          <div className="card">
            <h3>Strategy (FVB + BXT)</h3>
            <div className="form-grid">
              <NumField
                label="fvb_length"
                value={form.fvb_length}
                min={1}
                onChange={(v) => update({ fvb_length: v })}
                hint="Band std / VWAP smooth window"
              />
              <NumField
                label="fvb_band_mult"
                value={form.fvb_band_mult}
                step={0.05}
                min={0.1}
                onChange={(v) => update({ fvb_band_mult: v })}
                hint="Inner=1×mult; entry uses outer=2×mult"
              />
              <NumField
                label="bxt_l1 (fast)"
                value={form.bxt_l1}
                min={1}
                onChange={(v) => update({ bxt_l1: v })}
              />
              <NumField
                label="bxt_l2 (slow)"
                value={form.bxt_l2}
                min={2}
                onChange={(v) => update({ bxt_l2: v })}
              />
              <NumField
                label="confirmation_bars"
                value={form.confirmation_bars}
                min={1}
                onChange={(v) => update({ confirmation_bars: v })}
                hint="BXT zero-cross lookback"
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
            </div>
            <p className="muted" style={{ marginTop: 8 }}>
              Unused knobs (not wired): bxt_l3, bxt_ll1, bxt_ll2, adx_trend_max.
            </p>
          </div>

          <div className="card">
            <h3>Exit modes (test both)</h3>
            <p className="muted">
              Enable any combination. Priority: SL → partial TP → FVB revert →
              same-TF BXT flip → lower-TF BXT flip → fixed TP → max bars.
              Same settings drive live once applied via optimize / config.
            </p>
            <div className="form-grid">
              <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input
                  type="checkbox"
                  checked={form.use_fixed_tp}
                  onChange={(e) => update({ use_fixed_tp: e.target.checked })}
                />
                Fixed ATR/% TP
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input
                  type="checkbox"
                  checked={form.use_trail}
                  onChange={(e) => update({ use_trail: e.target.checked })}
                />
                Giveback trail
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input
                  type="checkbox"
                  checked={form.partial_tp_enabled}
                  onChange={(e) => update({ partial_tp_enabled: e.target.checked })}
                />
                Partial TP @ 1R (50%)
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input
                  type="checkbox"
                  checked={form.fvb_exit_enabled}
                  onChange={(e) => update({ fvb_exit_enabled: e.target.checked })}
                />
                FVB mean-revert exit
              </label>
              <div>
                <label>FVB exit target</label>
                <select
                  value={form.fvb_exit_target}
                  onChange={(e) =>
                    update({ fvb_exit_target: e.target.value as "vwap" | "inner" })
                  }
                  disabled={!form.fvb_exit_enabled}
                >
                  <option value="vwap">VWAP center (full revert)</option>
                  <option value="inner">Inner band (earlier)</option>
                </select>
              </div>
              <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input
                  type="checkbox"
                  checked={form.bxt_exit_same_tf_enabled}
                  onChange={(e) =>
                    update({ bxt_exit_same_tf_enabled: e.target.checked })
                  }
                />
                Faster same-TF BXT flip
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input
                  type="checkbox"
                  checked={form.bxt_exit_ltf_enabled}
                  onChange={(e) =>
                    update({ bxt_exit_ltf_enabled: e.target.checked })
                  }
                />
                Lower-TF BXT flip
              </label>
              <div>
                <label>Lower TF for BXT exit</label>
                <select
                  value={form.bxt_ltf}
                  onChange={(e) => update({ bxt_ltf: e.target.value })}
                  disabled={!form.bxt_exit_ltf_enabled}
                >
                  {TF_OPTIONS.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          <div className="card">
            <h3>Exits (mechanical sizes)</h3>
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
        </>
      )}

      {mode === "optimize" && (
        <div className="card">
          <h3>Per-perp optimizer</h3>
          <p className="muted">
            Grid-searches fvb_length, fvb_band_mult, bxt_l1, bxt_l2, confirmation_bars
            on a 70/30 train/test split. Writes data/params/&#123;SYMBOL&#125;.json.
          </p>
          <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <input
              type="checkbox"
              checked={applyBest}
              onChange={(e) => setApplyBest(e.target.checked)}
            />
            Apply best params into config.yaml symbol_params
          </label>
          <div className="row" style={{ marginBottom: 8 }}>
            <button
              className="primary"
              onClick={onOptimize}
              disabled={optimizing || !sym.ok.length}
            >
              {optimizing ? "optimizing… (may take a while)" : "run optimize"}
            </button>
            <button onClick={onReset}>reset</button>
          </div>
        </div>
      )}

      {err && <p className="error">error: {err}</p>}

      {optResult && (
        <div className="card">
          <h3>Optimize results</h3>
          {optResult.duration_s != null && (
            <p className="muted">duration: {optResult.duration_s.toFixed(1)}s</p>
          )}
          <table>
            <thead>
              <tr>
                <th>symbol</th>
                <th>fvb_len</th>
                <th>fvb_mult</th>
                <th>bxt_l1</th>
                <th>bxt_l2</th>
                <th>conf</th>
                <th className="right">train PF</th>
                <th className="right">test PF</th>
                <th className="right">train n</th>
                <th className="right">test n</th>
              </tr>
            </thead>
            <tbody>
              {(optResult.results ?? []).map((r) => {
                if (r.error) {
                  return (
                    <tr key={r.symbol}>
                      <td className="mono">{r.symbol}</td>
                      <td colSpan={9} className="error">
                        {r.error}
                      </td>
                    </tr>
                  );
                }
                const p = r.best?.params;
                const tr = r.best?.train_stats;
                const te = r.best?.test_stats;
                if (!p) {
                  return (
                    <tr key={r.symbol}>
                      <td className="mono">{r.symbol}</td>
                      <td colSpan={9} className="muted">
                        no viable params
                      </td>
                    </tr>
                  );
                }
                return (
                  <tr key={r.symbol}>
                    <td className="mono">{r.symbol}</td>
                    <td className="mono">{p.fvb_length}</td>
                    <td className="mono">{p.fvb_band_mult}</td>
                    <td className="mono">{p.bxt_l1}</td>
                    <td className="mono">{p.bxt_l2}</td>
                    <td className="mono">{p.confirmation_bars}</td>
                    <td className="right mono">
                      {tr?.profit_factor != null && Number.isFinite(tr.profit_factor)
                        ? Number(tr.profit_factor).toFixed(2)
                        : "—"}
                    </td>
                    <td className="right mono">
                      {te?.profit_factor != null && Number.isFinite(te.profit_factor)
                        ? Number(te.profit_factor).toFixed(2)
                        : "—"}
                    </td>
                    <td className="right mono">{tr?.trades ?? "—"}</td>
                    <td className="right mono">{te?.trades ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {optResult.persisted?.written && optResult.persisted.written.length > 0 && (
            <p className="muted" style={{ marginTop: 8 }}>
              wrote: {optResult.persisted.written.map((w) => w.path).join(", ")}
            </p>
          )}
        </div>
      )}

      {result && mode === "backtest" && (
        <>
          {result.totals && (
            <div className="card">
              <h3>Totals</h3>
              <div className="kv-inline">
                <span className="muted">trades:</span>
                <span className="mono">{result.totals.trades}</span>
                <span className="muted">·</span>
                <span className="muted">win rate:</span>
                <span className="mono">{fmtWinRate(result.totals.win_rate)}</span>
                <span className="muted">·</span>
                <span className="muted">profit factor:</span>
                <span className="mono">
                  {result.totals.profit_factor != null &&
                  Number.isFinite(result.totals.profit_factor)
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
                      {Number(result.duration_s).toFixed(1)}s
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
                  <th className="right">profit factor</th>
                  <th className="right">max DD $</th>
                </tr>
              </thead>
              <tbody>
                {results.map((s) => {
                  const dd = Number(s.max_drawdown ?? s.max_dd_pct ?? 0);
                  return (
                    <tr key={s.symbol}>
                      <td className="mono">{s.symbol}</td>
                      <td className="right mono">{s.trades}</td>
                      <td className="right mono">{fmtWinRate(s.win_rate)}</td>
                      <td
                        className={`right mono ${
                          s.pnl >= 0 ? "pnl-pos" : "pnl-neg"
                        }`}
                      >
                        {Number(s.pnl).toFixed(2)}
                      </td>
                      <td className="right mono">
                        {s.profit_factor != null && Number.isFinite(Number(s.profit_factor))
                          ? Number(s.profit_factor).toFixed(2)
                          : "—"}
                      </td>
                      <td className="right mono">{dd.toFixed(2)}</td>
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
                    <th>entry reason</th>
                    <th>exit</th>
                  </tr>
                </thead>
                <tbody>
                  {results
                    .flatMap((s) =>
                      (s.trade_list ?? []).map((t) => ({
                        ...t,
                        symbol: t.symbol ?? s.symbol,
                      }))
                    )
                    .sort((a, b) => {
                      const ta =
                        typeof a.exit_ts === "number"
                          ? a.exit_ts
                          : typeof a.ts === "number"
                          ? a.ts
                          : 0;
                      const tb =
                        typeof b.exit_ts === "number"
                          ? b.exit_ts
                          : typeof b.ts === "number"
                          ? b.ts
                          : 0;
                      return Number(tb) - Number(ta);
                    })
                    .map((t, i) => {
                      const ep = t.entry ?? t.entry_price ?? 0;
                      const xp = t.exit ?? t.exit_price ?? t.price ?? 0;
                      const side = String(t.side ?? t.direction ?? "").toLowerCase();
                      const ts =
                        typeof t.exit_ts === "number"
                          ? t.exit_ts
                          : typeof t.ts === "number"
                          ? t.ts
                          : 0;
                      return (
                        <tr key={`${t.symbol}-${i}`}>
                          <td className="muted mono">
                            {ts
                              ? new Date(ts < 1e12 ? ts * 1000 : ts)
                                  .toISOString()
                                  .slice(0, 19)
                                  .replace("T", " ")
                              : "—"}
                          </td>
                          <td className="mono">{t.symbol}</td>
                          <td className={side === "long" || side === "buy" ? "long" : "short"}>
                            {side.toUpperCase() || "—"}
                          </td>
                          <td className="right mono">{Number(ep).toFixed(4)}</td>
                          <td className="right mono">{Number(xp).toFixed(4)}</td>
                          <td
                            className={`right mono ${
                              Number(t.pnl) >= 0 ? "pnl-pos" : "pnl-neg"
                            }`}
                          >
                            {Number(t.pnl).toFixed(4)}
                          </td>
                          <td className="muted" style={{ fontSize: 11 }}>
                            {String(t.entry_reason ?? "—")}
                          </td>
                          <td>{String(t.reason ?? "—")}</td>
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
