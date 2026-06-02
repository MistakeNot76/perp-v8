import { useEffect, useState } from "react";
import { HashRouter, NavLink, Route, Routes } from "react-router-dom";
import { api, useWebSocket, type AppState, type WSMessage } from "./api";
import Positions from "./components/Positions";
import Trades from "./components/Trades";
import Backtester from "./components/Backtester";
import Config from "./components/Config";
import Logs from "./components/Logs";
import Cron from "./components/Cron";
import Validator from "./components/Validator";
import "./styles.css";

const TABS: { to: string; label: string; end?: boolean }[] = [
  { to: "/", label: "Positions", end: true },
  { to: "/trades", label: "Trades" },
  { to: "/backtester", label: "Backtester" },
  { to: "/config", label: "Config" },
  { to: "/logs", label: "Logs" },
  { to: "/cron", label: "Cron" },
  { to: "/validator", label: "Validator" },
];

export default function App() {
  const [state, setState] = useState<AppState | null>(null);
  const [ksBusy, setKsBusy] = useState(false);

  // initial fetch
  useEffect(() => {
    let alive = true;
    api.state()
      .then((s) => alive && setState(s))
      .catch(() => {});
    const onFocus = () =>
      api.state().then((s) => alive && setState(s)).catch(() => {});
    window.addEventListener("focus", onFocus);
    return () => {
      alive = false;
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  // live updates over websocket
  const { connected } = useWebSocket<WSMessage>((msg) => {
    if (msg.type === "state" && msg.data) {
      setState(msg.data as AppState);
    } else if (msg.type === "fills" || msg.type === "fills_update" || msg.type === "position" || msg.type === "positions") {
      api.state().then(setState).catch(() => {});
    } else if (msg.type === "killswitch" && msg.data) {
      setState((prev) => (prev ? { ...prev, ...(msg.data as object) } : prev));
    }
  });

  const equity = (state?.equity as number | undefined) ?? null;
  const upnl = (state?.upnl as number | undefined) ?? null;
  const killswitch = Boolean(state?.killswitch ?? state?.kill_switch);
  const running = Boolean(state?.running);

  const toggleKillswitch = async () => {
    if (ksBusy) return;
    setKsBusy(true);
    try {
      await api.killSwitch(killswitch ? "off" : "on");
      const s = await api.state();
      setState(s);
    } catch (e) {
      console.error("killswitch failed", e);
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
            <span>
              <span className={"dot " + (connected ? "on" : "off")} />
              {connected ? "ws" : "off"}
            </span>
            <span>
              <span className={"dot " + (running && !killswitch ? "on" : "off")} />
              {killswitch ? "KILLED" : running ? "running" : "stopped"}
            </span>
            {equity !== null && <span>eq {fmtNum(equity, 2)}</span>}
            {upnl !== null && (
              <span className={upnl >= 0 ? "pos" : "neg"}>uPNL {fmtNum(upnl, 2)}</span>
            )}
            <button
              className={"killswitch " + (killswitch ? "safe" : "danger")}
              onClick={toggleKillswitch}
              disabled={ksBusy}
              title={killswitch ? "Resume trading" : "Halt all trading"}
            >
              {ksBusy ? "..." : killswitch ? "RESUME" : "KILL"}
            </button>
          </div>
        </div>

        <div className="content">
          <Routes>
            <Route path="/" element={<Positions />} />
            <Route path="/trades" element={<Trades />} />
            <Route path="/backtester" element={<Backtester />} />
            <Route path="/config" element={<Config />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="/cron" element={<Cron />} />
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
