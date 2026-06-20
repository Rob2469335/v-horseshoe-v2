import { useEffect, useMemo, useState } from "react"
import { OrganismHero } from "../components/organism/OrganismHero"
import { OrganismAnatomySection } from "../components/organism/OrganismAnatomySection"
import { OrganismTimelineSection } from "../components/organism/OrganismTimelineSection"
import { OrganismTutorSection } from "../components/organism/OrganismTutorSection"
import { useOrganismData } from "../features/organism/organism-hooks"
import { LivingNervousSystem } from "../components/organism/LivingNervousSystem"
import { OrganismNarrator } from "../components/organism/OrganismNarrator"
import { SubsystemCard } from "../components/organism/SubsystemCard"
import { HealingTrigger } from "../components/organism/HealingTrigger"
import { GenerationHistory } from "../components/organism/GenerationHistory"
import { ModelPicker } from "../components/organism/ModelPicker"
import { AgentStepRunner } from "../components/organism/AgentStepRunner"
import { MemorySearchPanel } from "../components/MemorySearchPanel"
import { ReplayDashboard } from "../components/ReplayDashboard"
import { OmniDevInterface } from "../components/organism/OmniDevInterface"
import { getSubsystemTheme, organismTheme } from "../features/organism/organism-theme"
import type { OrganismSubsystem } from "../features/organism/organism-types"

interface CIResult {
  type: string
  payload?: {
    score?: number
    branch?: string
  }
  status?: string
  score?: number
  branch?: string | null
}

function formatCompact(value: number) {
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value)
}

