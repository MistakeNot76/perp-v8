import { useEffect, useState } from "react";
import { api } from "../api";
import type { ConfigYaml } from "../types";

type CommonForm = {
  mode: string;
  symbols: string;
  leverage: number;
  notional: number;
  max_open_positions: number;
  max_total_notional: number;
  max_daily_loss_pct: number;
  max_drawdown_pct: number;
  kill_switch: boolean;
  maker_pct: number;
  taker_pct: number;
  slippage_pct: number;
  funding_pct_per_8h: number;
};

function cfgToForm(c: ConfigYaml): CommonForm {
  const any = c as any;
  return {
    mode: String(any?.system?.mode ?? "paper"),
    symbols: Array.isArray(any?.symbols) ? any.symbols.join(", ") : "",
    leverage: Number(any?.execution?.leverage ?? 15),
    notional: Number(any?.execution?.notional_per_trade ?? 100),
    max_open_positions: Number(any?.execution?.max_open_positions ?? 20),
    max_total_notional: Number(any?.execution?.max_total_notional ?? 4000),
    max_daily_loss_pct: Number(any?.risk?.max_daily_loss_pct ?? 5),
    max_drawdown_pct: Number(any?.risk?.max_drawdown_pct ?? 15),
    kill_switch: Boolean(any?.risk?.kill_switch),
    maker_pct: Number(any?.fees?.maker_pct ?? 0.02),
    taker_pct: Number(any?.fees?.taker_pct ?? 0.06),
    slippage_pct: Number(any?.fees?.slippage_pct ?? 0.05),
    funding_pct_per_8h: Number(any?.fees?.funding_pct_per_8h ?? 0.01),
  };
}

function applyForm(cfg: ConfigYaml, form: CommonForm): ConfigYaml {
  const next = JSON.parse(JSON.stringify(cfg)) as any;
  next.system = { ...(next.system || {}), mode: form.mode };
  next.symbols = form.symbols
    .split(",")
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
  next.execution = {
    ...(next.execution || {}),
    leverage: form.leverage,
    notional_per_trade: form.notional,
    max_open_positions: form.max_open_positions,
    max_total_notional: form.max_total_notional,
  };
  next.risk = {
    ...(next.risk || {}),
    max_daily_loss_pct: form.max_daily_loss_pct,
    max_drawdown_pct: form.max_drawdown_pct,
    kill_switch: form.kill_switch,
  };
  next.fees = {
    ...(next.fees || {}),
    maker_pct: form.maker_pct,
    taker_pct: form.taker_pct,
    slippage_pct: form.slippage_pct,
    funding_pct_per_8h: form.funding_pct_per_8h,
  };
  return next;
}

