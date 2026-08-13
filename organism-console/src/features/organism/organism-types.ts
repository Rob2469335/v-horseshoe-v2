export type OrganismSubsystem = "learning" | "healing" | "autonomy" | "vision" | "operator"

export type OrganismStatusResponse = {
  installed_model_count?: number;
  installed_models?: string[];
  ready: boolean
  environment: string
  event_count: number
  events_path: string
  llamacpp_base_url: string
  llamacpp_reachable: boolean
  vision_configured: boolean
  vision_runtime_available: boolean
  vision_tool_exposed: boolean
  primary_vision_model: string | null
  vision_models_configured: string[]
  vision_models_installed: string[]
}

export type OrganismToolsResponse = {
  count: number
  capabilities: string[]
  vision_configured: boolean
  vision_runtime_available: boolean
  vision_tool_exposed: boolean
  vision_models_configured: string[]
  vision_models_installed: string[]
}

export type ToolsCacheResponse = {
  cache_size: number
  cached_keys: string[]
}

export type TimelinePoint = {
  bucket: string
  event_count: number
  success_count: number
  partial_count: number
  fail_count: number
}

export type TimelineResponse = {
  window_minutes: number
  points: TimelinePoint[]
}

export type SubsystemCardDisplay = {
  id: OrganismSubsystem
  label: string
  title: string
  value: string
  summary: string
  detail: string
  nextStep: string
}

export type PulseCardDisplay = {
  label: string
  value: string
  color: string
  detail: string
}

export type ReadoutItem = {
  label: string
  value: string
  accent: string
}

export type KnowledgePanelDisplay = {
  badge: string
  title: string
  intro: string
  bullets: string[]
  accent: string
}

export type RouterStatsResponse = {
  status: string
  total_routed: number
  success_rate: number
  active_model: string
  model_distribution: Record<string, number>
  status_counts: Record<string, number>
  latency_ms: { avg: number; max: number; min: number }
  error?: string
}

export type CriticStatsResponse = {
  status: string
  accept_rate: number
  accepted: number
  rejected: number
  partial: number
  total_evaluated: number
  trace_count: number
  verdict: string
  error?: string
}

export type HealingReadinessResponse = {
  status: string
  score: number
  rating: string
  factors: {
    verified_failures: number
    escalations: number
  }
  details?: {
    metrics?: {
      totals?: Record<string, number>
      recent?: unknown[]
    }
    audit_count?: number
    escalations?: number
  }
}

