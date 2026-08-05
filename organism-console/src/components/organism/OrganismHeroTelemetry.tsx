type Stat = {
  label: string
  value: string
  accent: string
  glow: string
}

type OrganismHeroTelemetryProps = {
  heroStats: Stat[]
  systemReady: boolean
}

export function OrganismHeroTelemetry({ heroStats, systemReady }: OrganismHeroTelemetryProps) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateRows: "repeat(4, minmax(0, 1fr))",
        gap: 10
      }}
    >
      {heroStats.map((stat) => (
        <div
          key={stat.label}
          style={{
            position: "relative",
            overflow: "hidden",
            borderRadius: 20,
            padding: "12px 14px",
            background: "linear-gradient(145deg, rgba(255,255,255,0.06), rgba(255,255,255,0.03))",
            border: "1px solid rgba(255,255,255,0.09)"
          }}
        >
          <div
            style={{
              position: "absolute",
              top: -20,
              right: -8,
              width: 80,
              height: 80,
              borderRadius: "50%",
              background: `${stat.accent}20`,
              filter: "blur(12px)",
              animation: "pulseHalo 3.5s ease-in-out infinite"
            }}
          />

          <div style={{ position: "relative", zIndex: 1 }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 7,
                color: stat.accent,
                fontSize: 11,
                fontWeight: 900,
                textTransform: "uppercase",
                letterSpacing: "0.08em",
                marginBottom: 6
              }}
            >
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  background: stat.accent,
                  boxShadow: `0 0 12px ${stat.accent}`,
                  animation: "statusBlink 1.8s ease-in-out infinite"
                }}
              />
              {stat.label}
            </div>

            <div style={{ color: "white", fontSize: 26, fontWeight: 900, lineHeight: 1 }}>
              {stat.value}
            </div>
          </div>
        </div>
      ))}

      <div
        style={{
          borderRadius: 20,
          padding: "12px 14px",
          background: systemReady
            ? "linear-gradient(145deg, rgba(74,222,128,0.12), rgba(74,222,128,0.05))"
            : "linear-gradient(145deg, rgba(251,191,36,0.12), rgba(251,191,36,0.05))",
          border: systemReady
            ? "1px solid rgba(74,222,128,0.28)"
            : "1px solid rgba(251,191,36,0.28)"
        }}
      >
        <div
          style={{
            color: systemReady ? "#4ade80" : "#fbbf24",
            fontSize: 11,
            fontWeight: 900,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            marginBottom: 6
          }}
        >
          System posture
        </div>
        <div style={{ color: "white", fontSize: 16, fontWeight: 800 }}>
          {systemReady ? "All systems nominal" : "Attention required"}
        </div>
      </div>
    </div>
  )
}