export default function Config() {
  const [cfg, setCfg] = useState<ConfigYaml | null>(null);
  const [form, setForm] = useState<CommonForm | null>(null);
  const [raw, setRaw] = useState("");
  const [showRaw, setShowRaw] = useState(false);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const load = () => {
    setLoading(true);
    setErr("");
    api
      .config()
      .then((c) => {
        setCfg(c);
        setForm(cfgToForm(c));
        setRaw(JSON.stringify(c, null, 2));
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const patch = (p: Partial<CommonForm>) => {
    if (!form) return;
    setForm({ ...form, ...p });
  };

  const saveCommon = () => {
    if (!cfg || !form) return;
    setSaving(true);
    setErr("");
    const next = applyForm(cfg, form);
    api
      .saveConfig(next)
      .then(() => {
        setCfg(next);
        setRaw(JSON.stringify(next, null, 2));
        setSavedAt(Date.now());
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setSaving(false));
  };

  const saveRaw = () => {
    setSaving(true);
    setErr("");
    let parsed: ConfigYaml;
    try {
      parsed = JSON.parse(raw);
    } catch (e) {
      setErr(`JSON parse error: ${(e as Error).message}`);
      setSaving(false);
      return;
    }
    api
      .saveConfig(parsed)
      .then(() => {
        setCfg(parsed);
        setForm(cfgToForm(parsed));
        setSavedAt(Date.now());
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setSaving(false));
  };

  return (
    <div>
      <h2 style={{ marginBottom: 8 }}>Config</h2>
      <p className="muted" style={{ marginBottom: 12 }}>
        Common knobs write to <code className="mono">config.yaml</code>. Advanced raw JSON is for full edits.
      </p>

      <div className="row" style={{ marginBottom: 8 }}>
        <button className="secondary" onClick={load} disabled={loading}>
          {loading ? "loading…" : "Reload"}
        </button>
        <button className="primary" onClick={showRaw ? saveRaw : saveCommon} disabled={saving || loading || !form}>
          {saving ? "saving…" : "Save"}
        </button>
        {err && <span className="error">{err}</span>}
        {savedAt && <span className="success">saved</span>}
        <span className="spacer" />
        <button className="secondary" onClick={() => setShowRaw((v) => !v)}>
          {showRaw ? "Hide raw JSON" : "Show raw JSON"}
        </button>
      </div>

      {form && !showRaw && (
        <>
          <div className="card">
            <h3>System & symbols</h3>
            <div className="form-grid">
              <div>
                <label>Mode</label>
                <select value={form.mode} onChange={(e) => patch({ mode: e.target.value })}>
                  <option value="paper">paper</option>
                  <option value="demo">demo</option>
                  <option value="live">live</option>
                </select>
              </div>
              <div style={{ gridColumn: "span 2" }}>
                <label>Symbols (comma-separated)</label>
                <input
                  value={form.symbols}
                  onChange={(e) => patch({ symbols: e.target.value })}
                  placeholder="SOLUSDT, BTCUSDT"
                />
              </div>
              <label className="check-inline">
                <input
                  type="checkbox"
                  checked={form.kill_switch}
                  onChange={(e) => patch({ kill_switch: e.target.checked })}
                />
                Kill switch armed
              </label>
            </div>
          </div>

          <div className="card" style={{ marginTop: 12 }}>
            <h3>Execution & risk</h3>
            <div className="form-grid">
              <Num label="Leverage (x)" value={form.leverage} onChange={(v) => patch({ leverage: v })} />
              <Num label="Notional / trade (USD)" value={form.notional} onChange={(v) => patch({ notional: v })} />
              <Num label="Max open positions" value={form.max_open_positions} onChange={(v) => patch({ max_open_positions: v })} />
              <Num label="Max total notional" value={form.max_total_notional} onChange={(v) => patch({ max_total_notional: v })} />
              <Num label="Max daily loss %" value={form.max_daily_loss_pct} onChange={(v) => patch({ max_daily_loss_pct: v })} />
              <Num label="Max drawdown %" value={form.max_drawdown_pct} onChange={(v) => patch({ max_drawdown_pct: v })} />
            </div>
            <p className="muted" style={{ marginTop: 8, fontSize: 12 }}>
              Leverage sets margin only (margin = notional ÷ leverage). PnL is on notional size.
            </p>
          </div>

          <div className="card" style={{ marginTop: 12 }}>
            <h3>Fees & costs</h3>
            <div className="form-grid">
              <Num label="Maker % (limit)" value={form.maker_pct} step={0.001} onChange={(v) => patch({ maker_pct: v })} />
              <Num label="Taker % (market)" value={form.taker_pct} step={0.001} onChange={(v) => patch({ taker_pct: v })} />
              <Num label="Slippage % / side" value={form.slippage_pct} step={0.001} onChange={(v) => patch({ slippage_pct: v })} />
              <Num label="Funding % / 8h (flat)" value={form.funding_pct_per_8h} step={0.001} onChange={(v) => patch({ funding_pct_per_8h: v })} />
            </div>
            <p className="muted" style={{ marginTop: 8, fontSize: 12 }}>
              Market fills use taker. Funding is a flat estimate, not live exchange rates.
            </p>
          </div>
        </>
      )}

      {showRaw && (
        <div className="card">
          <textarea
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            spellCheck={false}
            className="config-raw"
          />
        </div>
      )}
    </div>
  );
}

function Num({
  label,
  value,
  onChange,
  step,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  step?: number;
}) {
  return (
    <div>
      <label>{label}</label>
      <input
        type="number"
        value={Number.isFinite(value) ? value : 0}
        step={step ?? "any"}
        onChange={(e) => {
          const n = Number(e.target.value);
          if (Number.isFinite(n)) onChange(n);
        }}
      />
    </div>
  );
}
