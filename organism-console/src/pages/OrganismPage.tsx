import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "../lib/api"
import { useUiStore } from "../state/ui-store"

type OrganismStatusResponse = {
  ready?: boolean
  environment?: string
  event_count?: number
  events_path?: string
  ollama_reachable?: boolean
  primary_vision_model?: string
}

type OrganismToolsResponse = {
  count?: number
  capabilities?: string[]
}

type ToolsCacheResponse = {
  cache_size?: number
  cached_keys?: string[]
}

type TimelinePoint = {
  bucket: string
  event_count: number
  success_count: number
  partial_count: number
  fail_count: number
}

type TimelineResponse = {
  window_minutes: number
  points: TimelinePoint[]
}

type FeatureCardProps = {
  id: string
  activeId: string
  onActivate: (id: string) => void
  label: string
  title: string
  value: string
  summary: string
  detail: string
  nextStep: string
  accent: string
  glow: string
  gradient: string
}

type StatTileProps = {
  label: string
  value: string
  tone: string
  detail: string
}

type KnowledgePanelProps = {
  badge: string
  title: string
  intro: string
  bullets: string[]
  accent: string
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

function hasCapability(capabilities: string[] | undefined, name: string) {
  return (capabilities ?? []).some((item) => item.toLowerCase() === name.toLowerCase())
}

function getTimelineUrl(backendUrl: string) {
  return `${backendUrl.replace(/\/$/, "")}/timeline?window_minutes=20000`
}

function formatCompact(value: number) {
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value)
}

function getLinePoints(values: number[], width: number, height: number, padding: number) {
  if (!values.length) return ""
  const maxY = Math.max(...values, 1)
  const innerWidth = width - padding * 2
  const innerHeight = height - padding * 2
  const stepX = values.length > 1 ? innerWidth / (values.length - 1) : innerWidth / 2

  return values
    .map((value, index) => {
      const x = padding + index * stepX
      const y = padding + innerHeight - (value / maxY) * innerHeight
      return `${x},${y}`
    })
    .join(" ")
}

function getAreaPoints(values: number[], width: number, height: number, padding: number) {
  const line = getLinePoints(values, width, height, padding)
  if (!line || !values.length) return ""
  return `${padding},${height - padding} ${line} ${width - padding},${height - padding}`
}

function getStatusColor(value: boolean | undefined) {
  return value ? "#4ade80" : "#f59e0b"
}

function getStatusText(value: boolean | undefined, positive: string, negative: string) {
  return value ? positive : negative
}

function FeatureCard({
  id,
  activeId,
  onActivate,
  label,
  title,
  value,
  summary,
  detail,
  nextStep,
  accent,
  glow,
  gradient
}: FeatureCardProps) {
  const isActive = activeId === id

  return (
    <button
      type="button"
      onClick={() => onActivate(id)}
      aria-pressed={isActive}
      style={{
        position: "relative",
        overflow: "hidden",
        width: "100%",
        textAlign: "left",
        padding: 22,
        borderRadius: 24,
        border: isActive ? `1px solid ${accent}` : "1px solid rgba(255,255,255,0.08)",
        background: gradient,
        color: "white",
        cursor: "pointer",
        transform: isActive ? "translateY(-6px) scale(1.01)" : "translateY(0) scale(1)",
        boxShadow: isActive
          ? `0 26px 70px ${glow}, 0 0 0 1px ${accent}33 inset`
          : "0 20px 50px rgba(0,0,0,0.26)",
        transition: "transform 180ms ease, box-shadow 180ms ease, border-color 180ms ease",
        animation: isActive ? "activeCardGlow 2.8s ease-in-out infinite" : "floatCard 7s ease-in-out infinite"
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "linear-gradient(115deg, transparent 20%, rgba(255,255,255,0.14) 50%, transparent 80%)",
          transform: "translateX(-120%)",
          animation: isActive ? "scanSweep 2.8s linear infinite" : "none",
          pointerEvents: "none"
        }}
      />

      <div
        style={{
          position: "absolute",
          top: -30,
          right: -10,
          width: 150,
          height: 150,
          borderRadius: "50%",
          background: "rgba(255,255,255,0.10)",
          filter: "blur(10px)"
        }}
      />

      <div style={{ position: "relative", zIndex: 1 }}>
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 10,
            padding: "7px 12px",
            borderRadius: 999,
            background: "rgba(255,255,255,0.10)",
            marginBottom: 14,
            fontSize: 12,
            fontWeight: 800,
            letterSpacing: "0.08em",
            textTransform: "uppercase"
          }}
        >
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: accent,
              boxShadow: `0 0 18px ${accent}`,
              animation: "statusBlink 1.8s ease-in-out infinite"
            }}
          />
          {label}
        </div>

        <div style={{ fontSize: 14, opacity: 0.78, marginBottom: 8 }}>{title}</div>
        <div style={{ fontSize: 32, fontWeight: 900, lineHeight: 1.02, marginBottom: 10 }}>{value}</div>
        <div style={{ lineHeight: 1.65, color: "rgba(255,255,255,0.92)", marginBottom: 14 }}>{summary}</div>

        <div
          style={{
            borderRadius: 16,
            padding: 14,
            background: isActive ? "rgba(255,255,255,0.16)" : "rgba(255,255,255,0.08)",
            border: "1px solid rgba(255,255,255,0.10)",
            marginBottom: 12
          }}
        >
          <div style={{ fontSize: 12, letterSpacing: "0.06em", textTransform: "uppercase", opacity: 0.76, marginBottom: 6 }}>
            Operator meaning
          </div>
          <div style={{ lineHeight: 1.6 }}>{detail}</div>
        </div>

        <div
          style={{
            fontSize: 13,
            color: "rgba(255,255,255,0.84)",
            paddingTop: 4
          }}
        >
          Next move: {nextStep}
        </div>
      </div>
    </button>
  )
}

