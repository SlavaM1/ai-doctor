export type JsonObject = Record<string, unknown>

export interface Incident {
  id: number
  fingerprint: string
  severity: string
  status: string
  diagnosis_status: string
  exception_type: string
  message: string
  root_cause: string
  service: string
  version: string
  first_seen: string
  last_seen: string
  occurrences: number
  event: JsonObject
}

export interface Diagnosis {
  id: number
  incident_id: number
  created_at: string
  model: string
  result: JsonObject
  error: string
}

export interface FixJob {
  id: number
  incident_id: number
  created_at: string
  status: string
  model: string
  goal: string
  base_commit: string
  workspace: string
  patch: string
  patch_sha: string
  changed_files: unknown[]
  events: unknown[]
  tests: JsonObject
  summary: string
  error: string
  calls_used?: number
  remaining_calls?: number
  usage?: { input_tokens: number; output_tokens: number; total_tokens: number; calls: number; latency_ms: number; approximate: boolean }
}

export interface AgentRun {
  id: number
  incident_id: number
  kind: string
  model: string
  created_at: string
  input_tokens: number
  output_tokens: number
  total_tokens: number
  calls: number
  latency_ms: number
  approximate: boolean
  error: string
}

export interface IncidentDetail {
  incident: Incident
  diagnoses: Diagnosis[]
  fix_jobs: FixJob[]
  agent_runs: AgentRun[]
  chat: unknown[]
}

export interface Deployment {
  id: number
  fix_job_id: number
  created_at: string
  status: string
  result: JsonObject
}

export interface Status {
  target: { running?: boolean; healthy?: boolean; container_id?: string; health?: unknown; status?: string; [key: string]: unknown }
  collector: JsonObject
  stats: { active_incidents?: number; errors_hour?: number; errors_day?: number; completed_fixes?: number; excluded_incidents?: number; excluded_occurrences?: number; excluded_errors_hour?: number; excluded_errors_day?: number }
  usage: { input_tokens?: number; output_tokens?: number; total_tokens?: number; calls?: number; latency_ms?: number }
  [key: string]: unknown
}

export interface Model { id: string; [key: string]: unknown }
export interface Settings extends JsonObject {
  default_diagnosis_model?: string
  default_fix_model?: string
  warn_threshold?: number
  warn_window_seconds?: number
  diagnosis_cooldown_seconds?: number
  max_context_tokens?: number
  max_output_tokens?: number
  max_agent_iterations?: number
  has_api_key?: boolean
  models?: Model[]
}
