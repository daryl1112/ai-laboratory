"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Experiment } from "@/lib/types";
import PlanReview from "@/components/PlanReview";
import LiveConsole from "@/components/LiveConsole";

export default function ExperimentDetail({ params }: { params: { id: string } }) {
  const { id } = params;
  const [exp, setExp] = useState<Experiment | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    try {
      setExp(await api.get(id));
    } catch (e: any) {
      setErr(String(e.message));
    }
  }

  useEffect(() => {
    load();
    // Poll while the state is still settling; the console itself uses the WS.
    const t = setInterval(() => {
      setExp((cur) => {
        if (cur && ["running", "completed", "stopped"].includes(cur.status)) return cur;
        load();
        return cur;
      });
    }, 2000);
    return () => clearInterval(t);
  }, [id]);

  if (err) return <div className="view"><div className="err">{err}</div></div>;
  if (!exp) return <div className="view"><div className="empty"><span className="spin" /> Loading experiment…</div></div>;

  if (exp.status === "analyzing")
    return (
      <div className="view">
        <div className="eyebrow">Architect · #{exp.id}</div>
        <h1>Analyzing brief</h1>
        <div className="empty" style={{ marginTop: 20 }}>
          <span className="spin" /> The architect model is drafting a build plan…
        </div>
      </div>
    );

  if (exp.status === "failed" && !exp.plan)
    return (
      <div className="view">
        <div className="eyebrow">#{exp.id}</div>
        <h1>Analysis failed</h1>
        <div className="err">{exp.error}</div>
      </div>
    );

  if (exp.status === "awaiting_approval" && exp.plan)
    return <PlanReview exp={exp} onLaunched={load} />;

  // building / running / completed / stopped / failed-with-plan
  return <div className="view"><LiveConsole exp={exp} /></div>;
}
