import { useEffect, useState } from "react";
import { api } from "../api";
import type { CronStatus } from "../types";

function formatTs(ts: string | number | null | undefined): string {
  if (ts === undefined || ts === null) return "—";
  try {
    const ms = typeof ts === "number" ? (ts < 1e12 ? ts * 1000 : ts) : new Date(ts).getTime();
    return new Date(ms).toISOString().replace("T", " ").slice(0, 19);
  } catch {
    return String(ts);
  }
}

/** Process status (replaces legacy Cron tab). */
export default function Process() {
  const [data, setData] = useState<CronStatus | null>(null);
  const [err, setErr] = useState<string>("");
  const [loading, setLoading] = useState(false);

  const load = () => {
    setLoading(true);
    api
      .process()
      .catch(() => api.cron())
      .then((d) => {
        setData(d);
        setErr("");
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    const t = window.setInterval(load, 10000);
    return () => window.clearInterval(t);
  }, []);

  const jobs = data?.jobs ?? [];
  const enabled = data?.enabled;
  const running = (data as any)?.running as boolean | undefined;
  const mode = (data as any)?.mode as string | undefined;

  return (
    <div>
      <h2 style={{ marginBottom: 8 }}>Process</h2>
      <p className="muted" style={{ marginBottom: 12 }}>
        Live runner status. Start with <code className="mono">python3 run_live.py</code>.
        Kill switch is controlled from the top bar or Config.
      </p>

      <div className="row" style={{ marginBottom: 8 }}>
        <button className="secondary" onClick={load}>
          Refresh
        </button>
        {loading && <span className="muted">loading…</span>}
        {err && <span className="error">{err}</span>}
        {mode && <span className="badge gray">{mode}</span>}
        {running !== undefined && (
          <span className={running ? "badge green" : "badge gray"}>
            {running ? "runner up" : "runner down"}
          </span>
        )}
        {enabled !== undefined && (
          <span className={enabled ? "success" : "muted"}>
            {enabled ? "kill switch clear" : "kill switch armed"}
          </span>
        )}
      </div>

      <div className="card">
        {jobs.length === 0 ? (
          <div className="muted">No process info reported.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Schedule</th>
                  <th>Last Seen</th>
                  <th>Status</th>
                  <th>Message</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j, i) => (
                  <tr key={(j as any).id ?? j.name ?? i}>
                    <td>{j.name}</td>
                    <td className="mono">{j.schedule}</td>
                    <td className="mono">{formatTs(j.last_run)}</td>
                    <td
                      className={
                        j.last_status === "ok" || j.last_status === "running"
                          ? "pos"
                          : j.last_status === "error"
                          ? "neg"
                          : ""
                      }
                    >
                      <span
                        className={
                          "badge " +
                          (j.last_status === "running" || j.last_status === "ok"
                            ? "green"
                            : j.last_status === "error"
                            ? "red"
                            : "gray")
                        }
                      >
                        {j.last_status ?? "—"}
                      </span>
                    </td>
                    <td className="muted">{j.last_message ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
