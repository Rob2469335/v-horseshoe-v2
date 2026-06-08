import { useMemo, useState } from "react"
import { StatTile } from "../features/organism/StatTile"
import { KnowledgePanel } from "../features/organism/KnowledgePanel"
import { SubsystemCard } from "../features/organism/SubsystemCard"
import { useOrganismData } from "../features/organism/organism-hooks"
import { getSubsystemTheme, organismTheme } from "../features/organism/organism-theme"

function formatList(items: string[] | undefined) {
  if (!items || items.length === 0) return "None"
  return items.join(", ")
}

function formatBoolean(value: boolean | undefined) {
  return value ? "Yes" : "No"
}

function formatCompact(value: number) {
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value)
}
export default function OrganismPage() {
  const [activeCard, setActiveCard] = useState<import("../features/organism/organism-types").OrganismSubsystem>("learning")
  const [showDeepTutorial, setShowDeepTutorial] = useState(false)
  const {
    backendUrl,
    statusQuery,
    isLoading,
    isError,
    errorMessage,
    timelineQuery,
    capabilities,
    cacheSize,
    cachedKeys,
    eventCount,
    toolCount,
    systemReady,
    ollamaReady,
    totalTimelineEvents,
    totalTimelineSuccess,
    totalTimelinePartial,
    totalTimelineFail,
    successRate,
    failureRate,
    visionConfigured,
    visionExposedToTools,
    visionRuntimeReady,
    timelinePoints,
    latestBucket,
    tickerItems,
    pulseCards,
    chart
  } = useOrganismData()

  const {
    width,
    height,
    padding,
    allEventsLine,
    successLine,
    partialLine,
    failLine,
    allEventsArea
  } = chart

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

  

  const tutorialContent = useMemo(() => {
    switch (activeCard) {
      case "learning":
        return {
          steps: [
            "Watch the timeline and event counters first to understand incoming system activity.",
            "Inspect the learning card to see whether activity is turning into reusable signal and memory.",
            "Use the tutor rail to connect raw events to adaptive behavior instead of treating them as isolated logs."
          ],
          operatorAction: "Compare event volume with successful outcomes and look for learning signal that grows with throughput.",
          deepDive: [
            "Healthy learning means new activity increases system usefulness instead of only increasing noise.",
            "If event volume rises but interpretation stays flat, the organism may be observing without internalizing.",
            "Use this section to teach operators what a good learning loop looks like before tuning behavior."
          ]
        }
      case "healing":
        return {
          steps: [
            "Check readiness and runtime reachability before trusting downstream behavior.",
            "Look for degraded or amber states that suggest the organism is compensating rather than operating cleanly.",
            "Use the tutor rail to decide whether to stabilize first or continue observing."
          ],
          operatorAction: "Treat healing as operational resilience: if the runtime is weak, fix that before judging autonomy or vision.",
          deepDive: [
            "A system can appear functional while quietly degrading under the surface.",
            "Healing content should help the operator distinguish stable recovery from fragile temporary health.",
            "Use this section to explain why resilience is part of intelligence, not separate from it."
          ]
        }
      case "autonomy":
        return {
          steps: [
            "Inspect the exposed tools and cache posture to understand what the organism can actually do.",
            "Compare intent surfaces with available capabilities before assuming the organism can act.",
            "Use the tutor rail to translate capability into real execution confidence."
          ],
          operatorAction: "Autonomy is real only when intent, tools, and runtime availability line up at the same time.",
          deepDive: [
            "An organism is not autonomous because it has goals; it is autonomous because it can execute safely and repeatedly.",
            "Capability gaps should be visible here before they become confusing failures elsewhere.",
            "Use this section to teach the difference between apparent agency and operational agency."
          ]
        }
      case "vision":
        return {
          steps: [
            "Verify that vision is configured, exposed to tools, and live in the current runtime.",
            "Use the vision card and related metrics to tell whether image-aware workflows are truly available.",
            "Read the tutor guidance to connect model posture with user-facing workflow readiness."
          ],
          operatorAction: "Treat vision as a runtime pathway, not a checkbox; it matters only when the model is reachable and usable.",
          deepDive: [
            "Configured vision is not the same as usable vision.",
            "This section should teach the operator how to spot the gap between configuration, exposure, and live execution.",
            "Use it to keep image-aware workflows explainable instead of magical."
          ]
        }
      default:
        return {
          steps: [
            "Use the active system summary to decide where attention belongs first.",
            "Follow the tutor rail to connect subsystem posture with a concrete operator decision.",
            "Use deeper notes only when you need more explanation, not by default."
          ],
          operatorAction: "Operator control is the mechanism that keeps the organism understandable and steerable under changing conditions.",
          deepDive: [
            "Good operator surfaces reduce guessing and shorten the path from signal to action.",
            "This section should teach control, not just describe status.",
            "Use it to make complex system behavior understandable fast."
          ]
        }
    }
  }, [activeCard])
  const activeTheme = getSubsystemTheme(activeCard)

  const tutorMeta = useMemo(() => {
    switch (activeCard) {
      case "learning":
        return {
          eyebrow: "Focused subsystem: Learning",
          title: "Learning is shaping memory and pattern reuse.",
          intro: "This view emphasizes how event flow becomes retained context, reusable signal, and adaptive behavior."
        }
      case "healing":
        return {
          eyebrow: "Focused subsystem: Healing",
          title: "Healing is measuring resilience and recovery.",
          intro: "This view emphasizes organism stability, degradation signals, and whether the system can keep serving under pressure."
        }
      case "autonomy":
        return {
          eyebrow: "Focused subsystem: Autonomy",
          title: "Autonomy is about action through real capability.",
          intro: "This view emphasizes tool reach, execution posture, and whether the organism can convert intent into action."
        }
      case "vision":
        return {
          eyebrow: "Focused subsystem: Vision",
          title: "Vision is making image-aware workflows legible.",
          intro: "This view emphasizes whether visual reasoning is configured, exposed, and usable in live runtime paths."
        }
      default:
        return {
          eyebrow: "Focused subsystem: Operator",
          title: "Operator control keeps the organism steerable.",
          intro: "This view emphasizes explainability, guided control, and fast intervention when posture changes."
        }
    }
  }, [activeCard])

  

  return (
    <section
      className="page"
      style={{
        minHeight: "100vh",
        color: organismTheme.surface.text,
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

          
          @keyframes focusBreath {
            0% { transform: scale(1); filter: saturate(1); }
            50% { transform: scale(1.015); filter: saturate(1.08); }
            100% { transform: scale(1); filter: saturate(1); }
          }

          @keyframes tutorReveal {
            0% { opacity: 0; transform: translateY(10px); }
            100% { opacity: 1; transform: translateY(0); }
          }

          @keyframes railGlow {
            0% { box-shadow: 0 0 0 rgba(0,0,0,0); }
            50% { box-shadow: 0 0 30px var(--active-glow); }
            100% { box-shadow: 0 0 0 rgba(0,0,0,0); }
          }
          @keyframes dashFlow {
            to { stroke-dashoffset: -24; }
          }
        `}
      </style>

      <div style={{ maxWidth: 1440, margin: "0 auto", display: "grid", gap: 22 }}>
<div style={{ position: "fixed", top: 8, right: 8, zIndex: 9999, background: "red", color: "white", padding: 8 }}>
  NEW BUILD 2:27 PM
</div>
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
                    background: activeTheme.tint,
                    color: activeTheme.accent,
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
                      background: activeTheme.accent,
                      boxShadow: `0 0 18px ${activeTheme.accent}`,
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
                      color: organismTheme.surface.textSoft
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
                  <div style={{ color: activeTheme.accent, fontSize: 12, fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 8 }}>
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
              <SubsystemCard
                id="learning"
                activeId={activeCard}
                onActivate={setActiveCard}
                label="Learning"
                title="Memory + awareness"
                value={isLoading ? "Loading..." : formatCompact(totalTimelineEvents)}
                summary="The organism watches event flow and turns it into traces, memory, and adaptive signal."
                detail={`Observed events: ${eventCount}. Timeline events: ${totalTimelineEvents}. Cache size: ${cacheSize}.`}
                nextStep="Watch whether throughput rises while failures stay controlled."
              />

              <SubsystemCard
                id="healing"
                activeId={activeCard}
                onActivate={setActiveCard}
                label="Healing"
                title="Resilience + runtime health"
                value={systemReady ? "Healthy" : "Review"}
                summary="This shows whether the system is stable enough to trust the organism under active load."
                detail={`Ready: ${formatBoolean(statusQuery.data?.ready)}. Ollama reachable: ${formatBoolean(statusQuery.data?.ollama_reachable)}. Environment: ${statusQuery.data?.environment ?? "Unknown"}.`}
                nextStep="Fix health before trusting weak or partial organism behavior."
              />

              <SubsystemCard
                id="autonomy"
                activeId={activeCard}
                onActivate={setActiveCard}
                label="Autonomy"
                title="Tools + action surface"
                value={isLoading ? "Loading..." : `${toolCount} tools`}
                summary="Autonomy measures whether the organism can act through tool execution rather than only observe."
                detail={`Tool count: ${toolCount}. Capabilities: ${formatList(capabilities)}.`}
                nextStep="Low tool reach means the organism may understand but still fail to help."
              />

              <SubsystemCard
                id="vision"
                activeId={activeCard}
                onActivate={setActiveCard}
                label="Vision"
                title="Perception + visual workflows"
                value={visionRuntimeReady ? "Vision live" : "Pending"}
                summary="Vision determines whether screenshots, images, and visual state inspection are genuinely available."
                detail={`Vision configured: ${formatBoolean(visionConfigured)}. Vision exposed: ${formatBoolean(visionExposedToTools)}. Primary model: ${statusQuery.data?.primary_vision_model ?? "Unknown"}.`}
                nextStep="If pending, visual tasks will be weaker even if the core organism is up."
              />

              <SubsystemCard
                id="operator"
                activeId={activeCard}
                onActivate={setActiveCard}
                label="Human in the loop"
                title="Oversight + explainability"
                value={systemReady ? "Operator ready" : "Needs oversight"}
                summary="The organism remains useful only if you can inspect it, understand it, and steer it early."
                detail={`Backend URL: ${backendUrl}. Capability visibility: ${formatBoolean(toolCount > 0)}. Events path: ${statusQuery.data?.events_path ?? "Unknown"}.`}
                nextStep="Use this page as an early-warning control layer, not a passive dashboard."
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
                <div style={{ color: activeTheme.accent, fontSize: 12, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8 }}>
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
                  color: activeTheme.accent,
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
                { label: "Latest bucket", value: latestBucket, color: activeTheme.accent },
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
              {errorMessage}
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
            gridTemplateColumns: "minmax(0, 1.35fr) minmax(320px, 0.95fr)",
            gap: 18,
            alignItems: "start",
            padding: 18,
            borderRadius: 28,
            background: `linear-gradient(180deg, ${activeTheme.tint}, rgba(255,255,255,0.02))`,
            border: `1px solid ${activeTheme.tint}`,
            boxShadow: `0 24px 60px ${activeTheme.glow}`
          }}
        >
          <div
            style={{
              display: "grid",
              gap: 18
            }}
          >
            <div
              style={{
                display: "grid",
                gap: 12,
                padding: 18,
                borderRadius: 24,
                background: "linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.03))",
                border: `1px solid ${activeTheme.tint}`,
                boxShadow: `0 24px 60px ${activeTheme.glow}`,
                animation: "tutorReveal 260ms cubic-bezier(0.16, 1, 0.3, 1)"
              }}
            >
              <div
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 10,
                  width: "fit-content",
                  padding: "7px 12px",
                  borderRadius: 999,
                  background: activeTheme.tint,
                  color: activeTheme.accent,
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
                    background: activeTheme.accent,
                    boxShadow: `0 0 16px ${activeTheme.accent}`,
                    animation: "statusBlink 1.8s ease-in-out infinite"
                  }}
                />
                {tutorMeta.eyebrow}
              </div>

              <h2 style={{ margin: 0, color: "white", fontSize: 28, lineHeight: 1.05 }}>
                {tutorMeta.title}
              </h2>

              <p style={{ margin: 0, color: "rgba(236,243,255,0.78)", lineHeight: 1.7 }}>
                {tutorMeta.intro}
              </p>

              <div style={{ color: activeTheme.accent, fontSize: 12, fontWeight: 900, letterSpacing: "0.08em", textTransform: "uppercase" }}>
                Guided instructions
              </div>

              <div style={{ display: "grid", gap: 10 }}>
                {tutorialContent.steps.map((step, index) => (
                  <div
                    key={step}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "32px minmax(0, 1fr)",
                      gap: 12,
                      alignItems: "start",
                      padding: 12,
                      borderRadius: 16,
                      background: "rgba(255,255,255,0.03)",
                      border: "1px solid rgba(255,255,255,0.06)"
                    }}
                  >
                    <div
                      style={{
                        width: 32,
                        height: 32,
                        borderRadius: "50%",
                        display: "grid",
                        placeItems: "center",
                        background: activeTheme.tint,
                        color: activeTheme.accent,
                        fontWeight: 900
                      }}
                    >
                      {index + 1}
                    </div>

                    <div style={{ color: "rgba(236,243,255,0.92)", lineHeight: 1.65 }}>
                      {step}
                    </div>
                  </div>
                ))}
              </div>

              <div
                style={{
                  padding: 14,
                  borderRadius: 16,
                  background: activeTheme.tint,
                  border: `1px solid ${activeTheme.tint}`,
                  color: "white",
                  lineHeight: 1.65
                }}
              >
                <span style={{ color: activeTheme.accent, fontWeight: 900 }}>Operator action:</span> {tutorialContent.operatorAction}
              </div>

              <button
                type="button"
                onClick={() => setShowDeepTutorial((value) => !value)}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: "fit-content",
                  padding: "10px 14px",
                  borderRadius: 999,
                  border: `1px solid ${activeTheme.tint}`,
                  background: "rgba(255,255,255,0.04)",
                  color: "white",
                  fontWeight: 800,
                  cursor: "pointer"
                }}
              >
                {showDeepTutorial ? "Hide deeper guidance" : "Show deeper guidance"}
              </button>

              {showDeepTutorial ? (
                <div
                  style={{
                    display: "grid",
                    gap: 10,
                    padding: 14,
                    borderRadius: 18,
                    background: "rgba(255,255,255,0.03)",
                    border: "1px solid rgba(255,255,255,0.08)",
                    animation: "tutorReveal 220ms cubic-bezier(0.16, 1, 0.3, 1)"
                  }}
                >
                  {tutorialContent.deepDive.map((item) => (
                    <div key={item} style={{ color: "rgba(236,243,255,0.82)", lineHeight: 1.65 }}>
                      {item}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>

            <div
              style={{
                animation: "tutorReveal 320ms cubic-bezier(0.16, 1, 0.3, 1)",
                borderRadius: 24,
                boxShadow: `0 0 0 1px ${activeTheme.tint} inset, 0 24px 60px ${activeTheme.glow}`
              }}
            >
              <KnowledgePanel
                badge="Mental model"
                accent={activeCard === "learning" ? activeTheme.accent : "#7dd3fc"}
                title="What an organism is"
                intro="In this system, an organism converts raw activity into a more adaptive and explainable behavior loop."
                bullets={[
                  "It watches events instead of letting them disappear into logs.",
                  "It remembers patterns instead of starting fresh every time.",
                  "It can act through tools when action is available.",
                  "It stays visible to a human operator rather than becoming opaque."
                ]}
              />
            </div>

            <div
              style={{
                animation: "tutorReveal 420ms cubic-bezier(0.16, 1, 0.3, 1)"
              }}
            >
              <KnowledgePanel
                badge="Why it matters"
                accent={activeCard === "vision" ? activeTheme.accent : "#f472b6"}
                title="Why this matters"
                intro="A strong organism surface is part of the control system. It helps you steer, debug, and evolve faster."
                bullets={[
                  "It pushes the platform toward adaptive behavior instead of static status checking.",
                  "It gives you earlier warning for regressions and runtime weakness.",
                  "It makes tool reach and vision posture obvious instead of hidden.",
                  "It creates a stronger foundation for self-learning and guided autonomy."
                ]}
              />
            </div>
          </div>

          <div
            style={{
              display: "grid",
              gap: 18,
              opacity: 0.94
            }}
          >
            <div
              style={{
                animation: "tutorReveal 360ms cubic-bezier(0.16, 1, 0.3, 1)"
              }}
            >
              <KnowledgePanel
                badge="Usefulness"
                accent={activeCard === "healing" ? activeTheme.accent : "#4ade80"}
                title="What it is doing for you"
                intro="The organism improves observability, actionability, and adaptability at the same time."
                bullets={[
                  "Learning from event flow and timeline behavior.",
                  "Checking whether the runtime is healthy enough to keep serving.",
                  "Using tools and capabilities when those paths are exposed.",
                  "Preparing image-aware intelligence when vision support is live."
                ]}
              />
            </div>

            <div
              style={{
                animation: "tutorReveal 480ms cubic-bezier(0.16, 1, 0.3, 1)"
              }}
            >
              <KnowledgePanel
                badge="Reading guide"
                accent={activeCard === "autonomy" ? activeTheme.accent : "#a78bfa"}
                title="How to read this page"
                intro="This page is designed to make organism posture obvious without decoding raw backend jargon."
                bullets={[
                  "Start with the animated metric band to read immediate posture.",
                  "Use the anatomy cards to inspect each organism role.",
                  "Use the timeline chart to spot growth, stability, and failure pressure.",
                  "Treat amber and pink as attention surfaces, not decoration."
                ]}
              />
            </div>
          </div>
        </section>
      </div>
    </section>
  )
}



























