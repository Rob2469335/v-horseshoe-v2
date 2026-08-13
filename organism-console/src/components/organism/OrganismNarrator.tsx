import { useEffect, useState } from "react"

interface NarratorEvent {
  id: number
  text: string
  color: string
  ts: string
}

interface Props {
  backendUrl: string
  llamacppReachable: boolean
  statusKnown: boolean
  successRate: number
  eventCount: number
  healingReady: number
  healingKnown: boolean
  healingRating: string | null
  traceCount: number
}

let _eid = 0

export function OrganismNarrator({ backendUrl, llamacppReachable, statusKnown, successRate, eventCount, healingReady, healingKnown, healingRating, traceCount }: Props) {
  const [feed, setFeed] = useState<NarratorEvent[]>([])
  const [heartbeat, setHeartbeat] = useState(0)

  function push(text: string, color = "#7dd3fc") {
    const ts = new Date().toLocaleTimeString()
    setFeed(prev => [{ id: _eid++, text, color, ts }, ...prev].slice(0, 12))
  }

  useEffect(() => {
    push("🧠 Organism console initialised. All subsystems coming online.", "#a78bfa")
  }, [])

  useEffect(() => {
    if (!statusKnown) return
    if (llamacppReachable) push("✅ Llama.cpp brain connected. Inference engine is live.", "#22c55e")
    else push("⚠️ Llama.cpp brain offline. Generation will fail until reconnected.", "#f87171")
  }, [llamacppReachable, statusKnown])

  useEffect(() => {
    if (traceCount > 0) push(`📡 ${traceCount} trace events detected in this session.`, "#7dd3fc")
  }, [traceCount])

  useEffect(() => {
    if (successRate > 0) push(`🎯 Critic acceptance rate: ${successRate}%. ${successRate >= 80 ? "System performing strongly." : successRate >= 60 ? "System stable." : "Review failure pressure."}`, successRate >= 80 ? "#22c55e" : successRate >= 60 ? "#fbbf24" : "#f87171")
  }, [successRate])

  useEffect(() => {
    if (healingKnown && healingReady < 80) push(`🔧 Healing readiness ${healingReady}%. Self-repair may be needed.`, "#f87171")
  }, [healingReady, healingKnown])

  // Live heartbeat every 30s
  useEffect(() => {
    async function beat() {
      try {
        const res = await fetch(`${backendUrl}/traces?limit=3`)
        const data = await res.json()
        const traces = data.traces ?? []
        if (traces.length > 0) {
          const last = traces[0]
          const model = last.model ?? "unknown"
          const status = last.status ?? "unknown"
          const phase = last.phase ?? "unknown"
          const msgs: Record<string, string> = {
            router: `🔀 Router selected ${model} for a ${phase} task.`,
            generator: `⚡ Generator completed via ${model}. Status: ${status}.`,
            critic: `🎯 Critic evaluated response. Verdict: ${status}.`,
            planner: `📋 Planner mapped a new task sequence.`,
          }
          push(msgs[phase] ?? `📡 ${phase} phase completed on ${model}. Status: ${status}.`, status.includes("accept") || status.includes("success") || status.includes("complet") ? "#22c55e" : "#fbbf24")
        } else {
          const heartbeats = [
            "💓 Organism heartbeat nominal.",
            `🧬 Memory holding ${eventCount.toLocaleString()} events.`,
            "🔄 Router on standby. Ready to route next generation request.",
            `⚡ ${statusKnown ? (llamacppReachable ? "Llama.cpp reachable" : "Llama.cpp offline") : "Llama.cpp status checking…"}.`,
            `🛡️ Healing readiness ${healingKnown ? `${healingReady}% (${healingRating ?? "—"})` : "checking…"}.`,
          ]
          push(heartbeats[Math.floor(Math.random() * heartbeats.length)], "#7dd3fc")
        }
      } catch {
        push("📡 Heartbeat check failed. Backend may be restarting.", "#f59e0b")
      }
      setHeartbeat(h => h + 1)
    }
    const t = setInterval(beat, 12000)
    beat()
    return () => clearInterval(t)
  }, [backendUrl, llamacppReachable, eventCount, statusKnown, healingReady, healingKnown, healingRating])

  return (
    <div style={{
      background: "rgba(3,7,20,0.8)",
      border: "1px solid rgba(125,211,252,0.15)",
      borderRadius: 20,
      padding: "14px 18px",
      marginBottom: 16,
      backdropFilter: "blur(12px)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#22c55e", boxShadow: "0 0 12px #22c55e", animation: "pulse 2s ease-in-out infinite", display: "inline-block" }} />
        <span style={{ fontSize: 11, fontWeight: 700, color: "rgba(255,255,255,0.45)", textTransform: "uppercase", letterSpacing: "0.12em" }}>Live organism narrator</span>
        <span style={{ fontSize: 11, color: "rgba(255,255,255,0.25)", marginLeft: "auto" }}>heartbeat #{heartbeat}</span>
      </div>
      <div style={{ display: "grid", gap: 6, maxHeight: 180, overflowY: "auto" }}>
        {feed.map((item, i) => (
          <div key={item.id} style={{ display: "flex", gap: 10, alignItems: "flex-start", opacity: 1 - i * 0.07, transition: "opacity 0.3s" }}>
            <span style={{ fontSize: 11, color: "rgba(255,255,255,0.25)", flexShrink: 0, marginTop: 2 }}>{item.ts}</span>
            <span style={{ fontSize: 13, color: item.color, lineHeight: 1.5 }}>{item.text}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
