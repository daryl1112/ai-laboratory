"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";

const EXAMPLE = `You are an autonomous AI Computer Scientist tasked with researching, architecting, and optimizing a human-inspired AI Memory System. You have full access to tools that let you programmatically create, modify, and execute Docker containers.

Your goal is to find the most resource-efficient and highly accurate memory system by running an evolutionary, closed-loop development cycle across four pillars: Working Memory, Long-Term Consolidation, Retrieval Optimization, and Continuous Alignment.

Loop until diminishing returns or: Working Memory accuracy >= 95%, log compression >= 50% without fact loss, minimal retrieval latency under load.`;

export default function NewExperiment() {
  const router = useRouter();
  const [prompt, setPrompt] = useState(EXAMPLE);
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function analyze() {
    setBusy(true);
    setErr(null);
    try {
      const exp = await api.analyze(prompt, model || undefined);
      router.push(`/experiments/${exp.id}`);
    } catch (e: any) {
      setErr(String(e.message));
      setBusy(false);
    }
  }

  return (
    <div className="view">
      <div className="eyebrow">Define a run</div>
      <h1>New experiment</h1>
      <p className="sub">Describe the experiment or paste an autonomous agent brief. The architect model drafts a build plan for your approval.</p>

      <div className="compose">
        <div className="field-label">Experiment brief</div>
        <textarea className="prompt" spellCheck={false} value={prompt} onChange={(e) => setPrompt(e.target.value)} />

        <div className="field-label">Architect model (optional override)</div>
        <input className="text" placeholder="qwen2.5-coder:7b" value={model} onChange={(e) => setModel(e.target.value)} />

        {err && <div className="err">{err}</div>}

        <div className="compose-foot">
          <div style={{ flex: 1 }} />
          <button className="btn primary" onClick={analyze} disabled={busy || !prompt.trim()}>
            {busy ? <><span className="spin" /> Analyzing…</> : <>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
              Analyze &amp; generate plan
            </>}
          </button>
        </div>
      </div>
    </div>
  );
}
