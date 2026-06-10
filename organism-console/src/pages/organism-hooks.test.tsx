import React from "react"
import { describe, expect, it, vi, beforeEach } from "vitest"
import { renderHook } from "@testing-library/react"
import { useOrganismData } from "./organism-hooks"

const useQueryMock = vi.hoisted(() => vi.fn())

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-query")>("@tanstack/react-query")
  return {
    ...actual,
    useQuery: useQueryMock,
  }
})

vi.mock("../state/ui-store", () => ({
  useUiStore: (selector: (s: { backendUrl: string }) => unknown) =>
    selector({ backendUrl: "http://127.0.0.1:8000" }),
}))

function queryState(overrides = {}) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    isPending: false,
    error: null,
    ...overrides,
  }
}

describe("useOrganismData", () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it("resolves core organism data when all queries succeed", () => {
    useQueryMock
      .mockReturnValueOnce(queryState({
        data: { ready: true, environment: "test", ollama_reachable: true, events_path: "/tmp/events" },
      }))
      .mockReturnValueOnce(queryState({
        data: { tools: ["search", "memory", "vision"], count: 3, capabilities: ["search", "memory", "vision"] },
      }))
      .mockReturnValueOnce(queryState({
        data: [
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
            latest_timestamp_ms: Date.now() - 60000,
            action_count: 5,
          },
        ],
      }))

    const { result } = renderHook(() => useOrganismData())

    expect(result.current.backendUrl).toBe("http://127.0.0.1:8000")
    expect(result.current.systemReady).toBe(true)
    expect(result.current.toolCount).toBe(3)
    expect(result.current.traceCount).toBe(2)
    expect(result.current.isError).toBe(false)
    expect(result.current.traceSummaryQuery.isError).toBe(false)
  })

  it("keeps isError false when optional traceSummary query fails", () => {
    useQueryMock
      .mockReturnValueOnce(queryState({
        data: { ready: true, environment: "test", ollama_reachable: true, events_path: "/tmp/events" },
      }))
      .mockReturnValueOnce(queryState({
        data: { tools: ["search", "memory", "vision"], count: 3, capabilities: ["search", "memory", "vision"] },
      }))
      .mockReturnValueOnce(queryState({
        isError: true,
        error: new Error("404 Not Found"),
      }))

    const { result } = renderHook(() => useOrganismData())

    expect(result.current.systemReady).toBe(true)
    expect(result.current.toolCount).toBe(3)
    expect(result.current.traceSummaryQuery.isError).toBe(true)
    expect(result.current.isError).toBe(false)
    expect(result.current.traceSummaryItems).toEqual([])
    expect(result.current.traceCount).toBe(0)
  })

  it("sets isError when the core status query fails", () => {
    useQueryMock
      .mockReturnValueOnce(queryState({
        isError: true,
        error: new Error("503 Service Unavailable"),
      }))
      .mockReturnValueOnce(queryState({
        data: { tools: ["search", "memory", "vision"], count: 3, capabilities: ["search", "memory", "vision"] },
      }))
      .mockReturnValueOnce(queryState({
        data: [],
      }))

    const { result } = renderHook(() => useOrganismData())

    expect(result.current.statusQuery.isError).toBe(true)
    expect(result.current.isError).toBe(true)
    expect(result.current.toolCount).toBe(3)
  })

  it("sets isError when the core tools query fails", () => {
    useQueryMock
      .mockReturnValueOnce(queryState({
        data: { ready: true, environment: "test", ollama_reachable: true, events_path: "/tmp/events" },
      }))
      .mockReturnValueOnce(queryState({
        isError: true,
        error: new Error("500 Internal Server Error"),
      }))
      .mockReturnValueOnce(queryState({
        data: [],
      }))

    const { result } = renderHook(() => useOrganismData())

    expect(result.current.toolsQuery.isError).toBe(true)
    expect(result.current.isError).toBe(true)
    expect(result.current.toolCount).toBe(0)
  })
})
