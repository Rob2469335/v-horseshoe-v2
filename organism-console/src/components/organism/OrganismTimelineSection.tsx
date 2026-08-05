type TimelinePoint = {
  bucket: string
  event_count: number
  success_count: number
  partial_count: number
  fail_count: number
}

type OrganismTimelineSectionProps = {
  isLoading: boolean
  isError: boolean
  errorMessage: string | null
  timelinePoints: TimelinePoint[]
  totalTimelineSuccess: number
  totalTimelinePartial: number
  totalTimelineFail: number
  latestBucket: string
  activeAccent: string
  width: number
  height: number
  padding: number
  allEventsArea: string
  allEventsLine: string
  successLine: string
  partialLine: string
  failLine: string
  selectedBucket: string | null
  onSelectBucket: (bucket: string | null) => void
}

export function OrganismTimelineSection({
  isLoading,
  isError,
  errorMessage,
  timelinePoints,
  totalTimelineSuccess,
  totalTimelinePartial,
  totalTimelineFail,
  latestBucket,
  activeAccent,
  width,
  height,
  padding,
  allEventsArea,
  allEventsLine,
  successLine,
  partialLine,
  failLine,
  selectedBucket,
  onSelectBucket
}: OrganismTimelineSectionProps) {
  const maxEvents = Math.max(1, ...timelinePoints.map((p) => p.event_count))

  return (
    <article
      style={{
        borderRadius: 26,
        padding: 22,
        background: "linear-gradient(180deg, rgba(8,14,30,0.97), rgba(8,12,24,0.98))",
        border: "1px solid rgba(255,255,255,0.08)",
        boxShadow: "0 20px 56px rgba(0,0,0,0.26)"
      }}
    >
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: 16, marginBottom: 20 }}>
        <div>
          <div
            style={{
              display: "inline-flex",
              padding: "6px 12px",
              borderRadius: 999,
              background: "rgba(125,211,252,0.12)",
              color: activeAccent,
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
            Click a data point to focus on a specific time bucket.
          </p>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(140px, 1fr))", gap: 12, minWidth: 300 }}>
          {[
            { label: selectedBucket ? "Selected bucket" : "Latest bucket", value: selectedBucket ?? latestBucket, color: activeAccent },
            { label: "Success", value: String(totalTimelineSuccess), color: "#4ade80" },
            { label: "Partial", value: String(totalTimelinePartial), color: "#fbbf24" },
            { label: "Fail", value: String(totalTimelineFail), color: "#fb7185" }
          ].map((item) => (
            <div
              key={item.label}
              style={{
                borderRadius: 14, padding: 12, background: "rgba(255,255,255,0.035)", border: "1px solid rgba(255,255,255,0.07)"
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

      {isLoading ? (
        <div style={{ borderRadius: 20, padding: 22, background: "rgba(255,255,255,0.03)", color: "rgba(255,255,255,0.84)" }}>
          Loading timeline chart...
        </div>
      ) : isError ? (
        <div style={{ borderRadius: 20, padding: 22, background: "rgba(251,113,133,0.08)", color: "#ffe4e6", border: "1px solid rgba(251,113,133,0.25)" }}>
          {errorMessage}
        </div>
      ) : timelinePoints.length === 0 ? (
        <div style={{ borderRadius: 20, padding: 22, background: "rgba(255,255,255,0.03)", color: "rgba(255,255,255,0.82)" }}>
          No timeline data is available yet. Once the organism starts recording activity, this chart will come alive.
        </div>
      ) : (
        <>
          <div
            style={{
              width: "100%",
              overflowX: "auto",
              borderRadius: 20, padding: 16, background: "linear-gradient(180deg, rgba(255,255,255,0.03), rgba(0,0,0,0.10))", border: "1px solid rgba(255,255,255,0.07)"
            }}
          >
            <svg
              viewBox={`0 0 ${width} ${height}`}
              onClick={() => onSelectBucket(null)}
              style={{ width: "100%", minWidth: 860, height: 360, cursor: "crosshair" }}
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
                rx={18}
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
                    strokeWidth={1}
                  />
                )
              })}

              {allEventsArea && <polygon points={allEventsArea} fill="url(#eventsArea)" />}
              {allEventsLine && <polyline points={allEventsLine} fill="none" stroke="#38bdf8" strokeWidth={4} strokeLinecap="round" />}
              {successLine && <polyline points={successLine} fill="none" stroke="#4ade80" strokeWidth={3} strokeLinecap="round" />}
              {partialLine && <polyline points={partialLine} fill="none" stroke="#fbbf24" strokeWidth={3} strokeLinecap="round" />}
              {failLine && <polyline points={failLine} fill="none" stroke="#fb7185" strokeWidth={3} strokeLinecap="round" />}

              {timelinePoints.map((point, index) => {
                const x = padding + (index / (timelinePoints.length - 1)) * (width - padding * 2)
                const y = height - padding - (point.event_count / maxEvents) * (height - padding * 2)
                const isSelected = selectedBucket === point.bucket

                return (
                  <g 
                    key={point.bucket} 
                    onClick={(e) => { e.stopPropagation(); onSelectBucket(point.bucket); }}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.stopPropagation(); onSelectBucket(point.bucket); } }}
                    tabIndex={0}
                    role="button"
                    aria-label={`Select bucket ${point.bucket}`}
                    style={{ cursor: "pointer", outline: "none" }}
                  >
                    {isSelected && (
                      <line
                        x1={x}
                        y1={padding}
                        x2={x}
                        y2={height - padding}
                        stroke={activeAccent}
                        strokeWidth={2}
                        strokeDasharray="4 4"
                      />
                    )}
                    <circle
                      cx={x}
                      cy={y}
                      r={isSelected ? 8 : 6}
                      fill={isSelected ? activeAccent : "#38bdf8"}
                      stroke="white"
                      strokeWidth={isSelected ? 3 : 0}
                      style={{ 
                        transition: "all 200ms ease",
                        filter: isSelected ? `drop-shadow(0 0 8px ${activeAccent})` : "none"
                      }}
                    />
                  </g>
                )
              })}
            </svg>
          </div>

          {/* Structured Readout Table */}
          <div style={{ marginTop: 24, overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", color: "white", fontSize: 13, textAlign: "left" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.1)" }}>
                  <th style={{ padding: "12px 16px", color: "rgba(255,255,255,0.4)", fontWeight: 900, textTransform: "uppercase", fontSize: 11 }}>Bucket</th>
                  <th style={{ padding: "12px 16px", color: "rgba(255,255,255,0.4)", fontWeight: 900, textTransform: "uppercase", fontSize: 11 }}>Events</th>
                  <th style={{ padding: "12px 16px", color: "rgba(255,255,255,0.4)", fontWeight: 900, textTransform: "uppercase", fontSize: 11 }}>Success</th>
                  <th style={{ padding: "12px 16px", color: "rgba(255,255,255,0.4)", fontWeight: 900, textTransform: "uppercase", fontSize: 11 }}>Fail</th>
                  <th style={{ padding: "12px 16px", color: "rgba(255,255,255,0.4)", fontWeight: 900, textTransform: "uppercase", fontSize: 11 }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {timelinePoints.slice(-8).map((point) => {
                  const isSelected = selectedBucket === point.bucket
                  return (
                    <tr 
                      key={point.bucket}
                      style={{ 
                        background: isSelected ? `${activeAccent}15` : "transparent",
                        borderBottom: "1px solid rgba(255,255,255,0.05)",
                        transition: "background 200ms ease"
                      }}
                    >
                      <td style={{ padding: "12px 16px", fontWeight: 700 }}>{point.bucket}</td>
                      <td style={{ padding: "12px 16px", fontVariantNumeric: "tabular-nums" }}>{point.event_count}</td>
                      <td style={{ padding: "12px 16px", color: "#4ade80", fontVariantNumeric: "tabular-nums" }}>{point.success_count}</td>
                      <td style={{ padding: "12px 16px", color: "#fb7185", fontVariantNumeric: "tabular-nums" }}>{point.fail_count}</td>
                      <td style={{ padding: "12px 16px" }}>
                        <button
                          type="button"
                          onClick={() => onSelectBucket(isSelected ? null : point.bucket)}
                          style={{
                            background: isSelected ? activeAccent : "rgba(255,255,255,0.06)",
                            border: "1px solid rgba(255,255,255,0.08)", borderRadius: 999, color: "white", padding: "6px 12px",
                            fontSize: 11,
                            fontWeight: 800,
                            cursor: "pointer",
                            transition: "all 150ms ease"
                          }}
                        >
                          {isSelected ? "Selected" : "Select"}
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
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
                    animation: "statusBlink 1.8s ease-in-out infinite",
                    flexShrink: 0
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
  )
}




