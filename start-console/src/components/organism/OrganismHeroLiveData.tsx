export type TimelinePoint = {
  bucket: string
  event_count: number
  success_count: number
  partial_count: number
  fail_count: number
}

type LiveDataProps = {
  points: TimelinePoint[]
  isLoading: boolean
  activeTheme: {
    accent: string
    glow: string
  }
}

export function OrganismHeroLiveData({
  points,
  isLoading,
  activeTheme
}: LiveDataProps) {
  const safePoints = points.length > 0
    ? points.slice(-18)
    : Array.from({ length: 18 }, (_, index) => ({
        bucket: `slot-${index}`,
        event_count: 0,
        success_count: 0,
        partial_count: 0,
        fail_count: 0
      }))

  const maxEvents = Math.max(1, ...safePoints.map((point) => point.event_count))
  const maxOutcomes = Math.max(
    1,
    ...safePoints.map((point) => point.success_count + point.partial_count + point.fail_count)
  )

  const eventPath = safePoints
    .map((point, index) => {
      const x = (index / Math.max(1, safePoints.length - 1)) * 100
      const y = 88 - (point.event_count / maxEvents) * 62
      return `${index === 0 ? "M" : "L"} ${x} ${y}`
    })
    .join(" ")

  const areaPath = `${eventPath} L 100 96 L 0 96 Z`

  const statusLabel = isLoading
    ? "Streaming timeline..."
    : points.length > 0
      ? "Live timeline feed active"
      : "Waiting for timeline data"

  return (
    <div
      style={{
        borderRadius: 28,
        padding: 16,
        background: "linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0.04))",
        border: "1px solid rgba(255,255,255,0.10)",
        boxShadow: "0 24px 56px rgba(0,0,0,0.26)",
        transform: "translateZ(32px)"
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          marginBottom: 12
        }}
      >
        <div>
          <div style={{ color: "rgba(255,255,255,0.64)", fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.1em" }}>
            Live timeline chamber
          </div>
          <div style={{ color: "white", fontSize: 18, fontWeight: 800, marginTop: 4 }}>
            {statusLabel}
          </div>
        </div>

        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 12px",
            borderRadius: 999,
            background: "rgba(255,255,255,0.05)",
            border: "1px solid rgba(255,255,255,0.10)",
            color: "rgba(255,255,255,0.86)",
            fontSize: 12,
            fontWeight: 700
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
          5-10s refresh ready
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1.24fr 0.76fr",
          gap: 14,
          alignItems: "stretch"
        }}
      >
        <div
          style={{
            borderRadius: 22,
            padding: 14,
            background: "rgba(4,10,24,0.68)",
            border: "1px solid rgba(255,255,255,0.08)",
            boxShadow: `0 0 40px ${activeTheme.glow}`
          }}
        >
          <div style={{ color: "rgba(255,255,255,0.60)", fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 10 }}>
            Event throughput
          </div>

          <div
            style={{
              position: "relative",
              height: 220,
              borderRadius: 18,
              overflow: "hidden",
              background: `
                linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01)),
                linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px),
                linear-gradient(180deg, rgba(255,255,255,0.03) 1px, transparent 1px)
              `,
              backgroundSize: "auto, 14.285% 100%, 100% 25%"
            }}
          >
            <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>
              <defs>
                <linearGradient id="liveAreaGradient" x1="0%" y1="0%" x2="0%" y2="100%">
                  <stop offset="0%" stopColor="rgba(56,189,248,0.55)" />
                  <stop offset="100%" stopColor="rgba(56,189,248,0.02)" />
                </linearGradient>
                <linearGradient id="liveLineGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor="#38bdf8" />
                  <stop offset="50%" stopColor={activeTheme.accent} />
                  <stop offset="100%" stopColor="#f472b6" />
                </linearGradient>
              </defs>

              <path d={areaPath} fill="url(#liveAreaGradient)" opacity={0.95} />
              <path d={eventPath} fill="none" stroke="url(#liveLineGradient)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />

              {safePoints.map((point, index) => {
                const x = (index / Math.max(1, safePoints.length - 1)) * 100
                const y = 88 - (point.event_count / maxEvents) * 62
                return (
                  <circle
                    key={`${point.bucket}-${index}`}
                    cx={x}
                    cy={y}
                    r={index === safePoints.length - 1 ? 2.5 : 1.6}
                    fill={index === safePoints.length - 1 ? "#ffffff" : activeTheme.accent}
                    style={{
                      filter: `drop-shadow(0 0 8px ${activeTheme.accent})`
                    }}
                  />
                )
              })}
            </svg>

            <div
              style={{
                position: "absolute",
                inset: 0,
                background: "linear-gradient(110deg, transparent 0%, rgba(255,255,255,0.09) 46%, transparent 56%)",
                transform: "translateX(-100%)",
                animation: "scanSweep 7.2s linear infinite"
              }}
            />
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(6, minmax(0, 1fr))",
              gap: 6,
              marginTop: 10
            }}
          >
            {safePoints.slice(-6).map((point, index) => (
              <div
                key={`${point.bucket}-mini-${index}`}
                style={{
                  borderRadius: 14,
                  padding: 10,
                  background: "rgba(255,255,255,0.03)",
                  border: "1px solid rgba(255,255,255,0.07)"
                }}
              >
                <div style={{ color: "rgba(255,255,255,0.54)", fontSize: 10, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 6 }}>
                  {index + 1}
                </div>
                <div style={{ color: "white", fontSize: 16, fontWeight: 800 }}>{point.event_count}</div>
              </div>
            ))}
          </div>
        </div>

        <div
          style={{
            display: "grid",
            gap: 12
          }}
        >
          <div
            style={{
              borderRadius: 22,
              padding: 14,
              background: "rgba(4,10,24,0.68)",
              border: "1px solid rgba(255,255,255,0.08)"
            }}
          >
            <div style={{ color: "rgba(255,255,255,0.60)", fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 10 }}>
              Outcome mix
            </div>

            <div style={{ display: "grid", gap: 10 }}>
              {safePoints.slice(-5).map((point, index) => {
                const total = Math.max(1, point.success_count + point.partial_count + point.fail_count)
                const successWidth = (point.success_count / total) * 100
                const partialWidth = (point.partial_count / total) * 100
                const failWidth = (point.fail_count / total) * 100

                return (
                  <div key={`${point.bucket}-mix-${index}`} style={{ display: "grid", gap: 6 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, color: "rgba(255,255,255,0.74)", fontSize: 12 }}>
                      <span>Bucket {index + 1}</span>
                      <span>{point.success_count + point.partial_count + point.fail_count}</span>
                    </div>

                    <div
                      style={{
                        display: "flex",
                        overflow: "hidden",
                        borderRadius: 999,
                        height: 10,
                        background: "rgba(255,255,255,0.05)",
                        border: "1px solid rgba(255,255,255,0.06)"
                      }}
                    >
                      <div style={{ width: `${successWidth}%`, background: "#4ade80" }} />
                      <div style={{ width: `${partialWidth}%`, background: "#fbbf24" }} />
                      <div style={{ width: `${failWidth}%`, background: "#f472b6" }} />
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          <div
            style={{
              borderRadius: 22,
              padding: 14,
              background: "rgba(4,10,24,0.68)",
              border: "1px solid rgba(255,255,255,0.08)"
            }}
          >
            <div style={{ color: "rgba(255,255,255,0.60)", fontSize: 11, fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 10 }}>
              Bucket bars
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(9, minmax(0, 1fr))", gap: 6, alignItems: "end", height: 132 }}>
              {safePoints.slice(-9).map((point, index) => {
                const total = point.success_count + point.partial_count + point.fail_count
                const successHeight = (point.success_count / maxOutcomes) * 100
                const partialHeight = (point.partial_count / maxOutcomes) * 100
                const failHeight = (point.fail_count / maxOutcomes) * 100

                return (
                  <div key={`${point.bucket}-bar-${index}`} style={{ display: "flex", flexDirection: "column", justifyContent: "end", gap: 3, height: "100%" }}>
                    <div
                      style={{
                        display: "flex",
                        flexDirection: "column",
                        justifyContent: "end",
                        borderRadius: 999,
                        overflow: "hidden",
                        background: "rgba(255,255,255,0.04)",
                        border: "1px solid rgba(255,255,255,0.05)",
                        height: "100%"
                      }}
                    >
                      <div style={{ height: `${failHeight}%`, background: "#f472b6" }} />
                      <div style={{ height: `${partialHeight}%`, background: "#fbbf24" }} />
                      <div style={{ height: `${successHeight}%`, background: "#4ade80" }} />
                    </div>
                    <div style={{ color: "rgba(255,255,255,0.6)", textAlign: "center", fontSize: 10, fontWeight: 700 }}>
                      {total}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