function StatTile({ label, value, tone, detail }: StatTileProps) {
  return (
    <article
      style={{
        position: "relative",
        overflow: "hidden",
        borderRadius: 20,
        padding: 18,
        background: "linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.03))",
        border: "1px solid rgba(255,255,255,0.08)",
        minHeight: 142,
        boxShadow: "0 16px 40px rgba(0,0,0,0.18)",
        animation: "floatCard 8s ease-in-out infinite"
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: "auto -10px -30px auto",
          width: 120,
          height: 120,
          borderRadius: "50%",
          background: `${tone}18`,
          filter: "blur(12px)"
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "linear-gradient(120deg, transparent 0%, rgba(255,255,255,0.06) 48%, transparent 100%)",
          transform: "translateX(-120%)",
          animation: "scanSweep 5s linear infinite",
          pointerEvents: "none"
        }}
      />
      <div style={{ position: "relative", zIndex: 1 }}>
        <div
          style={{
            color: tone,
            fontSize: 12,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            fontWeight: 800,
            marginBottom: 10
          }}
        >
          {label}
        </div>
        <div style={{ color: "white", fontSize: 30, fontWeight: 900, lineHeight: 1.05, marginBottom: 8 }}>
          {value}
        </div>
        <div style={{ color: "rgba(255,255,255,0.70)", lineHeight: 1.55 }}>{detail}</div>
      </div>
    </article>
  )
}

function KnowledgePanel({ badge, title, intro, bullets, accent }: KnowledgePanelProps) {
  return (
    <article
      style={{
        position: "relative",
        overflow: "hidden",
        borderRadius: 22,
        padding: 22,
        background: "linear-gradient(180deg, rgba(14,20,35,0.96), rgba(8,11,20,0.98))",
        border: `1px solid ${accent}33`,
        boxShadow: "0 20px 50px rgba(0,0,0,0.24)",
        animation: "floatPanel 8.5s ease-in-out infinite"
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "linear-gradient(120deg, transparent 0%, rgba(255,255,255,0.05) 48%, transparent 100%)",
          transform: "translateX(-120%)",
          animation: "scanSweep 6.2s linear infinite"
        }}
      />

      <div style={{ position: "relative", zIndex: 1 }}>
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 10,
            padding: "6px 12px",
            borderRadius: 999,
            background: `${accent}1A`,
            color: accent,
            fontSize: 12,
            fontWeight: 800,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            marginBottom: 14
          }}
        >
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: accent,
              boxShadow: `0 0 16px ${accent}`,
              animation: "statusBlink 2s ease-in-out infinite"
            }}
          />
          {badge}
        </div>

        <h2 style={{ margin: "0 0 10px", color: "white", fontSize: 24 }}>{title}</h2>
        <p style={{ margin: "0 0 16px", color: "rgba(255,255,255,0.74)", lineHeight: 1.7 }}>{intro}</p>

        <div style={{ display: "grid", gap: 10 }}>
          {bullets.map((bullet) => (
            <div
              key={bullet}
              style={{
                borderRadius: 14,
                padding: "12px 14px",
                background: "rgba(255,255,255,0.04)",
                border: "1px solid rgba(255,255,255,0.08)",
                color: "rgba(255,255,255,0.90)",
                lineHeight: 1.6
              }}
            >
              {bullet}
            </div>
          ))}
        </div>
      </div>
    </article>
  )
}

