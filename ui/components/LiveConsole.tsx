"use client";

import { useEffect, useRef, useState } from "react";
import { api, wsUrl } from "@/lib/api";
import type { Experiment, WSMessage } from "@/lib/types";

type Row =
  | { kind: "log"; line: string; tag: string | null; ts: string }
  | { kind: "artifact"; path: string; size: number; ts: string }
  | { kind: "metric"; name: string; value: number; unit: string; ts: string }
  | { kind: "iteration"; n: number; ts: string };

function fmtSize(b: number): string {
  if (!b) return "";
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

function clockOf(iso: string): string {
  try { return new Date(iso).toTimeString().slice(0, 8); } catch { return ""; }
}

export default function LiveConsole({ exp }: { exp: Experiment }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [metrics, setMetrics] = useState<Record<string, { value: number; unit: string }>>({});
  const [artifacts, setArtifacts] = useState<Record<string, number>>({});
  const [live, setLive] = useState(false);
  const [status, setStatus] = useState(exp.status);
  const [iter, setIter] = useState(exp.iteration);
  const logsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // seed from persisted state
    const seedM: Record<string, { value: number; unit: string }> = {};
    for (const m of exp.metrics) seedM[m.name] = { value: m.value, unit: m.unit };
    setMetrics(seedM);
    const seedA: Record<string, number> = {};
    for (const a of exp.artifacts) seedA[a.path] = a.size_bytes;
    setArtifacts(seedA);
  }, [exp.id]);

  useEffect(() => {
    const ws = new WebSocket(wsUrl(exp.id));
    ws.onopen = () => setLive(true);
    ws.onclose = () => setLive(false);
    ws.onmessage = (ev) => {
      const msg: WSMessage = JSON.parse(ev.data);
      if (msg.type === "status") { setStatus(msg.data.status); return; }
      if (msg.type === "metric") {
        setMetrics((m) => ({ ...m, [msg.data.name]: { value: msg.data.value, unit: msg.data.unit } }));
        setRows((r) => [...r, { kind: "metric", name: msg.data.name, value: msg.data.value, unit: msg.data.unit, ts: msg.at }]);
        return;
      }
      if (msg.type === "artifact") {
        setArtifacts((a) => ({ ...a, [msg.data.path]: msg.data.size_bytes }));
        setRows((r) => [...r, { kind: "artifact", path: msg.data.path, size: msg.data.size_bytes, ts: msg.at }]);
        return;
      }
      if (msg.type === "iteration") {
        setIter(msg.data.iteration);
        setRows((r) => [...r, { kind: "iteration", n: msg.data.iteration, ts: msg.at }]);
        return;
      }
      setRows((r) => [...r, { kind: "log", line: msg.data.line, tag: msg.data.tag ?? null, ts: msg.at }]);
    };
    return () => ws.close();
  }, [exp.id]);

  useEffect(() => {
    logsRef.current?.scrollTo({ top: logsRef.current.scrollHeight });
  }, [rows.length]);

  const isRunning = status === "running" || status === "building";

  return (
    <>
      <div className="live-head">
        <div className="live-title">
          <span className={`badge ${status}`}>{status.replace(/_/g, " ")}</span>
          <div>
            <h1>{exp.plan?.title ?? exp.id}</h1>
            <div className="crumb" style={{ marginTop: 4 }}>
              #{exp.id}{exp.container_id ? ` · CONTAINER ${exp.container_id}` : ""} · MODEL {exp.model}
            </div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {iter > 0 && <span className="iter-badge">ITERATION {iter}</span>}
          {isRunning && (
            <button className="btn danger" onClick={() => api.stop(exp.id)}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="6" y="6" width="12" height="12" rx="1" /></svg>
              Stop
            </button>
          )}
        </div>
      </div>

      <div className="console-wrap">
        <div className="console">
          <div className="console-bar">
            <span className="cd r" /><span className="cd y" /><span className="cd g" />
            <span className="lbl">ai-lab-{exp.id} — /experiments/{exp.id}</span>
            <span className={`live ${live ? "" : "off"}`}><span className="p" /> {live ? "LIVE STREAM" : "DISCONNECTED"}</span>
          </div>
          <div className="logs" ref={logsRef}>
            {rows.length === 0 && <div className="ln"><span className="tx" style={{ color: "var(--muted)" }}>Waiting for output…</span></div>}
            {rows.map((r, i) => {
              if (r.kind === "iteration")
                return <div className="divider" key={i}>Iteration {r.n}<span className="hl" /></div>;
              if (r.kind === "artifact")
                return (
                  <div className="artifact" key={i}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M3 7l9-4 9 4-9 4-9-4zM3 7v10l9 4 9-4V7" /></svg>
                    <b>Artifact</b><span>{r.path}</span><span className="path">{fmtSize(r.size)}</span>
                  </div>
                );
              if (r.kind === "metric")
                return (
                  <div className="metricline" key={i}>
                    <div className="m"><b>{r.value}{r.unit ? ` ${r.unit}` : ""}</b><span>{r.name}</span></div>
                  </div>
                );
              return (
                <div className="ln" key={i}>
                  <span className="ts">{clockOf(r.ts)}</span>
                  <span className={`tx ${r.tag ? `tag-${r.tag}` : ""}`}>{r.line}</span>
                </div>
              );
            })}
          </div>
        </div>

        <div className="side">
          <div className="side-panel">
            <h4>Artifacts ({Object.keys(artifacts).length})</h4>
            <div className="artifacts-side">
              {Object.keys(artifacts).length === 0 && <div style={{ color: "var(--muted)", fontFamily: "var(--mono)", fontSize: 12 }}>none yet</div>}
              {Object.entries(artifacts).map(([p, s]) => (
                <div className="art-item" key={p}>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /></svg>
                  <span className="nm">{p}</span><span className="sz">{fmtSize(s)}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="side-panel">
            <h4>Live metrics</h4>
            <div className="metric-mini">
              {Object.keys(metrics).length === 0 && <div style={{ color: "var(--muted)", fontFamily: "var(--mono)", fontSize: 12 }}>none yet</div>}
              {Object.entries(metrics).map(([name, m]) => (
                <div className="mm" key={name}>
                  <div className="top"><span className="l">{name}</span><span className="r">{m.value}{m.unit ? ` ${m.unit}` : ""}</span></div>
                  <div className="track"><i style={{ width: `${Math.min(100, m.unit === "percent" ? m.value * 100 : m.value)}%` }} /></div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