export default function OrganismPage() {
  const {
    backendUrl,
    statusQuery,
    isLoading,
    isError,
    errorMessage,
    capabilities,
    cacheSize,
    cachedKeys,
    eventCount,
    toolCount,
    systemReady,
    totalTimelineEvents,
    totalTimelineSuccess,
    totalTimelinePartial,
    totalTimelineFail,
    successRate,
    failureRate,
    visionConfigured,
    visionRuntimeReady,
    timelinePoints,
    latestBucket,
    tickerItems,
    pulseCards,
    chart,
    insights
  } = useOrganismData()

  const { width, height, padding, allEventsLine, successLine, partialLine, failLine, allEventsArea } = chart

  // V10 Swarm Live Stream
  useEffect(() => {
    const es = new EventSource(`${backendUrl}/swarm/v10/stream`);

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        console.log("V10 PATCH EVENT:", data);

        window.dispatchEvent(new CustomEvent("swarm_v10_event", {
          detail: data
        }));
      } catch (e) {}
    };

    return () => es.close();
  }, [backendUrl]);
  const [activeCard, setActiveCard] = useState<OrganismSubsystem>("learning")
  const [showDeepTutorial, setShowDeepTutorial] = useState(false)
  const [swarmV10Feed, setSwarmV10Feed] = useState<CIResult[]>([])
  const [swarmCockpit, setSwarmCockpit] = useState({
    ciPass: 0,
    ciFail: 0,
    avgScore: 0,
    lastBranch: null as string | null
  })
  const [interactionMode, setInteractionMode] = useState<"observe" | "teach" | "drill">("teach")
  const [selectedBucket, setSelectedBucket] = useState<string | null>(null)

  useEffect(() => {
    const handler = (e: any) => {
      const data = e.detail as CIResult
      setSwarmCockpit((prev) => {
        const score = data.payload?.score ?? data.score ?? 0
        const isPass = data.type === "CI_RESULT" && score >= 0.85
        const isFail = data.type === "CI_RESULT" && score < 0.85

        return {
          ciPass: prev.ciPass + (isPass ? 1 : 0),
          ciFail: prev.ciFail + (isFail ? 1 : 0),
          avgScore: (prev.avgScore + score) / 2,
          lastBranch: data.payload?.branch ?? data.branch ?? prev.lastBranch
        }
      })
      setSwarmV10Feed(prev => [data, ...prev].slice(0, 50));
    };

    window.addEventListener("swarm_v10_event", handler);
    return () => window.removeEventListener("swarm_v10_event", handler);
  }, []);
  const [selectedModel, setSelectedModel] = useState<string | null>(null)

  useEffect(() => {
    setShowDeepTutorial(interactionMode === "drill")
  }, [activeCard, interactionMode])

  const activeBucketData = useMemo(() => {
    if (!selectedBucket) return null
    return timelinePoints.find((p) => p.bucket === selectedBucket) ?? null
  }, [selectedBucket, timelinePoints])

  const bucketComparison = useMemo(() => insights.getComparison(selectedBucket), [selectedBucket, insights])

  const activeTheme = getSubsystemTheme(activeCard)

  const activeMessage = useMemo(() => {
    if (activeBucketData) {
      return `Focused bucket ${activeBucketData.bucket} handled ${activeBucketData.event_count} events with ${activeBucketData.success_count} successes and ${activeBucketData.fail_count} failures.`
    }

    switch (activeCard) {
      case "learning":
        return `Learning converts live events into memory traces. Throughput is currently ${insights.volumeTrend}.`
      case "healing":
        return `Healing watches resilience and recovery posture. The organism is ${systemReady ? "stable" : "recovering"}.`
      case "autonomy":
        return `Autonomy measures action reach across tools and workflows. ${toolCount} tools are exposed to the organism.`
      case "vision":
        return `Vision maps image-aware reasoning into runtime action. The multimodal path is ${visionRuntimeReady ? "live" : "pending"}.`
      default:
        return `Operator control stays on top of every subsystem. ${insights.action}`
    }
  }, [activeBucketData, activeCard, insights, systemReady, toolCount, visionRuntimeReady])

  const tutorialContent = useMemo(() => {
    const base = (() => {
      switch (activeCard) {
        case "learning":
          return {
            steps: [
              "Watch raw events turn into reusable traces and memory structures.",
              "Compare throughput with success rate to see whether more activity is actually helping.",
              "Healthy learning means volume rises without collapse in outcome quality."
            ],
            operatorAction: insights.summary,
            deepDive: [
              "Trace reuse improves speed because the organism can recall prior successful work.",
              "Cache growth usually means the organism is building reusable operational memory.",
              "If failures climb during higher throughput, training quality is lagging behind activity."
            ]
          }
        case "healing":
          return {
            steps: [
              "Start by checking if readiness is healthy before trusting advanced metrics.",
              "Review failure pressure and bucket drift to see whether the organism is compensating.",
              "A healthy self-heal loop contains local damage before it spreads system-wide."
            ],
            operatorAction: `Healing posture is ${systemReady ? "healthy" : "degraded"}. ${systemReady ? "The organism is stable enough for normal use." : "Proceed carefully while recovery is active."}`,
            deepDive: [
              "Self-heal starts as visibility, not magic. You need signals before you can automate repair.",
              "Failure pressure matters more than a single red flag because it shows sustained stress.",
              "Recovery loops are strongest when they are observable, reversible, and narrow in scope."
            ]
          }
        case "autonomy":
          return {
            steps: [
              "Inspect tool reach first. If the organism cannot act, intelligence stays theoretical.",
              "Compare capabilities with failure pressure to judge safe autonomy.",
              "More tools increase reach, but they also increase the surface area for mistakes."
            ],
            operatorAction: insights.action,
            deepDive: [
              "Autonomy should follow a plan-execute-verify loop, not blind action.",
              "Capability exposure must stay human-steerable even as the system becomes more agentic.",
              "Reusable traces and cache hits make autonomous workflows faster and more predictable."
            ]
          }
        case "vision":
          return {
            steps: [
              "Vision becomes meaningful only when the runtime path is actually reachable.",
              "If vision is pending, multimodal flows will degrade into text-only behavior.",
              "Treat vision readiness as a dependency for screen, image, and spatial workflows."
            ],
            operatorAction: visionRuntimeReady
              ? "Vision is live. Image-aware behavior can participate in the organism."
              : "Vision is pending. Multimodal behavior is currently limited.",
            deepDive: [
              "Vision models turn pixel data into structured semantic tokens for higher-level reasoning.",
              "Grounding matters because perception without action is only observation.",
              `Installed vision models reported: ${statusQuery.data?.vision_models_installed?.join(", ") || "none reported"}`
            ]
          }
        default:
          return {
            steps: [
              "Use Observe for scanning, Teach for explanations, and Drill for deep inspection.",
              "The operator layer keeps human-in-the-loop control over every autonomous subsystem.",
              "A strong console teaches what the organism is doing instead of hiding its logic."
            ],
            operatorAction: `Operator oversight is active for the ${activeCard} subsystem.`,
            deepDive: [
              "Human steerability should remain visible even in highly autonomous systems.",
              "The best dashboards do not just show numbers; they explain consequences.",
              "Live organism control works best when telemetry and interpretation stay connected."
            ]
          }
      }
    })()

    if (activeBucketData) {
      const bucketSuccessRate = Math.round((activeBucketData.success_count / Math.max(1, activeBucketData.event_count)) * 100)
      return {
        ...base,
        operatorAction: `Focused on bucket ${activeBucketData.bucket}. Success rate in this window is ${bucketSuccessRate}%. ${bucketSuccessRate < 70 ? "This bucket shows notable failure pressure." : "This bucket is operating within healthy bounds."}`
      }
    }

    return base
  }, [activeCard, activeBucketData, insights, systemReady, visionRuntimeReady, statusQuery.data])

  const modeSummary = useMemo(() => {
    switch (interactionMode) {
      case "observe":
        return {
          title: "Observation posture",
          detail: "Fast scanning mode for operators who want the organism's live pulse without the full teaching overlay."
        }
      case "teach":
        return {
          title: "Tutor control mode",
          detail: "Plain-English explanations are active so every metric reads like guided instruction instead of raw telemetry."
        }
      default:
        return {
          title: "Deep inspection mode",
          detail: "Technical transparency is expanded so you can inspect the organism's mechanics, drift, and pressure in detail."
        }
    }
  }, [interactionMode])

  const tryThisNext = useMemo(() => {
    if (selectedBucket) return "Click the timeline background to release bucket focus and return to live stream monitoring."
    switch (activeCard) {
      case "learning":
        return "Drive more real traffic through the system and watch whether trace quality rises with volume."
      case "healing":
        return "Test a controlled failure and confirm that the organism recovers without broad instability."
      case "autonomy":
        return "Expand safe tool reach, then verify whether success holds under more autonomous action."
      case "vision":
        return "Bring the vision runtime online and compare before-and-after capability posture."
      default:
        return "Switch to Drill mode for deeper architectural explanation."
    }
  }, [activeCard, selectedBucket])

  const tutorMeta = useMemo(() => {
    const subsystemLabel = activeCard.charAt(0).toUpperCase() + activeCard.slice(1)
    return {
      eyebrow: `Subsystem focus: ${subsystemLabel}`,
      title: `${subsystemLabel} intelligence`,
      intro: `Direct teaching and control surface for the organism's ${activeCard} subsystem.`
    }
  }, [activeCard])

  const overviewCards = [
    {
      label: "Organism state",
      value: systemReady ? "ready" : "review",
      detail: systemReady
        ? "The main runtime is reporting healthy readiness."
        : "Core readiness is under pressure and needs review.",
      accent: systemReady ? "#22c55e" : "#f59e0b"
    },
    {
      label: "Timeline volume",
      value: formatCompact(totalTimelineEvents),
      detail: `${formatCompact(totalTimelineSuccess)} successful events across the tracked timeline.`,
      accent: "#38bdf8"
    },
    {
      label: "Autonomy reach",
      value: String(toolCount),
      detail: capabilities.length > 0
        ? `${Math.min(capabilities.length, 4)} visible capabilities include ${capabilities.slice(0, 4).join(", ")}`
        : "No capabilities are currently exposed.",
      accent: "#a78bfa"
    },
    {
      label: "Memory reuse",
      value: formatCompact(cacheSize),
      detail: cachedKeys.length > 0
        ? `${cachedKeys.length} cached keys are available for faster reuse.`
        : "No cached keys reported yet.",
      accent: "#f472b6"
    }
  ]

  return (
    <>
      <div style={{
        position: "sticky",
        top: 0,
        zIndex: 50,
        marginBottom: 16,
        padding: 12,
        borderRadius: 16,
        background: "rgba(0,0,0,0.55)",
        border: "1px solid rgba(255,255,255,0.12)",
        backdropFilter: "blur(12px)"
      }}>
        <div style={{ color: "#7dd3fc", fontWeight: 900, marginBottom: 6 }}>
          🧠 Swarm Cockpit Live Control
        </div>

        <div style={{ display: "flex", gap: 12, fontSize: 12 }}>
          <span style={{ color: "#22c55e" }}>✔ Pass: {swarmCockpit.ciPass}</span>
          <span style={{ color: "#f97316" }}>✖ Fail: {swarmCockpit.ciFail}</span>
          <span style={{ color: "#a78bfa" }}>Score: {swarmCockpit.avgScore?.toFixed?.(2)}</span>
          <span style={{ color: "#7dd3fc" }}>Branch: {swarmCockpit.lastBranch}</span>
        </div>
      </div>

      <section
        className="page"
        style={{
          minHeight: "100vh",
          color: organismTheme.surface.text,
          background: `
            radial-gradient(circle at 0% 0%, ${activeTheme.accent}18, transparent 35%),
            radial-gradient(circle at 100% 0%, rgba(168,85,247,0.16), transparent 30%),
            radial-gradient(circle at 50% 50%, ${activeTheme.glow}08, transparent 40%),
            linear-gradient(180deg, #040816 0%, #07101f 48%, #050815 100%)
          `,
          padding: "24px 16px 48px",
          transition: "background 500ms ease"
        }}
      >
      <style>{`
        @keyframes pulseHalo {
          0% { transform: scale(0.98); opacity: 0.18; }
          50% { transform: scale(1.02); opacity: 0.4; }
          100% { transform: scale(0.98); opacity: 0.18; }
        }
        @keyframes statusBlink {
          0% { opacity: 0.6; }
          50% { opacity: 1; }
          100% { opacity: 0.6; }
        }
        @keyframes scanSweep {
          0% { transform: translateX(-120%); }
          100% { transform: translateX(140%); }
        }
        @keyframes tickerMove {
          0% { transform: translateX(0); }
          100% { transform: translateX(-50%); }
        }
        @keyframes corePulse {
          0% { transform: scale(0.96); opacity: 0.78; }
          50% { transform: scale(1.08); opacity: 1; }
          100% { transform: scale(0.96); opacity: 0.78; }
        }
        @keyframes tutorReveal {
          0% { opacity: 0; transform: translateY(15px); }
          100% { opacity: 1; transform: translateY(0); }
        }
        @keyframes cardFocus {
          0% { box-shadow: 0 0 0 0px ${activeTheme.accent}33; }
          100% { box-shadow: 0 0 0 10px ${activeTheme.accent}00; }
        }
        @media (prefers-reduced-motion: reduce) {
          *, *::before, *::after {
            animation: none !important;
            transition: none !important;
          }
        }
      `}</style>

      <div style={{ maxWidth: 1440, margin: "0 auto", display: "grid", gap: 20 }}>
        <article
          style={{
            position: "relative",
            overflow: "hidden",
            borderRadius: 30,
            padding: 24,
            background: "linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.025))",
            border: "1px solid rgba(255,255,255,0.10)",
            boxShadow: "0 18px 56px rgba(0,0,0,0.24)",
            display: "grid",
            gap: 20
          }}
        >
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.05), transparent)",
              transform: "translateX(-120%)",
              animation: "scanSweep 6.4s linear infinite"
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
              <div
                style={{
                  color: activeTheme.accent,
                  fontSize: 12,
                  fontWeight: 900,
                  textTransform: "uppercase",
                  letterSpacing: "0.16em",
                  marginBottom: 10
                }}
              >
                Organism tutor / living systems console
              </div>
              <h1
                style={{
                  margin: 0,
                  color: "white",
                  fontSize: "clamp(2rem, 4vw, 3.6rem)",
                  lineHeight: 1.02,
                  fontWeight: 950,
                  letterSpacing: "-0.04em"
                }}
              >
                Adaptive organism control
              </h1>
              <p
                style={{
                  marginTop: 14,
                  color: "rgba(225,235,255,0.76)",
                  lineHeight: 1.7,
                  maxWidth: 780
                }}
              >
                This page teaches what the organism is doing, how healthy it is, and where learning, self-heal, autonomy, vision, and operator control are changing in real time.
              </p>

              <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 18 }}>
                {[
                  { label: "Backend", value: backendUrl, accent: activeTheme.accent },
                  { label: "Success", value: `${successRate}%`, accent: "#22c55e" },
                  { label: "Failure", value: `${failureRate}%`, accent: "#f97316" },
                  { label: "Latest bucket", value: latestBucket, accent: "#7dd3fc" }
                ].map((chip) => (
                  <div
                    key={chip.label}
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
                        background: chip.accent,
                        boxShadow: `0 0 18px ${chip.accent}`,
                        animation: "statusBlink 2.4s ease-in-out infinite"
                      }}
                    />
                    <span style={{ color: "rgba(255,255,255,0.6)", fontSize: 12, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.12em" }}>
                      {chip.label}
                    </span>
                    <span style={{ color: "white", fontWeight: 800 }}>
                      {chip.value}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <aside
              style={{
                borderRadius: 24,
                padding: 20,
                background: "rgba(4,10,24,0.56)",
                border: `1px solid ${activeTheme.accent}33`,
                boxShadow: `0 18px 42px ${activeTheme.glow}`,
                backdropFilter: "blur(12px)"
              }}
            >
              <div style={{ color: activeTheme.accent, fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.16em", marginBottom: 10 }}>
                Tutor interpretation
              </div>
              <div style={{ color: "white", fontSize: 25, fontWeight: 900, marginBottom: 10 }}>
                {modeSummary.title}
              </div>
              <div style={{ color: "rgba(225,235,255,0.74)", lineHeight: 1.7, fontSize: 14 }}>
                {modeSummary.detail}
              </div>
              <div
                style={{
                  marginTop: 16,
                  padding: 14,
                  borderRadius: 16,
                  background: `${activeTheme.accent}10`,
                  border: `1px solid ${activeTheme.accent}24`,
                  color: "rgba(240,246,255,0.86)",
                  lineHeight: 1.6,
                  fontSize: 13
                }}
              >
                {activeMessage}
              </div>
            </aside>
          </div>

          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 16
            }}
          >
            <nav
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 12,
                background: "rgba(0,0,0,0.18)",
                padding: 6,
                borderRadius: 999,
                border: "1px solid rgba(255,255,255,0.05)"
              }}
            >
              {[
                { key: "observe", label: "Observe", icon: "👁️" },
                { key: "teach", label: "Teach", icon: "🎓" },
                { key: "drill", label: "Drill", icon: "🔬" }
              ].map((mode) => {
                const isActive = interactionMode === mode.key
                return (
                  <button
                    key={mode.key}
                    type="button"
                    onClick={() => setInteractionMode(mode.key as "observe" | "teach" | "drill")}
                    aria-pressed={isActive}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      padding: "10px 18px",
                      borderRadius: 999,
                      border: "none",
                      background: isActive ? activeTheme.accent : "transparent",
                      color: isActive ? "white" : "rgba(255,255,255,0.62)",
                      fontWeight: 800,
                      fontSize: 13,
                      cursor: "pointer",
                      boxShadow: isActive ? `0 8px 18px ${activeTheme.glow}` : "none",
                      transform: isActive ? "translateY(-1px)" : "translateY(0)",
                      transition: "all 220ms cubic-bezier(0.16,1,0.3,1)"
                    }}
                  >
                    <span>{mode.icon}</span>
                    {mode.label}
                  </button>
                )
              })}
            </nav>

            <div
              style={{
                padding: "10px 14px",
                borderRadius: 16,
                background: "rgba(255,255,255,0.04)",
                border: "1px solid rgba(255,255,255,0.06)",
                color: "rgba(225,235,255,0.72)",
                fontSize: 13,
                lineHeight: 1.6
              }}
            >
              {selectedBucket
                ? `Focused on bucket ${selectedBucket}. Click the timeline background to return to live flow.`
                : "No bucket pinned. The organism is in live monitoring mode."}
            </div>
          </div>
        </article>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
            gap: 16
          }}
        >
          {overviewCards.map((card) => (
            <article
              key={card.label}
              style={{
                borderRadius: 24,
                padding: 20,
                background: "rgba(255,255,255,0.045)",
                border: "1px solid rgba(255,255,255,0.08)",
                boxShadow: "0 14px 28px rgba(0,0,0,0.18)"
              }}
            >
              <div style={{ color: card.accent, fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.14em", marginBottom: 8 }}>
                {card.label}
              </div>
              <div style={{ color: "white", fontSize: 26, fontWeight: 900, marginBottom: 8 }}>
                {card.value}
              </div>
              <div style={{ color: "rgba(225,235,255,0.7)", fontSize: 14, lineHeight: 1.65 }}>
                {card.detail}
              </div>
            </article>
          ))}
        </div>

        <article
          style={{
            borderRadius: 26,
            padding: 20,
            background: "linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.02))",
            border: "1px solid rgba(255,255,255,0.08)"
          }}
        >
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 14 }}>
            <div style={{ borderRadius: 18, padding: 18, background: "rgba(255,255,255,0.028)", border: "1px solid rgba(255,255,255,0.05)" }}>
              <div style={{ color: activeTheme.accent, fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.15em", marginBottom: 10 }}>
                Why this focus matters
              </div>
              <div style={{ color: "white", lineHeight: 1.7, fontSize: 14 }}>
                {activeCard === "learning" ? "Learning proves the organism is internalizing events into reusable knowledge rather than just reacting in the moment." :
                 activeCard === "healing" ? "Healing shows whether the organism can stay useful under stress instead of collapsing when conditions worsen." :
                 activeCard === "autonomy" ? "Autonomy tells you whether the organism can act, not just observe, while staying under human control." :
                 activeCard === "vision" ? "Vision determines whether image-aware workflows can participate in the organism's reasoning loop." :
                 "Operator control makes sure a human remains the final steering layer."}
              </div>
            </div>

            <div style={{ borderRadius: 18, padding: 18, background: "rgba(255,255,255,0.028)", border: "1px solid rgba(255,255,255,0.05)" }}>
              <div style={{ color: activeTheme.accent, fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.15em", marginBottom: 10 }}>
                What changed
              </div>
              <div style={{ color: "white", lineHeight: 1.7, fontSize: 14 }}>
                {bucketComparison ? (
                  <>
                    <strong style={{ color: activeTheme.accent }}>{bucketComparison.label}:</strong> {bucketComparison.description}
                  </>
                ) : (
                  <>
                    The organism is currently in <strong style={{ color: activeTheme.accent }}>{insights.volumeTrend}</strong> volume posture with <strong style={{ color: activeTheme.accent }}>{insights.successTrend}</strong> outcome quality.
                  </>
                )}
              </div>
            </div>

            <div style={{ borderRadius: 20, padding: 20, background: `${activeTheme.accent}0D`, border: `1px solid ${activeTheme.accent}26` }}>
              <div style={{ color: activeTheme.accent, fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.15em", marginBottom: 10 }}>
                Try this next
              </div>
              <div style={{ color: "white", lineHeight: 1.7, fontSize: 13, fontWeight: 700 }}>
                {tryThisNext}
              </div>
              <div style={{
  marginTop: 16,
  height: 6,
  borderRadius: 999,
  background: "rgba(255,255,255,0.08)",
  overflow: "hidden"
}}>
  <div style={{
    width: `${Math.min(100, swarmCockpit.avgScore * 100)}%`,
    height: "100%",
    background: "linear-gradient(90deg,#22c55e,#7dd3fc,#a78bfa)",
    transition: "width 300ms ease"
  }} />
</div>
</div>
</div>
</article>

        
      {/* Living Nervous System */}
      <div style={{ marginBottom: 24 }}>
        <LivingNervousSystem
          backendUrl={backendUrl}
          liveData={{
            ollamaReachable: statusQuery.data?.ollama_reachable ?? false,
            installedModels: statusQuery.data?.installed_model_count ?? 0,
            eventCount: statusQuery.data?.event_count ?? 0,
            traceCount: timelinePoints.length,
            healingReady: 100,
            successRate: successRate,
            cacheSize: cacheSize,
            visionAvailable: visionRuntimeReady,
          }}
        />
      </div>

      {/* Narrator */}
      <OrganismNarrator
        backendUrl={backendUrl}
        ollamaReachable={statusQuery.data?.ollama_reachable ?? false}
        successRate={successRate}
        eventCount={statusQuery.data?.event_count ?? 0}
        healingReady={100}
        traceCount={timelinePoints.length}
      />

      {/* Subsystem cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))", gap: 12, marginBottom: 24 }}>
        <SubsystemCard id="ollama" label="Ollama" color="#22c55e" health={statusQuery.data?.ollama_reachable ? 100 : 0} activity={statusQuery.data?.ollama_reachable ? 85 : 0} sublabel={`${statusQuery.data?.installed_model_count ?? 0} models`} backendUrl={backendUrl} prompt="You are Ollama. Report your current status in 3 bullet points. Be direct and technical." />
        <SubsystemCard id="router" label="Router" color="#7dd3fc" health={statusQuery.data?.ollama_reachable ? 100 : 40} activity={timelinePoints.length > 0 ? 90 : 30} sublabel="model selector" backendUrl={backendUrl} prompt={`You are the model router. You have routed ${timelinePoints.length} traces with ${successRate}% success rate. Explain what you are doing right now in 2 sentences.`} />
        <SubsystemCard id="critic" label="Critic" color="#f472b6" health={successRate || 50} activity={timelinePoints.length > 0 ? successRate : 20} sublabel={`${successRate}% accept rate`} backendUrl={backendUrl} prompt={`You are the AI critic evaluator. Your current acceptance rate is ${successRate}%. Explain your role and give a one-line quality verdict.`} />
        <SubsystemCard id="memory" label="Memory" color="#a78bfa" health={statusQuery.data?.event_count ?? 0 > 0 ? 100 : 50} activity={80} sublabel={`${(statusQuery.data?.event_count ?? 0).toLocaleString()} events`} backendUrl={backendUrl} prompt={`You are the memory subsystem. You have stored ${statusQuery.data?.event_count ?? 0} events. Explain what you store and why it matters in 2 sentences.`} />
        <SubsystemCard id="qdrant" label="Qdrant" color="#fb923c" health={100} activity={cacheSize > 0 ? 70 : 30} sublabel={`${cacheSize} cached`} backendUrl={backendUrl} prompt={`You are the Qdrant vector database. You have ${cacheSize} cached vectors. Explain semantic search in 2 sentences.`} />
        <SubsystemCard id="healer" label="Healer" color="#34d399" health={100} activity={60} sublabel="100% ready" backendUrl={backendUrl} prompt="You are the self-healing subsystem. All 4 checks (orchestrator, qdrant, ollama, api) are passing. Report your current status and what you are watching for." />
      </div>

      {/* Model picker + agent runner */}
      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: 12, alignItems: "start", marginBottom: 24 }}>
        <ModelPicker
          models={(statusQuery.data?.installed_models as string[] | undefined) ?? []}
          selected={selectedModel}
          onSelect={setSelectedModel}
        />
        <AgentStepRunner backendUrl={backendUrl} selectedModel={selectedModel} />
      </div>
      <OrganismHero
          activeTheme={activeTheme}
          activeMessage={activeMessage}
          isLoading={isLoading}
          eventCount={activeBucketData ? activeBucketData.event_count : eventCount}
          totalTimelineEvents={totalTimelineEvents}
          successRate={activeBucketData ? Math.round((activeBucketData.success_count / Math.max(1, activeBucketData.event_count)) * 100) : successRate}
          toolCount={toolCount}
          tickerItems={tickerItems}
          systemReady={systemReady}
          visionRuntimeReady={visionRuntimeReady}
          pulseCards={pulseCards}
          backendUrl={backendUrl}
          formatCompact={formatCompact}
          timelinePoints={timelinePoints}
          timelineLoading={isLoading}
        />

        <OrganismAnatomySection
          activeCard={activeCard}
          setActiveCard={setActiveCard}
          isLoading={isLoading}
          systemReady={systemReady}
          visionRuntimeReady={visionRuntimeReady}
          visionConfigured={visionConfigured}
          toolCount={toolCount}
          totalTimelineEvents={totalTimelineEvents}
          eventCount={eventCount}
          cacheSize={cacheSize}
          capabilities={capabilities}
          backendUrl={backendUrl}
          eventsPath={statusQuery.data?.events_path}
          ollamaReachable={statusQuery.data?.ollama_reachable}
          environment={statusQuery.data?.environment}
          primaryVisionModel={statusQuery.data?.primary_vision_model}
          statusReady={statusQuery.data?.ready}
          activeTheme={activeTheme}
          activeMessage={activeMessage}
          failureRate={failureRate}
          cachedKeys={cachedKeys}
          selectedBucket={selectedBucket}
        />

        <OrganismTimelineSection
          isLoading={isLoading}
          isError={isError}
          errorMessage={errorMessage}
          timelinePoints={timelinePoints}
          totalTimelineSuccess={totalTimelineSuccess}
          totalTimelinePartial={totalTimelinePartial}
          totalTimelineFail={totalTimelineFail}
          latestBucket={latestBucket}
          activeAccent={activeTheme.accent}
          width={width}
          height={height}
          padding={padding}
          allEventsArea={allEventsArea}
          allEventsLine={allEventsLine}
          successLine={successLine}
          partialLine={partialLine}
          failLine={failLine}
          selectedBucket={selectedBucket}
          onSelectBucket={setSelectedBucket}
        />

        {interactionMode !== "observe" && (
          <OrganismTutorSection
            activeCard={activeCard}
            activeTheme={activeTheme}
            tutorMeta={tutorMeta}
            tutorialContent={tutorialContent}
            showDeepTutorial={showDeepTutorial || interactionMode === "drill"}
            setShowDeepTutorial={setShowDeepTutorial}
            interactionMode={interactionMode}
          />
        )}
      </div>
    
      {/* Medium features */}
      <div style={{ display: "grid", gap: 16, marginTop: 24, paddingBottom: 48 }}>
        <HealingTrigger backendUrl={backendUrl} />
        <GenerationHistory backendUrl={backendUrl} />
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 16 }}>
          <MemorySearchPanel />
          <ReplayDashboard />
          <OmniDevInterface organismId="main" backendUrl={backendUrl} />
        </div>
      </div>
{/* V10 Live Swarm Feed */}
<div style={{
  marginTop: 24,
  padding: 16,
  borderRadius: 16,
  background: "rgba(0,0,0,0.35)",
  border: "1px solid rgba(255,255,255,0.1)"
}}>
  <div style={{ color: "#7dd3fc", fontWeight: 800, marginBottom: 8 }}>
    V10 Live Swarm Feed
  </div>

  <div style={{ maxHeight: 260, overflow: "auto", fontSize: 12 }}>
    {swarmV10Feed.length === 0 ? (
      <div style={{ color: "rgba(255,255,255,0.4)" }}>
        Waiting for swarm events...
      </div>
    ) : (
      swarmV10Feed.map((e, i) => (
        <div key={i} style={{ marginBottom: 8, color: "rgba(255,255,255,0.75)" }}>
          <span style={{ color: "#a78bfa", fontWeight: 700 }}>
            {e.status ?? "unknown"}
          </span>
          {" | score: "}
          {typeof e.score === "number" ? e.score.toFixed(2) : "—"}
          {" | branch: "}
          {e.branch ?? "—"}
        </div>
      ))
    )}
  </div>
</div>
</section>
    </>
  )
}















