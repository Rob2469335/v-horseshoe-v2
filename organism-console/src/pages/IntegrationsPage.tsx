import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "../lib/api"
import { useUiStore } from "../state/ui-store"

type StatusShape = {
  ready?: boolean
  environment?: string
  llamacpp_reachable?: boolean
  installed_models?: string[]
  primary_vision_model?: string | null
}

type CloudFallback = {
  model: string
  context_length?: number
  pricing?: string
  provider?: string
}

type CloudShape = {
  models?: CloudFallback[]
}

type AgentModelMap = Record<string, { model: string; backend: string }>

function shortModel(model: string) {
  return model.split("/").slice(-1)[0]
}

export default function IntegrationsPage() {
  const backendUrl = useUiStore((state) => state.backendUrl)

  const statusQuery = useQuery({
    queryKey: ["integrations-status", backendUrl],
    queryFn: () => api.getStatus<StatusShape>(backendUrl),
    retry: 1,
    refetchInterval: 30000
  })

  const cloudQuery = useQuery({
    queryKey: ["integrations-cloud", backendUrl],
    queryFn: async () => {
      const res = await fetch(`${backendUrl}/agents/models/cloud`, {
        headers: { Accept: "application/json" }
      })
      if (!res.ok) throw new Error(`Request failed: ${res.status}`)
      return (await res.json()) as CloudShape
    },
    retry: 1,
    refetchInterval: 60000
  })

  const agentsQuery = useQuery({
    queryKey: ["integrations-agents", backendUrl],
    queryFn: async () => {
      const res = await fetch(`${backendUrl}/agents/models`, {
        headers: { Accept: "application/json" }
      })
      if (!res.ok) throw new Error(`Request failed: ${res.status}`)
      return (await res.json()) as AgentModelMap
    },
    retry: 1,
    refetchInterval: 60000
  })

  const status = statusQuery.data
  const installedModels = status?.installed_models ?? []
  const llamacppReachable = status?.llamacpp_reachable ?? false

  const cloudProviders = useMemo(() => {
    const grouped = new Map<string, CloudFallback[]>()
    for (const f of cloudQuery.data?.models ?? []) {
      const key = f.provider ?? "unknown"
      if (!grouped.has(key)) grouped.set(key, [])
      grouped.get(key)!.push(f)
    }
    return Array.from(grouped.entries())
      .map(([name, models]) => ({ name, models }))
      .sort((a, b) => b.models.length - a.models.length)
  }, [cloudQuery.data])

  const agentModels = agentsQuery.data ?? {}
  const localAgents = Object.entries(agentModels).filter(([, m]) => m.backend !== "openai")
  const cloudAgents = Object.entries(agentModels).filter(([, m]) => m.backend === "openai")

  const isError = statusQuery.isError && cloudQuery.isError && agentsQuery.isError
  const isLoading = statusQuery.isLoading || cloudQuery.isLoading || agentsQuery.isLoading

  const providers = [
    {
      name: "llamacpp",
      type: "Local",
      online: llamacppReachable,
      configured: statusQuery.isSuccess,
      detail: statusQuery.isSuccess
        ? llamacppReachable
          ? `${installedModels.length} local model(s) served${status?.primary_vision_model ? ` · vision: ${status.primary_vision_model}` : ""}.`
          : "Local runtime is offline — generation will fall through to the cloud chain."
        : "Local runtime status unavailable."
    },
    ...cloudProviders.map((p) => ({
      name: p.name,
      type: "Cloud",
      online: p.models.length > 0,
      configured: cloudQuery.isSuccess,
      detail:
        p.models.length > 0
          ? `${p.models.length} model(s) in the live fallback chain${p.models[0].pricing ? ` · ${p.models[0].pricing}` : ""}.`
          : "No models from this provider are currently in the fallback chain."
    }))
  ]

  return (
    <section className="flex flex-col h-full w-full overflow-hidden p-6 text-slate-300">
      <header className="flex flex-col gap-2 bg-[#04080f]/60 border border-white/5 backdrop-blur-xl p-6 rounded-2xl mb-6 shadow-[0_0_30px_rgba(0,0,0,0.5)] shrink-0">
        <div className="flex items-center gap-2 text-[11px] font-black uppercase tracking-widest text-cyan-400">
          <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 shadow-[0_0_10px_#22d3ee]" />
          Integration surface
        </div>
        <h1 className="text-3xl font-black text-white m-0">Integrations</h1>
        <p className="text-sm text-slate-400 m-0">Live view of the local runtime and the cloud fallback chain the backend is actually configured with.</p>
      </header>

      <div className="flex flex-col gap-6 overflow-y-auto custom-scrollbar pb-10">
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {providers.map((p) => (
            <article
              key={p.name}
              className={`relative overflow-hidden rounded-2xl p-5 border shadow-lg backdrop-blur-md transition-all ${
                p.online
                  ? "bg-cyan-900/20 border-cyan-500/30 shadow-[0_16px_40px_rgba(0,0,0,0.2)]"
                  : "bg-slate-900/40 border-white/5 shadow-[0_12px_32px_rgba(0,0,0,0.16)]"
              }`}
            >
              <div
                className="absolute top-0 left-0 w-full h-px opacity-80 pointer-events-none"
                style={{
                  background: p.online
                    ? "linear-gradient(90deg, #22d3ee, transparent 72%)"
                    : "linear-gradient(90deg, rgba(255,255,255,0.12), transparent 72%)"
                }}
              />

              <div className="flex justify-between items-start gap-3">
                <div className="flex flex-col gap-2">
                  <div className="flex items-center gap-2.5">
                    <span
                      className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                        p.online ? "bg-emerald-400 shadow-[0_0_14px_rgba(52,211,153,0.45)]" : "bg-slate-600"
                      }`}
                    />
                    <div className="text-lg font-black text-white leading-tight">{p.name}</div>
                  </div>

                  <div className="inline-flex items-center gap-2 px-2.5 py-1 rounded-full bg-white/5 border border-white/10 text-[10px] font-black uppercase tracking-widest text-slate-400 w-fit">
                    {p.type} provider
                  </div>
                </div>

                <span
                  className={`px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest border ${
                    p.online
                      ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400"
                      : "bg-slate-800/50 border-slate-600/50 text-slate-500"
                  }`}
                >
                  {p.online ? "active" : "inactive"}
                </span>
              </div>

              <div className="mt-4 pt-3 border-t border-white/10 text-[13px] text-slate-400 leading-relaxed">
                {p.detail}
              </div>
            </article>
          ))}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <article className="relative overflow-hidden rounded-2xl p-6 bg-slate-900/50 border border-white/10 backdrop-blur-md">
            <div className="inline-flex items-center gap-2.5 px-3 py-1.5 rounded-full bg-cyan-500/10 border border-cyan-500/20 text-cyan-400 text-[11px] font-black uppercase tracking-[0.1em] mb-4">
              <span className="w-2 h-2 rounded-full bg-cyan-400 shadow-[0_0_14px_rgba(34,211,238,0.6)]" />
              Cloud fallback chain
            </div>

            <h2 className="text-xl font-bold text-white m-0">Live fallback models</h2>

            <div className="mt-4 space-y-3">
              {cloudProviders.length === 0 && (
                <div className="p-8 text-center rounded-2xl border border-dashed border-slate-600/50 bg-gradient-to-b from-white/5 to-transparent">
                  <p className="text-slate-400 text-sm m-0 leading-relaxed">
                    {isLoading
                      ? "Loading the cloud fallback chain…"
                      : "No cloud fallback models reported. If a cloud key is configured, check that the backend can reach the model catalogs."}
                  </p>
                </div>
              )}
              {cloudProviders.map((p) => (
                <div key={p.name} className="rounded-xl border border-white/10 bg-black/20 p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="font-bold text-white text-sm capitalize">{p.name}</span>
                    <span className="text-[10px] uppercase tracking-widest text-slate-500 font-bold">{p.models.length} model(s)</span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {p.models.map((f) => (
                      <span
                        key={f.model}
                        title={f.pricing ? `pricing: ${f.pricing}` : undefined}
                        className="rounded-md bg-white/5 border border-white/10 px-2 py-0.5 font-mono text-[11px] text-slate-300"
                      >
                        {shortModel(f.model)}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </article>

          <article className="relative overflow-hidden rounded-2xl p-6 bg-slate-900/50 border border-white/10 backdrop-blur-md">
            <div className="inline-flex items-center gap-2.5 px-3 py-1.5 rounded-full bg-cyan-500/10 border border-cyan-500/20 text-cyan-400 text-[11px] font-black uppercase tracking-[0.1em] mb-4">
              <span className="w-2 h-2 rounded-full bg-cyan-400 shadow-[0_0_14px_rgba(34,211,238,0.6)]" />
              Agent routing
            </div>

            <h2 className="text-xl font-bold text-white m-0">Per-agent model backends</h2>

            <div className="mt-4 space-y-3">
              {Object.keys(agentModels).length === 0 && (
                <div className="p-8 text-center rounded-2xl border border-dashed border-slate-600/50 bg-gradient-to-b from-white/5 to-transparent">
                  <p className="text-slate-400 text-sm m-0 leading-relaxed">
                    {isLoading ? "Loading agent routing…" : "No agent model mapping exposed by the backend."}
                  </p>
                </div>
              )}
              {localAgents.map(([agent, m]) => (
                <div key={agent} className="flex items-center justify-between gap-2 rounded-xl border border-white/10 bg-black/20 px-3 py-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold capitalize">{agent}</div>
                    <div className="truncate text-[11px] text-slate-500 font-mono">{m.model}</div>
                  </div>
                  <span className="shrink-0 rounded-full bg-slate-800/60 border border-slate-600/50 px-2.5 py-0.5 text-[10px] uppercase tracking-widest text-slate-400">
                    local
                  </span>
                </div>
              ))}
              {cloudAgents.map(([agent, m]) => (
                <div key={agent} className="flex items-center justify-between gap-2 rounded-xl border border-white/10 bg-black/20 px-3 py-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold capitalize">{agent}</div>
                    <div className="truncate text-[11px] text-slate-500 font-mono">{m.model}</div>
                  </div>
                  <span className="shrink-0 rounded-full bg-cyan-900/30 border border-cyan-500/30 px-2.5 py-0.5 text-[10px] uppercase tracking-widest text-cyan-400">
                    cloud
                  </span>
                </div>
              ))}
            </div>

            {isError && (
              <div className="mt-4 rounded-xl p-3 bg-red-900/30 border border-red-500/30 text-red-200 text-sm leading-relaxed">
                Integration telemetry could not be loaded — check that the backend is reachable.
              </div>
            )}
          </article>
        </div>
      </div>
    </section>
  )
}
