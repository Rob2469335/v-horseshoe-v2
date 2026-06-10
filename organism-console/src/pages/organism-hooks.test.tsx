import React from "react"
import { describe, expect, it, vi, beforeEach } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import { useOrganismData } from "./organism-hooks"

// ── Mocks ──────────────────────────────────────────────────────────────────

vi.mock("../state/ui-store", () => ({
  useUiStore: (selector: (s: { backendUrl: string }) => unknown) =>
    selector({ backendUrl: "http://127.0.0.1:8000" }),
}))

const mockStatus = {
  ready: true,
  environment: "test",
  ollama_reachable: true,
  events_path: "/tmp/events",
}

const mockTools = {
  tools: ["search", "memory", "vision"],
  count: 3,
  capabilities: ["search", "memory", "vision"],
}

const mockTraceSummary = [
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
]

// ── Helpers ────────────────────────────────────────────────────────────────

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  }
}

// ── Test suite ─────────────────────────────────────────────────────────────

describe("useOrganismData", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("resolves core organism data when all queries succeed", async () => {
    vi.mock("../lib/api", () => ({
      api: {
        getStatus: vi.fn().mockResolvedValue(mockStatus),
        getTools: vi.fn().mockResolvedValue(mockTools),
        getTraceSummary: vi.fn().mockResolvedValue(mockTraceSummary),
      },
    }))

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
    vi.mock("../lib/api", () => ({
      api: {
        getStatus: vi.fn().mockResolvedValue(mockStatus),
        getTools: vi.fn().mockResolvedValue(mockTools),
        getTraceSummary: vi.fn().mockRejectedValue(new Error("404 Not Found")),
      },
    }))

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
    vi.mock("../lib/api", () => ({
      api: {
        getStatus: vi.fn().mockRejectedValue(new Error("503 Service Unavailable")),
        getTools: vi.fn().mockResolvedValue(mockTools),
        getTraceSummary: vi.fn().mockResolvedValue(mockTraceSummary),
      },
    }))

    const { result } = renderHook(() => useOrganismData(), { wrapper: makeWrapper() })

    await waitFor(() => expect(result.current.isLoading).toBe(false))

    expect(result.current.isError).toBe(true)
    expect(result.current.statusQuery.isError).toBe(true)
    expect(result.current.toolCount).toBe(3)
  })

  it("sets isError when the core tools query fails", async () => {
    vi.mock("../lib/api", () => ({
      api: {
        getStatus: vi.fn().mockResolvedValue(mockStatus),
        getTools: vi.fn().mockRejectedValue(new Error("500 Internal Server Error")),
        getTraceSummary: vi.fn().mockResolvedValue(mockTraceSummary),
      },
    }))

    const { result } = renderHook(() => useOrganismData(), { wrapper: makeWrapper() })

    await waitFor(() => expect(result.current.isLoading).toBe(false))

    expect(result.current.isError).toBe(true)
    expect(result.current.toolsQuery.isError).toBe(true)
    expect(result.current.toolCount).toBe(0)
  })
})
