import { appConfig } from "./config"
import type { ChatResponse } from "./types"

export type GenerateRequest = {
  prompt: string
  model?: string | null
}

async function fetchJson<T>(baseUrl: string, path: string): Promise<T> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), appConfig.requestTimeoutMs)

  try {
    const response = await fetch(`${baseUrl}${path}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: controller.signal
    })

    if (!response.ok) {
      throw new Error(`Request failed: ${response.status} ${response.statusText}`)
    }

    return (await response.json()) as T
  } finally {
    window.clearTimeout(timeoutId)
  }
}

async function postJson<TRequest, TResponse>(baseUrl: string, path: string, body: TRequest): Promise<TResponse> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), appConfig.requestTimeoutMs)

  try {
    const response = await fetch(`${baseUrl}${path}`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json"
      },
      body: JSON.stringify(body),
      signal: controller.signal
    })

    if (!response.ok) {
      throw new Error(`Request failed: ${response.status} ${response.statusText}`)
    }

    return (await response.json()) as TResponse
  } finally {
    window.clearTimeout(timeoutId)
  }
}

export const api = {
  getHealth: <T>(baseUrl: string) => fetchJson<T>(baseUrl, appConfig.endpoints.health),
  getReady: <T>(baseUrl: string) => fetchJson<T>(baseUrl, appConfig.endpoints.ready),
  getStatus: <T>(baseUrl: string) => fetchJson<T>(baseUrl, appConfig.endpoints.status),
  getTools: <T>(baseUrl: string) => fetchJson<T>(baseUrl, appConfig.endpoints.tools),
  getToolsCache: <T>(baseUrl: string) => fetchJson<T>(baseUrl, appConfig.endpoints.toolsCache),
  getAdminStatus: <T>(baseUrl: string) => fetchJson<T>(baseUrl, appConfig.endpoints.adminStatus),
  getAdminDashboard: <T>(baseUrl: string) => fetchJson<T>(baseUrl, appConfig.endpoints.adminDashboard),
  getAdminGeneration: <T>(baseUrl: string) => fetchJson<T>(baseUrl, appConfig.endpoints.adminGeneration),
  sendChat: (baseUrl: string, prompt: string) =>
    postJson<GenerateRequest, ChatResponse>(baseUrl, "/generate", { prompt })
}
