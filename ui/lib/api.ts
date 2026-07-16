import type { Experiment } from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export function wsUrl(expId: string): string {
  const base = API_BASE.replace(/^http/, "ws");
  return `${base}/api/experiments/${expId}/logs`;
}

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  list: () =>
    fetch(`${API_BASE}/api/experiments`, { cache: "no-store" }).then((r) =>
      j<Experiment[]>(r)
    ),
  get: (id: string) =>
    fetch(`${API_BASE}/api/experiments/${id}`, { cache: "no-store" }).then((r) =>
      j<Experiment>(r)
    ),
  analyze: (prompt: string, model?: string) =>
    fetch(`${API_BASE}/api/experiments/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, model: model || null }),
    }).then((r) => j<Experiment>(r)),
  approve: (id: string) =>
    fetch(`${API_BASE}/api/experiments/${id}/approve`, { method: "POST" }).then(
      (r) => j<Experiment>(r)
    ),
  stop: (id: string) =>
    fetch(`${API_BASE}/api/experiments/${id}/stop`, { method: "POST" }).then((r) =>
      j<{ stopped: string }>(r)
    ),
};

export function statusLabel(s: string): string {
  return s.replace(/_/g, " ");
}
