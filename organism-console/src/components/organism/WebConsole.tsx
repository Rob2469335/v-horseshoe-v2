import { useState, useRef, useEffect } from "react"

interface Props {
  backendUrl: string
}

export function WebConsole({ backendUrl }: Props) {
  const [command, setCommand] = useState("")
  const [history, setHistory] = useState<{ cmd: string; output: string }[]>([
    { cmd: "system init", output: "<span style='color: #a78bfa;'>Welcome to Zenith Live Swarm Web Console. Type <span style='color: #22c55e;'>/help</span> to begin.</span>" }
  ])
  const [loading, setLoading] = useState(false)
  const outputEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    outputEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [history, loading])

  const handleExecute = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!command.trim()) return

    const currentCmd = command
    setCommand("")
    setLoading(true)

    // Add immediate echo
    setHistory((prev) => [...prev, { cmd: currentCmd, output: "⏳ Executing..." }])

    try {
      const res = await fetch(`${backendUrl}/api/cli/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: currentCmd })
      })
      if (!res.ok) {
        throw new Error(`Execution error: ${res.statusText}`)
      }
      const data = await res.json()
      setHistory((prev) => {
        const copy = [...prev]
        if (copy.length > 0 && copy[copy.length - 1].output === "⏳ Executing...") {
          copy.pop() // remove echo loading
        }
        return [...copy, { cmd: currentCmd, output: data.output || "No output returned." }]
      })
    } catch (err: any) {
      setHistory((prev) => {
        const copy = [...prev]
        if (copy.length > 0 && copy[copy.length - 1].output === "⏳ Executing...") {
          copy.pop()
        }
        return [...copy, { cmd: currentCmd, output: `<span style='color: #f87171;'>Error: ${err.message}</span>` }]
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      borderRadius: 18,
      border: "1px solid rgba(255,255,255,0.08)",
      background: "linear-gradient(180deg, rgba(20,27,41,0.78), rgba(12,18,30,0.66))",
      boxShadow: "0 16px 42px rgba(0,0,0,0.24)",
      display: "grid",
      gridTemplateRows: "38px 1fr",
      minHeight: 400,
      maxHeight: 500,
      overflow: "hidden"
    }}>
      {/* Title bar */}
      <div style={{
        background: "rgba(0,0,0,0.3)",
        borderBottom: "1px solid rgba(255,255,255,0.05)",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0 16px"
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#f87171" }} />
          <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#fbbf24" }} />
          <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#34d399" }} />
          <span style={{ color: "rgba(255,255,255,0.5)", fontSize: 11, fontWeight: 800, marginLeft: 8, textTransform: "uppercase", letterSpacing: "0.08em" }}>
            zenith_repl_uplink.sh
          </span>
        </div>
        <span style={{ color: "var(--page-accent)", fontSize: 11, fontWeight: 900 }}>
          {loading ? "📡 SYNCING" : "⚡ ONLINE"}
        </span>
      </div>

      {/* Screen body */}
      <div style={{
        display: "grid",
        gridTemplateRows: "1fr auto",
        background: "rgba(3,7,18,0.72)",
        backdropFilter: "blur(12px)",
        padding: 16,
        overflow: "hidden"
      }}>
        {/* Terminal logs */}
        <div style={{
          overflowY: "auto",
          fontSize: 13,
          fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
          lineHeight: 1.6,
          paddingBottom: 8,
          scrollbarWidth: "thin"
        }}>
          {history.map((item, index) => (
            <div key={index} style={{ marginBottom: 14 }}>
              <div style={{ color: "rgba(255,255,255,0.35)", marginBottom: 4, display: "flex", alignItems: "center", gap: 6 }}>
                <span>❯</span>
                <span style={{ color: "#7dd3fc", fontWeight: "bold" }}>{item.cmd}</span>
              </div>
              <div
                style={{ overflowX: "auto", whiteSpace: "pre-wrap", color: "#e2e8f0" }}
                dangerouslySetInnerHTML={{ __html: item.output }}
              />
            </div>
          ))}
          {loading && (
            <div style={{ color: "#fbbf24", animation: "pulse 1.5s infinite" }}>
              📡 querying core services...
            </div>
          )}
          <div ref={outputEndRef} />
        </div>

        {/* Console Input Bar */}
        <form onSubmit={handleExecute} style={{
          borderTop: "1px solid rgba(255,255,255,0.06)",
          paddingTop: 12,
          display: "flex",
          alignItems: "center",
          gap: 10
        }}>
          <span style={{ color: "var(--page-accent)", fontWeight: "bold", fontSize: 14 }}>❯</span>
          <input
            type="text"
            className="topbar__input"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            disabled={loading}
            placeholder="Type REPL command (e.g. /plan, /vote, /impact, /memory query)..."
            style={{
              flex: 1,
              background: "rgba(0,0,0,0.4)",
              border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 10,
              padding: "8px 12px",
              color: "white",
              fontSize: 13,
              fontFamily: "inherit",
              outline: "none"
            }}
          />
          <button
            type="submit"
            className="topbar__button"
            disabled={loading}
            style={{
              padding: "8px 16px",
              fontSize: 12,
              fontWeight: 800,
              border: "1px solid var(--page-accent)",
              background: "var(--page-accent-tint)",
              color: "white",
              minHeight: "auto",
              height: "auto",
              cursor: loading ? "not-allowed" : "pointer"
            }}
          >
            EXECUTE
          </button>
        </form>
      </div>
    </div>
  )
}
