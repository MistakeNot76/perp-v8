import { useEffect, useRef, useState, useCallback } from "react";

// ---------- Types ----------
// The FastAPI server is the source of truth. These are kept loose (lots of
// optional fields and index signatures) so that the UI degrades gracefully
// when the backend is mid-deploy or returns a slightly different shape.

export type Side = "long" | "short";
export type Mode = "paper" | "demo" | "live";

export interface Position {
  symbol: string;
  side: Side | "flat" | string;
  size?: number;
  qty?: number;
  entry_price?: number;
  entry?: number;
  mark_price?: number;
  mark?: number;
  liq?: number;
  leverage?: number;
  lev?: number;
  unrealized_pnl?: number;
  upnl?: number;
  upnl_pct?: number;
  opened_at?: string;
  stop_loss?: number | null;
  take_profit?: number | null;
  [k: string]: unknown;
}

export interface Trade {
  id?: string | number;
  symbol: string;
  side: Side | "buy" | "sell" | string;
  entry_price?: number;
  entry?: number;
  exit_price?: number;
  exit?: number;
  price?: number;
  size?: number;
  qty?: number;
  pnl: number;
  pnl_pct?: number;
  reason?: string;
  opened_at?: string;
  closed_at?: string;
  ts?: number | string;
  fees?: number;
  [k: string]: unknown;
}

export interface AppState {
  mode?: Mode;
  uptime_s?: number;
  positions?: Position[];
  equity?: number;
  available?: number;
  margin?: number;
  daily_pnl?: number;
  total_pnl?: number;
  upnl?: number;
  kill_switch?: boolean;
  killswitch?: boolean;
  running?: boolean;
  active_symbols?: string[];
  timestamp?: string;
  [k: string]: unknown;
}

export interface LogFile {
  name: string;
  size: number;
  modified?: string;
  [k: string]: unknown;
}

export interface LogsResponse {
  file: string;
  lines: string[];
  total_lines: number;
}

export type ConfigYaml = Record<string, unknown>;

export interface BacktestRequest {
  symbols: string[];
  tf?: string;
  days?: number;
  strategy?: Record<string, number>;
  exits?: Record<string, number>;
  fees?: Record<string, number>;
  leverage?: number;
  notional?: number;
  overrides?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface BacktestSymbolResult {
  symbol: string;
  trades: number;
  win_rate: number;
  pnl: number;
  pnl_pct?: number;
  profit_factor?: number;
  max_drawdown_pct?: number;
  max_dd_pct?: number;
  equity_curve?: { ts: number | string; equity: number }[];
  equity?: { ts: number | string; equity: number }[];
  trade_list?: Trade[];
  trades_detail?: Trade[];
  [k: string]: unknown;
}

export interface BacktestResponse {
  symbols?: BacktestSymbolResult[];
  results?: BacktestSymbolResult[];
  totals?: {
    trades: number;
    pnl: number;
    win_rate: number;
    profit_factor: number;
  };
  duration_s?: number;
  [k: string]: unknown;
}

export interface ValidatorFailure {
  rule: string;
  severity: "error" | "warn" | "info";
  message: string;
  symbol?: string;
  detected_at: string;
}

export interface ValidatorResponse {
  ok: boolean;
  failures?: ValidatorFailure[];
  checks?: { name: string; ok: boolean; detail?: string }[];
  last_run?: string;
  [k: string]: unknown;
}

export interface CronStatus {
  jobs: {
    id?: string;
    name: string;
    schedule: string;
    last_run: string | number | null;
    next_run: string | number | null;
    last_status: "ok" | "error" | "running" | "never" | string;
    last_message?: string;
  }[];
  enabled?: boolean;
}

export type WSMessage = { type: string; data?: unknown; [k: string]: unknown };

// ---------- API client ----------

const JSON_HEADERS = { "Content-Type": "application/json" };

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) {
    const t = await res.text().catch(() => "");
    throw new Error(`${path} -> ${res.status}${t ? `: ${t}` : ""}`);
  }
  return (await res.json()) as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const t = await res.text().catch(() => "");
    throw new Error(`${path} -> ${res.status}${t ? `: ${t}` : ""}`);
  }
  return (await res.json()) as T;
}

