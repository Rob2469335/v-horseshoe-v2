import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { api } from "../lib/api"
import type { ChatResponse } from "../lib/types"
import { useUiStore } from "../state/ui-store"

function getDisplayText(data: ChatResponse | undefined) {
  if (!data) return ""
  return String(
    data.response ??
      data.answer ??
      data.output ??
      data.result ??
      JSON.stringify(data, null, 2),
  )
}

export default function AgentPage() {
  const backendUrl = useUiStore((state) => state.backendUrl)
  const [message, setMessage] = useState(
    "Report the live status of this local system in 4 bullets: backend API health, frontend UI health, latest generate request result, and any active errors. Use only observed current state. No generic placeholders.",
  )
  const [lastResponse, setLastResponse] = useState<ChatResponse | undefined>(undefined)

  const chatMutation = useMutation<ChatResponse, Error, string>({
    mutationFn: (nextMessage: string) => api.sendChat(backendUrl, nextMessage),
    onSuccess: (data: ChatResponse) => setLastResponse(data),
  })

  return (
    <section className="page">
      <div className="pageheader">
        <div>
          <h1>Agent</h1>
          <p className="page-subtitle">
            Send a live prompt to the backend agent and inspect the returned response.
          </p>
        </div>
      </div>

      <div className="agent-layout">
        <article className="agent-panel panel-accent-top">
          <h2>Prompt</h2>
          <textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            className="agent-textarea"
            placeholder="Enter a prompt for the backend agent"
            style={{
              boxShadow: "inset 0 2px 10px rgba(0,0,0,0.45)",
              color: "var(--text-soft)",
              padding: 20,
              fontSize: 14,
              lineHeight: 1.6,
              minHeight: 240,
              resize: "vertical",
            }}
          />
          <button
            className="agent-send-button"
            onClick={() => chatMutation.mutate(message)}
            disabled={chatMutation.isPending || !message.trim()}
            style={{
              width: "100%",
              height: 48,
              background: "var(--page-accent)",
              color: "#000",
              fontWeight: 900,
              textTransform: "uppercase",
              letterSpacing: "0.1em",
              border: "none",
              borderRadius: 12,
              cursor: "pointer",
              boxShadow: "0 0 20px var(--page-accent-glow)",
              opacity: chatMutation.isPending ? 0.6 : 1,
            }}
          >
            {chatMutation.isPending ? "Connecting to brain..." : "Execute Intent"}
          </button>
        </article>

        <article className="agent-panel panel-accent-top panel-accent-glow">
          <h2>Live Interpretation</h2>
          <pre className="agent-response" style={{ minHeight: 320, padding: 18 }}>
            {chatMutation.isPending
              ? "Receiving streaming tokens..."
              : chatMutation.isError
                ? String(chatMutation.error.message)
                : getDisplayText(lastResponse) || "Waiting for signal..."}
          </pre>
        </article>
      </div>
    </section>
  )
}
