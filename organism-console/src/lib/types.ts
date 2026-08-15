export type PanelKey =
  | "command"
  | "lawyer"
  | "books"
  | "chess-trainer"
  | "agent"
  | "workspace"
  | "organism"
  | "memory-search"
  | "memories"
  | "ops"
  | "integrations"

export interface NavItem {
  key: PanelKey
  label: string
  path: string
  description: string
}

export interface HealthResponse {
  status?: string
  app?: string
  version?: string
  llamacpp?: string
  environment?: string
}

export interface StatusResponse {
  ready: boolean
  events_path?: string
  event_count?: number
  llamacpp_base_url?: string
  environment?: string
  llamacpp_reachable?: boolean
}

export interface ReadyResponse {
  ready?: boolean
}

export interface ToolsResponse {
  capabilities?: string[]
  count?: number
  tools?: string[]
}

export interface TraceItem {
  trace_id: string
  step_id: string
  phase: string
  actor: string
  action: string
  status: string
  duration_ms: number
  timestamp_ms?: number
  model?: string
  tokens?: number
  cost?: number
  summary?: string
  metadata?: Record<string, unknown>
}

export interface TracesResponse {
  count: number
  traces: TraceItem[]
}

export interface TraceSummaryItem {
  trace_id: string
  first_phase: string
  last_status: string
  total_duration_ms: number
  latest_timestamp_ms?: number
  summary?: string
  action_count: number
}

// Backend /traces/summary returns a dict: {count, window, status_counts, phase_counts, model_counts, latency_ms}
export interface TraceSummaryResponse {
  count: number
  window?: number
  status_counts?: Record<string, number>
  phase_counts?: Record<string, number>
  model_counts?: Record<string, number>
  latency_ms?: { avg: number; max: number; min: number }
}

export interface AdminStatusResponse {
  [key: string]: unknown
}

export interface AdminDashboardResponse {
  [key: string]: unknown
}

export interface AdminGenerationResponse {
  [key: string]: unknown
}

export type ChatRequest = {
  message: string
}

export type ChatResponse = {
  response?: string
  answer?: string
  output?: string
  result?: string
  [key: string]: unknown
}



