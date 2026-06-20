import { useState } from "react"

interface Sparkpoint { v: number }

interface Props {
  id: string
  label: string
  color: string
  health: number
  activity: number
  sublabel: string
  backendUrl: string
  spark?: Sparkpoint[]
  prompt: string
}

export function SubsystemCard({ id, label, color, health, activity, sublabel, backendUrl, spark = [], prompt }: Props) {
  const [open, setOpen] = useState(false)
  const [response, setResponse] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function ask() {
    if (response) { setOpen(o => !o); return }
    setOpen(true); setLoading(true)
    try {
      const res = await fetch(`${backendUrl}/generate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt }) })
      const data = await res.json()
      setResponse(data.content ?? "No response")
    } catch(e) { setResponse(`Error: ${e}`) }
    finally { setLoading(false) }
  }

  const breatheAmt = 0.4 + (activity / 100) * 0.6
  const sparkMax = Math.max(...spark.map(s => s.v), 1)
  const sparkW = 80, sparkH = 28

  return (
    <div style={{
      background: `rgba(10,16,32,0.85)`,
      border: `1px solid ${open ? color : color + "33"}`,
      borderRadius: 18,
      padding: "14px 16px",
      cursor: "pointer",
      transition: "all 0.2s",
      boxShadow: open ? `0 0 28px ${color}33` : "none",
      position: "relative",
      overflow: "hidden",
    }} onClick={ask}>
      {/* breathing glow */}
      <div style={{
        position: "absolute", inset: 0, borderRadius: 18,
        background: `radial-gradient(circle at 50% 50%, ${color}${Math.round(breatheAmt * 20).toString(16).padStart(2,"0")}, transparent 70%)`,
        pointerEvents: "none",
        animation: `breathe-${id} ${2 + (1 - activity / 100)}s ease-in-out infinite`,
      }} />

      <div style={{ position: "relative" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8 }}>
          <div>
            <div style={{ fontSize: 10, color: color, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 2 }}>{id}</div>
            <div style={{ fontSize: 17, color: "white", fontWeight: 800 }}>{label}</div>
            <div style={{ fontSize: 12, color: "rgba(255,255,255,0.45)" }}>{sublabel}</div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: 22, fontWeight: 800, color: health > 70 ? "#22c55e" : health > 40 ? "#fbbf24" : "#f87171" }}>{health}%</div>
            <div style={{ fontSize: 11, color: "rgba(255,255,255,0.4)" }}>health</div>
          </div>
        </div>

        {/* sparkline */}
        {spark.length > 1 && (
          <svg width={sparkW} height={sparkH} style={{ display: "block", marginBottom: 8 }}>
            <polyline
              points={spark.map((s, i) => `${(i / (spark.length - 1)) * sparkW},${sparkH - (s.v / sparkMax) * sparkH}`).join(" ")}
              fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" opacity="0.7"
            />
          </svg>
        )}

        {/* activity bar */}
        <div style={{ height: 3, borderRadius: 999, background: "rgba(255,255,255,0.08)", overflow: "hidden", marginBottom: 8 }}>
          <div style={{ height: "100%", width: `${activity}%`, background: color, borderRadius: 999, transition: "width 0.5s" }} />
        </div>

        <div style={{ fontSize: 11, color: color, opacity: 0.7 }}>
          {open ? "▲ click to collapse" : "▼ click to ask what I am doing"}
        </div>

        {open && (
          <div style={{ marginTop: 10, paddingTop: 10, borderTop: `1px solid ${color}33`, fontSize: 13, color: "rgba(220,235,255,0.85)", lineHeight: 1.7 }}>
            {loading ? <span style={{ color }}>⏳ Asking {label}...</span> : response}
          </div>
        )}
      </div>
    </div>
  )
}
