import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "../lib/api"
import { useUiStore } from "../state/ui-store"

type StatusShape = {
  ready?: boolean
  event_count?: number
  environment?: string
  ollama_reachable?: boolean
  primary_vision_model?: string | null
  vision_runtime_available?: boolean
  vision_configured?: boolean
}

type ToolsShape = {
  count?: number
  capabilities?: string[]
}

type ToolsCacheShape = {
  cache_size?: number
  cached_keys?: string[]
}

type TracePoint = {
  bucket: string
  event_count: number
  success_count: number
  partial_count: number
  fail_count: number
}

type TracesShape = {
  points?: TracePoint[]
}

function formatCompact(value: number) {
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 1
  }).format(value)
}

function getLinePoints(values: number[], width: number, height: number, padding: number) {
  if (values.length === 0) return ""
  const max = Math.max(...values, 1)
  const usableWidth = width - padding * 2
  const usableHeight = height - padding * 2

  return values
    .map((value, index) => {
      const x = padding + (index * usableWidth) / Math.max(values.length - 1, 1)
      const y = height - padding - (value / max) * usableHeight
      return `${x},${y}`
    })
    .join(" ")
}

function getAreaPoints(values: number[], width: number, height: number, padding: number) {
  if (values.length === 0) return ""
  const line = getLinePoints(values, width, height, padding)
  if (!line) return ""
  const firstX = padding
  const lastX = width - padding
  const baseline = height - padding
  return `${firstX},${baseline} ${line} ${lastX},${baseline}`
}

function getStatusTone(ok: boolean) {
  return ok
    ? { label: "Healthy", accent: "#22c55e", glow: "rgba(34,197,94,0.30)" }
    : { label: "Attention", accent: "#f59e0b", glow: "rgba(245,158,11,0.28)" }
}

