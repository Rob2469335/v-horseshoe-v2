import { useState } from "react"

const HINTS = [
  {
    icon: "◉",
    color: "#38bdf8",
    title: "What you are seeing",
    text: "This hero pulls live timeline data from your organism backend and renders it as a real-time chart. The orbit rings and pulse nodes are wired to actual system state, not static decoration."
  },
  {
    icon: "⟳",
    color: "#4ade80",
    title: "How the organism learns",
    text: "Each timeline event is a learning signal. When successful outcomes outnumber failures, the organism has enough evidence to improve routing decisions on the next cycle."
  },
  {
    icon: "⬡",
    color: "#c4b5fd",
    title: "What the 3D depth means",
    text: "Move your mouse over this hero. The cards shift in 3D space as you move. The higher floating elements are meant to pull operator attention first."
  },
  {
    icon: "◈",
    color: "#fbbf24",
    title: "When to watch the bars",
    text: "The stacked outcome bars show success, partial, and failure for each time bucket. More green with less pink usually means the organism is stabilizing."
  }
]

export function OrganismHeroHints() {
  const [active, setActive] = useState(0)
  const hint = HINTS[active]

  return (
    <div
      style={{
        borderRadius: 22,
        padding: 16,
        background: "linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.03))",
        border: "1px solid rgba(255,255,255,0.09)"
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 12,
          gap: 10
        }}
      >
        <div
          style={{
            color: "rgba(255,255,255,0.56)",
            fontSize: 11,
            fontWeight: 900,
            textTransform: "uppercase",
            letterSpacing: "0.09em"
          }}
        >
          Beginner guide
        </div>

        <div style={{ display: "flex", gap: 6 }}>
          {HINTS.map((h, i) => (
            <button
              key={i}
              type="button"
              onClick={() => setActive(i)}
              style={{
                width: 26,
                height: 26,
                borderRadius: "50%",
                border: i === active ? `1px solid ${h.color}` : "1px solid rgba(255,255,255,0.14)",
                background: i === active ? `${h.color}22` : "rgba(255,255,255,0.04)",
                color: h.color,
                fontSize: 12,
                cursor: "pointer",
                display: "grid",
                placeItems: "center",
                transition: "all 180ms ease"
              }}
            >
              {h.icon}
            </button>
          ))}
        </div>
      </div>

      <div
        key={active}
        style={{
          animation: "tutorReveal 200ms cubic-bezier(0.16,1,0.3,1)"
        }}
      >
        <div
          style={{
            color: hint.color,
            fontSize: 13,
            fontWeight: 900,
            marginBottom: 6
          }}
        >
          {hint.title}
        </div>
        <div
          style={{
            color: "rgba(220,230,255,0.82)",
            fontSize: 13,
            lineHeight: 1.65
          }}
        >
          {hint.text}
        </div>
      </div>
    </div>
  )
}
