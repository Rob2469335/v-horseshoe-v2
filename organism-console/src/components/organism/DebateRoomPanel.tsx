import { useState } from "react"

interface Props {
  backendUrl: string
}

export function DebateRoomPanel({ backendUrl }: Props) {
  const [goal, setGoal] = useState("")
  const [loading, setLoading] = useState(false)
  const [activeTab, setActiveTab] = useState<"proposal" | "critique" | "synthesis">("synthesis")
  const [result, setResult] = useState<{
    proposal: string
    critique: string
    synthesis: string
  } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [phase, setPhase] = useState<string>("")

  const runDebate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!goal.trim()) return

    setLoading(true)
    setError(null)
    setResult({ proposal: "", critique: "", synthesis: "" })
    setPhase("Initiating collaborative debate...")

    try {
      const response = await fetch(`${backendUrl}/features/debate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal })
      })

      if (!response.ok) {
        throw new Error(`Failed to initiate debate: ${response.statusText}`)
      }

      if (!response.body) {
        throw new Error("API returned an empty response stream.")
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ""

      while (true) {
        const { value, done } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split("\n\n")
        buffer = lines.pop() || ""

        for (const line of lines) {
          const trimmed = line.trim()
          if (trimmed.startsWith("data: ")) {
            try {
              const data = JSON.parse(trimmed.slice(6))
              
              if (data.phase === "status") {
                setPhase(data.message)
              } else if (data.phase === "proposal") {
                setResult(prev => ({
                  proposal: (prev?.proposal || "") + data.content,
                  critique: prev?.critique || "",
                  synthesis: prev?.synthesis || ""
                }))
                setActiveTab("proposal")
              } else if (data.phase === "critique") {
                setResult(prev => ({
                  proposal: prev?.proposal || "",
                  critique: (prev?.critique || "") + data.content,
                  synthesis: prev?.synthesis || ""
                }))
                setActiveTab("critique")
              } else if (data.phase === "synthesis") {
                setResult(prev => ({
                  proposal: prev?.proposal || "",
                  critique: prev?.critique || "",
                  synthesis: (prev?.synthesis || "") + data.content
                }))
                setActiveTab("synthesis")
              } else if (data.phase === "error") {
                throw new Error(data.message || "An error occurred during streaming.")
              }
            } catch (err) {
              console.error("Failed to parse SSE line:", trimmed, err)
            }
          }
        }
      }
    } catch (err: any) {
      setError(err.message || "An unexpected error occurred during the swarm debate.")
    } finally {
      setLoading(false)
      setPhase("")
    }
  }

  const isResultEmpty = !result || (!result.proposal && !result.critique && !result.synthesis)

  return (
    <div style={{
      background: "rgba(255, 255, 255, 0.03)",
      border: "1px solid rgba(255, 255, 255, 0.08)",
      borderRadius: 20,
      padding: "20px",
      boxShadow: "0 14px 28px rgba(0,0,0,0.18)",
      display: "grid",
      gap: 16
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: "rgba(255, 255, 255, 0.45)", textTransform: "uppercase", letterSpacing: "0.1em" }}>
            🐝 Swarm Collaborative Agent Debate
          </div>
          <div style={{ fontSize: 14, color: "rgba(255,255,255,0.8)", fontWeight: 600, marginTop: 4 }}>
            Debate proposals between Planner, Reviewer, and Coordinator roles.
          </div>
        </div>
        {loading && (
          <span style={{ fontSize: 12, color: "#a78bfa", fontWeight: 700, animation: "statusBlink 1.5s infinite" }}>
            ⚡ DEBATE STREAMING
          </span>
        )}
      </div>

      <form onSubmit={runDebate} style={{ display: "flex", gap: 10 }}>
        <input
          type="text"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="Enter a development goal to debate (e.g. Optimize cache invalidation latency)..."
          disabled={loading}
          style={{
            flex: 1,
            background: "rgba(0,0,0,0.35)",
            border: "1px solid rgba(255,255,255,0.12)",
            borderRadius: 10,
            padding: "10px 14px",
            color: "white",
            fontSize: 13,
            outline: "none"
          }}
        />
        <button
          type="submit"
          disabled={loading || !goal.trim()}
          style={{
            padding: "10px 20px",
            borderRadius: 10,
            border: "none",
            background: loading ? "rgba(255,255,255,0.1)" : "linear-gradient(135deg, #a78bfa, #818cf8)",
            color: "white",
            fontWeight: 700,
            fontSize: 13,
            cursor: loading ? "not-allowed" : "pointer",
            boxShadow: loading ? "none" : "0 4px 12px rgba(139,92,246,0.25)"
          }}
        >
          {loading ? "Streaming..." : "Initiate Debate"}
        </button>
      </form>

      {phase && (
        <div style={{
          padding: "12px 16px",
          borderRadius: 12,
          background: "rgba(167,139,250,0.08)",
          border: "1px solid rgba(167,139,250,0.2)",
          color: "#c084fc",
          fontSize: 13,
          fontWeight: 500,
          display: "flex",
          alignItems: "center",
          gap: 10
        }}>
          <span style={{ animation: "pulseHalo 2s infinite", display: "inline-block" }}>💡</span>
          {phase}
        </div>
      )}

      {error && (
        <div style={{
          padding: "12px 16px",
          borderRadius: 12,
          background: "rgba(239,68,68,0.08)",
          border: "1px solid rgba(239,68,68,0.2)",
          color: "#fca5a5",
          fontSize: 13
        }}>
          ❌ {error}
        </div>
      )}

      {!isResultEmpty && result && (
        <div style={{ display: "grid", gap: 12 }}>
          <div style={{
            display: "flex",
            gap: 4,
            borderBottom: "1px solid rgba(255,255,255,0.08)",
            paddingBottom: 6
          }}>
            {[
              { id: "proposal", label: "Planner Proposal", accent: "#fbbf24" },
              { id: "critique", label: "Reviewer Critique", accent: "#f472b6" },
              { id: "synthesis", label: "Coordinator Synthesis", accent: "#34d399" }
            ].map((tab) => {
              const isActive = activeTab === tab.id
              const hasContent = result[tab.id as keyof typeof result] !== ""
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setActiveTab(tab.id as any)}
                  style={{
                    padding: "8px 16px",
                    background: isActive ? "rgba(255,255,255,0.06)" : "transparent",
                    border: "none",
                    borderBottom: isActive ? `2px solid ${tab.accent}` : "none",
                    color: isActive ? "white" : hasContent ? "rgba(255,255,255,0.76)" : "rgba(255,255,255,0.3)",
                    fontSize: 12,
                    fontWeight: 700,
                    cursor: "pointer",
                    transition: "all 200ms ease"
                  }}
                >
                  {tab.label} {hasContent && !isActive && "●"}
                </button>
              )
            })}
          </div>

          <div style={{
            background: "rgba(0,0,0,0.3)",
            borderRadius: 12,
            padding: "16px",
            border: "1px solid rgba(255,255,255,0.05)",
            maxHeight: "300px",
            overflowY: "auto"
          }}>
            <pre style={{
              margin: 0,
              fontSize: 12,
              lineHeight: 1.6,
              color: "white",
              fontFamily: 'Consolas, Monaco, "Andale Mono", monospace',
              whiteSpace: "pre-wrap"
            }}>
              {result[activeTab] || "Formulating..."}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}
