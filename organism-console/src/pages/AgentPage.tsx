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
    JSON.stringify(data, null, 2)
  )
}

export default function AgentPage() {
  const backendUrl = useUiStore((state) => state.backendUrl)
  const [message, setMessage] = useState("summarize current system status")
  const [lastResponse, setLastResponse] = useState<ChatResponse | undefined>(undefined)

  const chatMutation = useMutation({
    mutationFn: (nextMessage: string) => api.sendChat(backendUrl, nextMessage),
    onSuccess: (data) => setLastResponse(data)
  })

  return (
    <section className="page">
      <h1>Agent</h1>
      <p>Send a live prompt to the backend agent and inspect the returned response.</p>

      <div className="agent-layout">
        <article className="agent-panel">
          <h2>Prompt</h2>
          <textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            rows={10}
            className="agent-textarea"
            placeholder="Enter a prompt for the backend agent"
          />
          <button
            className="agent-send-button"
            onClick={() => chatMutation.mutate(message)}
            disabled={chatMutation.isPending || !message.trim()}
          >
            {chatMutation.isPending ? "Sending..." : "Send"}
          </button>
        </article>

        <article className="agent-panel">
          <h2>Response</h2>
          <pre className="agent-response">
            {chatMutation.isPending
              ? "Waiting for backend response..."
              : chatMutation.isError
                ? String(chatMutation.error.message)
                : getDisplayText(lastResponse) || "No response yet."}
          </pre>
        </article>
      </div>
    </section>
  )
}
