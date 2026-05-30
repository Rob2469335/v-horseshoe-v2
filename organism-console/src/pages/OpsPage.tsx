import { useEffect, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "../lib/api"
import type {
  AdminDashboardResponse,
  AdminGenerationResponse,
  AdminStatusResponse,
  HealthResponse,
  ReadyResponse,
  StatusResponse,
  ToolsResponse,
  TraceItem,
  TraceSummaryItem,
  TraceSummaryResponse,
  TracesResponse
} from "../lib/types"
import { useUiStore } from "../state/ui-store"

function formatJson(value: unknown) {
  return JSON.stringify(value, null, 2)
}

function formatTimestamp(timestampMs?: number) {
  if (!timestampMs) return "—"
  return new Date(timestampMs).toLocaleString()
}

function formatDuration(durationMs?: number) {
  if (!durationMs) return "0 ms"
  if (durationMs < 1000) return `${durationMs.toFixed(1)} ms`
  return `${(durationMs / 1000).toFixed(2)} s`
}

export default function OpsPage() {
  const backendUrl = useUiStore((state) => state.backendUrl)
  const setConnectionStatus = useUiStore((state) => state.setConnectionStatus)
  const [selectedTraceId, setSelectedTraceId] = useState<string>("")

  const healthQuery = useQuery({
    queryKey: ["health", backendUrl],
    queryFn: () => api.getHealth<HealthResponse>(backendUrl),
    retry: 1,
    refetchInterval: 30000
  })

  const readyQuery = useQuery({
    queryKey: ["readyz", backendUrl],
    queryFn: () => api.getReady<ReadyResponse>(backendUrl),
    retry: 1,
    refetchInterval: 30000
  })

  const statusQuery = useQuery({
    queryKey: ["status", backendUrl],
    queryFn: () => api.getStatus<StatusResponse>(backendUrl),
    retry: 1,
    refetchInterval: 30000
  })

  const toolsQuery = useQuery({
    queryKey: ["tools", backendUrl],
    queryFn: () => api.getTools<ToolsResponse>(backendUrl),
    retry: 1,
    refetchInterval: 60000
  })

  const toolsCacheQuery = useQuery({
    queryKey: ["tools-cache", backendUrl],
    queryFn: () => api.getToolsCache<Record<string, unknown>>(backendUrl),
    retry: 1,
    refetchInterval: 60000
  })

  const tracesQuery = useQuery({
    queryKey: ["traces", backendUrl],
    queryFn: () => api.getTraces(backendUrl),
    retry: 1,
    refetchInterval: 15000
  })

  const traceSummaryQuery = useQuery({
    queryKey: ["trace-summary", backendUrl],
    queryFn: () => api.getTraceSummary(backendUrl),
    retry: 1,
    refetchInterval: 15000
  })

  const adminStatusQuery = useQuery({
    queryKey: ["admin-status", backendUrl],
    queryFn: () => api.getAdminStatus<AdminStatusResponse>(backendUrl),
    retry: 1,
    refetchInterval: 60000
  })

  const adminDashboardQuery = useQuery({
    queryKey: ["admin-dashboard", backendUrl],
    queryFn: () => api.getAdminDashboard<AdminDashboardResponse>(backendUrl),
    retry: 1,
    refetchInterval: 60000
  })

  const adminGenerationQuery = useQuery({
    queryKey: ["admin-generation", backendUrl],
    queryFn: () => api.getAdminGeneration<AdminGenerationResponse>(backendUrl),
    retry: 1,
    refetchInterval: 60000
  })

  useEffect(() => {
    if (healthQuery.isSuccess && readyQuery.isSuccess) {
      setConnectionStatus("online")
      return
    }

    if (healthQuery.isError || readyQuery.isError || statusQuery.isError || toolsQuery.isError) {
      setConnectionStatus("offline")
      return
    }

    if (healthQuery.isLoading || readyQuery.isLoading || statusQuery.isLoading || toolsQuery.isLoading) {
      setConnectionStatus("connecting")
    }
  }, [
    healthQuery.isSuccess,
    readyQuery.isSuccess,
    healthQuery.isError,
    readyQuery.isError,
    statusQuery.isError,
    toolsQuery.isError,
    healthQuery.isLoading,
    readyQuery.isLoading,
    statusQuery.isLoading,
    toolsQuery.isLoading,
    setConnectionStatus
  ])

  const traceSummary = (traceSummaryQuery.data ?? []) as TraceSummaryResponse
  const traceItems = ((tracesQuery.data as TracesResponse | undefined)?.items ?? []) as TraceItem[]

  useEffect(() => {
    if (!traceSummary.length) {
      setSelectedTraceId("")
      return
    }

    setSelectedTraceId((current) => {
      if (current && traceSummary.some((item) => item.trace_id === current)) {
        return current
      }

      return traceSummary[0]?.trace_id ?? ""
    })
  }, [traceSummary])

  const selectedSummary = useMemo<TraceSummaryItem | undefined>(() => {
    return traceSummary.find((item) => item.trace_id === selectedTraceId)
  }, [traceSummary, selectedTraceId])

  const selectedTraceEvents = useMemo<TraceItem[]>(() => {
    return traceItems.filter((item) => item.trace_id === selectedTraceId)
  }, [traceItems, selectedTraceId])

  return (
    <section className="page page--ops">
      <h1>Ops</h1>
      <p>Backend proof for health, readiness, status, tools, cache, traces, and admin surfaces.</p>

      <div className="ops-grid">
        <article className="ops-panel">
          <h2>/health</h2>
          <pre>{healthQuery.data ? formatJson(healthQuery.data) : String(healthQuery.error?.message ?? "Loading")}</pre>
        </article>

        <article className="ops-panel">
          <h2>/readyz</h2>
          <pre>{readyQuery.data ? formatJson(readyQuery.data) : String(readyQuery.error?.message ?? "Loading")}</pre>
        </article>

        <article className="ops-panel">
          <h2>/status</h2>
          <pre>{statusQuery.data ? formatJson(statusQuery.data) : String(statusQuery.error?.message ?? "Loading")}</pre>
        </article>

        <article className="ops-panel">
          <h2>/tools</h2>
          <pre>{toolsQuery.data ? formatJson(toolsQuery.data) : String(toolsQuery.error?.message ?? "Loading")}</pre>
        </article>

        <article className="ops-panel">
          <h2>/tools/cache</h2>
          <pre>{toolsCacheQuery.data ? formatJson(toolsCacheQuery.data) : String(toolsCacheQuery.error?.message ?? "Loading")}</pre>
        </article>

        <article className="ops-panel ops-panel--span-2">
          <h2>/traces/summary</h2>
          {traceSummaryQuery.data ? (
            <div className="trace-summary-list">
              {traceSummary.length ? (
                traceSummary.map((item) => (
                  <button
                    key={item.trace_id}
                    type="button"
                    className={`trace-summary-item${item.trace_id === selectedTraceId ? " trace-summary-item--active" : ""}`}
                    onClick={() => setSelectedTraceId(item.trace_id)}
                  >
                    <div className="trace-summary-item__top">
                      <span className="trace-summary-item__trace">{item.trace_id}</span>
                      <span className="trace-summary-item__time">{formatTimestamp(item.latest_timestamp_ms)}</span>
                    </div>
                    <div className="trace-summary-item__meta">
                      <span>{item.first_phase} → {item.last_status}</span>
                      <span>{item.action_count} steps</span>
                      <span>{formatDuration(item.total_duration_ms)}</span>
                    </div>
                    <div className="trace-summary-item__summary">{item.summary || "No summary"}</div>
                  </button>
                ))
              ) : (
                <div className="trace-empty">No trace summaries available.</div>
              )}
            </div>
          ) : (
            <pre>{String(traceSummaryQuery.error?.message ?? "Loading")}</pre>
          )}
        </article>

        <article className="ops-panel ops-panel--span-2">
          <h2>/traces</h2>
          {tracesQuery.data ? (
            <div className="trace-events">
              {selectedSummary ? (
                <div className="trace-events__header">
                  <div><strong>Selected trace:</strong> {selectedSummary.trace_id}</div>
                  <div><strong>Latest:</strong> {formatTimestamp(selectedSummary.latest_timestamp_ms)}</div>
                </div>
              ) : null}

              {selectedTraceEvents.length ? (
                <div className="trace-event-list">
                  {selectedTraceEvents.map((event, index) => (
                    <article key={`${event.trace_id}-${event.step_id}-${event.phase}-${index}`} className="trace-event-card">
                      <div className="trace-event-card__top">
                        <span className="trace-event-card__phase">{event.phase}</span>
                        <span className="trace-event-card__status">{event.status}</span>
                      </div>
                      <div className="trace-event-card__meta">
                        <span><strong>Actor:</strong> {event.actor}</span>
                        <span><strong>Action:</strong> {event.action}</span>
                        <span><strong>Step:</strong> {event.step_id}</span>
                        <span><strong>Time:</strong> {formatTimestamp(event.timestamp_ms)}</span>
                        <span><strong>Duration:</strong> {formatDuration(event.duration_ms)}</span>
                        <span><strong>Model:</strong> {event.model || "—"}</span>
                      </div>
                      <div className="trace-event-card__summary">{event.summary || "No summary"}</div>
                      {event.metadata ? (
                        <pre>{formatJson(event.metadata)}</pre>
                      ) : null}
                    </article>
                  ))}
                </div>
              ) : (
                <div className="trace-empty">No raw events available for the selected trace.</div>
              )}
            </div>
          ) : (
            <pre>{String(tracesQuery.error?.message ?? "Loading")}</pre>
          )}
        </article>

        <article className="ops-panel">
          <h2>/api/admin/status</h2>
          <pre>{adminStatusQuery.data ? formatJson(adminStatusQuery.data) : String(adminStatusQuery.error?.message ?? "Loading")}</pre>
        </article>

        <article className="ops-panel">
          <h2>/api/admin/dashboard</h2>
          <pre>{adminDashboardQuery.data ? formatJson(adminDashboardQuery.data) : String(adminDashboardQuery.error?.message ?? "Loading")}</pre>
        </article>

        <article className="ops-panel">
          <h2>/api/admin/generation</h2>
          <pre>{adminGenerationQuery.data ? formatJson(adminGenerationQuery.data) : String(adminGenerationQuery.error?.message ?? "Loading")}</pre>
        </article>
      </div>
    </section>
  )
}

