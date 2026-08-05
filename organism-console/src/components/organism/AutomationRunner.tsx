import { useState } from "react"
import { RvFinderRunner } from "./RvFinderRunner"

interface Props {
  backendUrl: string
  automationId: string
  automationTitle: string
  prompt: string
  example: string
  inputs: string[]
}

interface RunResult {
  status: "success" | "error"
  model?: string
  content?: string
  message?: string
  duration_ms?: number
}

export function AutomationRunner({ backendUrl, automationId, automationTitle, prompt, example }: Props) {
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<RunResult | null>(null)
  const [userInput, setUserInput] = useState("")

  if (automationId === "used-rv-finder") {
    return <RvFinderRunner backendUrl={backendUrl} />
  }

  async function run() {
    const finalPrompt = prompt.replace("{input}", userInput.trim() || example)
    setRunning(true)
    setResult(null)
    const start = Date.now()
    try {
      const res = await fetch(`${backendUrl}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: finalPrompt })
      })
      const data = await res.json()
      setResult({ status: "success", model: data.model, content: data.content || data.response, duration_ms: Date.now() - start })
    } catch (e) {
      setResult({ status: "error", message: String(e), duration_ms: Date.now() - start })
    } finally {
      setRunning(false)
    }
  }

  return (
    <div style={{ marginTop: 20, borderTop: "2px solid rgba(251,191,36,0.3)", paddingTop: 20 }}>

      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
        <span style={{ fontSize: 18 }}>▶</span>
        <div>
          <div style={{ fontSize: 14, fontWeight: 800, color: "#fbbf24", textTransform: "uppercase", letterSpacing: "0.08em" }}>Run this automation</div>
          <div style={{ fontSize: 12, color: "rgba(255,255,255,0.5)", marginTop: 2 }}>Type your input below or leave blank to use the example</div>
        </div>
      </div>

      {/* Result appears FIRST so it's visible */}
      {result && (
        <div style={{
          marginBottom: 16,
          borderRadius: 16,
          background: result.status === "success" ? "rgba(34,197,94,0.08)" : "rgba(239,68,68,0.08)",
          border: `2px solid ${result.status === "success" ? "rgba(34,197,94,0.4)" : "rgba(239,68,68,0.4)"}`,
          padding: "16px 18px",
          boxShadow: result.status === "success" ? "0 0 32px rgba(34,197,94,0.15)" : "0 0 32px rgba(239,68,68,0.15)"
        }}>
          {result.status === "success" ? (
            <>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 16 }}>✅</span>
                  <span style={{ fontSize: 13, color: "#22c55e", fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.08em" }}>Automation complete</span>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 12, color: "rgba(255,255,255,0.5)" }}>{result.model}</div>
                  <div style={{ fontSize: 11, color: "rgba(255,255,255,0.3)" }}>{((result.duration_ms ?? 0) / 1000).toFixed(1)}s</div>
                </div>
              </div>
              <div style={{
                fontSize: 14,
                color: "rgba(225,245,225,0.92)",
                lineHeight: 1.85,
                whiteSpace: "pre-wrap",
                background: "rgba(0,0,0,0.3)",
                borderRadius: 12,
                padding: "14px 16px",
                maxHeight: 400,
                overflowY: "auto",
                fontFamily: "inherit"
              }}>
                {result.content}
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                <button onClick={() => { setResult(null); setUserInput("") }}
                  style={{ padding: "7px 16px", borderRadius: 10, border: "1px solid rgba(255,255,255,0.15)", background: "transparent", color: "rgba(255,255,255,0.6)", fontSize: 12, cursor: "pointer" }}>
                  🔄 Run again
                </button>
                <button onClick={() => navigator.clipboard?.writeText(result.content ?? "")}
                  style={{ padding: "7px 16px", borderRadius: 10, border: "1px solid rgba(255,255,255,0.15)", background: "transparent", color: "rgba(255,255,255,0.6)", fontSize: 12, cursor: "pointer" }}>
                  📋 Copy result
                </button>
              </div>
            </>
          ) : (
            <div>
              <div style={{ fontSize: 13, color: "#f87171", fontWeight: 700, marginBottom: 6 }}>❌ Automation failed</div>
              <div style={{ fontSize: 13, color: "rgba(255,200,200,0.8)" }}>{result.message}</div>
              <button onClick={() => setResult(null)} style={{ marginTop: 10, padding: "6px 14px", borderRadius: 10, border: "1px solid rgba(239,68,68,0.3)", background: "transparent", color: "#f87171", fontSize: 12, cursor: "pointer" }}>Try again</button>
            </div>
          )}
        </div>
      )}

      {/* Input */}
      <textarea
        value={userInput}
        onChange={e => setUserInput(e.target.value)}
        onKeyDown={e => { if (e.key === "Enter" && e.ctrlKey) run() }}
        placeholder={example}
        rows={3}
        style={{
          width: "100%",
          background: "rgba(0,0,0,0.4)",
          border: "1px solid rgba(251,191,36,0.2)",
          borderRadius: 14,
          padding: "12px 16px",
          color: "white",
          fontSize: 14,
          resize: "vertical",
          outline: "none",
          fontFamily: "inherit",
          lineHeight: 1.6,
          marginBottom: 12,
          boxSizing: "border-box",
          transition: "border-color 0.2s"
        }}
      />

      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <button
          onClick={run}
          disabled={running}
          style={{
            padding: "11px 28px",
            borderRadius: 14,
            border: "none",
            background: running ? "rgba(255,255,255,0.08)" : "linear-gradient(135deg,#d97706,#fbbf24)",
            color: running ? "rgba(255,255,255,0.4)" : "#000",
            fontWeight: 800,
            fontSize: 14,
            cursor: running ? "not-allowed" : "pointer",
            transition: "all 0.15s",
            display: "flex",
            alignItems: "center",
            gap: 8
          }}
        >
          {running ? (
            <>
              <span style={{ display: "inline-block", animation: "spin 1s linear infinite" }}>⏳</span>
              Running automation...
            </>
          ) : `▶ Run ${automationTitle}`}
        </button>
        <div style={{ fontSize: 12, color: "rgba(255,255,255,0.35)" }}>or press Ctrl + Enter</div>
      </div>

    </div>
  )
}