export default function OrganismPage() {
  const backendUrl = useUiStore((state) => state.backendUrl)
  const [activeCard, setActiveCard] = useState("learning")

  const statusQuery = useQuery({
    queryKey: ["organism-status", backendUrl],
    queryFn: () => api.getStatus<OrganismStatusResponse>(backendUrl),
    retry: 1,
    refetchInterval: 30000
  })

  const toolsQuery = useQuery({
    queryKey: ["organism-tools", backendUrl],
    queryFn: () => api.getTools<OrganismToolsResponse>(backendUrl),
    retry: 1,
    refetchInterval: 60000
  })

  const toolsCacheQuery = useQuery({
    queryKey: ["organism-tools-cache", backendUrl],
    queryFn: () => api.getToolsCache<ToolsCacheResponse>(backendUrl),
    retry: 1,
    refetchInterval: 60000
  })

  const timelineQuery = useQuery<TimelineResponse, Error>({
    queryKey: ["organism-timeline", backendUrl],
    queryFn: async () => {
      const response = await fetch(getTimelineUrl(backendUrl))
      if (!response.ok) {
        throw new Error(`Timeline request failed with ${response.status}`)
      }
      return (await response.json()) as TimelineResponse
    },
    retry: 1,
    refetchInterval: 15000
  })

  const isLoading =
    statusQuery.isLoading || toolsQuery.isLoading || toolsCacheQuery.isLoading || timelineQuery.isLoading

  const isError =
    statusQuery.isError || toolsQuery.isError || toolsCacheQuery.isError || timelineQuery.isError

  const errorMessage =
    statusQuery.isError
      ? getErrorMessage(statusQuery.error)
      : toolsQuery.isError
        ? getErrorMessage(toolsQuery.error)
        : toolsCacheQuery.isError
          ? getErrorMessage(toolsCacheQuery.error)
          : timelineQuery.isError
            ? getErrorMessage(timelineQuery.error)
            : null

  const capabilities = toolsQuery.data?.capabilities ?? []
  const cacheSize = toolsCacheQuery.data?.cache_size ?? 0
  const cachedKeys = toolsCacheQuery.data?.cached_keys ?? []
  const eventCount = statusQuery.data?.event_count ?? 0
  const timelinePoints = timelineQuery.data?.points ?? []
  const toolCount = toolsQuery.data?.count ?? 0
  const systemReady = !!statusQuery.data?.ready
  const ollamaReady = !!statusQuery.data?.ollama_reachable

  const totalTimelineEvents = timelinePoints.reduce((sum, point) => sum + point.event_count, 0)
  const totalTimelineSuccess = timelinePoints.reduce((sum, point) => sum + point.success_count, 0)
  const totalTimelinePartial = timelinePoints.reduce((sum, point) => sum + point.partial_count, 0)
  const totalTimelineFail = timelinePoints.reduce((sum, point) => sum + point.fail_count, 0)

  const successRate = totalTimelineEvents > 0 ? Math.round((totalTimelineSuccess / totalTimelineEvents) * 100) : 0
  const failureRate = totalTimelineEvents > 0 ? Math.round((totalTimelineFail / totalTimelineEvents) * 100) : 0

  const visionExposedToTools =
    hasCapability(capabilities, "vision") ||
    hasCapability(capabilities, "moondream") ||
    hasCapability(capabilities, "qwen3-vl") ||
    hasCapability(capabilities, "qwen2.5-vl") ||
    hasCapability(capabilities, "llava")

  const visionConfigured = true
  const visionRuntimeReady = visionConfigured && visionExposedToTools && ollamaReady

  const width = 1100
  const height = 340
  const padding = 28

  const allEventsValues = timelinePoints.map((point) => point.event_count)
  const successValues = timelinePoints.map((point) => point.success_count)
  const partialValues = timelinePoints.map((point) => point.partial_count)
  const failValues = timelinePoints.map((point) => point.fail_count)

  const allEventsLine = getLinePoints(allEventsValues, width, height, padding)
  const successLine = getLinePoints(successValues, width, height, padding)
  const partialLine = getLinePoints(partialValues, width, height, padding)
  const failLine = getLinePoints(failValues, width, height, padding)
  const allEventsArea = getAreaPoints(allEventsValues, width, height, padding)

  const latestBucket = timelinePoints[timelinePoints.length - 1]?.bucket ?? "No timeline yet"

  const activeMessage = useMemo(() => {
    switch (activeCard) {
      case "learning":
        return "Learning turns event flow into memory, context, and adaptive behavior."
      case "healing":
        return "Healing tells you whether the organism is resilient enough to keep serving."
      case "autonomy":
        return "Autonomy shows whether the organism can act through real capabilities."
      case "vision":
        return "Vision tells you whether image-aware workflows are live and usable."
      default:
        return "Operator control keeps the system explainable, reviewable, and directed by you."
    }
  }, [activeCard])

  const tickerItems = [
    `events ${eventCount}`,
    `timeline ${totalTimelineEvents}`,
    `success ${successRate}%`,
    `fail ${failureRate}%`,
    `tools ${toolCount}`,
    `cache ${cacheSize}`,
    `vision ${visionRuntimeReady ? "live" : "pending"}`,
    `ollama ${ollamaReady ? "reachable" : "offline"}`
  ]

  const pulseCards = [
    {
      label: "State",
      value: getStatusText(systemReady, "Ready", "Review"),
      color: getStatusColor(systemReady),
      detail: systemReady ? "Organism status endpoint reports ready." : "Readiness is not fully healthy yet."
    },
    {
      label: "Ollama",
      value: getStatusText(ollamaReady, "Reachable", "Offline"),
      color: getStatusColor(ollamaReady),
      detail: ollamaReady ? "Model runtime is reachable from the console." : "Vision and inference paths may degrade."
    },
    {
      label: "Vision",
      value: visionRuntimeReady ? "Live" : "Pending",
      color: visionRuntimeReady ? "#f472b6" : "#f59e0b",
      detail: visionRuntimeReady ? "Visual model path is exposed to tools." : "Visual workflows are not fully available yet."
    },
    {
      label: "Tools cache",
      value: String(cacheSize),
      color: "#7dd3fc",
      detail: cachedKeys.length > 0 ? `${cachedKeys.length} cached keys exposed.` : "No cached tool keys reported yet."
    }
  ]

  return (
    <section
      className="page"
      style={{
        minHeight: "100vh",
        color: "#ecf3ff",
        background: `
          radial-gradient(circle at 0% 0%, rgba(14,165,233,0.18), transparent 26%),
          radial-gradient(circle at 100% 0%, rgba(139,92,246,0.20), transparent 24%),
          radial-gradient(circle at 100% 100%, rgba(236,72,153,0.18), transparent 24%),
          radial-gradient(circle at 0% 100%, rgba(34,197,94,0.12), transparent 22%),
          linear-gradient(180deg, #040816 0%, #07101f 48%, #050815 100%)
        `,
        padding: "28px 18px 56px"
      }}
    >
      <style>
        {`
          @keyframes pulseHalo {
            0% { transform: scale(0.96); opacity: 0.35; }
            50% { transform: scale(1.08); opacity: 0.9; }
            100% { transform: scale(0.96); opacity: 0.35; }
          }

          @keyframes statusBlink {
            0% { opacity: 0.45; transform: scale(0.95); }
            50% { opacity: 1; transform: scale(1.18); }
            100% { opacity: 0.45; transform: scale(0.95); }
          }

          @keyframes scanSweep {
            0% { transform: translateX(-120%); }
            100% { transform: translateX(140%); }
          }

          @keyframes floatCard {
            0% { transform: translateY(0px); }
            50% { transform: translateY(-4px); }
            100% { transform: translateY(0px); }
          }

          @keyframes floatPanel {
            0% { transform: translateY(0px); }
            50% { transform: translateY(-6px); }
            100% { transform: translateY(0px); }
          }

          @keyframes tickerMove {
            0% { transform: translateX(0); }
            100% { transform: translateX(-50%); }
          }

          @keyframes activeCardGlow {
            0% { box-shadow: 0 26px 70px rgba(59,130,246,0.16); }
            50% { box-shadow: 0 30px 90px rgba(59,130,246,0.30); }
            100% { box-shadow: 0 26px 70px rgba(59,130,246,0.16); }
          }

          @keyframes orbitSpin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
          }

          @keyframes orbitReverse {
            from { transform: rotate(360deg); }
            to { transform: rotate(0deg); }
          }

          @keyframes corePulse {
            0% { transform: scale(0.96); opacity: 0.78; }
            50% { transform: scale(1.08); opacity: 1; }
            100% { transform: scale(0.96); opacity: 0.78; }
          }

          @keyframes dashFlow {
            to { stroke-dashoffset: -24; }
          }
        `}
      </style>

      <div style={{ maxWidth: 1440, margin: "0 auto", display: "grid", gap: 22 }}>
        <header
          style={{
            position: "relative",
            overflow: "hidden",
            borderRadius: 30,
            border: "1px solid rgba(255,255,255,0.10)",
            background: "linear-gradient(135deg, rgba(8,17,35,0.98), rgba(18,30,60,0.94) 45%, rgba(10,16,34,0.98))",
            boxShadow: "0 30px 90px rgba(0,0,0,0.34)"
          }}
        >
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: "linear-gradient(90deg, rgba(255,255,255,0.03), transparent 25%, transparent 75%, rgba(255,255,255,0.03))",
              pointerEvents: "none"
            }}
          />

          <div
            style={{
              position: "absolute",
              top: -40,
              right: 40,
              width: 320,
              height: 320,
              borderRadius: "50%",
              background: "radial-gradient(circle, rgba(56,189,248,0.28), transparent 70%)",
              filter: "blur(12px)",
              animation: "pulseHalo 4.8s ease-in-out infinite"
            }}
          />

          <div
            style={{
              position: "absolute",
              bottom: -70,
              left: -20,
              width: 320,
              height: 320,
              borderRadius: "50%",
              background: "radial-gradient(circle, rgba(236,72,153,0.20), transparent 70%)",
              filter: "blur(12px)",
              animation: "pulseHalo 4.2s ease-in-out infinite"
            }}
          />

          <div style={{ position: "relative", zIndex: 1, padding: "30px 28px 28px" }}>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(0, 1.45fr) minmax(320px, 0.95fr)",
                gap: 22,
                alignItems: "stretch"
              }}
            >
              <div style={{ display: "grid", gap: 18 }}>
                <div
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 10,
                    width: "fit-content",
                    padding: "8px 14px",
                    borderRadius: 999,
                    background: "rgba(56,189,248,0.12)",
                    color: "#7dd3fc",
                    fontSize: 12,
                    fontWeight: 900,
                    letterSpacing: "0.08em",
                    textTransform: "uppercase"
                  }}
                >
                  <span
                    style={{
                      width: 10,
                      height: 10,
                      borderRadius: "50%",
                      background: "#7dd3fc",
                      boxShadow: "0 0 18px #7dd3fc",
                      animation: "statusBlink 1.8s ease-in-out infinite"
                    }}
                  />
                  Live organism control surface
                </div>

                <div style={{ display: "grid", gap: 12 }}>
                  <h1 style={{ margin: 0, fontSize: "clamp(2.7rem, 5vw, 5rem)", lineHeight: 0.96 }}>
                    Organism Console
                  </h1>

                  <p
                    style={{
                      margin: 0,
                      maxWidth: 860,
                      fontSize: 17,
                      lineHeight: 1.78,
                      color: "rgba(236,243,255,0.82)"
                    }}
                  >
                    This page should feel like a living system surface, not a static status page. It shows readiness,
                    organism activity, tool reach, visual perception, and operator guidance in one high-information view.
                  </p>
                </div>

                <div
                  style={{
                    position: "relative",
                    overflow: "hidden",
                    borderRadius: 16,
                    border: "1px solid rgba(255,255,255,0.08)",
                    background: "rgba(255,255,255,0.04)",
                    padding: "10px 0"
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      width: "max-content",
                      animation: "tickerMove 20s linear infinite"
                    }}
                  >
                    {[...tickerItems, ...tickerItems].map((item, index) => (
                      <div
                        key={`${item}-${index}`}
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: 10,
                          padding: "0 18px",
                          color: "rgba(255,255,255,0.82)",
                          whiteSpace: "nowrap",
                          fontSize: 13,
                          fontWeight: 700,
                          textTransform: "uppercase",
                          letterSpacing: "0.06em"
                        }}
                      >
                        <span
                          style={{
                            width: 8,
                            height: 8,
                            borderRadius: "50%",
                            background: index % 4 === 0 ? "#7dd3fc" : index % 4 === 1 ? "#86efac" : index % 4 === 2 ? "#fbbf24" : "#f9a8d4",
                            boxShadow: "0 0 12px currentColor"
                          }}
                        />
                        {item}
                      </div>
                    ))}
                  </div>
                </div>

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                    gap: 14
                  }}
                >
                  <StatTile
                    label="Observed events"
                    value={isLoading ? "Loading..." : formatCompact(eventCount)}
                    tone="#7dd3fc"
                    detail="Raw event volume reported by organism status."
                  />
                  <StatTile
                    label="Timeline throughput"
                    value={isLoading ? "Loading..." : formatCompact(totalTimelineEvents)}
                    tone="#a78bfa"
                    detail={activeMessage}
                  />
                  <StatTile
                    label="Success rate"
                    value={isLoading ? "Loading..." : `${successRate}%`}
                    tone="#4ade80"
                    detail="Share of timeline activity ending in successful outcomes."
                  />
                  <StatTile
                    label="Tool surface"
                    value={isLoading ? "Loading..." : `${toolCount}`}
                    tone="#f59e0b"
                    detail="Total tool count visible to the organism right now."
                  />
                </div>
              </div>

              <div
                style={{
                  position: "relative",
                  overflow: "hidden",
                  display: "grid",
                  gap: 14,
                  padding: 18,
                  borderRadius: 24,
                  background: "linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.03))",
                  border: "1px solid rgba(255,255,255,0.08)",
                  backdropFilter: "blur(8px)"
                }}
              >
                <div
                  style={{
                    display: "grid",
                    placeItems: "center",
                    minHeight: 330,
                    borderRadius: 22,
                    background: "radial-gradient(circle at center, rgba(255,255,255,0.05), rgba(255,255,255,0.02) 45%, transparent 70%)",
                    border: "1px solid rgba(255,255,255,0.06)",
                    position: "relative",
                    overflow: "hidden"
                  }}
                >
                  <svg
                    viewBox="0 0 420 320"
                    style={{ width: "100%", height: "100%", maxWidth: 420 }}
                    role="img"
                    aria-label="Animated organism core visualization"
                  >
                    <defs>
                      <radialGradient id="coreGlow" cx="50%" cy="50%" r="50%">
                        <stop offset="0%" stopColor="rgba(125,211,252,0.95)" />
                        <stop offset="45%" stopColor="rgba(96,165,250,0.38)" />
                        <stop offset="100%" stopColor="rgba(96,165,250,0)" />
                      </radialGradient>
                      <radialGradient id="pinkGlow" cx="50%" cy="50%" r="50%">
                        <stop offset="0%" stopColor="rgba(244,114,182,0.90)" />
                        <stop offset="45%" stopColor="rgba(244,114,182,0.30)" />
                        <stop offset="100%" stopColor="rgba(244,114,182,0)" />
                      </radialGradient>
                      <radialGradient id="greenGlow" cx="50%" cy="50%" r="50%">
                        <stop offset="0%" stopColor="rgba(74,222,128,0.88)" />
                        <stop offset="45%" stopColor="rgba(74,222,128,0.28)" />
                        <stop offset="100%" stopColor="rgba(74,222,128,0)" />
                      </radialGradient>
                    </defs>

                    <g style={{ transformOrigin: "210px 160px", animation: "orbitSpin 16s linear infinite" }}>
                      <circle cx="210" cy="160" r="116" fill="none" stroke="rgba(125,211,252,0.22)" strokeWidth="1.6" strokeDasharray="8 10" />
                      <circle cx="326" cy="160" r="8" fill="#7dd3fc" style={{ filter: "drop-shadow(0 0 10px #7dd3fc)" }} />
                      <circle cx="210" cy="44" r="7" fill="#4ade80" style={{ filter: "drop-shadow(0 0 10px #4ade80)" }} />
                      <circle cx="94" cy="160" r="8" fill="#fbbf24" style={{ filter: "drop-shadow(0 0 10px #fbbf24)" }} />
                      <circle cx="210" cy="276" r="8" fill="#f472b6" style={{ filter: "drop-shadow(0 0 10px #f472b6)" }} />
                    </g>

                    <g style={{ transformOrigin: "210px 160px", animation: "orbitReverse 10s linear infinite" }}>
                      <circle cx="210" cy="160" r="76" fill="none" stroke="rgba(196,181,253,0.24)" strokeWidth="1.4" strokeDasharray="6 9" />
                      <circle cx="286" cy="160" r="6" fill="#c4b5fd" style={{ filter: "drop-shadow(0 0 10px #c4b5fd)" }} />
                      <circle cx="172" cy="94" r="5" fill="#7dd3fc" style={{ filter: "drop-shadow(0 0 8px #7dd3fc)" }} />
                      <circle cx="172" cy="226" r="5" fill="#86efac" style={{ filter: "drop-shadow(0 0 8px #86efac)" }} />
                    </g>

                    <g>
                      <path d="M94 160 L172 160 L210 160" fill="none" stroke="rgba(251,191,36,0.55)" strokeWidth="2" strokeDasharray="5 6" style={{ animation: "dashFlow 1.6s linear infinite" }} />
                      <path d="M210 44 L210 116 L210 160" fill="none" stroke="rgba(74,222,128,0.55)" strokeWidth="2" strokeDasharray="5 6" style={{ animation: "dashFlow 1.8s linear infinite" }} />
                      <path d="M210 160 L286 160 L326 160" fill="none" stroke="rgba(125,211,252,0.60)" strokeWidth="2" strokeDasharray="5 6" style={{ animation: "dashFlow 2s linear infinite" }} />
                      <path d="M210 160 L210 220 L210 276" fill="none" stroke="rgba(244,114,182,0.55)" strokeWidth="2" strokeDasharray="5 6" style={{ animation: "dashFlow 1.7s linear infinite" }} />
                    </g>

                    <g style={{ transformOrigin: "210px 160px", animation: "corePulse 3s ease-in-out infinite" }}>
                      <circle cx="210" cy="160" r="56" fill="url(#coreGlow)" />
                      <circle cx="210" cy="160" r="34" fill="rgba(8,18,38,0.92)" stroke="rgba(125,211,252,0.55)" strokeWidth="2" />
                      <circle cx="210" cy="160" r="16" fill="#7dd3fc" style={{ filter: "drop-shadow(0 0 18px #7dd3fc)" }} />
                    </g>

                    <circle cx="258" cy="116" r="42" fill="url(#pinkGlow)" opacity="0.55" />
                    <circle cx="164" cy="210" r="36" fill="url(#greenGlow)" opacity="0.45" />
                  </svg>

                  <div
                    style={{
                      position: "absolute",
                      left: 16,
                      right: 16,
                      bottom: 14,
                      display: "grid",
                      gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                      gap: 10
                    }}
                  >
                    {[
                      { label: "Core", value: systemReady ? "stable" : "review", color: systemReady ? "#86efac" : "#fbbf24" },
                      { label: "Vision", value: visionRuntimeReady ? "online" : "pending", color: visionRuntimeReady ? "#f9a8d4" : "#fbbf24" },
                      { label: "Action", value: toolCount > 0 ? "enabled" : "limited", color: toolCount > 0 ? "#7dd3fc" : "#fb7185" }
                    ].map((item) => (
                      <div
                        key={item.label}
                        style={{
                          borderRadius: 12,
                          padding: "10px 12px",
                          background: "rgba(255,255,255,0.05)",
                          border: "1px solid rgba(255,255,255,0.08)"
                        }}
                      >
                        <div style={{ color: item.color, fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 4 }}>
                          {item.label}
                        </div>
                        <div style={{ color: "white", fontWeight: 800 }}>{item.value}</div>
                      </div>
                    ))}
                  </div>
                </div>

                <div>
                  <div
                    style={{
                      display: "inline-flex",
                      padding: "6px 12px",
                      borderRadius: 999,
                      background: "rgba(74,222,128,0.10)",
                      color: "#86efac",
                      fontSize: 12,
                      fontWeight: 800,
                      textTransform: "uppercase",
                      letterSpacing: "0.06em",
                      marginBottom: 10
                    }}
                  >
                    Live pulse
                  </div>

                  <h2 style={{ margin: "0 0 8px", color: "white", fontSize: 24 }}>Operator summary</h2>
                  <p style={{ margin: 0, color: "rgba(255,255,255,0.74)", lineHeight: 1.7 }}>
                    A compact mission summary of system state, runtime reachability, vision exposure, and cache posture.
                  </p>
                </div>

                <div style={{ display: "grid", gap: 12 }}>
                  {pulseCards.map((item) => (
                    <div
                      key={item.label}
                      style={{
                        position: "relative",
                        overflow: "hidden",
                        display: "grid",
                        gap: 6,
                        borderRadius: 16,
                        padding: "14px 16px",
                        background: "rgba(255,255,255,0.04)",
                        border: "1px solid rgba(255,255,255,0.08)"
                      }}
                    >
                      <div
                        style={{
                          position: "absolute",
                          top: -30,
                          right: -10,
                          width: 90,
                          height: 90,
                          borderRadius: "50%",
                          background: `${item.color}18`,
                          filter: "blur(12px)",
                          animation: "pulseHalo 3.8s ease-in-out infinite"
                        }}
                      />

                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          gap: 12,
                          position: "relative",
                          zIndex: 1
                        }}
                      >
                        <div
                          style={{
                            color: item.color,
                            fontSize: 12,
                            fontWeight: 800,
                            letterSpacing: "0.08em",
                            textTransform: "uppercase"
                          }}
                        >
                          {item.label}
                        </div>
                        <div
                          style={{
                            minWidth: 10,
                            width: 10,
                            height: 10,
                            borderRadius: "50%",
                            background: item.color,
                            boxShadow: `0 0 16px ${item.color}`,
                            animation: "statusBlink 1.8s ease-in-out infinite"
                          }}
                        />
                      </div>
                      <div style={{ color: "white", fontSize: 24, fontWeight: 900, lineHeight: 1.05, position: "relative", zIndex: 1 }}>{item.value}</div>
                      <div style={{ color: "rgba(255,255,255,0.68)", lineHeight: 1.55, position: "relative", zIndex: 1 }}>{item.detail}</div>
                    </div>
                  ))}
                </div>

                <div
                  style={{
                    borderRadius: 16,
                    padding: 16,
                    background: "rgba(125,211,252,0.06)",
                    border: "1px solid rgba(125,211,252,0.18)"
                  }}
                >
                  <div style={{ color: "#7dd3fc", fontSize: 12, fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 8 }}>
                    Active backend
                  </div>
                  <div style={{ color: "white", fontSize: 15, lineHeight: 1.6, wordBreak: "break-word" }}>{backendUrl}</div>
                </div>
              </div>
            </div>
          </div>
        </header>

        {isError ? (
          <article
            style={{
              borderRadius: 22,
              padding: 20,
              background: "linear-gradient(180deg, rgba(69,10,10,0.94), rgba(31,10,18,0.95))",
              border: "1px solid rgba(251,113,133,0.45)",
              color: "#ffe4e6"
            }}
          >
            <h2 style={{ marginTop: 0, marginBottom: 10 }}>Connection issue</h2>
            <p style={{ margin: 0, lineHeight: 1.7 }}>
              One or more live organism data sources could not be loaded. Current error: {errorMessage}
            </p>
          </article>
        ) : null}

        <section
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 1.35fr) minmax(320px, 0.9fr)",
            gap: 20,
            alignItems: "start"
          }}
        >
          <article
            style={{
              borderRadius: 28,
              padding: 22,
              background: "linear-gradient(180deg, rgba(10,14,28,0.96), rgba(8,11,23,0.98))",
              border: "1px solid rgba(255,255,255,0.10)",
              boxShadow: "0 24px 70px rgba(0,0,0,0.30)"
            }}
          >
            <div style={{ marginBottom: 18 }}>
              <div
                style={{
                  display: "inline-flex",
                  padding: "6px 12px",
                  borderRadius: 999,
                  background: "rgba(74,222,128,0.10)",
                  color: "#86efac",
                  fontSize: 12,
                  fontWeight: 800,
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  marginBottom: 10
                }}
              >
                Interactive organism roles
              </div>

              <h2 style={{ margin: "0 0 8px", fontSize: 28, color: "white" }}>Operational anatomy</h2>
              <p style={{ margin: 0, color: "rgba(255,255,255,0.75)", lineHeight: 1.7 }}>
                The organism is more legible when its major roles are broken into learning, healing, autonomy,
                vision, and operator control. Click any card to change the live explanation context.
              </p>
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
                gap: 18
              }}
            >
              <FeatureCard
                id="learning"
                activeId={activeCard}
                onActivate={setActiveCard}
                label="Learning"
                title="Memory + awareness"
                value={isLoading ? "Loading..." : formatCompact(totalTimelineEvents)}
                summary="The organism watches event flow and turns it into traces, memory, and adaptive signal."
                detail={`Observed events: ${eventCount}. Timeline events: ${totalTimelineEvents}. Cache size: ${cacheSize}.`}
                nextStep="Watch whether throughput rises while failures stay controlled."
                gradient="linear-gradient(135deg, #0ea5e9 0%, #2563eb 100%)"
                glow="rgba(37,99,235,0.35)"
                accent="#7dd3fc"
              />

              <FeatureCard
                id="healing"
                activeId={activeCard}
                onActivate={setActiveCard}
                label="Healing"
                title="Resilience + runtime health"
                value={getStatusText(systemReady, "Healthy", "Review")}
                summary="This shows whether the system is stable enough to trust the organism under active load."
                detail={`Ready: ${formatBoolean(statusQuery.data?.ready)}. Ollama reachable: ${formatBoolean(statusQuery.data?.ollama_reachable)}. Environment: ${statusQuery.data?.environment ?? "Unknown"}.`}
                nextStep="Fix health before trusting weak or partial organism behavior."
                gradient="linear-gradient(135deg, #22c55e 0%, #15803d 100%)"
                glow="rgba(34,197,94,0.30)"
                accent="#86efac"
              />

              <FeatureCard
                id="autonomy"
                activeId={activeCard}
                onActivate={setActiveCard}
                label="Autonomy"
                title="Tools + action surface"
                value={isLoading ? "Loading..." : `${toolCount} tools`}
                summary="Autonomy measures whether the organism can act through tool execution rather than only observe."
                detail={`Tool count: ${toolCount}. Capabilities: ${formatList(capabilities)}.`}
                nextStep="Low tool reach means the organism may understand but still fail to help."
                gradient="linear-gradient(135deg, #8b5cf6 0%, #6d28d9 100%)"
                glow="rgba(139,92,246,0.32)"
                accent="#c4b5fd"
              />

              <FeatureCard
                id="vision"
                activeId={activeCard}
                onActivate={setActiveCard}
                label="Vision"
                title="Perception + visual workflows"
                value={visionRuntimeReady ? "Vision live" : "Pending"}
                summary="Vision determines whether screenshots, images, and visual state inspection are genuinely available."
                detail={`Vision configured: ${formatBoolean(visionConfigured)}. Vision exposed: ${formatBoolean(visionExposedToTools)}. Primary model: ${statusQuery.data?.primary_vision_model ?? "Unknown"}.`}
                nextStep="If pending, visual tasks will be weaker even if the core organism is up."
                gradient="linear-gradient(135deg, #ec4899 0%, #ef4444 100%)"
                glow="rgba(236,72,153,0.30)"
                accent="#f9a8d4"
              />

              <FeatureCard
                id="operator"
                activeId={activeCard}
                onActivate={setActiveCard}
                label="Human in the loop"
                title="Oversight + explainability"
                value={systemReady ? "Operator ready" : "Needs oversight"}
                summary="The organism remains useful only if you can inspect it, understand it, and steer it early."
                detail={`Backend URL: ${backendUrl}. Capability visibility: ${formatBoolean(toolCount > 0)}. Events path: ${statusQuery.data?.events_path ?? "Unknown"}.`}
                nextStep="Use this page as an early-warning control layer, not a passive dashboard."
                gradient="linear-gradient(135deg, #f97316 0%, #ef4444 100%)"
                glow="rgba(249,115,22,0.28)"
                accent="#fdba74"
              />
            </div>
          </article>

          <article
            style={{
              display: "grid",
              gap: 16,
              borderRadius: 28,
              padding: 22,
              background: "linear-gradient(180deg, rgba(11,17,32,0.98), rgba(8,12,22,0.98))",
              border: "1px solid rgba(255,255,255,0.10)",
              boxShadow: "0 24px 70px rgba(0,0,0,0.28)"
            }}
          >
            <div>
              <div
                style={{
                  display: "inline-flex",
                  padding: "6px 12px",
                  borderRadius: 999,
                  background: "rgba(251,191,36,0.10)",
                  color: "#fbbf24",
                  fontSize: 12,
                  fontWeight: 800,
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  marginBottom: 10
                }}
              >
                Read model
              </div>

              <h2 style={{ margin: "0 0 8px", color: "white", fontSize: 26 }}>Operator readout</h2>
              <p style={{ margin: 0, color: "rgba(255,255,255,0.74)", lineHeight: 1.7 }}>
                A denser right rail for at-a-glance interpretation of the organism without scrolling through raw backend fields.
              </p>
            </div>

            <div
              style={{
                position: "relative",
                overflow: "hidden",
                borderRadius: 18,
                padding: 16,
                background: "rgba(255,255,255,0.04)",
                border: "1px solid rgba(255,255,255,0.08)"
              }}
            >
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  background: "linear-gradient(120deg, transparent 0%, rgba(255,255,255,0.10) 45%, transparent 80%)",
                  transform: "translateX(-120%)",
                  animation: "scanSweep 3.8s linear infinite"
                }}
              />
              <div style={{ position: "relative", zIndex: 1 }}>
                <div style={{ color: "#7dd3fc", fontSize: 12, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8 }}>
                  Active interpretation
                </div>
                <div style={{ color: "white", fontSize: 20, fontWeight: 800, marginBottom: 8 }}>{activeCard}</div>
                <div style={{ color: "rgba(255,255,255,0.74)", lineHeight: 1.7 }}>{activeMessage}</div>
              </div>
            </div>

            <div style={{ display: "grid", gap: 12 }}>
              {[
                {
                  label: "Environment",
                  value: statusQuery.data?.environment ?? "Unknown",
                  accent: "#c4b5fd"
                },
                {
                  label: "Primary vision model",
                  value: statusQuery.data?.primary_vision_model ?? "Unknown",
                  accent: "#f9a8d4"
                },
                {
                  label: "Cached keys",
                  value: cachedKeys.length > 0 ? cachedKeys.join(", ") : "None",
                  accent: "#86efac"
                },
                {
                  label: "Failure rate",
                  value: `${failureRate}%`,
                  accent: failureRate > 25 ? "#fb7185" : "#fbbf24"
                },
                {
                  label: "Capabilities",
                  value: formatList(capabilities),
                  accent: "#7dd3fc"
                }
              ].map((item) => (
                <div
                  key={item.label}
                  style={{
                    borderRadius: 16,
                    padding: 14,
                    background: "rgba(255,255,255,0.03)",
                    border: "1px solid rgba(255,255,255,0.08)"
                  }}
                >
                  <div style={{ color: item.accent, fontSize: 12, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 6 }}>
                    {item.label}
                  </div>
                  <div style={{ color: "white", lineHeight: 1.6, wordBreak: "break-word" }}>{item.value}</div>
                </div>
              ))}
            </div>
          </article>
        </section>

        <article
          style={{
            borderRadius: 28,
            padding: 24,
            background: "linear-gradient(180deg, rgba(8,14,30,0.97), rgba(8,12,24,0.98))",
            border: "1px solid rgba(255,255,255,0.10)",
            boxShadow: "0 24px 70px rgba(0,0,0,0.30)"
          }}
        >
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              justifyContent: "space-between",
              gap: 16,
              marginBottom: 20
            }}
          >
            <div>
              <div
                style={{
                  display: "inline-flex",
                  padding: "6px 12px",
                  borderRadius: 999,
                  background: "rgba(125,211,252,0.12)",
                  color: "#7dd3fc",
                  fontSize: 12,
                  fontWeight: 800,
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  marginBottom: 10
                }}
              >
                Activity stream
              </div>
              <h2 style={{ margin: "0 0 8px", fontSize: 28, color: "white" }}>Timeline kinetics</h2>
              <p style={{ margin: 0, color: "rgba(255,255,255,0.75)", lineHeight: 1.7, maxWidth: 860 }}>
                Blue is total activity, green is successful work, amber is partial progress, and pink is failure pressure.
              </p>
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(2, minmax(140px, 1fr))",
                gap: 12,
                minWidth: 300
              }}
            >
              {[
                { label: "Latest bucket", value: latestBucket, color: "#7dd3fc" },
                { label: "Success", value: String(totalTimelineSuccess), color: "#4ade80" },
                { label: "Partial", value: String(totalTimelinePartial), color: "#fbbf24" },
                { label: "Fail", value: String(totalTimelineFail), color: "#fb7185" }
              ].map((item) => (
                <div
                  key={item.label}
                  style={{
                    borderRadius: 16,
                    padding: 14,
                    background: "rgba(255,255,255,0.04)",
                    border: "1px solid rgba(255,255,255,0.08)"
                  }}
                >
                  <div style={{ color: item.color, fontSize: 12, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 6 }}>
                    {item.label}
                  </div>
                  <div style={{ color: "white", fontSize: 14, lineHeight: 1.5, wordBreak: "break-word" }}>{item.value}</div>
                </div>
              ))}
            </div>
          </div>

          {timelineQuery.isLoading ? (
            <div
              style={{
                borderRadius: 20,
                padding: 24,
                background: "rgba(255,255,255,0.03)",
                color: "rgba(255,255,255,0.84)"
              }}
            >
              Loading timeline chart...
            </div>
          ) : timelineQuery.isError ? (
            <div
              style={{
                borderRadius: 20,
                padding: 24,
                background: "rgba(251,113,133,0.08)",
                color: "#ffe4e6",
                border: "1px solid rgba(251,113,133,0.25)"
              }}
            >
              {getErrorMessage(timelineQuery.error)}
            </div>
          ) : timelinePoints.length === 0 ? (
            <div
              style={{
                borderRadius: 20,
                padding: 24,
                background: "rgba(255,255,255,0.03)",
                color: "rgba(255,255,255,0.82)"
              }}
            >
              No timeline data is available yet. Once the organism starts recording activity, this chart will come alive.
            </div>
          ) : (
            <>
              <div
                style={{
                  width: "100%",
                  overflowX: "auto",
                  borderRadius: 22,
                  padding: 18,
                  background: "linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.02))",
                  border: "1px solid rgba(255,255,255,0.08)"
                }}
              >
                <svg
                  viewBox={`0 0 ${width} ${height}`}
                  style={{ width: "100%", minWidth: 860, height: 360 }}
                  role="img"
                  aria-label="Organism activity chart"
                >
                  <defs>
                    <linearGradient id="eventsArea" x1="0" x2="0" y1="0" y2="1">
                      <stop offset="0%" stopColor="rgba(56,189,248,0.34)" />
                      <stop offset="100%" stopColor="rgba(56,189,248,0.03)" />
                    </linearGradient>
                    <linearGradient id="chartGlow" x1="0" x2="1" y1="0" y2="0">
                      <stop offset="0%" stopColor="rgba(255,255,255,0.04)" />
                      <stop offset="50%" stopColor="rgba(255,255,255,0.10)" />
                      <stop offset="100%" stopColor="rgba(255,255,255,0.04)" />
                    </linearGradient>
                  </defs>

                  <rect
                    x={padding}
                    y={padding}
                    width={width - padding * 2}
                    height={height - padding * 2}
                    rx="18"
                    fill="rgba(255,255,255,0.02)"
                    stroke="url(#chartGlow)"
                  />

                  {[0, 1, 2, 3, 4].map((line) => {
                    const y = padding + ((height - padding * 2) / 4) * line
                    return (
                      <line
                        key={line}
                        x1={padding}
                        y1={y}
                        x2={width - padding}
                        y2={y}
                        stroke="rgba(255,255,255,0.08)"
                        strokeWidth="1"
                      />
                    )
                  })}

                  <polygon points={allEventsArea} fill="url(#eventsArea)" />
                  <polyline points={allEventsLine} fill="none" stroke="#38bdf8" strokeWidth="4" strokeLinecap="round" />
                  <polyline points={successLine} fill="none" stroke="#4ade80" strokeWidth="3" strokeLinecap="round" />
                  <polyline points={partialLine} fill="none" stroke="#fbbf24" strokeWidth="3" strokeLinecap="round" />
                  <polyline points={failLine} fill="none" stroke="#fb7185" strokeWidth="3" strokeLinecap="round" />
                </svg>
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                  gap: 12,
                  marginTop: 16
                }}
              >
                {[
                  { label: "All events", color: "#38bdf8", text: "Everything the organism processed." },
                  { label: "Success", color: "#4ade80", text: "Healthy or completed outcomes." },
                  { label: "Partial", color: "#fbbf24", text: "Some progress, but not complete." },
                  { label: "Fail", color: "#fb7185", text: "Blocked, failed, or errored work." }
                ].map((item) => (
                  <div
                    key={item.label}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      padding: "12px 14px",
                      borderRadius: 14,
                      background: "rgba(255,255,255,0.04)",
                      border: "1px solid rgba(255,255,255,0.08)"
                    }}
                  >
                    <span
                      style={{
                        width: 12,
                        height: 12,
                        borderRadius: "50%",
                        background: item.color,
                        boxShadow: `0 0 14px ${item.color}`,
                        animation: "statusBlink 1.8s ease-in-out infinite"
                      }}
                    />
                    <div>
                      <div style={{ color: "white", fontSize: 14, fontWeight: 700 }}>{item.label}</div>
                      <div style={{ color: "rgba(255,255,255,0.70)", fontSize: 13 }}>{item.text}</div>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </article>

        <section
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
            gap: 18
          }}
        >
          <KnowledgePanel
            badge="Mental model"
            title="What an organism is"
            accent="#7dd3fc"
            intro="In this system, an organism converts raw activity into a more adaptive and explainable behavior loop."
            bullets={[
              "It watches events instead of letting them disappear into logs.",
              "It remembers patterns instead of starting fresh every time.",
              "It can act through tools when action is available.",
              "It stays visible to a human operator rather than becoming opaque."
            ]}
          />

          <KnowledgePanel
            badge="Usefulness"
            title="What it is doing for you"
            accent="#86efac"
            intro="The organism improves observability, actionability, and adaptability at the same time."
            bullets={[
              "Learning from event flow and timeline behavior.",
              "Checking whether the runtime is healthy enough to keep serving.",
              "Using tools and capabilities when those paths are exposed.",
              "Preparing image-aware intelligence when vision support is live."
            ]}
          />

          <KnowledgePanel
            badge="Reading guide"
            title="How to read this page"
            accent="#fbbf24"
            intro="This page is designed to make organism posture obvious without decoding raw backend jargon."
            bullets={[
              "Start with the animated metric band to read immediate posture.",
              "Use the anatomy cards to inspect each organism role.",
              "Use the timeline chart to spot growth, stability, and failure pressure.",
              "Treat amber and pink as attention surfaces, not decoration."
            ]}
          />

          <KnowledgePanel
            badge="Why it matters"
            title="Why this matters"
            accent="#f9a8d4"
            intro="A strong organism surface is part of the control system. It helps you steer, debug, and evolve faster."
            bullets={[
              "It pushes the platform toward adaptive behavior instead of static status checking.",
              "It gives you earlier warning for regressions and runtime weakness.",
              "It makes tool reach and vision posture obvious instead of hidden.",
              "It creates a stronger foundation for self-learning and guided autonomy."
            ]}
          />
        </section>
      </div>
    </section>
  )
}