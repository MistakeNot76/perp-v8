import { useEffect, useState } from "react";
import { HashRouter, NavLink, Route, Routes } from "react-router-dom";
import { api, useWebSocket, type AppState, type WSMessage } from "./api";
import Positions from "./components/Positions";
import Trades from "./components/Trades";
import Backtester from "./components/Backtester";
import Config from "./components/Config";
import Logs from "./components/Logs";
import Process from "./components/Process";
import Validator from "./components/Validator";
import "./styles.css";

const TABS: { to: string; label: string; end?: boolean }[] = [
  { to: "/", label: "Positions", end: true },
  { to: "/trades", label: "Trades" },
  { to: "/backtester", label: "Backtester" },
  { to: "/config", label: "Config" },
  { to: "/logs", label: "Logs" },
  { to: "/process", label: "Process" },
  { to: "/validator", label: "Validator" },
];

export default function App() {
  const [state, setState] = useState<AppState | null>(null);
  const [stateErr, setStateErr] = useState<string>("");
  const [ksBusy, setKsBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .state()
      .then((s) => {
        if (!alive) return;
        setState(s);
        setStateErr("");
      })
      .catch((e) => alive && setStateErr(String(e)));
    const onFocus = () =>
      api
        .state()
        .then((s) => alive && setState(s))
        .catch((e) => alive && setStateErr(String(e)));
    window.addEventListener("focus", onFocus);
    return () => {
      alive = false;
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  const { connected } = useWebSocket<WSMessage>((msg) => {
    if (msg.type === "state" && msg.data) {
      setState(msg.data as AppState);
      setStateErr("");
    } else if (
      msg.type === "fills" ||
      msg.type === "fills_update" ||
      msg.type === "position" ||
      msg.type === "positions"
    ) {
      api.state().then(setState).catch(() => {});
    } else if (msg.type === "killswitch" && msg.data) {
      setState((prev) => (prev ? { ...prev, ...(msg.data as object) } : prev));
    }
  });

  const equity = (state?.equity as number | undefined) ?? null;
  const upnl = (state?.upnl as number | undefined) ?? null;
  const killswitch = Boolean(state?.killswitch ?? state?.kill_switch);
  const running = Boolean(state?.running);
  const mode = (state?.mode as string | undefined) ?? "—";

  const toggleKillswitch = async () => {
    if (ksBusy) return;
    const next = killswitch ? "resume" : "halt";
    const ok = window.confirm(
      next === "halt"
        ? "Arm kill switch? The live runner will stop opening/managing new risk."
        : "Resume trading? This clears the kill switch in config.yaml."
    );
    if (!ok) return;
    setKsBusy(true);
    try {
      await api.killSwitch(killswitch ? "off" : "on");
      const s = await api.state();
      setState(s);
    } catch (e) {
      setStateErr(String(e));
    } finally {
      setKsBusy(false);
    }
  };

  return (
    <HashRouter>
      <div className="app">
        <div className="topbar">
          <div className="brand">perp-v8</div>
          <nav className="tabs">
            {TABS.map((t) => (
              <NavLink
                key={t.to}
                to={t.to}
                end={t.end}
                className={({ isActive }) => "tab" + (isActive ? " active" : "")}
              >
                {t.label}
              </NavLink>
            ))}
          </nav>
          <div className="status">
            <span title="WebSocket">
              <span className={"dot " + (connected ? "on" : "off")} />
              {connected ? "Connected" : "Offline"}
            </span>
            <span className="sep">·</span>
            <span className="mono">{mode}</span>
            <span className="sep">·</span>
            <span>
              <span className={"dot " + (running && !killswitch ? "on" : "off")} />
              {killswitch ? "Killed" : running ? "Running" : "Stopped"}
            </span>
            {equity !== null && (
              <span>
                Equity <strong className="mono">{fmtNum(equity, 2)}</strong>
              </span>
            )}
            {upnl !== null && (
              <span className={upnl >= 0 ? "pos" : "neg"}>
                Unrealized <strong className="mono">{fmtNum(upnl, 2)}</strong>
              </span>
            )}
            <button
              className={"killswitch " + (killswitch ? "safe" : "danger")}
              onClick={toggleKillswitch}
              disabled={ksBusy}
              title={killswitch ? "Resume trading" : "Halt all trading"}
            >
              {ksBusy ? "…" : killswitch ? "Resume" : "Kill"}
            </button>
          </div>
        </div>

        {stateErr && (
          <div className="banner error">
            Cannot reach API: {stateErr}. Is the dashboard server running?
          </div>
        )}

        <div className="content">
          <Routes>
            <Route path="/" element={<Positions />} />
            <Route path="/trades" element={<Trades />} />
            <Route path="/backtester" element={<Backtester />} />
            <Route path="/config" element={<Config />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="/process" element={<Process />} />
            <Route path="/cron" element={<Process />} />
            <Route path="/validator" element={<Validator />} />
            <Route path="*" element={<Positions />} />
          </Routes>
        </div>
      </div>
    </HashRouter>
  );
}

function fmtNum(n: number, digits = 2): string {
  const abs = Math.abs(n);
  if (abs >= 1e6) return (n / 1e6).toFixed(digits) + "M";
  if (abs >= 1e3) return (n / 1e3).toFixed(digits) + "K";
  return n.toFixed(digits);
}
