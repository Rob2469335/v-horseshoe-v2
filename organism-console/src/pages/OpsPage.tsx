import { useEffect } from "react"
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
  TraceSummaryResponse,
  TracesResponse
} from "../lib/types"
import { useUiStore } from "../state/ui-store"

function formatJson(value: unknown) {
  return JSON.stringify(value, null, 2)
}

export default function OpsPage() {
  const backendUrl = useUiStore((state) => state.backendUrl)
  const setConnectionStatus = useUiStore((state) => state.setConnectionStatus)

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

        <article className="ops-panel">
          <h2>/traces</h2>
          <pre>{tracesQuery.data ? formatJson(tracesQuery.data as TracesResponse) : String(tracesQuery.error?.message ?? "Loading")}</pre>
        </article>

        <article className="ops-panel">
          <h2>/traces/summary</h2>
          <pre>{traceSummaryQuery.data ? formatJson(traceSummaryQuery.data as TraceSummaryResponse) : String(traceSummaryQuery.error?.message ?? "Loading")}</pre>
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
