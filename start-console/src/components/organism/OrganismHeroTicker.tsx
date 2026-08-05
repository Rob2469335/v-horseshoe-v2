type OrganismHeroTickerProps = {
  ticker: string[]
  activeTheme: { accent: string }
}

export function OrganismHeroTicker({ ticker, activeTheme }: OrganismHeroTickerProps) {
  return (
    <div
      style={{
        position: "relative",
        overflow: "hidden",
        borderRadius: 20,
        padding: "10px 0",
        background: "rgba(255,255,255,0.04)",
        border: "1px solid rgba(255,255,255,0.08)"
      }}
    >
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          bottom: 0,
          width: 44,
          background: "linear-gradient(90deg, rgba(4,8,22,0.95), transparent)",
          zIndex: 2,
          pointerEvents: "none"
        }}
      />
      <div
        style={{
          position: "absolute",
          right: 0,
          top: 0,
          bottom: 0,
          width: 44,
          background: "linear-gradient(270deg, rgba(4,8,22,0.95), transparent)",
          zIndex: 2,
          pointerEvents: "none"
        }}
      />

      <div
        style={{
          display: "flex",
          width: "max-content",
          animation: "tickerMove 22s linear infinite"
        }}
      >
        {ticker.map((item, index) => (
          <div
            key={`tick-${index}`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 10,
              padding: "0 20px",
              whiteSpace: "nowrap",
              fontSize: 12,
              fontWeight: 800,
              textTransform: "uppercase",
              letterSpacing: "0.07em",
              color: "rgba(255,255,255,0.80)"
            }}
          >
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                background:
                  index % 6 === 0 ? "#38bdf8" :
                  index % 6 === 1 ? "#86efac" :
                  index % 6 === 2 ? "#fbbf24" :
                  index % 6 === 3 ? "#f9a8d4" :
                  index % 6 === 4 ? "#c4b5fd" :
                  activeTheme.accent,
                boxShadow: "0 0 10px currentColor",
                flexShrink: 0
              }}
            />
            {item}
          </div>
        ))}
      </div>
    </div>
  )
}
