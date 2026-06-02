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

export default function Cron() {
  const [data, setData] = useState<CronStatus | null>(null);
  const [err, setErr] = useState<string>("");
  const [loading, setLoading] = useState(false);

  const load = () => {
    setLoading(true);
    api.cron()
      .then((d) => setData(d))
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

  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <button className="secondary" onClick={load}>Refresh</button>
        {loading && <span className="muted">loading…</span>}
        {err && <span className="error">{err}</span>}
        {enabled !== undefined && (
          <span className={enabled ? "success" : "muted"}>
            {enabled ? "enabled" : "disabled"}
          </span>
        )}
      </div>

      <div className="card">
        {jobs.length === 0 ? (
          <div className="muted">No jobs configured.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Schedule</th>
                  <th>Last Run</th>
                  <th>Next Run</th>
                  <th>Last Status</th>
                  <th>Message</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j, i) => (
                  <tr key={(j as any).id ?? j.name ?? i}>
                    <td>{j.name}</td>
                    <td className="mono">{j.schedule}</td>
                    <td className="mono">{formatTs(j.last_run)}</td>
                    <td className="mono">{formatTs(j.next_run)}</td>
                    <td className={j.last_status === "ok" ? "pos" : j.last_status === "error" ? "neg" : ""}>
                      {j.last_status ?? "—"}
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
