import type { OrganismSubsystem } from "../../features/organism/organism-types"
import { SubsystemCard } from "../../features/organism/SubsystemCard"

function formatBoolean(value: boolean | undefined) {
  return value ? "Yes" : "No"
}

function formatCompact(value: number) {
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value)
}

type OrganismAnatomySectionProps = {
  activeCard: OrganismSubsystem
  setActiveCard: (id: OrganismSubsystem) => void
  isLoading: boolean
  systemReady: boolean
  visionRuntimeReady: boolean
  visionConfigured: boolean
  toolCount: number
  totalTimelineEvents: number
  eventCount: number
  cacheSize: number
  capabilities: string[]
  backendUrl: string
  eventsPath: string | undefined
  llamacppReachable: boolean | undefined
  environment: string | undefined
  primaryVisionModel: string | null | undefined
  statusReady: boolean | undefined
  activeTheme: { accent: string }
  activeMessage: string
  failureRate: number
  cachedKeys: string[]
  selectedBucket: string | null
}

export function OrganismAnatomySection({
  activeCard,
  setActiveCard,
  isLoading,
  systemReady,
  visionRuntimeReady,
  visionConfigured,
  toolCount,
  totalTimelineEvents,
  eventCount,
  cacheSize,
  capabilities,
  backendUrl,
  eventsPath,
  llamacppReachable,
  environment,
  primaryVisionModel,
  statusReady,
  activeTheme,
  activeMessage,
  failureRate,
  cachedKeys,
  selectedBucket
}: OrganismAnatomySectionProps) {
  return (
    <section
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1.45fr) minmax(360px, 0.8fr)",
        gap: 16,
        alignItems: "start"
      }}
    >
      <article
        style={{
          borderRadius: 28,
          padding: 24,
          background: "linear-gradient(180deg, rgba(10,14,28,0.96), rgba(8,11,23,0.98))",
          border: "1px solid rgba(255,255,255,0.10)",
          boxShadow: "0 22px 60px rgba(0,0,0,0.30)"
        }}
      >
        <div style={{ marginBottom: 24 }}>
          <div
            style={{
              display: "inline-flex",
              padding: "8px 16px",
              borderRadius: 999,
              background: `${activeTheme.accent}22`,
              color: activeTheme.accent,
              fontSize: 12,
              fontWeight: 900,
              textTransform: "uppercase",
              letterSpacing: "0.1em",
              marginBottom: 14,
              border: `1px solid ${activeTheme.accent}44`
            }}
          >
            Subsystem control map
          </div>

          <h2 style={{ margin: "0 0 10px", fontSize: 32, color: "white", fontWeight: 900, letterSpacing: "-0.02em" }}>Operational Anatomy</h2>
          <p style={{ margin: 0, color: "rgba(255,255,255,0.65)", lineHeight: 1.7, fontSize: 15 }}>
            The organism's intelligence is distributed across five primary subsystems.
            <strong> Click any card</strong> to shift the entire dashboard's focus and tune the tutor's guidance.
          </p>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
            gap: 16
          }}
        >
          <SubsystemCard
            id="learning"
            activeId={activeCard}
            onActivate={setActiveCard}
            label="Learning"
            title="Memory + awareness"
            value={isLoading ? "Loading..." : formatCompact(totalTimelineEvents)}
            summary="Watch raw event flow turn into memory, traces, and adaptive signal."
            detail={`Events: ${eventCount}. Cache: ${cacheSize}. Outcome analysis active.`}
            nextStep="Monitor throughput growth vs. failure pressure."
          />

          <SubsystemCard
            id="healing"
            activeId={activeCard}
            onActivate={setActiveCard}
            label="Healing"
            title="Resilience + stability"
            value={systemReady ? "Healthy" : "Recovering"}
            summary="Detects anomalies and triggers autonomous recovery paths."
            detail={`Ready: ${formatBoolean(statusReady)}. Llama.cpp: ${formatBoolean(llamacppReachable)}. Env: ${environment ?? "---"}.`}
            nextStep="Prioritize stability before increasing autonomy."
          />

          <SubsystemCard
            id="autonomy"
            activeId={activeCard}
            onActivate={setActiveCard}
            label="Autonomy"
            title="Tools + action surface"
            value={isLoading ? "Loading..." : `${toolCount} tools`}
            summary="Measures the organism's ability to act via tool execution."
            detail={`Tools: ${toolCount}. Reach: ${capabilities.length > 0 ? capabilities.length : 0} capabilities.`}
            nextStep="Verify tool reach before assuming autonomous success."
          />

          <SubsystemCard
            id="vision"
            activeId={activeCard}
            onActivate={setActiveCard}
            label="Vision"
            title="Perception + visual"
            value={visionRuntimeReady ? "Vision Live" : "Pending"}
            summary="Exposes image-aware reasoning and multimodal UI inspection."
            detail={`Config: ${formatBoolean(visionConfigured)}. Model: ${primaryVisionModel || "---"}.`}
            nextStep="Vision is required for screenshot-based automation."
          />

          <SubsystemCard
            id="operator"
            activeId={activeCard}
            onActivate={setActiveCard}
            label="Human"
            title="Oversight + steering"
            value={systemReady ? "Guided" : "Override"}
            summary="Ensures the system remains transparent and steerable by you."
            detail={`URL: ${backendUrl.replace("http://", "")}. Path: ${eventsPath || "---"}.`}
            nextStep="Use the Interaction Deck to change tutor behavior."
          />
        </div>
      </article>

      <article
        style={{
          display: "grid",
          gap: 16,
          borderRadius: 28,
          padding: 24,
          background: "linear-gradient(180deg, rgba(11,17,32,0.98), rgba(8,12,22,0.98))",
          border: "1px solid rgba(255,255,255,0.12)",
          boxShadow: "0 24px 80px rgba(0,0,0,0.35)",
          position: "relative",
          overflow: "hidden"
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
              fontWeight: 900,
              textTransform: "uppercase",
              letterSpacing: "0.12em",
              marginBottom: 12
            }}
          >
            Deep Readout
          </div>

          <h2 style={{ margin: "0 0 10px", color: "white", fontSize: 28, fontWeight: 900, letterSpacing: "-0.01em" }}>Operator Analysis</h2>
          <p style={{ margin: 0, color: "rgba(255,255,255,0.6)", lineHeight: 1.6, fontSize: 14 }}>
            {selectedBucket 
              ? `Currently inspecting a historical snapshot from bucket ${selectedBucket}. Focus is locked.`
              : "Live interpretation of the organism's current posture and recent trends."}
          </p>
        </div>

        <div
          key={`${activeCard}-${selectedBucket}`}
          style={{
            position: "relative",
            overflow: "hidden",
            borderRadius: 20,
            padding: 20,
            background: `${activeTheme.accent}12`,
            border: `1px solid ${activeTheme.accent}22`,
            animation: "tutorReveal 400ms ease-out"
          }}
        >
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: `linear-gradient(120deg, transparent 0%, ${activeTheme.accent}15 45%, transparent 80%)`,
              transform: "translateX(-120%)",
              animation: "scanSweep 3s linear infinite"
            }}
          />
          <div style={{ position: "relative", zIndex: 1 }}>
            <div
              style={{
                color: activeTheme.accent,
                fontSize: 11,
                fontWeight: 900,
                textTransform: "uppercase",
                letterSpacing: "0.15em",
                marginBottom: 10
              }}
            >
              Interpretation: {activeCard}
            </div>
            <div style={{ color: "white", fontSize: 20, fontWeight: 800, marginBottom: 10, lineHeight: 1.3 }}>
              {selectedBucket ? "Temporal Snapshot" : "Dynamic Posture"}
            </div>
            <div style={{ color: "rgba(255,255,255,0.85)", lineHeight: 1.65, fontSize: 15 }}>{activeMessage}</div>
          </div>
        </div>

        <div style={{ display: "grid", gap: 12 }}>
          {[
            { label: "Environment", value: environment ?? "Unknown", accent: "#c4b5fd" },
            { label: "Vision Brain", value: primaryVisionModel ?? "None", accent: "#f9a8d4" },
            { label: "Memory Cache", value: cachedKeys.length > 0 ? `${cachedKeys.length} items` : "Empty", accent: "#86efac" },
            { label: "Failure Pressure", value: `${failureRate}%`, accent: failureRate > 25 ? "#fb7185" : "#fbbf24" },
            { label: "Capabilities", value: capabilities.length > 0 ? capabilities.join(", ") : "Manual only", accent: "#7dd3fc" },
            { label: "Model Runtime", value: llamacppReachable ? "Reachable" : "Offline", accent: llamacppReachable ? "#4ade80" : "#fb7185" }
          ].map((item) => (
            <div
              key={item.label}
              style={{
                borderRadius: 18,
                padding: 14,
                background: "rgba(255,255,255,0.03)",
                border: "1px solid rgba(255,255,255,0.06)",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 12
              }}
            >
              <div
                style={{
                  color: "rgba(255,255,255,0.46)", fontSize: 10,
                  fontWeight: 900,
                  textTransform: "uppercase",
                  letterSpacing: "0.08em"
                }}
              >
                {item.label}
              </div>
              <div style={{ color: item.accent, fontWeight: 700, fontSize: 13, textAlign: "right", wordBreak: "break-word", maxWidth: "60%" }}>{item.value}</div>
            </div>
          ))}
        </div>
      </article>
    </section>
  )
}



