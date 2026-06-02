import { useEffect, useState } from "react";
import { api } from "../api";
import type { ValidatorResponse } from "../types";

function formatTs(ts: string | undefined): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toISOString().replace("T", " ").slice(0, 19);
  } catch {
    return ts;
  }
}

export default function Validator() {
  const [data, setData] = useState<ValidatorResponse | null>(null);
  const [err, setErr] = useState<string>("");
  const [loading, setLoading] = useState(false);

  const load = () => {
    setLoading(true);
    api.validator()
      .then((d) => setData(d))
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    const t = window.setInterval(load, 15000);
    return () => window.clearInterval(t);
  }, []);

  const failures = data?.failures ?? [];
  const checks = (data as any)?.checks as Array<{ name: string; ok: boolean; detail?: string }> | undefined;
  const checkList = Array.isArray(checks) ? checks : [];

  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <button className="secondary" onClick={load}>Refresh</button>
        {loading && <span className="muted">loading…</span>}
        {err && <span className="error">{err}</span>}
        {data && (
          <span className={data.ok ? "success" : "error"}>
            {data.ok ? "all checks pass" : "issues found"}
          </span>
        )}
        {data?.last_run && <span className="muted">last run {formatTs(data.last_run)}</span>}
      </div>

      {checkList.length > 0 && (
        <div className="card">
          {checkList.map((c, i) => (
            <div className="check-row" key={c.name ?? i}>
              <span className={c.ok ? "pos" : "neg"}>{c.ok ? "[OK]" : "[FAIL]"}</span>
              <strong>{c.name}</strong>
              {c.detail && <span className="muted">— {c.detail}</span>}
            </div>
          ))}
        </div>
      )}

      {failures.length > 0 && (
        <div className="card" style={{ marginTop: 14 }}>
          <h2 style={{ marginBottom: 8 }}>Failures ({failures.length})</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Severity</th>
                  <th>Rule</th>
                  <th>Symbol</th>
                  <th>Message</th>
                  <th>Detected</th>
                </tr>
              </thead>
              <tbody>
                {failures.map((f, i) => (
                  <tr key={i}>
                    <td className={f.severity === "error" ? "neg" : f.severity === "warn" ? "" : "muted"}>
                      {f.severity}
                    </td>
                    <td className="mono">{f.rule}</td>
                    <td className="mono">{f.symbol ?? "—"}</td>
                    <td>{f.message}</td>
                    <td className="muted mono">{formatTs(f.detected_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {checkList.length === 0 && failures.length === 0 && !loading && !err && (
        <div className="card muted">No checks reported.</div>
      )}
    </div>
  );
}
