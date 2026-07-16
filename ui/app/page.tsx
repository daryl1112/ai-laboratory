"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, statusLabel } from "@/lib/api";
import type { Experiment } from "@/lib/types";

function metricSummary(e: Experiment): string {
  const latest: Record<string, number> = {};
  for (const m of e.metrics) latest[m.name] = m.value;
  const parts = Object.entries(latest).slice(0, 3).map(([k, v]) => `${k} ${v}`);
  return parts.join("  ·  ");
}

export default function Dashboard() {
  const [exps, setExps] = useState<Experiment[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.list().then((d) => alive && setExps(d)).catch((e) => alive && setErr(String(e.message)));
    load();
    const t = setInterval(load, 3000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const count = (s: string) => (exps ?? []).filter((e) => e.status === s).length;
  const total = exps?.length ?? 0;
  const completed = count("completed");

  return (
    <div className="view">
      <div className="eyebrow">Mission control</div>
      <h1>Experiment dashboard</h1>
      <p className="sub">All research runs, their live state, and outcomes across the lab.</p>

      <div className="stats">
        <div className="stat"><div className="k">Total runs</div><div className="v">{total}</div><div className="tr">all time</div></div>
        <div className="stat"><div className="k">Active now</div><div className="v cyan">{count("running") + count("building")}</div><div className="tr">containers live</div></div>
        <div className="stat"><div className="k">Completed</div><div className="v mint">{completed}</div><div className="tr">{total ? Math.round((completed / total) * 100) : 0}% of runs</div></div>
        <div className="stat"><div className="k">Awaiting approval</div><div className="v amber">{count("awaiting_approval")}</div><div className="tr">needs review</div></div>
      </div>

      <div className="rowhead">
        <h2>Recent experiments</h2>
        <Link href="/new" className="btn primary">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14" /></svg>
          New experiment
        </Link>
      </div>

      {err && <div className="err">Could not reach the backend at the API base. Is it running? ({err})</div>}

      {exps === null && !err && <div className="empty"><span className="spin" /> Loading experiments…</div>}

      {exps && exps.length === 0 && (
        <div className="empty">
          No experiments yet.<br />
          <Link href="/new" className="btn primary" style={{ marginTop: 16 }}>Launch your first experiment</Link>
        </div>
      )}

      <div className="exp-list">
        {(exps ?? []).map((e) => (
          <Link href={`/experiments/${e.id}`} key={e.id} className={`exp ${e.status}`}>
            <div className="bar" />
            <div className="mid">
              <div className="title">
                {e.plan?.title ?? "Analyzing brief…"} <span className="id">#{e.id}</span>
              </div>
              <div className="desc">{e.plan?.summary ?? e.prompt}</div>
              <div className="meta">
                {e.iteration > 0 && <span>ITER <b>{e.iteration}</b></span>}
                {e.metrics.length > 0 && <span>{metricSummary(e)}</span>}
                {e.container_id && <span>CTR <b>{e.container_id}</b></span>}
                <span>MODEL <b>{e.model}</b></span>
              </div>
            </div>
            <div className="right">
              <span className={`badge ${e.status}`}>{statusLabel(e.status)}</span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
