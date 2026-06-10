import { useEffect, useMemo, useState } from "react"
import { OrganismHero } from "../components/organism/OrganismHero"
import { OrganismAnatomySection } from "../components/organism/OrganismAnatomySection"
import { OrganismTimelineSection } from "../components/organism/OrganismTimelineSection"
import { OrganismTutorSection } from "../components/organism/OrganismTutorSection"
import { useOrganismData } from "../features/organism/organism-hooks"
import { getSubsystemTheme, organismTheme } from "../features/organism/organism-theme"
import type { OrganismSubsystem } from "../features/organism/organism-types"

function formatCompact(value: number) {
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value)
}

export default function OrganismPage() {
  const [activeCard, setActiveCard] = useState<OrganismSubsystem>("learning")
  const [showDeepTutorial, setShowDeepTutorial] = useState(false)
  const [interactionMode, setInteractionMode] = useState<"observe" | "teach" | "drill">("teach")
  const [selectedBucket, setSelectedBucket] = useState<string | null>(null)

  useEffect(() => {
    // Reset deep tutorial when changing card or mode to ensure progressive disclosure
    setShowDeepTutorial(interactionMode === "drill")
  }, [activeCard, interactionMode])

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

  const activeBucketData = useMemo(() => {
    if (!selectedBucket) return null
    return timelinePoints.find(p => p.bucket === selectedBucket)
  }, [selectedBucket, timelinePoints])

  const bucketComparison = useMemo(() => {
    return insights.getComparison(selectedBucket)
  }, [selectedBucket, insights])

  const activeTheme = getSubsystemTheme(activeCard)

  const activeMessage = useMemo(() => {
    if (activeBucketData) {
      return `Detailed snapshot for ${activeBucketData.bucket}: ${activeBucketData.event_count} events processed. Success: ${activeBucketData.success_count}, Fail: ${activeBucketData.fail_count}.`
    }
    switch (activeCard) {
      case "learning":
        return "Learning translates live events into memory and patterns. Current trend: " + insights.volumeTrend + "."
      case "healing":
        return "Healing monitors system stability and recovery paths. Status: " + (systemReady ? "Stable" : "Recovering") + "."
      case "autonomy":
        return "Autonomy measures the organism's reach through tools. Reach: " + toolCount + " tools active."
      case "vision":
        return "Vision exposes image-aware reasoning paths. Posture: " + (visionRuntimeReady ? "Live" : "Pending") + "."
      default:
        return "Operator control is your steering layer. " + insights.action
    }
  }, [activeCard, activeBucketData, insights, systemReady, toolCount, visionRuntimeReady])

  const tutorialContent = useMemo(() => {
    const base = (() => {
      switch (activeCard) {
        case "learning":
          return {
            steps: [
              "Watch how raw event throughput turns into reusable 'Traces' for future tasks.",
              "Observe the learning cards to see if memory cache size grows alongside event volume.",
              "Healthy systems see rising throughput while maintaining a high success rate."
            ],
            operatorAction: insights.summary,
            deepDive: [
              "Memory recall uses vector similarity to find past successful traces.",
              "Policy updates are batched to maintain runtime performance.",
              "Failure analysis triggers 'Retraining' loops to correct recurring errors."
            ]
          }
        case "healing":
          return {
            steps: [
              "Verify the 'System Ready' pulse. A healthy system recovers from minor network dips automatically.",
              "Look for 'Review' states in the status cards; these indicate a recovery path is active.",
              "Stability is prerequisite for autonomy. Fix healing issues first."
            ],
            operatorAction: "Resilience is currently " + (systemReady ? "Healthy" : "Degraded") + ". Proceed with " + (systemReady ? "Standard tasks." : "Caution."),
            deepDive: [
              "Uses circuit breakers to prevent error propagation across subsystems.",
              "Periodic health checks verify Ollama and backend reachability.",
              "Auto-rollback logic protects system state during tool execution failures."
            ]
          }
        case "autonomy":
          return {
            steps: [
              "Inspect the Tool Reach. If reach is zero, the organism is observant but paralyzed.",
              "Check for exposed capabilities. These represent the specific 'Hands' available to the model.",
              "Autonomy grows with successful tool executions. Watch for failure pressure here."
            ],
            operatorAction: insights.action,
            deepDive: [
              "Agentic loops follow a Plan-Execute-Verify pattern for every tool call.",
              "Capability exposure is limited by the current security sandbox configuration.",
              "Cache size correlates with autonomous efficiency (reusing results)."
            ]
          }
        case "vision":
          return {
            steps: [
              "Vision is active when the primary VLM (Vision Language Model) is reachable.",
              "Screen inspection and image understanding depend on this runtime pathway.",
              "If 'Vision Pending', multimodal tools will revert to text-only mode."
            ],
            operatorAction: visionRuntimeReady ? "Vision is live. Image-aware tools are fully reachable." : "Vision is pending. Multimodal capabilities are currently restricted.",
            deepDive: [
              "VLMs convert pixel buffers into semantic tokens for the core model.",
              "Visual grounding allows the system to interact with UI coordinates.",
              "Supported models: " + (statusQuery.data?.vision_models_installed?.join(", ") || "None found")
            ]
          }
        default:
          return {
            steps: [
              "Use the Interaction Deck to change how the page teaches you.",
              "Observe mode is for scanning; Teach mode is for learning; Drill mode is for inspection.",
              "The operator readout provides a real-time interpretation of all subsystem data."
            ],
            operatorAction: "Operator oversight is active. You are steering the " + activeCard + " subsystem.",
            deepDive: [
              "Real-time feedback loops ensure user overrides are instantaneous.",
              "Transparent logging allows for auditing every autonomous action.",
              "Multi-modal status aggregation keeps the dashboard synchronized with the backend."
            ]
          }
      }
    })()

    if (activeBucketData) {
      const bucketSuccessRate = Math.round((activeBucketData.success_count / Math.max(1, activeBucketData.event_count)) * 100)
      return {
        ...base,
        operatorAction: `Focused on bucket ${activeBucketData.bucket}. Success rate for this window: ${bucketSuccessRate}%. ${bucketSuccessRate < 70 ? "Caution: high failure pressure detected." : "Behavior within healthy bounds."}`
      }
    }

    return base
  }, [activeCard, insights, activeBucketData, systemReady, visionRuntimeReady, statusQuery.data])

  const modeSummary = useMemo(() => {
    switch (interactionMode) {
      case "observe":
        return {
          title: "Observation posture",
          detail: "Dashboard optimized for at-a-glance scanning. Guided tutor sections are minimized to focus on raw live-data heartbeat."
        }
      case "teach":
        return {
          title: "Tutor control mode",
          detail: "Active educational overlay enabled. Every metric and subsystem is explained in plain English for new operators."
        }
      default:
        return {
          title: "Deep inspection mode",
          detail: "Full technical transparency. Progressive disclosure is bypassed to show deep mechanics and raw architectural traces."
        }
    }
  }, [interactionMode])

  const tryThisNext = useMemo(() => {
    if (selectedBucket) return "Click the timeline background to return to live stream monitoring."
    switch (activeCard) {
      case "learning": return "Increase system throughput to gather more memory traces."
      case "healing": return "Simulate a network dip to test automated recovery paths."
      case "autonomy": return "Expose more tool capabilities to increase action reach."
      default: return "Switch to 'Drill' mode to see architectural traces."
    }
  }, [activeCard, selectedBucket])

  const tutorMeta = useMemo(() => {
    const subsystemLabel = activeCard.charAt(0).toUpperCase() + activeCard.slice(1)
    return {
      eyebrow: `Subsystem focus: ${subsystemLabel}`,
      title: `${subsystemLabel} Intelligence`,
      intro: `Direct control and tutoring for the organism's ${activeCard} subsystem.`
    }
  }, [activeCard])

  return (
    <section
      className="page"
      style={{
        minHeight: "100vh",
        color: organismTheme.surface.text,
        background: `
          radial-gradient(circle at 0% 0%, ${activeTheme.accent}18, transparent 35%),
          radial-gradient(circle at 100% 0%, rgba(139,92,246,0.15), transparent 30%),
          radial-gradient(circle at 50% 50%, ${activeTheme.glow}08, transparent 40%),
          linear-gradient(180deg, #040816 0%, #07101f 48%, #050815 100%)
        `,
        padding: "28px 18px 56px",
        transition: "background 500ms ease"
      }}
    >
      <style>{`
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

      <div style={{ maxWidth: 1440, margin: "0 auto", display: "grid", gap: 24 }}>
        {/* INTERACTION DECK - The Master Control */}
        <article
          style={{
            borderRadius: 32,
            padding: 24,
            background: "linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0.03))",
            border: "1px solid rgba(255,255,255,0.12)",
            boxShadow: "0 24px 80px rgba(0,0,0,0.3)",
            display: "grid",
            gap: 20
          }}
        >
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 20
            }}
          >
            <div>
              <div
                style={{
                  color: activeTheme.accent,
                  fontSize: 12,
                  fontWeight: 900,
                  textTransform: "uppercase",
                  letterSpacing: "0.15em",
                  marginBottom: 8
                }}
              >
                Operator Command Deck
              </div>
              <div style={{ color: "white", fontSize: 32, fontWeight: 900, letterSpacing: "-0.02em" }}>
                {modeSummary.title}
              </div>
              <div style={{ color: "rgba(225,235,255,0.7)", marginTop: 8, lineHeight: 1.6, maxWidth: 600 }}>
                {modeSummary.detail}
              </div>
            </div>

            <nav style={{ display: "flex", flexWrap: "wrap", gap: 12, background: "rgba(0,0,0,0.2)", padding: 8, borderRadius: 999, border: "1px solid rgba(255,255,255,0.06)" }}>
              {[
                { key: "observe", label: "Observe", icon: "👁️" },
                { key: "teach", label: "Teach", icon: "🎓" },
                { key: "drill", label: "Drill", icon: "🔍" }
              ].map((mode) => {
                const isActive = interactionMode === mode.key
                return (
                  <button
                    key={mode.key}
                    type="button"
                    onClick={() => setInteractionMode(mode.key as any)}
                    aria-pressed={isActive}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      padding: "12px 24px",
                      borderRadius: 999,
                      border: "none",
                      background: isActive ? activeTheme.accent : "transparent",
                      color: isActive ? "white" : "rgba(255,255,255,0.6)",
                      fontWeight: 800,
                      fontSize: 14,
                      cursor: "pointer",
                      boxShadow: isActive ? `0 10px 24px ${activeTheme.glow}` : "none",
                      transform: isActive ? "translateY(-1px)" : "translateY(0)",
                      transition: "all 200ms cubic-bezier(0.16,1,0.3,1)"
                    }}
                  >
                    <span>{mode.icon}</span>
                    {mode.label}
                  </button>
                )
              })}
            </nav>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
              gap: 16
            }}
          >
            {[
              {
                label: "Volume trend",
                value: insights.volumeTrend.charAt(0).toUpperCase() + insights.volumeTrend.slice(1),
                detail: insights.summary
              },
              {
                label: "Timeline focus",
                value: selectedBucket ?? "Live Stream",
                detail: selectedBucket ? "Inspecting a fixed point in time. Click chart background to release." : "Monitoring real-time activity events as they happen.",
                highlight: !!selectedBucket,
                action: selectedBucket ? () => setSelectedBucket(null) : undefined
              },
              {
                label: "System posture",
                value: insights.successTrend.charAt(0).toUpperCase() + insights.successTrend.slice(1),
                detail: insights.action
              }
            ].map((item) => (
              <div
                key={item.label}
                onClick={item.action}
                style={{
                  borderRadius: 24,
                  padding: 18,
                  background: item.highlight ? `${activeTheme.accent}15` : "rgba(255,255,255,0.04)",
                  border: `1px solid ${item.highlight ? activeTheme.accent : "rgba(255,255,255,0.1)"}`,
                  boxShadow: item.highlight ? `0 0 30px ${activeTheme.glow}` : "none",
                  cursor: item.action ? "pointer" : "default",
                  transition: "all 300ms ease",
                  position: "relative",
                  overflow: "hidden"
                }}
              >
                {item.highlight && <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 2, background: activeTheme.accent }} />}
                <div style={{ color: activeTheme.accent, fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.12em", marginBottom: 8 }}>
                  {item.label}
                </div>
                <div style={{ color: "white", fontSize: 24, fontWeight: 900, marginBottom: 8 }}>
                  {item.value}
                </div>
                <div style={{ color: "rgba(225,235,255,0.6)", lineHeight: 1.5, fontSize: 13 }}>
                  {item.detail}
                </div>
              </div>
            ))}
          </div>
          
          {/* Why this matters & What changed Section */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16, marginTop: 10 }}>
            <div style={{ borderRadius: 20, padding: 20, background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
              <div style={{ color: activeTheme.accent, fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.15em", marginBottom: 10 }}>
                Why this focus matters
              </div>
              <div style={{ color: "white", lineHeight: 1.7, fontSize: 14 }}>
                {activeCard === "learning" ? "Learning confirms that raw events are being internalized into reusable traces." :
                 activeCard === "healing" ? "Healing visibility ensures the operator knows when the organism is compensating for errors." :
                 activeCard === "autonomy" ? "Autonomy posture tells you whether the organism can execute intent safely." :
                 activeCard === "vision" ? "Vision readiness is critical for any workflow requiring image-based reasoning." :
                 "Operator oversight ensures human steerability remains the final authority."}
              </div>
            </div>
            
            <div style={{ borderRadius: 20, padding: 20, background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
              <div style={{ color: activeTheme.accent, fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.15em", marginBottom: 10 }}>
                What changed
              </div>
              <div style={{ color: "white", lineHeight: 1.7, fontSize: 14 }}>
                {bucketComparison ? (
                  <>
                    <strong style={{ color: activeTheme.accent }}>{bucketComparison.label}:</strong> {bucketComparison.description}
                  </>
                ) : (
                  <>The system is currently in <strong style={{ color: activeTheme.accent }}>{insights.volumeTrend}</strong> throughput mode with <strong style={{ color: activeTheme.accent }}>{insights.successTrend}</strong> outcome posture.</>
                )}
              </div>
            </div>

            <div style={{ borderRadius: 20, padding: 20, background: `${activeTheme.accent}11`, border: `1px solid ${activeTheme.accent}22` }}>
              <div style={{ color: activeTheme.accent, fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.15em", marginBottom: 10 }}>
                Try this next
              </div>
              <div style={{ color: "white", lineHeight: 1.7, fontSize: 14, fontWeight: 700 }}>
                {tryThisNext}
              </div>
            </div>
          </div>
        </article>

        {/* HERO SECTION - The Heartbeat */}
        <OrganismHero
          activeTheme={activeTheme}
          activeMessage={activeMessage}
          isLoading={isLoading}
          eventCount={activeBucketData ? activeBucketData.event_count : eventCount}
          totalTimelineEvents={totalTimelineEvents}
          successRate={activeBucketData ? Math.round((activeBucketData.success_count / activeBucketData.event_count) * 100) : successRate}
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

        {/* ANATOMY SECTION - Subsystem Selectors */}
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

        {/* TIMELINE SECTION - Temporal Controls */}
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

        {/* TUTOR SECTION - The Guide (Conditional based on Mode) */}
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
    </section>
  )
}




