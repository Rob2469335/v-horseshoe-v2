import { OrganismHeroCore } from "./OrganismHeroCore"
import { OrganismHeroHints } from "./OrganismHeroHints"
import { OrganismHeroLiveData, type TimelinePoint } from "./OrganismHeroLiveData"
import { OrganismHeroPulses } from "./OrganismHeroPulses"
import { OrganismHeroTelemetry } from "./OrganismHeroTelemetry"
import { OrganismHeroTicker } from "./OrganismHeroTicker"
import { useHeroDepth } from "./useHeroDepth"

type ActiveTheme = {
  accent: string
  tint: string
  glow: string
}

type PulseCard = {
  label: string
  value: string
  accent: string
  detail: string
}

type OrganismHeroProps = {
  activeTheme: ActiveTheme
  activeMessage: string
  isLoading: boolean
  eventCount: number
  totalTimelineEvents: number
  successRate: number
  toolCount: number
  tickerItems: string[]
  systemReady: boolean
  visionRuntimeReady: boolean
  pulseCards: PulseCard[]
  backendUrl: string
  formatCompact: (value: number) => string
  timelinePoints?: TimelinePoint[]
  timelineLoading?: boolean
}

export function OrganismHero({
  activeTheme,
  activeMessage,
  isLoading,
  eventCount,
  totalTimelineEvents,
  successRate,
  toolCount,
  tickerItems,
  systemReady,
  visionRuntimeReady,
  pulseCards,
  backendUrl,
  formatCompact,
  timelinePoints = [],
  timelineLoading = false
}: OrganismHeroProps) {
  const { ref, tilt } = useHeroDepth()

  const ticker = tickerItems.length > 0
    ? [...tickerItems, ...tickerItems]
    : [
        "Learning signal online",
        "Healing telemetry active",
        "Autonomy surface mapped",
        "Vision posture monitored",
        "Operator oversight loop active",
        "Adaptive memory flow visible",
        "Subsystem awakening in progress",
        "Chamber pulse synchronized"
      ]

  const heroStats = [
    {
      label: "Observed events",
      value: isLoading ? "Loading..." : formatCompact(eventCount),
      accent: "#38bdf8",
      glow: "rgba(56,189,248,0.36)"
    },
    {
      label: "Timeline volume",
      value: isLoading ? "Loading..." : formatCompact(totalTimelineEvents),
      accent: "#a78bfa",
      glow: "rgba(167,139,250,0.36)"
    },
    {
      label: "Success rate",
      value: isLoading ? "Loading..." : `${successRate}%`,
      accent: "#4ade80",
      glow: "rgba(74,222,128,0.36)"
    },
    {
      label: "Tool reach",
      value: isLoading ? "Loading..." : `${toolCount}`,
      accent: "#fbbf24",
      glow: "rgba(251,191,36,0.36)"
    }
  ]

  const pulses = pulseCards.length > 0
    ? pulseCards
    : [
        { label: "Learning pulse", value: "Adaptive", accent: "#38bdf8", detail: "Event traces are available for interpretation." },
        { label: "Healing pulse", value: "Stable", accent: "#4ade80", detail: "Recovery posture is visible to the operator." },
        { label: "Autonomy pulse", value: "Tool-aware", accent: "#fbbf24", detail: "Action surface can be read before execution." },
        { label: "Vision pulse", value: "Monitored", accent: "#f472b6", detail: "Visual workflow readiness is exposed." }
      ]

  return (
    <section
      ref={ref}
      style={{
        position: "relative",
        overflow: "hidden",
        borderRadius: 40,
        padding: 28,
        perspective: "2400px",
        transformStyle: "preserve-3d",
        background: `
          radial-gradient(circle at 10% 14%, rgba(56,189,248,0.24), transparent 18%),
          radial-gradient(circle at 88% 10%, rgba(167,139,250,0.24), transparent 18%),
          radial-gradient(circle at 82% 82%, rgba(244,114,182,0.22), transparent 22%),
          radial-gradient(circle at 16% 84%, rgba(74,222,128,0.18), transparent 18%),
          linear-gradient(145deg, rgba(3,6,18,1), rgba(10,17,36,0.985) 40%, rgba(4,8,22,1) 100%)
        `,
        border: "1px solid rgba(255,255,255,0.10)",
        boxShadow: "0 42px 160px rgba(0,0,0,0.50)"
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          background: `
            linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px),
            linear-gradient(180deg, rgba(255,255,255,0.03) 1px, transparent 1px)
          `,
          backgroundSize: "42px 42px",
          maskImage: "linear-gradient(180deg, rgba(0,0,0,0.88), transparent)"
        }}
      />

      <div
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          background: "linear-gradient(110deg, transparent 0%, rgba(255,255,255,0.07) 46%, transparent 56%)",
          transform: "translateX(-100%)",
          animation: "scanSweep 8s linear infinite"
        }}
      />

      <div
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          background: `radial-gradient(circle at ${tilt.glowX}% ${tilt.glowY}%, rgba(255,255,255,${0.06 + tilt.surge * 0.08}), transparent 20%)`,
          filter: "blur(18px)"
        }}
      />

      <div
        style={{
          position: "relative",
          zIndex: 1,
          display: "grid",
          gap: 18,
          transformStyle: "preserve-3d"
        }}
      >
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 1.34fr) minmax(350px, 0.96fr)",
            gap: 18,
            alignItems: "stretch"
          }}
        >
          <div style={{ display: "grid", gap: 16 }}>
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 10,
                width: "fit-content",
                padding: "9px 15px",
                borderRadius: 999,
                background: "rgba(255,255,255,0.07)",
                border: "1px solid rgba(255,255,255,0.12)",
                color: "rgba(255,255,255,0.95)",
                fontSize: 12,
                fontWeight: 900,
                textTransform: "uppercase",
                letterSpacing: "0.09em",
                boxShadow: `0 0 ${30 + tilt.surge * 14}px ${activeTheme.glow}`,
                transform: `translateZ(24px) translateX(${tilt.driftX * 0.35}px) translateY(${tilt.driftY * -0.2}px)`
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
              2027 live-data organism theater
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(0, 1fr) 236px",
                gap: 14,
                alignItems: "start"
              }}
            >
              <div style={{ display: "grid", gap: 12 }}>
                <h1
                  style={{
                    margin: 0,
                    color: "white",
                    fontSize: "clamp(3.2rem, 8vw, 6.8rem)",
                    lineHeight: 0.82,
                    letterSpacing: "-0.07em",
                    textShadow: "0 0 42px rgba(255,255,255,0.10)",
                    transform: `translateZ(40px) translateX(${tilt.driftX * 0.7}px) translateY(${tilt.driftY * -0.55}px)`
                  }}
                >
                  Organism
                  <br />
                  live-data wow
                </h1>

                <p
                  style={{
                    margin: 0,
                    maxWidth: 780,
                    color: "rgba(227,236,255,0.82)",
                    fontSize: 17,
                    lineHeight: 1.78,
                    transform: `translateZ(20px) translateX(${tilt.driftX * 0.45}px) translateY(${tilt.driftY * -0.25}px)`
                  }}
                >
                  A dramatic live chamber for self-learning, self-healing, autonomy, vision readiness, and human oversight.
                  This pass brings the first real graph heartbeat into the hero so the spectacle is driven by actual timeline flow, not only decoration.
                </p>
              </div>

              <div
                style={{
                  borderRadius: 24,
                  padding: 14,
                  background: "linear-gradient(180deg, rgba(255,255,255,0.09), rgba(255,255,255,0.04))",
                  border: "1px solid rgba(255,255,255,0.11)",
                  boxShadow: `0 22px 46px rgba(0,0,0,0.26), 0 0 ${30 + tilt.surge * 14}px ${activeTheme.glow}`,
                  display: "grid",
                  gap: 10,
                  transform: `translateZ(42px) rotateX(${4 + tilt.rotateX * 0.22}deg) rotateY(${-4 + tilt.rotateY * 0.22}deg)`
                }}
              >
                <div style={{ color: "rgba(255,255,255,0.64)", fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.1em" }}>
                  Active subsystem
                </div>

                <div
                  style={{
                    borderRadius: 18,
                    padding: 14,
                    background: activeTheme.tint,
                    border: `1px solid ${activeTheme.accent}66`,
                    boxShadow: `0 0 ${28 + tilt.surge * 10}px ${activeTheme.glow}`
                  }}
                >
                  <div
                    style={{
                      color: activeTheme.accent,
                      fontSize: 12,
                      fontWeight: 900,
                      textTransform: "uppercase",
                      letterSpacing: "0.08em",
                      marginBottom: 8
                    }}
                  >
                    Tutor spotlight
                  </div>
                  <div style={{ color: "white", lineHeight: 1.7, fontSize: 14 }}>{activeMessage}</div>
                </div>

                <div style={{ display: "grid", gap: 8, color: "rgba(255,255,255,0.76)", fontSize: 13, lineHeight: 1.55 }}>
                  <div>Feed mode: real timeline heartbeat</div>
                  <div>Feeling: dramatic, data-aware, alive</div>
                  <div>Priority: teach with motion and evidence</div>
                </div>
              </div>
            </div>

            <OrganismHeroLiveData
              points={timelinePoints}
              isLoading={timelineLoading}
              activeTheme={activeTheme}
            />

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1.36fr 0.96fr",
                gap: 14,
                alignItems: "stretch"
              }}
            >
              <OrganismHeroCore
                activeTheme={activeTheme}
                toolCount={toolCount}
                isLoading={isLoading}
                systemReady={systemReady}
                visionRuntimeReady={visionRuntimeReady}
                backendUrl={backendUrl}
                tilt={tilt}
              />

              <OrganismHeroTelemetry
                heroStats={heroStats}
                systemReady={systemReady}
              />
            </div>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateRows: "auto auto 1fr",
              gap: 14
            }}
          >
            <OrganismHeroTicker ticker={ticker} activeTheme={activeTheme} />
            <OrganismHeroHints />
            <OrganismHeroPulses pulses={pulses} />
          </div>
        </div>
      </div>
    </section>
  )
}


