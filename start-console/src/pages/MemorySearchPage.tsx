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
    <section
      className="page"
      style={{
        minHeight: "100vh",
        color: "#ebf3ff",
        background: `
          radial-gradient(circle at 10% 0%, rgba(59,130,246,0.22), transparent 28%),
          radial-gradient(circle at 90% 0%, rgba(168,85,247,0.18), transparent 26%),
          radial-gradient(circle at 50% 45%, rgba(14,165,233,0.08), transparent 40%),
          linear-gradient(180deg, #030712 0%, #07111f 44%, #030814 100%)
        `,
        padding: "24px 16px 48px"
      }}
    >
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
        @media (prefers-reduced-motion: reduce) {
          *, *::before, *::after {
            animation: none !important;
            transition: none !important;
          }
        }
      `}</style>

      <div style={{ maxWidth: 1440, margin: "0 auto", display: "grid", gap: 20 }}>
        <header
          style={{
            position: "relative",
            overflow: "hidden",
            borderRadius: 30,
            padding: "28px 24px",
            background: "linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0.03))",
            border: "1px solid rgba(255,255,255,0.10)",
            boxShadow: "0 20px 60px rgba(0,0,0,0.28)"
          }}
        >
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.08), transparent)",
              transform: "translateX(-120%)",
              animation: "memorySweep 5.8s linear infinite"
            }}
          />
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1.25fr) minmax(320px, 0.75fr)",
              gap: 20,
              alignItems: "center"
            }}
          >
            <div>
              <div style={{ color: "#7dd3fc", fontSize: 12, fontWeight: 900, letterSpacing: "0.16em", textTransform: "uppercase", marginBottom: 10 }}>
                Memory bridge / semantic recall
              </div>
              <h1 style={{ margin: 0, fontSize: "clamp(2rem, 4vw, 3.4rem)", lineHeight: 1.02, fontWeight: 950, letterSpacing: "-0.04em", color: "white" }}>
                Living memory tutor
              </h1>
              <p style={{ marginTop: 14, maxWidth: 760, color: "rgba(230,238,255,0.78)", lineHeight: 1.7 }}>
                Watch how the organism stores traces, reuses past work, and judges retrieval quality in real time. This page explains what the memory system is doing, whether it is healthy, and where self-heal pressure is building.
              </p>

              <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 20 }}>
                {[
                  { label: "Readiness", value: readinessTone.label, accent: readinessTone.accent },
                  { label: "Recall", value: retrievalTone.label, accent: retrievalTone.accent },
                  { label: "Runtime", value: streamTone.label, accent: streamTone.accent },
                  { label: "Environment", value: derived.status.environment ?? "unknown", accent: "#a78bfa" }
                ].map((item) => (
                  <div
                    key={item.label}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      padding: "10px 14px",
                      borderRadius: 999,
                      background: "rgba(255,255,255,0.05)",
                      border: "1px solid rgba(255,255,255,0.08)"
                    }}
                  >
                    <span
                      style={{
                        width: 10,
                        height: 10,
                        borderRadius: 999,
                        background: item.accent,
                        boxShadow: `0 0 18px ${item.accent}`,
                        animation: "memoryPulse 2.4s ease-in-out infinite"
                      }}
                    />
                    <span style={{ color: "rgba(255,255,255,0.66)", fontSize: 12, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.12em" }}>
                      {item.label}
                    </span>
                    <span style={{ color: "white", fontWeight: 800 }}>
                      {item.value}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <aside
              style={{
                borderRadius: 24,
                padding: 20,
                background: "rgba(3,8,20,0.56)",
                border: "1px solid rgba(125,211,252,0.18)",
                boxShadow: "0 18px 44px rgba(2,8,23,0.26)",
                backdropFilter: "blur(12px)",
                animation: "memoryFloat 5s ease-in-out infinite"
              }}
            >
              <div style={{ color: "#7dd3fc", fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.16em", marginBottom: 10 }}>
                Tutor readout
              </div>
              <div style={{ color: "white", fontSize: 24, fontWeight: 900, marginBottom: 10 }}>
                {derived.qualityTone}
              </div>
              <div style={{ color: "rgba(230,238,255,0.76)", lineHeight: 1.7, fontSize: 14 }}>
                {derived.successRate >= 80
                  ? "Memory retrieval is performing strongly. The organism is finding and reusing prior traces with high confidence."
                  : derived.successRate >= 60
                    ? "Memory retrieval is usable but should still be watched. There is enough signal for guidance, but not enough for autopilot confidence."
                    : "Memory retrieval is under pressure. Either stored traces are too sparse, the runtime is unstable, or downstream execution is failing after recall."}
              </div>
            </aside>
          </div>
        </header>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
            gap: 16
          }}
        >
          {heroCards.map((card, index) => (
            <article
              key={card.label}
              style={{
                borderRadius: 24,
                padding: 20,
                background: index % 2 === 0 ? "rgba(255,255,255,0.05)" : "rgba(125,211,252,0.06)",
                border: "1px solid rgba(255,255,255,0.08)",
                boxShadow: "0 14px 30px rgba(0,0,0,0.18)"
              }}
            >
              <div style={{ color: "#7dd3fc", fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.14em", marginBottom: 8 }}>
                {card.label}
              </div>
              <div style={{ color: "white", fontSize: 26, fontWeight: 900, letterSpacing: "-0.03em", marginBottom: 8 }}>
                {card.value}
              </div>
              <div style={{ color: "rgba(232,240,255,0.72)", fontSize: 14, lineHeight: 1.65 }}>
                {card.detail}
              </div>
            </article>
          ))}
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 1.4fr) minmax(320px, 0.8fr)",
            gap: 20
          }}
        >
          <article
            style={{
              borderRadius: 28,
              padding: 22,
              background: "linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.02))",
              border: "1px solid rgba(255,255,255,0.08)",
              boxShadow: "0 20px 54px rgba(0,0,0,0.22)"
            }}
          >
            <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 16 }}>
              <div>
                <div style={{ color: "#7dd3fc", fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.14em", marginBottom: 8 }}>
                  Retrieval stream
                </div>
                <h2 style={{ margin: 0, color: "white", fontSize: 24, fontWeight: 900 }}>
                  Trace kinetics
                </h2>
              </div>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                {[
                  { label: "Events", color: "#7dd3fc" },
                  { label: "Success", color: "#22c55e" },
                  { label: "Fail", color: "#f97316" }
                ].map((legend) => (
                  <div key={legend.label} style={{ display: "flex", alignItems: "center", gap: 8, color: "rgba(236,242,255,0.7)", fontSize: 12, fontWeight: 700 }}>
                    <span style={{ width: 10, height: 10, borderRadius: 999, background: legend.color }} />
                    {legend.label}
                  </div>
                ))}
              </div>
            </div>

            {derived.points.length > 0 ? (
              <div
                style={{
                  position: "relative",
                  overflow: "hidden",
                  borderRadius: 24,
                  padding: 14,
                  background: "rgba(2,6,23,0.48)",
                  border: "1px solid rgba(255,255,255,0.06)"
                }}
              >
                <svg viewBox={`0 0 ${chart.width} ${chart.height}`} style={{ width: "100%", height: "auto", display: "block" }}>
                  <defs>
                    <linearGradient id="memoryAreaFill" x1="0" x2="0" y1="0" y2="1">
                      <stop offset="0%" stopColor="rgba(125,211,252,0.34)" />
                      <stop offset="100%" stopColor="rgba(125,211,252,0.03)" />
                    </linearGradient>
                  </defs>

                  {[0.2, 0.4, 0.6, 0.8].map((step) => {
                    const y = chart.height - chart.padding - (chart.height - chart.padding * 2) * step
                    return (
                      <line
                        key={step}
                        x1={chart.padding}
                        x2={chart.width - chart.padding}
                        y1={y}
                        y2={y}
                        stroke="rgba(255,255,255,0.08)"
                        strokeDasharray="4 8"
                      />
                    )
                  })}

                  {chart.area ? (
                    <polygon points={chart.area} fill="url(#memoryAreaFill)" />
                  ) : null}

                  {chart.eventsLine ? (
                    <polyline points={chart.eventsLine} fill="none" stroke="#7dd3fc" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
                  ) : null}

                  {chart.successLine ? (
                    <polyline points={chart.successLine} fill="none" stroke="#22c55e" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
                  ) : null}

                  {chart.failLine ? (
                    <polyline points={chart.failLine} fill="none" stroke="#f97316" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
                  ) : null}
                </svg>

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: `repeat(${Math.min(Math.max(derived.points.length, 1), 8)}, minmax(0, 1fr))`,
                    gap: 10,
                    marginTop: 12
                  }}
                >
                  {derived.points.slice(-8).map((point) => (
                    <div
                      key={point.bucket}
                      style={{
                        padding: 10,
                        borderRadius: 16,
                        background: "rgba(255,255,255,0.04)",
                        border: "1px solid rgba(255,255,255,0.05)"
                      }}
                    >
                      <div style={{ color: "rgba(255,255,255,0.58)", fontSize: 11, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>
                        {point.bucket}
                      </div>
                      <div style={{ color: "white", fontSize: 16, fontWeight: 800, marginBottom: 4 }}>
                        {point.event_count} events
                      </div>
                      <div style={{ color: "rgba(220,231,255,0.68)", fontSize: 12, lineHeight: 1.5 }}>
                        {point.success_count} ok · {point.fail_count} fail · {point.partial_count} partial
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div
                style={{
                  borderRadius: 24,
                  padding: "44px 24px",
                  textAlign: "center",
                  background: "rgba(255,255,255,0.03)",
                  border: "1px dashed rgba(125,211,252,0.28)"
                }}
              >
                <div
                  style={{
                    width: 16,
                    height: 16,
                    margin: "0 auto 16px",
                    borderRadius: 999,
                    background: "#7dd3fc",
                    boxShadow: "0 0 20px rgba(125,211,252,0.6)",
                    animation: "memoryPulse 2.2s ease-in-out infinite"
                  }}
                />
                <div style={{ color: "white", fontWeight: 900, marginBottom: 8 }}>
                  No retrieval stream yet
                </div>
                <div style={{ color: "rgba(236,242,255,0.68)", maxWidth: 520, margin: "0 auto", lineHeight: 1.7 }}>
                  The page is live and waiting for trace buckets. Once memory search activity is reported, this section will show retrieval pressure, recall quality, and failure drift across time.
                </div>
              </div>
            )}
          </article>

          <aside style={{ display: "grid", gap: 16 }}>
            <article
              style={{
                borderRadius: 24,
                padding: 20,
                background: "rgba(34,197,94,0.08)",
                border: "1px solid rgba(34,197,94,0.18)"
              }}
            >
              <div style={{ color: "#86efac", fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.14em", marginBottom: 8 }}>
                Memory health
              </div>
              <h3 style={{ margin: 0, color: "white", fontSize: 24, fontWeight: 900, marginBottom: 10 }}>
                {derived.successRate}%
              </h3>
              <div style={{ color: "rgba(237,255,243,0.82)", lineHeight: 1.7, fontSize: 14 }}>
                Successful retrieval outcomes across the reported trace stream.
              </div>
            </article>

            <article
              style={{
                borderRadius: 24,
                padding: 20,
                background: "rgba(168,85,247,0.08)",
                border: "1px solid rgba(168,85,247,0.18)"
              }}
            >
              <div style={{ color: "#c4b5fd", fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.14em", marginBottom: 8 }}>
                Tool reach
              </div>
              <h3 style={{ margin: 0, color: "white", fontSize: 24, fontWeight: 900, marginBottom: 10 }}>
                {derived.toolCount}
              </h3>
              <div style={{ color: "rgba(244,239,255,0.82)", lineHeight: 1.7, fontSize: 14 }}>
                Active capabilities currently exposed to the organism.
              </div>
            </article>

            <article
              style={{
                borderRadius: 24,
                padding: 20,
                background: "rgba(249,115,22,0.08)",
                border: "1px solid rgba(249,115,22,0.18)"
              }}
            >
              <div style={{ color: "#fdba74", fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.14em", marginBottom: 8 }}>
                Failure pressure
              </div>
              <h3 style={{ margin: 0, color: "white", fontSize: 24, fontWeight: 900, marginBottom: 10 }}>
                {derived.failureRate}%
              </h3>
              <div style={{ color: "rgba(255,242,233,0.82)", lineHeight: 1.7, fontSize: 14 }}>
                Buckets that recalled or executed poorly and may need tuning, retries, or self-heal intervention.
              </div>
            </article>
          </aside>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 1fr) minmax(320px, 0.9fr)",
            gap: 20
          }}
        >
          <article
            style={{
              borderRadius: 28,
              padding: 22,
              background: "linear-gradient(180deg, rgba(255,255,255,0.045), rgba(255,255,255,0.02))",
              border: "1px solid rgba(255,255,255,0.08)"
            }}
          >
            <h2 style={{ margin: 0, color: "#7dd3fc", fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.14em", marginBottom: 10 }}>
              Live memory structure
            </h2>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                gap: 14
              }}
            >
              {[
                {
                  label: "Vector cache",
                  value: formatCompact(derived.cacheSize),
                  detail: derived.cacheSize > 0
                    ? `${derived.cachedKeys.length} named cached keys are visible.`
                    : "No cache entries reported yet."
                },
                {
                  label: "Event memory",
                  value: formatCompact(derived.eventCount),
                  detail: "Total events seen by the organism runtime status feed."
                },
                {
                  label: "Capabilities",
                  value: formatCompact(derived.capabilities.length),
                  detail: derived.capabilities.length > 0
                    ? derived.capabilities.slice(0, 4).join(", ")
                    : "No capability list exposed."
                },
                {
                  label: "Vision path",
                  value: derived.visionRuntime ? "live" : "pending",
                  detail: derived.status.primary_vision_model
                    ? `Primary model: ${derived.status.primary_vision_model}`
                    : "No primary vision model reported."
                }
              ].map((item) => (
                <div
                  key={item.label}
                  style={{
                    borderRadius: 18,
                    padding: 18,
                    background: "rgba(255,255,255,0.035)",
                    border: "1px solid rgba(255,255,255,0.06)"
                  }}
                >
                  <div style={{ color: "rgba(165,222,255,0.78)", fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.14em", marginBottom: 8 }}>
                    {item.label}
                  </div>
                  <div style={{ color: "white", fontSize: 22, fontWeight: 900, marginBottom: 8 }}>
                    {item.value}
                  </div>
                  <div style={{ color: "rgba(226,236,255,0.7)", fontSize: 13, lineHeight: 1.6 }}>
                    {item.detail}
                  </div>
                </div>
              ))}
            </div>
          </article>

          <article
            style={{
              borderRadius: 28,
              padding: 22,
              background: "rgba(6,11,25,0.65)",
              border: "1px solid rgba(255,255,255,0.08)"
            }}
          >
            <h2 style={{ margin: 0, color: "#7dd3fc", fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.14em", marginBottom: 12 }}>
              Tutor guidance
            </h2>
            <div style={{ display: "grid", gap: 12 }}>
              {tutorCards.map((card) => (
                <div
                  key={card.title}
                  style={{
                    borderRadius: 18,
                    padding: 16,
                    background: "rgba(255,255,255,0.035)",
                    border: "1px solid rgba(255,255,255,0.05)"
                  }}
                >
                  <div style={{ color: "white", fontWeight: 800, marginBottom: 8 }}>
                    {card.title}
                  </div>
                  <div style={{ color: "rgba(232,240,255,0.72)", lineHeight: 1.7, fontSize: 14 }}>
                    {card.body}
                  </div>
                </div>
              ))}
            </div>

            {isLoading ? (
              <div style={{ marginTop: 16, color: "rgba(232,240,255,0.66)", fontSize: 14 }}>
                Loading live memory telemetry…
              </div>
            ) : null}

            {isError ? (
              <div
                style={{
                  marginTop: 16,
                  borderRadius: 16,
                  padding: 14,
                  background: "rgba(127,29,29,0.26)",
                  border: "1px solid rgba(248,113,113,0.24)",
                  color: "#fecaca",
                  lineHeight: 1.6
                }}
              >
                Memory telemetry could not be loaded: {errorMessage}
              </div>
            ) : null}
          </article>
        </div>
      </div>
    </section>
  )
}
