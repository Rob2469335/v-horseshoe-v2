import type { HeroTilt } from "./useHeroDepth"

type OrganismHeroCoreProps = {
  activeTheme: { accent: string; glow: string; tint: string }
  toolCount: number
  isLoading: boolean
  systemReady: boolean
  visionRuntimeReady: boolean
  backendUrl: string
  tilt: HeroTilt
}

export function OrganismHeroCore({
  activeTheme,
  toolCount,
  isLoading,
  systemReady,
  visionRuntimeReady,
  backendUrl,
  tilt
}: OrganismHeroCoreProps) {
  return (
    <div
      style={{
        position: "relative",
        minHeight: 280,
        borderRadius: 28,
        background: "linear-gradient(180deg, rgba(255,255,255,0.07), rgba(255,255,255,0.03))",
        border: "1px solid rgba(255,255,255,0.10)",
        overflow: "hidden",
        boxShadow: `0 0 ${40 + tilt.surge * 30}px ${activeTheme.glow}`,
        transform: `translateZ(32px) rotateX(${tilt.rotateX * 0.4}deg) rotateY(${tilt.rotateY * 0.4}deg)`,
        transition: "box-shadow 200ms ease"
      }}
    >
      <svg
        viewBox="0 0 420 280"
        style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
        aria-hidden="true"
      >
        <defs>
          <radialGradient id="heroCoreGlow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor={activeTheme.accent} stopOpacity="0.90" />
            <stop offset="40%" stopColor={activeTheme.accent} stopOpacity="0.22" />
            <stop offset="100%" stopColor={activeTheme.accent} stopOpacity="0" />
          </radialGradient>
        </defs>

        <circle
          cx="210"
          cy="140"
          r="114"
          fill="none"
          stroke="rgba(125,211,252,0.18)"
          strokeWidth="1.4"
          strokeDasharray="7 11"
          style={{ transformOrigin: "210px 140px", animation: "orbitSpin 20s linear infinite" }}
        />

        <g style={{ transformOrigin: "210px 140px", animation: "orbitSpin 20s linear infinite" }}>
          <circle cx="324" cy="140" r="7" fill="#38bdf8" style={{ filter: "drop-shadow(0 0 10px #38bdf8)" }} />
          <circle cx="210" cy="26" r="6" fill="#4ade80" style={{ filter: "drop-shadow(0 0 10px #4ade80)" }} />
          <circle cx="96" cy="140" r="7" fill="#fbbf24" style={{ filter: "drop-shadow(0 0 10px #fbbf24)" }} />
          <circle cx="210" cy="254" r="7" fill="#f472b6" style={{ filter: "drop-shadow(0 0 10px #f472b6)" }} />
        </g>

        <circle
          cx="210"
          cy="140"
          r="72"
          fill="none"
          stroke="rgba(196,181,253,0.22)"
          strokeWidth="1.2"
          strokeDasharray="5 8"
          style={{ transformOrigin: "210px 140px", animation: "orbitReverse 12s linear infinite" }}
        />

        <g style={{ transformOrigin: "210px 140px", animation: "orbitReverse 12s linear infinite" }}>
          <circle cx="282" cy="140" r="5" fill="#c4b5fd" style={{ filter: "drop-shadow(0 0 8px #c4b5fd)" }} />
          <circle cx="174" cy="84" r="5" fill="#7dd3fc" style={{ filter: "drop-shadow(0 0 8px #7dd3fc)" }} />
        </g>

        <g strokeDasharray="4 5" strokeWidth="1.8">
          <path d="M210 140 L96 140" fill="none" stroke="rgba(251,191,36,0.45)" style={{ animation: "dashFlow 1.6s linear infinite" }} />
          <path d="M210 140 L210 26" fill="none" stroke="rgba(74,222,128,0.45)" style={{ animation: "dashFlow 1.9s linear infinite" }} />
          <path d="M210 140 L324 140" fill="none" stroke="rgba(56,189,248,0.50)" style={{ animation: "dashFlow 2.1s linear infinite" }} />
          <path d="M210 140 L210 254" fill="none" stroke="rgba(244,114,182,0.45)" style={{ animation: "dashFlow 1.75s linear infinite" }} />
        </g>

        <circle
          cx="210"
          cy="140"
          r="52"
          fill="url(#heroCoreGlow)"
          style={{ transformOrigin: "210px 140px", animation: "corePulse 3.5s ease-in-out infinite" }}
        />

        <circle cx="210" cy="140" r="28" fill="rgba(6,12,28,0.90)" stroke={`${activeTheme.accent}88`} strokeWidth="2" />
        <circle cx="210" cy="140" r="12" fill={activeTheme.accent} style={{ filter: `drop-shadow(0 0 18px ${activeTheme.accent})` }} />
      </svg>

      <div
        style={{
          position: "absolute",
          left: 14,
          right: 14,
          bottom: 14,
          display: "grid",
          gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
          gap: 10
        }}
      >
        {[
          {
            label: "Core",
            value: systemReady ? "Stable" : "Review",
            color: systemReady ? "#4ade80" : "#fbbf24"
          },
          {
            label: "Vision",
            value: visionRuntimeReady ? "Online" : "Pending",
            color: visionRuntimeReady ? "#f9a8d4" : "#fbbf24"
          },
          {
            label: "Tools",
            value: isLoading ? "—" : `${toolCount} active`,
            color: toolCount > 0 ? "#7dd3fc" : "#fb7185"
          }
        ].map((item) => (
          <div
            key={item.label}
            style={{
              borderRadius: 14,
              padding: "10px 12px",
              background: "rgba(4,10,24,0.78)",
              border: "1px solid rgba(255,255,255,0.08)",
              backdropFilter: "blur(4px)"
            }}
          >
            <div
              style={{
                color: item.color,
                fontSize: 11,
                fontWeight: 900,
                textTransform: "uppercase",
                letterSpacing: "0.08em",
                marginBottom: 4
              }}
            >
              {item.label}
            </div>
            <div style={{ color: "white", fontWeight: 800, fontSize: 13 }}>{item.value}</div>
          </div>
        ))}
      </div>

      <div
        style={{
          position: "absolute",
          top: 14,
          left: 14,
          right: 14,
          borderRadius: 14,
          padding: "8px 12px",
          background: "rgba(4,10,24,0.68)",
          border: "1px solid rgba(125,211,252,0.16)",
          backdropFilter: "blur(4px)"
        }}
      >
        <div
          style={{
            color: "#7dd3fc",
            fontSize: 10,
            fontWeight: 900,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            marginBottom: 3
          }}
        >
          Active backend
        </div>
        <div
          style={{
            color: "rgba(255,255,255,0.82)",
            fontSize: 12,
            wordBreak: "break-all"
          }}
        >
          {backendUrl}
        </div>
      </div>
    </div>
  )
}

