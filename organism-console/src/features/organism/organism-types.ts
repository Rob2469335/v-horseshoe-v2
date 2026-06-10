export type OrganismSubsystem = "learning" | "healing" | "autonomy" | "vision" | "operator"

export type OrganismStatusResponse = {
  ready: boolean
  environment: string
  event_count: number
  events_path: string
  ollama_base_url: string
  ollama_reachable: boolean
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
