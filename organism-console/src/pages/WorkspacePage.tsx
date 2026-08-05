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
    <section className="flex flex-col h-full w-full overflow-hidden p-6 text-slate-300">
      {/* Header */}
      <div className="flex justify-between items-center bg-[#04080f]/60 border border-white/5 backdrop-blur-xl p-6 rounded-2xl mb-6 shadow-[0_0_30px_rgba(0,0,0,0.5)] shrink-0">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-3">
            <span className="w-2 h-2 rounded-full bg-pink-400 shadow-[0_0_10px_#f472b6]"></span>
            Workspace Telemetry
          </h1>
          <p className="text-sm text-slate-400 mt-2">
            Live workspace context from the local backend, including readiness, events, tools, and cache state.
          </p>
        </div>
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 overflow-y-auto custom-scrollbar pb-10">
        
        {/* System Posture */}
        <article className="flex flex-col gap-4 bg-[#04080f]/40 border border-pink-500/20 p-6 rounded-2xl shadow-[inset_0_0_20px_rgba(244,114,182,0.05)] backdrop-blur-md">
          <h2 className="text-sm font-bold text-pink-400 uppercase tracking-widest border-b border-pink-500/20 pb-3">System posture</h2>
          <pre className="p-4 rounded-xl bg-black/60 border border-white/5 text-xs text-pink-100/80 font-mono overflow-auto custom-scrollbar min-h-[160px]">
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
                        llamacppBaseUrl: statusQuery.data?.llamacpp_base_url ?? null,
                        llamacppReachable: statusQuery.data?.llamacpp_reachable ?? false,
                      },
                      null,
                      2,
                    )}
          </pre>
        </article>

        {/* Workspace Summary */}
        <article className="flex flex-col gap-4 bg-[#04080f]/40 border border-white/10 p-6 rounded-2xl backdrop-blur-md">
          <h2 className="text-sm font-bold text-white uppercase tracking-widest border-b border-white/10 pb-3">Workspace summary</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
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
                label: "Llama.cpp reachable",
                value: formatBoolean(statusQuery.data?.llamacpp_reachable),
                accent: true,
              },
            ].map((item) => (
              <div key={item.label} className="flex flex-col bg-slate-900/60 p-3 rounded-xl border border-white/5">
                <span className="text-[10px] text-slate-500 font-bold uppercase tracking-widest mb-1">{item.label}</span>
                <span className={`text-sm font-mono truncate ${item.accent ? "text-pink-400 font-bold" : "text-white"}`}>
                  {item.value}
                </span>
              </div>
            ))}
          </div>
        </article>

        {/* Capabilities */}
        <article className="flex flex-col gap-4 bg-[#04080f]/40 border border-white/10 p-6 rounded-2xl backdrop-blur-md">
          <h2 className="text-sm font-bold text-white uppercase tracking-widest border-b border-white/10 pb-3">Capabilities</h2>
          <div className="flex flex-wrap gap-2 mt-2">
            {Array.isArray(toolsQuery.data?.capabilities) && toolsQuery.data.capabilities.length > 0 ? (
              toolsQuery.data.capabilities.map((cap: string) => (
                <span
                  key={cap}
                  className="px-3 py-1 bg-pink-500/10 border border-pink-500/30 rounded-full text-xs font-bold text-pink-300 tracking-wide"
                >
                  {cap}
                </span>
              ))
            ) : (
              <span className="text-slate-500 text-sm">No capabilities detected.</span>
            )}
          </div>
          <pre className="p-4 rounded-xl bg-black/60 border border-white/5 text-[11px] text-slate-400 font-mono overflow-auto custom-scrollbar h-[160px] mt-2">
            {toolsLoading ? "Sensing tools..." : JSON.stringify(toolsQuery.data, null, 2)}
          </pre>
        </article>

        {/* Tool Cache */}
        <article className="flex flex-col gap-4 bg-pink-950/20 border border-pink-500/30 p-6 rounded-2xl shadow-[0_0_30px_rgba(244,114,182,0.1)] backdrop-blur-md relative overflow-hidden">
          <div className="absolute top-0 right-0 w-32 h-32 bg-pink-500/10 blur-[50px] pointer-events-none" />
          <h2 className="text-sm font-bold text-pink-400 uppercase tracking-widest border-b border-pink-500/20 pb-3 relative z-10">Tool Cache</h2>
          <div className="text-sm text-slate-400 relative z-10">
            Currently holding strong{" "}
            <strong className="text-pink-400 font-bold font-mono text-base">{toolsCacheQuery.data?.cache_size ?? 0}</strong> active traces in local memory.
          </div>
          <pre className="p-4 rounded-xl bg-black/60 border border-white/5 text-xs text-pink-100/70 font-mono overflow-auto custom-scrollbar h-[160px] relative z-10">
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

