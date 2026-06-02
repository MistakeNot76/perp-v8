import { useEffect, useState } from "react";
import { api, useLocalStorage } from "../api";
import type { LogFile, LogsResponse } from "../types";

const REFRESH_MS = 5000;

function classify(line: string): string {
  const l = line.toLowerCase();
  if (l.includes("error") || l.includes("exception") || l.includes("traceback")) return "error";
  if (l.includes("warn")) return "warn";
  if (l.includes("debug")) return "debug";
  return "info";
}

export default function Logs() {
  const [files, setFiles] = useState<LogFile[]>([]);
  const [selected, setSelected] = useLocalStorage<string>("logs.file", "");
  const [data, setData] = useState<LogsResponse | null>(null);
  const [err, setErr] = useState<string>("");
  const [lines, setLines] = useState<number>(200);
  const [loading, setLoading] = useState(false);

  const loadFiles = () =>
    api
      .logFiles()
      .then((d) => {
        setFiles(d);
        if (!selected && d.length > 0) {
          const sys = d.find((f) => /system/i.test(f.name));
          setSelected(sys?.name ?? d[0].name);
        }
      })
      .catch((e) => setErr(String(e)));

  const loadTail = (name: string) => {
    if (!name) return;
    setLoading(true);
    api
      .logs(name, lines)
      .then((d) => {
        setData(d);
        setErr("");
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadFiles();
    const t = window.setInterval(loadFiles, 30000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selected) return;
    loadTail(selected);
    const t = window.setInterval(() => loadTail(selected), REFRESH_MS);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, lines]);

  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <label className="field" style={{ flex: "0 0 320px" }}>
          File
          <select value={selected} onChange={(e) => setSelected(e.target.value)}>
            {files.length === 0 && <option value="">(no files)</option>}
            {files.map((f) => (
              <option key={f.name} value={f.name}>
                {f.name} ({(f.size / 1024).toFixed(1)} KB)
              </option>
            ))}
          </select>
        </label>
        <label className="field" style={{ flex: "0 0 120px" }}>
          Lines
          <select
            value={String(lines)}
            onChange={(e) => setLines(Number(e.target.value))}
          >
            <option value="50">50</option>
            <option value="200">200</option>
            <option value="500">500</option>
            <option value="2000">2000</option>
          </select>
        </label>
        <button className="secondary" onClick={() => selected && loadTail(selected)}>Refresh</button>
        <span className="spacer" />
        {loading && <span className="muted">loading…</span>}
        {err && <span className="error">{err}</span>}
        {data && <span className="muted">{data.lines.length} / {data.total_lines} lines</span>}
      </div>

      <div className="card">
        {!selected ? (
          <div className="muted">Pick a file to view logs.</div>
        ) : data?.lines.length === 0 ? (
          <div className="muted">(empty)</div>
        ) : (
          <pre className="log">
            {(data?.lines ?? []).map((line, i) => (
              <span key={i} className={`log-line lv-${classify(line).toUpperCase()}`}>
                {line}
                {"\n"}
              </span>
            ))}
          </pre>
        )}
      </div>
    </div>
  );
}
