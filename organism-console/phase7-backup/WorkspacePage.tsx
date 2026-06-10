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
    refetchInterval: 30000
  })

  const readyQuery = useQuery({
    queryKey: ["workspace-ready", backendUrl],
    queryFn: () => api.getReady<ReadyResponse>(backendUrl),
    retry: 1,
    refetchInterval: 30000
  })

  const toolsQuery = useQuery({
    queryKey: ["workspace-tools", backendUrl],
    queryFn: () => api.getTools<ToolsResponse>(backendUrl),
    retry: 1,
    refetchInterval: 60000
  })

  const toolsCacheQuery = useQuery({
    queryKey: ["workspace-tools-cache", backendUrl],
    queryFn: () => api.getToolsCache<ToolsCacheResponse>(backendUrl),
    retry: 1,
    refetchInterval: 60000
  })

  const statusLoading = statusQuery.isLoading || readyQuery.isLoading
  const toolsLoading = toolsQuery.isLoading || toolsCacheQuery.isLoading

  return (
    <section className="page">
      <h1>Workspace</h1>
      <p>Live workspace context from the local backend, including readiness, events, tools, and cache state.</p>

      <div className="agent-layout">
        <article className="agent-panel" style={{ borderTop: "4px solid var(--page-accent)" }}>
          <h2>System posture</h2>
          <pre className="agent-response" style={{ background: "rgba(0,0,0,0.2)" }}>
{statusLoading
  ? "Sensing environment..."
  : statusQuery.isError
    ? getErrorMessage(statusQuery.error)
    : readyQuery.isError
      ? getErrorMessage(readyQuery.error)
      : JSON.stringify({
          backend_url: backendUrl,
          ready: readyQuery.data?.ready ?? statusQuery.data?.ready ?? false,
          environment: statusQuery.data?.environment ?? null,
          events_path: statusQuery.data?.events_path ?? null,
          event_count: statusQuery.data?.event_count ?? 0,
          ollama_base_url: statusQuery.data?.ollama_base_url ?? null,
          ollama_reachable: statusQuery.data?.ollama_reachable ?? false
        }, null, 2)}
          </pre>
        </article>

        <article className="agent-panel" style={{ background: "var(--page-accent-tint)" }}>
          <h2>Workspace summary</h2>
          <div style={{ display: "grid", gap: "12px", marginTop: "12px" }}>
            {[
              { label: "Backend URL", value: backendUrl },
              { label: "Ready", value: formatBoolean(readyQuery.data?.ready ?? statusQuery.data?.ready), highlight: true },
              { label: "Environment", value: statusQuery.data?.environment ?? "Unknown" },
              { label: "Events path", value: statusQuery.data?.events_path ?? "Unknown" },
              { label: "Event count", value: statusQuery.data?.event_count ?? 0 },
              { label: "Ollama reachable", value: formatBoolean(statusQuery.data?.ollama_reachable), highlight: true }
            ].map(item => (
              <div key={item.label} style={{ display: "flex", justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid var(--border)" }}>
                <span style={{ color: "var(--text-muted)", fontSize: "12px", fontWeight: "700", textTransform: "uppercase" }}>{item.label}</span>
                <span style={{ 
                  color: item.highlight ? "var(--page-accent)" : "var(--text-soft)", 
                  fontWeight: item.highlight ? "900" : "400",
                  textShadow: item.highlight ? "0 0 10px var(--page-accent-glow)" : "none"
                }}>{item.value}</span>
              </div>
            ))}
          </div>
        </article>

        <article className="agent-panel" style={{ borderBottom: "4px solid var(--page-accent)" }}>
          <h2>Capabilities</h2>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", marginTop: "12px" }}>
            {toolsQuery.data?.capabilities?.map(cap => (
              <span key={cap} className="lesson-badge" style={{ borderColor: "var(--page-accent)", color: "var(--page-accent)" }}>{cap}</span>
            )) || <span style={{ color: "var(--text-muted)" }}>No capabilities detected.</span>}
          </div>
          <pre className="agent-response" style={{ marginTop: "20px", fontSize: "11px", height: "160px", background: "rgba(0,0,0,0.3)" }}>
            {toolsLoading ? "Sensing tools..." : JSON.stringify(toolsQuery.data, null, 2)}
          </pre>
        </article>

        <article className="agent-panel">
          <h2>Tool Cache</h2>
          <div style={{ fontSize: "14px", color: "var(--text-soft)", marginBottom: "16px" }}>
            Currently holding <strong style={{ color: "var(--page-accent)" }}>{toolsCacheQuery.data?.cache_size ?? 0}</strong> active traces in local memory.
          </div>
          <pre className="agent-response" style={{ height: "200px", background: "rgba(0,0,0,0.3)" }}>
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