export default function MemorySearchPage() {
  const backendUrl = useUiStore((state) => state.backendUrl)

  const statusQuery = useQuery({
    queryKey: ["memory-status", backendUrl],
    queryFn: () => api.getStatus<StatusShape>(backendUrl),
    retry: 1,
    refetchInterval: 30000
  })

  const toolsQuery = useQuery({
    queryKey: ["memory-tools", backendUrl],
    queryFn: () => api.getTools<ToolsShape>(backendUrl),
    retry: 1,
    refetchInterval: 60000
  })

  const cacheQuery = useQuery({
    queryKey: ["memory-tools-cache", backendUrl],
    queryFn: () => api.getToolsCache<ToolsCacheShape>(backendUrl),
    retry: 1,
    refetchInterval: 60000
  })

  const tracesQuery = useQuery({
    queryKey: ["memory-traces", backendUrl],
    queryFn: async () => {
      const traces = await api.getTraces(backendUrl)
      return traces as unknown as TracesShape
    },
    retry: 1,
    refetchInterval: 15000
  })

  const isLoading =
    statusQuery.isLoading ||
    toolsQuery.isLoading ||
    cacheQuery.isLoading ||
    tracesQuery.isLoading

  const isError =
    statusQuery.isError &&
    toolsQuery.isError &&
    cacheQuery.isError &&
    tracesQuery.isError

  const errorMessage = useMemo(() => {
    const candidate =
      statusQuery.error ||
      toolsQuery.error ||
      cacheQuery.error ||
      tracesQuery.error

    if (!candidate) return "Unknown memory telemetry error."
    return candidate instanceof Error ? candidate.message : String(candidate)
  }, [statusQuery.error, toolsQuery.error, cacheQuery.error, tracesQuery.error])

  const derived = useMemo(() => {
    const status = statusQuery.data ?? {}
    const tools = toolsQuery.data ?? {}
    const cache = cacheQuery.data ?? {}
    const points = tracesQuery.data?.points ?? []

    const eventCount = status.event_count ?? 0
    const toolCount = tools.count ?? 0
    const capabilities = tools.capabilities ?? []
    const cacheSize = cache.cache_size ?? 0
    const cachedKeys = cache.cached_keys ?? []

    const totalEvents = points.reduce((sum, point) => sum + point.event_count, 0)
    const totalSuccess = points.reduce((sum, point) => sum + point.success_count, 0)
    const totalFail = points.reduce((sum, point) => sum + point.fail_count, 0)
    const totalPartial = points.reduce((sum, point) => sum + point.partial_count, 0)

    const successRate = totalEvents > 0 ? Math.round((totalSuccess / totalEvents) * 100) : 0
    const failureRate = totalEvents > 0 ? Math.round((totalFail / totalEvents) * 100) : 0
    const partialRate = totalEvents > 0 ? Math.round((totalPartial / totalEvents) * 100) : 0

    const recent = points.slice(-6)
    const previous = points.slice(-12, -6)

    const recentVolume = recent.reduce((sum, point) => sum + point.event_count, 0)
    const previousVolume = previous.reduce((sum, point) => sum + point.event_count, 0)

    const retrievalTrend =
      previousVolume > 0
        ? recentVolume > previousVolume
          ? "rising"
          : recentVolume < previousVolume
            ? "cooling"
            : "steady"
        : "steady"

    const systemReady = status.ready ?? false
    const ollamaReachable = status.ollama_reachable ?? false
    const visionRuntime = status.vision_runtime_available ?? false

    const qualityTone =
      successRate >= 80 ? "excellent" :
      successRate >= 60 ? "stable" :
      successRate >= 40 ? "mixed" :
      "fragile"

    const memoryTone =
      cacheSize > 0 && totalEvents > 0
        ? "learning actively"
        : cacheSize > 0
          ? "memory present"
          : "memory bootstrapping"

    const latest = points[points.length - 1] ?? null

    return {
      status,
      toolCount,
      capabilities,
      cacheSize,
      cachedKeys,
      eventCount,
      points,
      totalEvents,
      totalSuccess,
      totalFail,
      totalPartial,
      successRate,
      failureRate,
      partialRate,
      retrievalTrend,
      systemReady,
      ollamaReachable,
      visionRuntime,
      qualityTone,
      memoryTone,
      latest
    }
  }, [statusQuery.data, toolsQuery.data, cacheQuery.data, tracesQuery.data])

  const chart = useMemo(() => {
    const width = 1120
    const height = 320
    const padding = 26
    const values = derived.points.map((point) => point.event_count)
    const successValues = derived.points.map((point) => point.success_count)
    const failValues = derived.points.map((point) => point.fail_count)

    return {
      width,
      height,
      padding,
      eventsLine: getLinePoints(values, width, height, padding),
      successLine: getLinePoints(successValues, width, height, padding),
      failLine: getLinePoints(failValues, width, height, padding),
      area: getAreaPoints(values, width, height, padding)
    }
  }, [derived.points])

  const readinessTone = getStatusTone(derived.systemReady)
  const retrievalTone = getStatusTone(derived.successRate >= 60)
  const streamTone = getStatusTone(derived.ollamaReachable)

  const heroCards = [
    {
      label: "Memory posture",
      value: derived.memoryTone,
      detail: derived.cacheSize > 0
        ? `${formatCompact(derived.cacheSize)} cached tool entries are available for reuse.`
        : "The system has not exposed cache artifacts yet."
    },
    {
      label: "Recall quality",
      value: `${derived.successRate}%`,
      detail: `${derived.totalSuccess} successful trace outcomes across ${derived.totalEvents} retrieval events.`
    },
    {
      label: "Search pressure",
      value: derived.retrievalTrend,
      detail: derived.latest
        ? `Latest bucket ${derived.latest.bucket} processed ${derived.latest.event_count} retrieval events.`
        : "No retrieval buckets have been reported yet."
    },
    {
      label: "Semantic runtime",
      value: derived.ollamaReachable ? "reachable" : "offline",
      detail: derived.ollamaReachable
        ? "Inference path is reachable from the console."
        : "Semantic retrieval explanations may degrade while the runtime is offline."
    }
  ]

  const tutorCards = [
    {
      title: "What this page means",
      body:
        "This page shows whether memory is being stored, reused, and retrieved successfully. Think of it as the organism's long-term recall center."
    },
    {
      title: "How to read recall quality",
      body:
        "Higher success rate means the memory system is finding useful past traces. A rising failure rate means retrieval or downstream execution needs attention."
    },
    {
      title: "How self-heal appears here",
      body:
        "If search quality drops while system health stays online, the organism is still alive but learning under stress. That is where repair loops and tuning should focus."
    }
  ]

  return (
    <section className="flex flex-col h-full w-full overflow-hidden p-6 text-slate-300">
      <style>{`
        @keyframes memoryPulse {
          0% { transform: scale(0.96); opacity: 0.45; }
          50% { transform: scale(1.08); opacity: 1; }
          100% { transform: scale(0.96); opacity: 0.45; }
        }
        @keyframes memorySweep {
          0% { transform: translateX(-120%); }
          100% { transform: translateX(160%); }
        }
        @keyframes memoryFloat {
          0% { transform: translateY(0px); }
          50% { transform: translateY(-8px); }
          100% { transform: translateY(0px); }
        }
      `}</style>

      <div className="flex flex-col gap-6 h-full min-h-0 overflow-y-auto custom-scrollbar pb-10">
        
        <header className="relative overflow-hidden rounded-3xl p-8 bg-slate-900/60 border border-white/10 shadow-[0_20px_60px_rgba(0,0,0,0.5)] shrink-0">
          <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/5 to-transparent -translate-x-[120%] animate-[memorySweep_5.8s_linear_infinite]" />
          
          <div className="grid grid-cols-1 lg:grid-cols-[1.25fr_0.75fr] gap-8 items-center relative z-10">
            <div>
              <div className="text-cyan-300 text-xs font-black tracking-[0.16em] uppercase mb-3">
                Memory bridge / semantic recall
              </div>
              <h1 className="text-4xl md:text-5xl font-black tracking-tight text-white m-0">
                Living memory tutor
              </h1>
              <p className="mt-4 max-w-[760px] text-slate-300/80 leading-relaxed">
                Watch how the organism stores traces, reuses past work, and judges retrieval quality in real time. This page explains what the memory system is doing, whether it is healthy, and where self-heal pressure is building.
              </p>

              <div className="flex flex-wrap gap-3 mt-6">
                {[
                  { label: "Readiness", value: readinessTone.label, accent: readinessTone.accent },
                  { label: "Recall", value: retrievalTone.label, accent: retrievalTone.accent },
                  { label: "Runtime", value: streamTone.label, accent: streamTone.accent },
                  { label: "Environment", value: derived.status.environment ?? "unknown", accent: "#a78bfa" }
                ].map((item) => (
                  <div key={item.label} className="flex items-center gap-3 px-4 py-2.5 rounded-full bg-white/5 border border-white/10">
                    <span 
                      className="w-2.5 h-2.5 rounded-full" 
                      style={{ background: item.accent, boxShadow: `0 0 15px ${item.accent}`, animation: "memoryPulse 2.4s ease-in-out infinite" }}
                    />
                    <span className="text-white/70 text-xs font-black uppercase tracking-widest">{item.label}</span>
                    <span className="text-white font-black text-sm">{item.value}</span>
                  </div>
                ))}
              </div>
            </div>

            <aside className="rounded-3xl p-6 bg-[#030814]/60 border border-cyan-400/20 shadow-[0_18px_44px_rgba(0,0,0,0.5)] backdrop-blur-xl animate-[memoryFloat_5s_ease-in-out_infinite]">
              <div className="text-cyan-300 text-[11px] font-black uppercase tracking-[0.16em] mb-3">
                Tutor readout
              </div>
              <div className="text-white text-3xl font-black mb-3">
                {derived.qualityTone}
              </div>
              <div className="text-slate-300/80 leading-relaxed text-sm">
                {derived.successRate >= 80
                  ? "Memory retrieval is performing strongly. The organism is finding and reusing prior traces with high confidence."
                  : derived.successRate >= 60
                    ? "Memory retrieval is usable but should still be watched. There is enough signal for guidance, but not enough for autopilot confidence."
                    : "Memory retrieval is under pressure. Either stored traces are too sparse, the runtime is unstable, or downstream execution is failing after recall."}
              </div>
            </aside>
          </div>
        </header>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 shrink-0">
          {heroCards.map((card, index) => (
            <article key={card.label} className={`rounded-3xl p-6 border border-white/10 shadow-lg ${index % 2 === 0 ? "bg-white/5" : "bg-cyan-500/5"}`}>
              <div className="text-cyan-300 text-[11px] font-black uppercase tracking-[0.14em] mb-2">{card.label}</div>
              <div className="text-white text-3xl font-black tracking-tight mb-2">{card.value}</div>
              <div className="text-slate-300/80 text-sm leading-relaxed">{card.detail}</div>
            </article>
          ))}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[1.4fr_0.8fr] gap-5 shrink-0">
          <article className="rounded-3xl p-6 bg-white/5 border border-white/10 shadow-xl">
            <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
              <div>
                <div className="text-cyan-300 text-[11px] font-black uppercase tracking-[0.14em] mb-2">Retrieval stream</div>
                <div className="text-white text-2xl font-black">Trace kinetics</div>
              </div>
              <div className="flex gap-3 flex-wrap">
                {[
                  { label: "Events", color: "#7dd3fc" },
                  { label: "Success", color: "#22c55e" },
                  { label: "Fail", color: "#f97316" }
                ].map((legend) => (
                  <div key={legend.label} className="flex items-center gap-2 text-slate-300/80 text-xs font-bold">
                    <span className="w-2.5 h-2.5 rounded-full" style={{ background: legend.color }} />
                    {legend.label}
                  </div>
                ))}
              </div>
            </div>

            {derived.points.length > 0 ? (
              <div className="relative overflow-hidden rounded-2xl p-4 bg-[#020617]/60 border border-white/5">
                <svg viewBox={`0 0 ${chart.width} ${chart.height}`} className="w-full h-auto block">
                  <defs>
                    <linearGradient id="memoryAreaFill" x1="0" x2="0" y1="0" y2="1">
                      <stop offset="0%" stopColor="rgba(125,211,252,0.34)" />
                      <stop offset="100%" stopColor="rgba(125,211,252,0.03)" />
                    </linearGradient>
                  </defs>

                  {[0.2, 0.4, 0.6, 0.8].map((step) => {
                    const y = chart.height - chart.padding - (chart.height - chart.padding * 2) * step
                    return (
                      <line key={step} x1={chart.padding} x2={chart.width - chart.padding} y1={y} y2={y} stroke="rgba(255,255,255,0.08)" strokeDasharray="4 8" />
                    )
                  })}

                  {chart.area && <polygon points={chart.area} fill="url(#memoryAreaFill)" />}
                  {chart.eventsLine && <polyline points={chart.eventsLine} fill="none" stroke="#7dd3fc" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />}
                  {chart.successLine && <polyline points={chart.successLine} fill="none" stroke="#22c55e" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />}
                  {chart.failLine && <polyline points={chart.failLine} fill="none" stroke="#f97316" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />}
                </svg>

                <div className="grid grid-cols-[repeat(auto-fit,minmax(0,1fr))] gap-2.5 mt-3">
                  {derived.points.slice(-8).map((point) => (
                    <div key={point.bucket} className="p-3 rounded-xl bg-white/5 border border-white/10">
                      <div className="text-white/60 text-[11px] font-black uppercase tracking-widest mb-1">{point.bucket}</div>
                      <div className="text-white text-base font-black mb-1">{point.event_count} events</div>
                      <div className="text-slate-300/70 text-xs leading-relaxed">{point.success_count} ok · {point.fail_count} fail · {point.partial_count} partial</div>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="rounded-2xl p-10 text-center bg-white/5 border border-dashed border-cyan-400/30">
                <div className="w-4 h-4 mx-auto mb-4 rounded-full bg-cyan-300 shadow-[0_0_20px_rgba(125,211,252,0.6)] animate-[memoryPulse_2.2s_ease-in-out_infinite]" />
                <div className="text-white font-black mb-2">No retrieval stream yet</div>
                <div className="text-slate-300/70 max-w-[520px] mx-auto leading-relaxed">
                  The page is live and waiting for trace buckets. Once memory search activity is reported, this section will show retrieval pressure, recall quality, and failure drift across time.
                </div>
              </div>
            )}
          </article>

          <aside className="flex flex-col gap-4">
            <article className="rounded-3xl p-5 bg-green-500/10 border border-green-500/20">
              <div className="text-green-300 text-[11px] font-black uppercase tracking-[0.14em] mb-2">Memory health</div>
              <div className="text-white text-2xl font-black mb-2">{derived.successRate}%</div>
              <div className="text-green-100/80 text-sm leading-relaxed">Successful retrieval outcomes across the reported trace stream.</div>
            </article>

            <article className="rounded-3xl p-5 bg-purple-500/10 border border-purple-500/20">
              <div className="text-purple-300 text-[11px] font-black uppercase tracking-[0.14em] mb-2">Tool reach</div>
              <div className="text-white text-2xl font-black mb-2">{derived.toolCount}</div>
              <div className="text-purple-100/80 text-sm leading-relaxed">Active capabilities currently exposed to the organism.</div>
            </article>

            <article className="rounded-3xl p-5 bg-orange-500/10 border border-orange-500/20">
              <div className="text-orange-300 text-[11px] font-black uppercase tracking-[0.14em] mb-2">Failure pressure</div>
              <div className="text-white text-2xl font-black mb-2">{derived.failureRate}%</div>
              <div className="text-orange-100/80 text-sm leading-relaxed">Buckets that recalled or executed poorly and may need tuning, retries, or self-heal intervention.</div>
            </article>
          </aside>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[1fr_0.9fr] gap-5 shrink-0">
          <article className="rounded-3xl p-6 bg-white/5 border border-white/10 shadow-xl">
            <div className="text-cyan-300 text-[11px] font-black uppercase tracking-[0.14em] mb-3">Live memory structure</div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {[
                { label: "Vector cache", value: formatCompact(derived.cacheSize), detail: derived.cacheSize > 0 ? `${derived.cachedKeys.length} named cached keys are visible.` : "No cache entries reported yet." },
                { label: "Event memory", value: formatCompact(derived.eventCount), detail: "Total events seen by the organism runtime status feed." },
                { label: "Capabilities", value: formatCompact(derived.capabilities.length), detail: derived.capabilities.length > 0 ? derived.capabilities.slice(0, 4).join(", ") : "No capability list exposed." },
                { label: "Vision path", value: derived.visionRuntime ? "live" : "pending", detail: derived.status.primary_vision_model ? `Primary model: ${derived.status.primary_vision_model}` : "No primary vision model reported." }
              ].map((item) => (
                <div key={item.label} className="rounded-2xl p-5 bg-white/5 border border-white/10">
                  <div className="text-cyan-100/80 text-[11px] font-black uppercase tracking-[0.14em] mb-2">{item.label}</div>
                  <div className="text-white text-2xl font-black mb-2">{item.value}</div>
                  <div className="text-slate-200/70 text-sm leading-relaxed">{item.detail}</div>
                </div>
              ))}
            </div>
          </article>

          <article className="rounded-3xl p-6 bg-[#060b19]/70 border border-white/10 backdrop-blur-md">
            <div className="text-cyan-300 text-[11px] font-black uppercase tracking-[0.14em] mb-3">Tutor guidance</div>
            <div className="flex flex-col gap-3">
              {tutorCards.map((card) => (
                <div key={card.title} className="rounded-2xl p-4 bg-white/5 border border-white/10">
                  <div className="text-white font-black mb-2">{card.title}</div>
                  <div className="text-slate-300/80 text-sm leading-relaxed">{card.body}</div>
                </div>
              ))}
            </div>

            {isLoading && <div className="mt-4 text-slate-300/70 text-sm">Loading live memory telemetry…</div>}
            {isError && (
              <div className="mt-4 rounded-xl p-4 bg-red-900/30 border border-red-500/30 text-red-200 leading-relaxed text-sm">
                Memory telemetry could not be loaded: {errorMessage}
              </div>
            )}
          </article>
        </div>
      </div>
    </section>
  )
}
