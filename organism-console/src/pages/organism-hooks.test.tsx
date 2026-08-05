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
      events_path: "/tmp/events",
    },
    tools: {
      tools: ["search", "memory", "vision"],
      count: 3,
      capabilities: ["search", "memory", "vision"],
    },
    traceSummary: [
      {
        trace_id: "trace-001",
        first_phase: "plan",
        last_status: "success",
        total_duration_ms: 450,
        latest_timestamp_ms: Date.now(),
        action_count: 3,
      },
      {
        trace_id: "trace-002",
        first_phase: "execute",
        last_status: "partial",
        total_duration_ms: 800,
        latest_timestamp_ms: Date.now() - 60_000,
        action_count: 5,
      },
    ],
  }
})

vi.mock("../state/ui-store", () => ({
  useUiStore: (selector: (s: { backendUrl: string }) => unknown) =>
    selector({ backendUrl: "http://127.0.0.1:8000" }),
}))

const apiMock = vi.hoisted(() => ({
  api: {
    getStatus: vi.fn(),
    getTools: vi.fn(),
    getTraceSummary: vi.fn(),
  },
}))

vi.mock("../lib/api", () => apiMock)

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
    apiMock.api.getTraceSummary.mockReset()
  })

  it("resolves core organism data when all queries succeed", async () => {
    apiMock.api.getStatus.mockResolvedValue(mocks.status)
    apiMock.api.getTools.mockResolvedValue(mocks.tools)
    apiMock.api.getTraceSummary.mockResolvedValue(mocks.traceSummary)

    const { result } = renderHook(() => useOrganismData(), { wrapper: makeWrapper() })

    await waitFor(() => expect(result.current.isLoading).toBe(false))

    expect(result.current.backendUrl).toBe("http://127.0.0.1:8000")
    expect(result.current.systemReady).toBe(true)
    expect(result.current.toolCount).toBe(3)
    expect(result.current.traceCount).toBe(2)
    expect(result.current.isError).toBe(false)
    expect(result.current.traceSummaryQuery.isError).toBe(false)
  })

  it("keeps isError false when optional traceSummary query fails (page must not crash)", async () => {
    apiMock.api.getStatus.mockResolvedValue(mocks.status)
    apiMock.api.getTools.mockResolvedValue(mocks.tools)
    apiMock.api.getTraceSummary.mockRejectedValue(new Error("404 Not Found"))

    const { result } = renderHook(() => useOrganismData(), { wrapper: makeWrapper() })

    await waitFor(() => expect(result.current.isLoading).toBe(false))

    expect(result.current.systemReady).toBe(true)
    expect(result.current.toolCount).toBe(3)
    expect(result.current.traceSummaryQuery.isError).toBe(true)
    expect(result.current.isError).toBe(false)
    expect(result.current.traceSummaryItems).toEqual([])
    expect(result.current.traceCount).toBe(0)
  })

  it("sets isError when the core status query fails", async () => {
    apiMock.api.getStatus.mockRejectedValue(new Error("503 Service Unavailable"))
    apiMock.api.getTools.mockResolvedValue(mocks.tools)
    apiMock.api.getTraceSummary.mockResolvedValue(mocks.traceSummary)

    const { result } = renderHook(() => useOrganismData(), { wrapper: makeWrapper() })

    // statusQuery uses retry: 1, and React Query applies its default backoff
    // (~1000ms) before the retry completes, so isError flips true only after
    // that delay — beyond waitFor's 1s default. Give it more time.
    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 4000 })

    expect(result.current.statusQuery.isError).toBe(true)
    expect(result.current.toolCount).toBe(3)
  })

  it("sets isError when the core tools query fails", async () => {
    apiMock.api.getStatus.mockResolvedValue(mocks.status)
    apiMock.api.getTools.mockRejectedValue(new Error("500 Internal Server Error"))
    apiMock.api.getTraceSummary.mockResolvedValue(mocks.traceSummary)

    const { result } = renderHook(() => useOrganismData(), { wrapper: makeWrapper() })

    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 4000 })

    expect(result.current.toolsQuery.isError).toBe(true)
    expect(result.current.toolCount).toBe(0)
  })
})
