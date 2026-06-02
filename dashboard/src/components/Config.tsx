import { useEffect, useState } from "react";
import { api } from "../api";
import type { ConfigYaml } from "../types";

export default function Config() {
  const [text, setText] = useState("");
  const [err, setErr] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const load = () => {
    setLoading(true);
    setErr("");
    api.config()
      .then((c) => {
        try {
          setText(JSON.stringify(c, null, 2));
        } catch {
          setText("");
        }
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const save = () => {
    setSaving(true);
    setErr("");
    let parsed: ConfigYaml | Record<string, unknown>;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      setErr(`JSON parse error: ${(e as Error).message}`);
      setSaving(false);
      return;
    }
    api.saveConfig(parsed)
      .then(() => {
        setSavedAt(Date.now());
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setSaving(false));
  };

  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <button className="secondary" onClick={load} disabled={loading}>
          {loading ? "loading…" : "Reload"}
        </button>
        <button className="primary" onClick={save} disabled={saving || loading}>
          {saving ? "saving…" : "Save"}
        </button>
        {err && <span className="error">{err}</span>}
        {savedAt && <span className="success">saved</span>}
        <span className="spacer" />
        <span className="muted">edit config as JSON; the server writes it to disk</span>
      </div>

      <div className="card">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
          style={{
            width: "100%",
            minHeight: "60vh",
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            fontSize: 12,
            whiteSpace: "pre",
          }}
        />
      </div>
    </div>
  );
}
