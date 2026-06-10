type StatTileProps = {
  label: string
  value: string
  tone: string
  detail: string
}

export function StatTile({ label, value, tone, detail }: StatTileProps) {
  return (
    <article
      style={{
        position: "relative",
        overflow: "hidden",
        borderRadius: 20,
        padding: 18,
        background: "linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.03))",
        border: "1px solid rgba(255,255,255,0.08)",
        minHeight: 142,
        boxShadow: "0 16px 40px rgba(0,0,0,0.18)",
        animation: "floatCard 8s ease-in-out infinite"
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: "auto -10px -30px auto",
          width: 120,
          height: 120,
          borderRadius: "50%",
          background: `${tone}18`,
          filter: "blur(12px)"
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "linear-gradient(120deg, transparent 0%, rgba(255,255,255,0.06) 48%, transparent 100%)",
          transform: "translateX(-120%)",
          animation: "scanSweep 5s linear infinite",
          pointerEvents: "none"
        }}
      />
      <div style={{ position: "relative", zIndex: 1 }}>
        <div
          style={{
            color: tone,
            fontSize: 12,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            fontWeight: 800,
            marginBottom: 10
          }}
        >
          {label}
        </div>
        <div style={{ color: "white", fontSize: 30, fontWeight: 900, lineHeight: 1.05, marginBottom: 8 }}>
          {value}
        </div>
        <div style={{ color: "rgba(255,255,255,0.70)", lineHeight: 1.55 }}>{detail}</div>
      </div>
    </article>
  )
}
