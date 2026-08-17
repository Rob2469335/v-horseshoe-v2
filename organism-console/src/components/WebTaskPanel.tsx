import { useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card"
import { Button } from "./ui/button"
import { Badge } from "./ui/badge"
import { ActionOnboardingCard } from "./ui/action-onboarding-card"
import { Globe } from "lucide-react"

type Step = {
  action: string
  params?: Record<string, unknown>
  reason?: string
  result?: unknown
}

type TaskResult = {
  status: string
  goal?: string
  steps?: number
  url?: string
  reason?: string
  message?: string
  pending_action?: string
  pending_params?: Record<string, unknown>
  failed?: Array<{ name: string; error: string }>
  error?: string
  history?: Step[]
}

type Props = { backendUrl: string }

export default function WebTaskPanel({ backendUrl }: Props) {
  const [goal, setGoal] = useState("")
  const [result, setResult] = useState<TaskResult | null>(null)
  const [running, setRunning] = useState(false)
  const [log, setLog] = useState<string[]>([])

  const runTask = async (confirmCritical = false) => {
    if (!goal.trim() && !confirmCritical) return
    setRunning(true)
    setLog((l) => [...l, "▶ running browser task…"])
    setResult(null)
    try {
      const body: Record<string, unknown> = { goal, max_steps: 12 }
      if (confirmCritical) body.confirm = true
      const r = await (await fetch(`${backendUrl}/features/browser-task`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })).json()
      setResult(r)
      if (r.history) {
        setLog(r.history.map((h: Step) => `• ${h.action} ${JSON.stringify(h.params ?? {})}${h.reason ? ` — ${h.reason}` : ""}`))
      }
      if (r.status === "approval_requested") setLog((l) => [...l, `⚠ ${r.reason} — approve to continue`])
      if (r.error) setLog((l) => [...l, `✗ ${r.error}`])
    } catch (e: any) {
      setLog((l) => [...l, `✗ ${e.message}`])
    } finally { setRunning(false) }
  }

  const statusTone =
    result?.status === "done" ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300"
    : result?.status === "approval_requested" ? "border-amber-400/40 bg-amber-400/10 text-amber-300"
    : result?.status === "error" || result?.status === "fill_failed" ? "border-red-400/40 bg-red-400/10 text-red-300"
    : "border-white/20 bg-white/5 text-white/40"

  return (
    <Card className="border-white/10 bg-panel xl:col-span-2">
      <CardHeader>
        <CardTitle>Web Task — Perplexity-style agent</CardTitle>
        <CardDescription>
          Drive the persistent browser to complete a goal (fill a form, navigate, do a task). The agent reads the
          page as text, acts one step at a time, verifies each change, and stops for approval on critical actions.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!running && !result && (
          <ActionOnboardingCard
            id="webtask-quickstart"
            title="Drive the Browser"
            description="The agent can read pages, click, and navigate on your behalf. Try a quick web task."
            actionLabel="Run Example Task"
            icon={<Globe size={20} />}
            onAction={() => {
              setGoal("Find the latest React 19 release notes")
              setTimeout(() => {
                const btn = document.getElementById("webtask-run-btn")
                if (btn) btn.click()
              }, 100)
            }}
          />
        )}
        <div className="flex gap-2">
          <input
            className="flex-1 rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-sm text-white/80"
            placeholder="e.g. go to duckduckgo.com, search for 'perplexity computer', click the first result"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runTask()}
            disabled={running}
          />
          <Button id="webtask-run-btn" size="sm" disabled={running || !goal.trim()} onClick={() => runTask()}>
            {running ? "Working…" : "Run task"}
          </Button>
        </div>

        {result && (
          <div className={`rounded-xl border p-3 ${statusTone}`}>
            <div className="flex items-center justify-between">
              <span className="font-semibold capitalize">{result.status}</span>
              {result.steps != null && <Badge variant="outline">{result.steps} steps</Badge>}
            </div>
            {result.url && <div className="mt-1 text-xs text-white/40 truncate">{result.url}</div>}
            {result.reason && <div className="mt-1 text-xs">{result.reason}</div>}
            {result.message && (
              <div className="mt-2 rounded-lg border border-violet-400/30 bg-violet-400/10 p-2 text-sm text-violet-200">
                🤝 Agent needs you: {result.message}
              </div>
            )}
            {result.failed && result.failed.length > 0 && (
              <div className="mt-2 text-xs text-red-300">
                {result.failed.map((f, i) => <div key={i}>✗ {f.name}: {f.error}</div>)}
              </div>
            )}
            {result.status === "approval_requested" && result.pending_action && (
              <div className="mt-3 flex items-center gap-2">
                <span className="text-xs">Pending: {result.pending_action} {JSON.stringify(result.pending_params ?? {})}</span>
                <Button size="sm" variant="destructive" onClick={() => runTask(true)}>Approve & continue</Button>
              </div>
            )}
          </div>
        )}

        {log.length > 0 && (
          <div className="max-h-48 space-y-1 overflow-y-auto rounded-xl border border-white/10 bg-black/30 p-3 font-mono text-xs">
            {log.map((line, i) => <div key={i} className={line.startsWith("⚠") ? "text-amber-300" : line.startsWith("✗") ? "text-red-300" : "text-white/70"}>{line}</div>)}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
