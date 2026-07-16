export type Status =
  | "analyzing"
  | "awaiting_approval"
  | "building"
  | "running"
  | "completed"
  | "failed"
  | "stopped";

export interface PlanFile { path: string; purpose: string; content: string }
export interface DockerSpec { base_image: string; system_packages: string[]; entrypoint: string[] }

export interface Plan {
  title: string;
  objective: string;
  summary: string;
  language: string;
  libraries: string[];
  tools: string[];
  files: PlanFile[];
  docker: DockerSpec;
  benchmarks: string[];
  success_criteria: string[];
  risks: string[];
}

export interface Metric { name: string; value: number; unit: string; iteration: number | null; at: string }
export interface Artifact { path: string; size_bytes: number; kind: string; at: string }

export interface Experiment {
  id: string;
  prompt: string;
  status: Status;
  plan: Plan | null;
  model: string;
  container_id: string | null;
  metrics: Metric[];
  artifacts: Artifact[];
  iteration: number;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface WSMessage {
  type: "log" | "artifact" | "metric" | "iteration" | "status";
  data: any;
  at: string;
}
