import React from "react"
import { describe, expect, it, vi, beforeEach } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import { useOrganismData } from "./organism-hooks"

// vi.mock is hoisted to the top of the module by Vitest, so any data the mock
// factory closes over must be created via vi.hoisted() to avoid a TDZ
// ReferenceError ("Cannot access 'X' before initialization").
const mocks = vi.hoisted(() => {
  return {
    status: {
      ready: true,
      environment: "test",
      ollama_reachable: true,
      llamacpp_reachable: true,
      event_count: 12,
      vision_configured: true,
      vision_tool_exposed: true,
      vision_runtime_available: true,
      installed_models: ["qwen3.5-4b"],
    },
    tools: {
      tools: ["search", "memory", "vision"],
      count: 3,
      capabilities: ["search", "memory", "vision"],
    },
    toolsCache: {
      cache_size: 4,
      cached_keys: ["k1", "k2"],
    },
    routerStats: {
      status: "active",
      total_routed: 42,
      success_rate: 95,
      active_model: "qwen3.5-4b",
      model_distribution: {},
      status_counts: {},
      latency_ms: { avg: 100, max: 200, min: 50 },
    },
    criticStats: {
      status: "online",
      accept_rate: 90,
      accepted: 9,
      rejected: 1,
      partial: 0,
      total_evaluated: 10,
      trace_count: 5,
      verdict: "healthy",
    },
    timeline: {
      points: [
        { bucket: "b1", event_count: 8, success_count: 7, partial_count: 1, fail_count: 0 },
        { bucket: "b2", event_count: 4, success_count: 4, partial_count: 0, fail_count: 0 },
      ],
    },
  }
})

const apiMock = vi.hoisted(() => ({
  api: {
    getStatus: vi.fn(),
    getTools: vi.fn(),
    getToolsCache: vi.fn(),
    getRouterStats: vi.fn(),
    getCriticStats: vi.fn(),
  },
}))

vi.mock("../../lib/api", () => apiMock)
vi.mock("../../state/ui-store", () => ({
  useUiStore: (selector: (s: { backendUrl: string }) => unknown) =>
    selector({ backendUrl: "http://127.0.0.1:8000" }),
}))

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  }
}

describe("useOrganismData", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMock.api.getStatus.mockReset()
    apiMock.api.getTools.mockReset()
    apiMock.api.getToolsCache.mockReset()
    apiMock.api.getRouterStats.mockReset()
    apiMock.api.getCriticStats.mockReset()
  })

  it("resolves core organism data when all queries succeed", async () => {
    apiMock.api.getStatus.mockResolvedValue(mocks.status)
    apiMock.api.getTools.mockResolvedValue(mocks.tools)
    apiMock.api.getToolsCache.mockResolvedValue(mocks.toolsCache)
    apiMock.api.getRouterStats.mockResolvedValue(mocks.routerStats)
    apiMock.api.getCriticStats.mockResolvedValue(mocks.criticStats)
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(mocks.timeline), { status: 200 })
    )

    const { result } = renderHook(() => useOrganismData(), { wrapper: makeWrapper() })

    await waitFor(() => expect(result.current.isLoading).toBe(false))

    expect(result.current.backendUrl).toBe("http://127.0.0.1:8000")
    expect(result.current.systemReady).toBe(true)
    expect(result.current.llamacppReady).toBe(true)
    expect(result.current.toolCount).toBe(3)
    expect(result.current.cacheSize).toBe(4)
    expect(result.current.eventCount).toBe(12)
    expect(result.current.totalTimelineEvents).toBe(12)
    expect(result.current.successRate).toBe(Math.round((11 / 12) * 100))
    expect(result.current.isError).toBe(false)
    expect(result.current.routerStats.total_routed).toBe(42)
    expect(result.current.criticAcceptRate).toBe(90)
  })

  it("keeps isError false when optional router/critic queries fail (page must not crash)", async () => {
    apiMock.api.getStatus.mockResolvedValue(mocks.status)
    apiMock.api.getTools.mockResolvedValue(mocks.tools)
    apiMock.api.getToolsCache.mockResolvedValue(mocks.toolsCache)
    apiMock.api.getRouterStats.mockRejectedValue(new Error("404 Not Found"))
    apiMock.api.getCriticStats.mockRejectedValue(new Error("404 Not Found"))
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(mocks.timeline), { status: 200 })
    )

    const { result } = renderHook(() => useOrganismData(), { wrapper: makeWrapper() })

    await waitFor(() => expect(result.current.isLoading).toBe(false))

    expect(result.current.systemReady).toBe(true)
    expect(result.current.isError).toBe(false)
    // fallback derived stats, not undefined
    expect(result.current.routerStats.status).toBe("active")
    expect(result.current.criticAcceptRate).toBeGreaterThan(0)
  })

  it("sets isError when the core status query fails", async () => {
    apiMock.api.getStatus.mockRejectedValue(new Error("503 Service Unavailable"))
    apiMock.api.getTools.mockResolvedValue(mocks.tools)
    apiMock.api.getToolsCache.mockResolvedValue(mocks.toolsCache)
    apiMock.api.getRouterStats.mockResolvedValue(mocks.routerStats)
    apiMock.api.getCriticStats.mockResolvedValue(mocks.criticStats)

    const { result } = renderHook(() => useOrganismData(), { wrapper: makeWrapper() })

    // statusQuery uses retry: 1, and React Query applies its default backoff
    // (~1000ms) before the retry completes, so isError flips true only after
    // that delay — beyond waitFor's 1s default. Give it more time.
    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 4000 })

    expect(result.current.systemReady).toBe(false)
  })
})
