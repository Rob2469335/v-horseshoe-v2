export type PanelKey =
  | "agent"
  | "workspace"
  | "organism"
  | "memory-search"
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
  ollama?: string
  environment?: string
}

export interface StatusResponse {
  ready: boolean
  events_path?: string
  event_count?: number
  ollama_base_url?: string
  environment?: string
  ollama_reachable?: boolean
}

export interface ReadyResponse {
  ready?: boolean
}

export interface ToolsResponse {
  capabilities?: string[]
  count?: number
  tools?: string[]
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
