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
        borderRadius: 18,
        padding: 18,
        background: "linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.025))",
        border: "1px solid rgba(255,255,255,0.08)",
        minHeight: 132,
        boxShadow: "0 12px 30px rgba(0,0,0,0.14)",
        
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: "auto -14px -36px auto",
          width: 110,
          height: 110,
          borderRadius: "50%",
          background: `${tone}18`,
          filter: "blur(14px)"
        }}
      />
      <div style={{ position: "relative", zIndex: 1 }}>
        <div
          style={{
            color: tone,
            fontSize: 11,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            fontWeight: 800,
            marginBottom: 10
          }}
        >
          {label}
        </div>
        <div style={{ color: "white", fontSize: 28, fontWeight: 900, lineHeight: 1.05, marginBottom: 8, fontVariantNumeric: "tabular-nums" }}>
          {value}
        </div>
        <div style={{ color: "rgba(255,255,255,0.68)", lineHeight: 1.55, fontSize: 14 }}>{detail}</div>
      </div>
    </article>
  )
}