export const api = {
  // canonical names
  state: () => getJson<AppState>("/api/state"),
  trades: () => getJson<Trade[]>("/api/trades"),
  logFiles: () => getJson<LogFile[]>("/api/logs").catch(() => [] as LogFile[]),
  logs: (file: string, lines = 200) =>
    getJson<LogsResponse>(`/api/logs?file=${encodeURIComponent(file)}&lines=${lines}`),
  config: () => getJson<ConfigYaml>("/api/config"),
  saveConfig: (cfg: ConfigYaml) => postJson<{ ok: boolean }>("/api/config", cfg),
  killSwitch: (action: "on" | "off" | { on: boolean; reason?: string }) =>
    postJson<{ ok: boolean; killswitch?: boolean }>("/api/killswitch", action),
  backtest: (req: BacktestRequest) => postJson<BacktestResponse>("/api/backtest", req),
  optimize: (req: Record<string, unknown>) =>
    postJson<Record<string, unknown>>("/api/optimize", req),
  validator: () => getJson<ValidatorResponse>("/api/validator"),
  cron: () => getJson<CronStatus>("/api/cron"),

  // legacy aliases
  getState: () => getJson<AppState>("/api/state"),
  getTrades: (limit = 500) =>
    getJson<Trade[]>(`/api/trades?limit=${limit}`),
  getConfig: () => getJson<ConfigYaml>("/api/config"),
  getCron: () => getJson<CronStatus>("/api/cron"),
  getValidator: () => getJson<ValidatorResponse>("/api/validator"),
  listLogs: () => getJson<LogFile[]>("/api/logs").catch(() => [] as LogFile[]),
  tailLog: (name: string, lines = 200) =>
    getJson<LogsResponse>(`/api/logs?file=${encodeURIComponent(name)}&lines=${lines}`),
  runBacktest: (req: BacktestRequest) => postJson<BacktestResponse>("/api/backtest", req),
  runOptimize: (req: Record<string, unknown>) =>
    postJson<Record<string, unknown>>("/api/optimize", req),
  toggleKillswitch: () =>
    postJson<{ ok: boolean; kill_switch: boolean }>("/api/killswitch", { on: true }),
};

// ---------- WebSocket hook ----------

export function useWebSocket<T = WSMessage>(onMessage?: (data: T) => void) {
  const [lastMessage, setLast] = useState<T | null>(null);
  const [connected, setConnected] = useState(false);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${window.location.host}/ws`;
    let ws: WebSocket | null = null;
    let alive = true;
    let retry = 0;

    const connect = () => {
      if (!alive) return;
      try {
        ws = new WebSocket(url);
      } catch {
        retry = Math.min(retry + 1, 6);
        setTimeout(connect, 500 * 2 ** retry);
        return;
      }
      ws.onopen = () => {
        retry = 0;
        setConnected(true);
      };
      ws.onclose = () => {
        setConnected(false);
        if (!alive) return;
        retry = Math.min(retry + 1, 6);
        setTimeout(connect, 500 * 2 ** retry);
      };
      ws.onerror = () => {
        try {
          ws?.close();
        } catch {
          /* noop */
        }
      };
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data) as T;
          setLast(data);
          onMessageRef.current?.(data);
        } catch {
          // ignore non-JSON
        }
      };
    };
    connect();
    return () => {
      alive = true;
      try {
        ws?.close();
      } catch {
        /* noop */
      }
    };
  }, []);

  return { lastMessage, connected };
}

// ---------- Imperative WebSocket client (legacy) ----------

export function connectWS(
  onMessage: (msg: WSMessage) => void,
  onStatus?: (ok: boolean) => void
): () => void {
  let stopped = false;
  let ws: WebSocket | null = null;
  let retry = 0;
  let timer: number | null = null;

  const open = () => {
    if (stopped) return;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}//${window.location.host}/ws`;
    try {
      ws = new WebSocket(url);
    } catch {
      onStatus?.(false);
      timer = window.setTimeout(open, 1000);
      return;
    }
    ws.onopen = () => {
      retry = 0;
      onStatus?.(true);
    };
    ws.onmessage = (ev) => {
      try {
        onMessage(JSON.parse(ev.data) as WSMessage);
      } catch {
        // ignore non-JSON
      }
    };
    ws.onclose = () => {
      onStatus?.(false);
      if (stopped) return;
      retry = Math.min(retry + 1, 6);
      timer = window.setTimeout(open, 500 * 2 ** retry);
    };
    ws.onerror = () => {
      onStatus?.(false);
      ws?.close();
    };
  };
  open();

  return () => {
    stopped = true;
    if (timer !== null) window.clearTimeout(timer);
    if (ws) {
      ws.onclose = null;
      ws.close();
    }
  };
}

// ---------- Polling hook ----------

export function usePoll<T>(
  fn: () => Promise<T>,
  intervalMs: number,
  deps: unknown[] = []
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refetch = useCallback(async () => {
    try {
      const v = await fn();
      setData(v);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    let timer: number | null = null;
    let alive = true;

    const tick = async () => {
      if (!alive) return;
      if (document.hidden) {
        timer = window.setTimeout(tick, intervalMs);
        return;
      }
      await refetch();
      if (!alive) return;
      timer = window.setTimeout(tick, intervalMs);
    };
    tick();
    return () => {
      alive = false;
      if (timer !== null) window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, refetch, ...deps]);

  return { data, error, loading, refetch };
}

// ---------- Local storage hook ----------

export function useLocalStorage<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw === null) return initial;
      return JSON.parse(raw) as T;
    } catch {
      return initial;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // quota or private mode
    }
  }, [key, value]);

  return [value, setValue] as const;
}

// ---------- Formatters ----------

export function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e9) return (n / 1e9).toFixed(digits) + "B";
  if (abs >= 1e6) return (n / 1e6).toFixed(digits) + "M";
  if (abs >= 1e3) return (n / 1e3).toFixed(digits) + "K";
  return n.toFixed(digits);
}

export function fmtPct(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return (n * 100).toFixed(digits) + "%";
}
