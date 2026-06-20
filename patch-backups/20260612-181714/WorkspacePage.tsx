import { useQuery } from "@tanstack/react-query"
import { api } from "../lib/api"
import type { ReadyResponse, StatusResponse, ToolsResponse } from "../lib/types"
import { useUiStore } from "../state/ui-store"

type ToolsCacheResponse = {
  cache_size?: number
  cached_keys?: string[]
}

function formatBoolean(value: boolean | undefined) {
  return value ? "Yes" : "No"
}

function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

export default function WorkspacePage() {
  const backendUrl = useUiStore((state) => state.backendUrl)

  const statusQuery = useQuery({
    queryKey: ["workspace-status", backendUrl],
    queryFn: () => api.getStatus<StatusResponse>(backendUrl),
    retry: 1,
    refetchInterval: 30000,
  })

  const readyQuery = useQuery({
    queryKey: ["workspace-ready", backendUrl],
    queryFn: () => api.getReady<ReadyResponse>(backendUrl),
    retry: 1,
    refetchInterval: 30000,
  })

  const toolsQuery = useQuery({
    queryKey: ["workspace-tools", backendUrl],
    queryFn: () => api.getTools<ToolsResponse>(backendUrl),
    retry: 1,
    refetchInterval: 60000,
  })

  const toolsCacheQuery = useQuery({
    queryKey: ["workspace-tools-cache", backendUrl],
    queryFn: () => api.getToolsCache<ToolsCacheResponse>(backendUrl),
    retry: 1,
    refetchInterval: 60000,
  })

  const statusLoading = statusQuery.isLoading || readyQuery.isLoading
  const toolsLoading = toolsQuery.isLoading || toolsCacheQuery.isLoading

  return (
    <section className="page">
      <div className="pageheader">
        <div>
          <h1>Workspace</h1>
          <p className="page-subtitle">
            Live workspace context from the local backend, including readiness, events, tools, and cache state.
          </p>
        </div>
      </div>

      <div className="agent-layout">
        <article className="agent-panel panel-accent-top">
          <h2>System posture</h2>
          <pre className="agent-response" style={{ padding: 18, background: "rgba(0,0,0,0.2)" }}>
            {statusLoading
              ? "Sensing environment..."
              : statusQuery.isError
                ? getErrorMessage(statusQuery.error)
                : readyQuery.isError
                  ? getErrorMessage(readyQuery.error)
                  : JSON.stringify(
                      {
                        backendUrl,
                        ready: readyQuery.data?.ready ?? statusQuery.data?.ready ?? false,
                        environment: statusQuery.data?.environment ?? null,
                        eventsPath: statusQuery.data?.events_path ?? null,
                        eventCount: statusQuery.data?.event_count ?? 0,
                        ollamaBaseUrl: statusQuery.data?.ollama_base_url ?? null,
                        ollamaReachable: statusQuery.data?.ollama_reachable ?? false,
                      },
                      null,
                      2,
                    )}
          </pre>
        </article>

        <article className="agent-panel panel-accent-top">
          <h2>Workspace summary</h2>
          <div className="metric-list">
            {[
              { label: "Backend URL", value: backendUrl },
              {
                label: "Ready",
                value: formatBoolean(readyQuery.data?.ready ?? statusQuery.data?.ready),
                accent: true,
              },
              { label: "Environment", value: statusQuery.data?.environment ?? "Unknown" },
              { label: "Events path", value: statusQuery.data?.events_path ?? "Unknown" },
              { label: "Event count", value: String(statusQuery.data?.event_count ?? 0) },
              {
                label: "Ollama reachable",
                value: formatBoolean(statusQuery.data?.ollama_reachable),
                accent: true,
              },
            ].map((item) => (
              <div key={item.label} className="metric-row">
                <span className="metric-label">{item.label}</span>
                <span className={item.accent ? "metric-value metric-value--accent" : "metric-value"}>
                  {item.value}
                </span>
              </div>
            ))}
          </div>
        </article>

        <article className="agent-panel panel-accent-top">
          <h2>Capabilities</h2>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
            {toolsQuery.data?.capabilities?.length ? (
              toolsQuery.data.capabilities.map((cap: string) => (
                <span
                  key={cap}
                  className="lesson-badge"
                  style={{ borderColor: "var(--page-accent)", color: "var(--page-accent)" }}
                >
                  {cap}
                </span>
              ))
            ) : (
              <span style={{ color: "var(--text-muted)" }}>No capabilities detected.</span>
            )}
          </div>
          <pre
            className="agent-response"
            style={{ marginTop: 20, fontSize: 11, height: 160, padding: 16, background: "rgba(0,0,0,0.3)" }}
          >
            {toolsLoading ? "Sensing tools..." : JSON.stringify(toolsQuery.data, null, 2)}
          </pre>
        </article>

        <article className="agent-panel panel-accent-top panel-accent-glow">
          <h2>Tool Cache</h2>
          <div style={{ fontSize: 14, color: "var(--text-soft)", marginBottom: 16 }}>
            Currently holding strong{" "}
            <strong style={{ color: "var(--page-accent)" }}>{toolsCacheQuery.data?.cache_size ?? 0}</strong> active traces in local memory.
          </div>
          <pre className="agent-response" style={{ height: 200, padding: 16, background: "rgba(0,0,0,0.3)" }}>
            {toolsLoading
              ? "Reading cache..."
              : toolsCacheQuery.isError
                ? getErrorMessage(toolsCacheQuery.error)
                : JSON.stringify(toolsCacheQuery.data?.cached_keys ?? [], null, 2)}
          </pre>
        </article>
      </div>
    </section>
  )
}


