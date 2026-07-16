"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import type { Experiment } from "@/lib/types";

export default function PlanReview({ exp, onLaunched }: { exp: Experiment; onLaunched: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const plan = exp.plan!;
  const [policy, setPolicy] = useState<string>(plan.network ?? "none");
  const [allow, setAllow] = useState<string>((plan.network_allowlist ?? []).join(", "));

  async function approve() {
    setBusy(true);
    setErr(null);
    try {
      const domains = allow.split(",").map((d) => d.trim()).filter(Boolean);
      await api.approve(exp.id, policy, policy === "restricted" ? domains : []);
      onLaunched();
    } catch (e: any) {
      setErr(String(e.message));
      setBusy(false);
    }
  }

  const policyBlurb: Record<string, string> = {
    none: "Fully isolated — no network. Dependencies are baked in at build time.",
    restricted: "Internet only for the allowlisted domains below, via a default-deny proxy.",
    open: "Full internet access. Use only when the experiment genuinely needs it.",
  };

  return (
    <div className="view">
      <div className="eyebrow">Architect output · #{exp.id}</div>
      <h1>Review experiment plan</h1>
      <p className="sub">The model analyzed your brief and proposed the following build. Nothing runs until you approve.</p>

      <div className="plan-grid">
        <div className="plan-main">
          <div className="panel pcard">
            <h3>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M4 6h16M4 12h16M4 18h10" /></svg>
              Summary
            </h3>
            <p>{plan.summary}</p>
          </div>

          <div className="panel pcard">
            <h3>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M3 7l9-4 9 4-9 4-9-4zM3 7v10l9 4 9-4V7M12 11v10" /></svg>
              Files to be generated ({plan.files.length})
            </h3>
            <div className="filelist">
              {plan.files.map((f) => (
                <div key={f.path}><span className="fi">▸</span> {f.path}</div>
              ))}
              {plan.files.length === 0 && <div style={{ color: "var(--muted)" }}>A demo orchestrator will be generated.</div>}
            </div>
          </div>

          <div className="panel pcard">
            <h3>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M8 12l3 3 5-6" /></svg>
              Libraries &amp; tools
            </h3>
            <div className="taglist" style={{ marginBottom: 14 }}>
              {plan.libraries.map((l) => <span className="tag" key={l}>{l}</span>)}
            </div>
            <div className="taglist">
              {plan.tools.map((t) => <span className="tag tool" key={t}>{t}</span>)}
            </div>
          </div>

          {plan.risks.length > 0 && (
            <div className="warn">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M12 9v4M12 17h.01M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /></svg>
              <div>{plan.risks.join(" · ")} — runs with CPU/memory/PID caps, dropped capabilities, a read-only root filesystem, and a hard timeout. Network access follows the policy you set on the right. The host Docker socket is never exposed to experiment code.</div>
            </div>
          )}

          {err && <div className="err">{err}</div>}
        </div>

        <aside style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="panel side-card">
            <h4>Run configuration</h4>
            <div className="kv"><span className="kk">Base image</span><span className="vv">{plan.docker.base_image}</span></div>
            <div className="kv"><span className="kk">Language</span><span className="vv">{plan.language}</span></div>
            <div className="kv"><span className="kk">Entrypoint</span><span className="vv">{plan.docker.entrypoint.join(" ")}</span></div>
            <div className="kv"><span className="kk">Network</span><span className="vv" style={{ color: policy === "none" ? "var(--mint)" : policy === "open" ? "var(--red)" : "var(--amber)" }}>{policy}</span></div>
          </div>

          <div className="panel side-card">
            <h4>Network access</h4>
            <div className="seg">
              {(["none", "restricted", "open"] as const).map((p) => (
                <button
                  key={p}
                  className={`seg-btn ${policy === p ? "on" : ""} ${p === "open" ? "danger" : ""}`}
                  onClick={() => setPolicy(p)}
                >
                  {p}
                </button>
              ))}
            </div>
            <p style={{ color: "var(--muted)", fontSize: 12, lineHeight: 1.6, marginTop: 10 }}>
              {policyBlurb[policy]}
            </p>
            {plan.network !== policy && (
              <p style={{ color: "var(--amber)", fontSize: 11, fontFamily: "var(--mono)", marginTop: 6 }}>
                overriding architect recommendation: {plan.network}
              </p>
            )}
            {policy === "restricted" && (
              <>
                <div className="field-label" style={{ margin: "14px 0 8px" }}>Allowed domains</div>
                <input
                  className="text"
                  style={{ fontSize: 12 }}
                  placeholder="pypi.org, huggingface.co"
                  value={allow}
                  onChange={(e) => setAllow(e.target.value)}
                />
              </>
            )}
          </div>

          <div className="panel side-card">
            <h4>Success criteria</h4>
            {plan.success_criteria.map((c, i) => (
              <div className="crit" key={i}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M5 12l5 5L20 7" /></svg>
                {c}
              </div>
            ))}
            {plan.success_criteria.length === 0 && <div style={{ color: "var(--muted)", fontSize: 12 }}>none specified</div>}
          </div>

          {plan.benchmarks.length > 0 && (
            <div className="panel side-card">
              <h4>Benchmarks</h4>
              {plan.benchmarks.map((b, i) => (
                <div className="crit" key={i}>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></svg>
                  {b}
                </div>
              ))}
            </div>
          )}
        </aside>
      </div>

      <div className="approve-bar">
        <Link href="/new" className="btn">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M19 12H5M11 18l-6-6 6-6" /></svg>
          Edit brief
        </Link>
        <div style={{ flex: 1 }} />
        <button className="btn primary" onClick={approve} disabled={busy}>
          {busy ? <><span className="spin" /> Launching…</> : <>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 4l14 8-14 8V4z" /></svg>
            Approve &amp; launch experiment
          </>}
        </button>
      </div>
    </div>
  );
}
