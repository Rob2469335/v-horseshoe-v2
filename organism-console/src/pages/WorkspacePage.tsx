import { useQuery } from "@tanstack/react-query"
import { api } from "../lib/api"
import type { ReadyResponse, StatusResponse, ToolsResponse } from "../lib/types"
import { useUiStore } from "../state/ui-store"

type ToolsCacheResponse = {
  cache_size?: number
  cached_keys?: string[]
}

function formatList(items: string[] | undefined) {
  if (!items || items.length === 0) return "None"
  return items.join(", ")
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
        <article className="agent-panel">
          <h2>System status</h2>
          <pre className="agent-response">
{statusLoading
  ? "Loading workspace status..."
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

        <article className="agent-panel">
          <h2>Workspace summary</h2>
          <pre className="agent-response">
{statusLoading
  ? "Building workspace summary..."
  : statusQuery.isError
    ? getErrorMessage(statusQuery.error)
    : readyQuery.isError
      ? getErrorMessage(readyQuery.error)
      : [
          `Backend URL: ${backendUrl}`,
          `Ready: ${formatBoolean(readyQuery.data?.ready ?? statusQuery.data?.ready)}`,
          `Environment: ${statusQuery.data?.environment ?? "Unknown"}`,
          `Events path: ${statusQuery.data?.events_path ?? "Unknown"}`,
          `Event count: ${statusQuery.data?.event_count ?? 0}`,
          `Ollama reachable: ${formatBoolean(statusQuery.data?.ollama_reachable)}`
        ].join("\n")}
          </pre>
        </article>

        <article className="agent-panel">
          <h2>Tools</h2>
          <pre className="agent-response">
{toolsLoading
  ? "Loading tools..."
  : toolsQuery.isError
    ? getErrorMessage(toolsQuery.error)
    : JSON.stringify({
        tool_count: toolsQuery.data?.count ?? 0,
        capabilities: toolsQuery.data?.capabilities ?? [],
        capabilities_summary: formatList(toolsQuery.data?.capabilities)
      }, null, 2)}
          </pre>
        </article>

        <article className="agent-panel">
          <h2>Tool cache</h2>
          <pre className="agent-response">
{toolsLoading
  ? "Loading tool cache..."
  : toolsCacheQuery.isError
    ? getErrorMessage(toolsCacheQuery.error)
    : JSON.stringify({
        cache_size: toolsCacheQuery.data?.cache_size ?? 0,
        cached_keys: toolsCacheQuery.data?.cached_keys ?? [],
        cached_keys_summary: formatList(toolsCacheQuery.data?.cached_keys)
      }, null, 2)}
          </pre>
        </article>
      </div>
    </section>
  )
}
